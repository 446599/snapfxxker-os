"""Development-only persistent route ownership.

All motion callers must acquire the SAME owner before sending anything.
A read/check alone is not a lease. The route must come from verified physical
topology, never HEAD == SLOT. No device I/O or firmware packaging in this file.
"""
import fcntl
import json
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass(frozen=True)
class Route:
    uid: str
    port: str
    lane: int
    reader: str
    heads: tuple

    def __post_init__(self):
        if (not isinstance(self.uid, str) or not self.uid
                or not isinstance(self.port, str) or not self.port
                or type(self.lane) is not int or not 0 <= self.lane < 4
                or not isinstance(self.reader, str)
                or not isinstance(self.heads, tuple)
                or any(type(h) is not int or not 0 <= h < 4 for h in self.heads)
                or len(set(self.heads)) != len(self.heads)):
            raise ValueError("invalid explicit physical route")

    def conflicts(self, other):
        # A replaced unit on the same port must not evade a recovery hold.
        same_identity = self.uid == other.uid or self.port == other.port
        moved_identity = self.uid != other.uid or self.port != other.port
        return (same_identity and
                    (self.lane == other.lane or bool(self.reader) and self.reader == other.reader
                     or moved_identity and bool(self.reader and other.reader))
                or bool(set(self.heads) & set(other.heads)))


@dataclass(frozen=True)
class Claim:
    token: str
    route: Route
    state: str


class RouteBusy(RuntimeError):
    pass


class RouteOwner:
    """One durable journal/owner per provider; no silent recovery or TTL."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._mutex = threading.RLock()
        self._broken = False
        self._claims = {}
        self._lock = open(str(self.path) + ".lock", "a+b")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.path.exists():
                rows = json.loads(self.path.read_text())
                if not isinstance(rows, dict) or rows.get("schema") != 1 or not isinstance(rows.get("claims"), list):
                    raise ValueError("bad route journal")
                for row in rows["claims"]:
                    fields = dict(row["route"])
                    fields["heads"] = tuple(fields["heads"])
                    route = Route(**fields)
                    token = row["token"]
                    if (not isinstance(token, str) or not token or token in self._claims
                            or row["state"] not in ("reserved", "dispatched", "verified", "recovery")
                            or any(route.conflicts(c.route) for c in self._claims.values())):
                        raise ValueError("invalid persisted claim")
                    self._claims[token] = Claim(token, route, "recovery")
                self._persist(self._claims)
        except BaseException:
            self._lock.close()
            raise

    def close(self):
        with self._mutex:
            self._broken = True
            self._lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _require_usable(self):
        if self._broken:
            raise RuntimeError("route journal unavailable")

    def _persist(self, claims):
        fd, name = tempfile.mkstemp(prefix=".route-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"schema": 1, "claims": [asdict(c) for c in claims.values()]}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, self.path)
            dfd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except BaseException:
            self._broken = True
            raise
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _change(self, claims):
        self._require_usable()
        self._persist(claims)
        self._claims = claims

    def reserve(self, route):
        if not isinstance(route, Route):
            raise TypeError("explicit Route required")
        with self._mutex:
            self._require_usable()
            if any(route.conflicts(c.route) for c in self._claims.values()):
                raise RouteBusy("physical route already held")
            token = uuid.uuid4().hex
            claim = Claim(token, route, "reserved")
            self._change({**self._claims, token: claim})
            return claim

    def snapshot(self):
        with self._mutex:
            self._require_usable()
            return tuple(self._claims.values())

    def _get(self, claim):
        self._require_usable()
        current = self._claims.get(claim.token)
        if current != claim:
            raise RouteBusy("stale route claim")
        return current

    def mark_dispatched(self, claim):
        """Commit before the first possible wire write. Never replay this call."""
        with self._mutex:
            current = self._get(claim)
            if current.state != "reserved":
                raise RouteBusy("already dispatched or recovering")
            updated = Claim(current.token, current.route, "dispatched")
            self._change({**self._claims, updated.token: updated})
            return updated

    def mark_verified(self, claim):
        """Persist verified completion while keeping the motion reservation."""
        with self._mutex:
            current = self._get(claim)
            if current.state != "dispatched":
                raise RouteBusy("no dispatched operation to complete")
            updated = Claim(current.token, current.route, "verified")
            self._change({**self._claims, updated.token: updated})
            return updated

    def hold_recovery(self, claim):
        with self._mutex:
            current = self._get(claim)
            updated = Claim(current.token, current.route, "recovery")
            self._change({**self._claims, updated.token: updated})
            return updated

    def abandon_before_dispatch(self, claim):
        with self._mutex:
            current = self._get(claim)
            if current.state != "reserved":
                raise RouteBusy("motion may have started")
            self._change({k: v for k, v in self._claims.items() if k != current.token})

    def release_verified(self, claim, verify):
        """Coordinator-only: verifier must prove this claim's live stop AND route.

        The verifier must be nonblocking, not yield, and not start motion.
        It must use the actual connection/task evidence. No timeout releases.
        Caller must serialize verification with its transport and motion paths.
        """
        with self._mutex:
            current = self._get(claim)
            if current.state == "reserved" or verify(current) is not True:
                raise RouteBusy("no verified stop and route")
            self._change({k: v for k, v in self._claims.items() if k != current.token})
