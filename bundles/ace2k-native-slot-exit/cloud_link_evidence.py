"""Offline production-link evidence adapter; not a motion entry point.

The connection coordinator owns its lifetime and must call invalidate() before
replacing/reconnecting the transport. Never accept a sample supplied by UI.
"""
import math
import struct
import uuid
from dataclasses import dataclass
from lane_contract import native_sample
from native_eject_contract import outcome


@dataclass(frozen=True)
class BoundSample:
    session: str
    uid: str
    lane: int
    sample: object


class LinkEvidence:
    def __init__(self, link):
        self._link = link
        self._raw = link._raw
        self._uid = bytes(link.uid)
        if len(self._uid) != 12 or not any(self._uid):
            raise ValueError("unverified native UID")
        self.uid = "uid:" + "-".join("%08x" % word for word in struct.unpack("<3I", self._uid))
        self.epoch = uuid.uuid4().hex
        self._valid = True

    def invalidate(self):
        with self._link._io:
            self._valid = False

    def _live(self):
        return (self._valid and self._link._raw is self._raw
                and self._link.is_open and self._link._dead is None
                and bytes(self._link.uid) == self._uid)

    def sample(self, lane, after=None):
        link = self._link
        with link._io:
            if not self._live():
                return None
            sample = native_sample(link._evidence_sensors, link._feed,
                                   link._evidence_enc, link._fg, lane,
                                   link._now(), after=after)
            return BoundSample(self.epoch, self.uid, lane, sample) if sample is not None else None

    def matches(self, op, sample):
        return (self._live() and isinstance(sample, BoundSample)
                and (op.connection_epoch, op.device_uid, op.lane) ==
                    (self.epoch, self.uid, sample.lane)
                and (sample.session, sample.uid) == (self.epoch, self.uid))

    def verdict(self, op, event, a, b, received_at):
        with self._link._io:
            if not (self.matches(op, a) and self.matches(op, b)):
                return "unconfirmed"
            return outcome(op, event, a.sample, b.sample,
                           event_received_at=received_at, now=self._link._now(),
                           device_uid=self.uid, connection_epoch=self.epoch)

    def bind_event(self, wire, event):
        """Call inside the actual link's feed-event dispatch, before queue drain.

        Records only; never issues a wire frame from the receive callback.
        """
        with self._link._io:
            now = self._link._now()
            if (not self._live() or wire.link is not self._link
                    or wire.op.connection_epoch != self.epoch
                    or wire.op.device_uid != self.uid or not math.isfinite(now)):
                return False
            return wire.note_event(event, received_at=now,
                                   device_uid=self.uid, connection_epoch=self.epoch)
