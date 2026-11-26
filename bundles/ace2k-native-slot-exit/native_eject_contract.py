"""Staged ACE2K native unload contract. No transport, G-code, or deployment hook.

A caller must establish route ownership and bind the task to a live physical
unit/connection before invoking its transport. This module only describes
the native start and verifies its observed outcome. Do not substitute a
screen's cached position or a generic HTTP response for that proof.
"""
from dataclasses import dataclass
from math import isfinite
from lane_contract import still


@dataclass(frozen=True)
class Operation:
    device_uid: str
    connection_epoch: str
    lane: int
    seq: int
    issued_at: float


def _valid_operation(op):
    return (isinstance(op, Operation) and bool(op.device_uid)
            and bool(op.connection_epoch) and type(op.lane) is int
            and 0 <= op.lane < 4 and type(op.seq) is int and 1 <= op.seq <= 255
            and type(op.issued_at) in (int, float) and isfinite(op.issued_at))


def native_start_fields(op, speed_um_s, min_speed, max_speed):
    """Prepare exactly one native UNLOAD request, not a fixed-length rollback.

    length_um=0 delegates the movement budget to the MCU's unload mode.
    This is NOT authorization to move. The host owns the route and lane lock.
    The seq must be acquired from the existing link, not a second allocator.
    """
    if not _valid_operation(op):
        raise ValueError("invalid unload identity")
    if (any(type(n) is not int for n in (speed_um_s, min_speed, max_speed))
            or not 0 < min_speed <= speed_um_s <= max_speed):
        raise ValueError("invalid native speed")
    return dict(lane=op.lane, mode=4, length_um=0,
                speed_um_s=speed_um_s, seq=op.seq, free_tip=0)


def fresh_sample(sample, *, after, now):
    """Require independent native sensor, mode, encoder and FG observations."""
    stamp = getattr(sample, "stamp", ())
    return (isinstance(stamp, (tuple, list)) and len(stamp) == 4
            and all(type(t) in (int, float) and isfinite(t)
                    and after < t <= now and now - t <= 3.5 for t in stamp))


def start_gate(op, a, b, *, now, device_uid, connection_epoch):
    """Only the native lane evidence; the host must separately prove route ownership.

    The two observations must be independent reports and show a stationary
    lane with filament. A disconnected or unverified route is not authorized
    by a True result. No synthetic ready state is accepted.
    """
    if (not _valid_operation(op) or op.device_uid != device_uid
            or op.connection_epoch != connection_epoch):
        return False
    # issued_at is the preflight/ownership epoch, before actual start.
    return (fresh_sample(a, after=op.issued_at, now=now)
            and fresh_sample(b, after=op.issued_at, now=now)
            and a.present is True and b.present is True and still(a, b))


def terminal_event(op, event, *, device_uid, connection_epoch):
    """Classify a real feed event (or feed_last_query result) for this operation."""
    if (not _valid_operation(op) or op.device_uid != device_uid
            or op.connection_epoch != connection_epoch or not isinstance(event, dict)):
        return "unconfirmed"
    if (type(event.get("lane")) is not int or event["lane"] != op.lane
            or type(event.get("seq")) is not int or event["seq"] != op.seq
            or type(event.get("mode")) is not int or event["mode"] != 3):
        return "unconfirmed"
    kind = event.get("kind")
    if type(kind) is not int:
        return "unconfirmed"
    if kind == 6:  # unloaded
        return "unloaded"
    if kind == 1:  # stopped
        return "stopped"
    if kind in (2, 3, 4, 7, 8, 9, 10, 11, 12, 13, 17, 18, 19, 20, 21):
        return "failed"
    return "unconfirmed"


def outcome(op, event, a, b, *, event_received_at, now,
            device_uid, connection_epoch):
    """Completion requires the MCU terminal event AND post-event stillness.

    On a missing event, the caller may query ace2k_feed_last_query once using
    the same live connection and expected lane/seq. An empty sensor alone is
    not completion. On failure retain the source/occupancy record; the owner
    is not released until the actual stop is separately verified.
    """
    verdict = terminal_event(op, event, device_uid=device_uid,
                             connection_epoch=connection_epoch)
    if verdict == "failed":
        return "failed"
    if (verdict == "unconfirmed" or type(event_received_at) not in (int, float)
            or not isfinite(event_received_at) or event_received_at < op.issued_at
            or not fresh_sample(a, after=event_received_at, now=now)
            or not fresh_sample(b, after=event_received_at, now=now)
            or not still(a, b)):
        return "unconfirmed"
    if verdict == "stopped":
        return "cancelled"
    return "complete" if a.present is False and b.present is False else "unconfirmed"
