"""Protocol-neutral evidence for existing slot operations, no new alarm codes."""
from dataclasses import dataclass


@dataclass(frozen=True)
class LaneSample:
    present: bool
    state: str
    encoder: int
    motor: int
    stamp: tuple = ()


def accepted(reply):
    return (isinstance(reply, dict) and type(reply.get('code')) is int
            and reply['code'] == 0 and str(reply.get('msg', '')).strip().upper() != 'FORBIDDEN'
            and isinstance(reply.get('result'), dict))


def uid(reply):
    if not accepted(reply):
        return None
    values = tuple(reply['result'].get('uid%d' % n) for n in (1, 2, 3))
    return values if any(values) and all(type(v) is int and 0 <= v <= 0xffffffff for v in values) else None


def native_sample(sensors, feed, counters, fg, slot, now, *, after=None):
    """Use actual independent sensor/feed/encoder/tach reports, not cached ready.

    Counter units are native (micrometres and FG pulses); only compare
    samples from the same connection and driver. Missing data is None.
    """
    if type(slot) is not int or not 0 <= slot < 4:
        return None
    reports = (sensors, feed, counters, fg)
    if any(r is None or not 0 <= now - r[0] <= 3.5 for r in reports):
        return None
    if after is not None and any(r[0] <= after for r in reports):
        return None
    modes = feed[1].get('mode', ())
    if len(modes) != 4 or len(counters[1]) != 4 or len(fg[1]) != 4:
        return None
    states = {0: 'ready', 1: 'feeding', 2: 'rollback', 3: 'rollback',
              4: 'preloading', 5: 'assisting', 6: 'rollback_assisting',
              7: 'feed_error', 8: 'assisting'}
    if modes[slot] not in states or type(sensors[1]) is not int:
        return None
    enc, motor = counters[1][slot], fg[1][slot]
    if type(enc) is not int or type(motor) is not int or motor < 0:
        return None
    return LaneSample(bool(sensors[1] & (1 << slot)), states[modes[slot]], enc, motor,
                      tuple(r[0] for r in reports))


def stock_sample(status, gates, feed, slot):
    """V2 reports retain their real V2 validation; no manufactured fields."""
    if type(slot) is not int or not 0 <= slot < 4:
        return None
    if not all(accepted(r) for r in (status, gates, feed)):
        return None
    if not all(r.get('_v2_frame_valid') is True and r.get('_v2_payload_valid') is True
               and r.get('_v2_command') == cmd
               for r, cmd in zip((status, gates, feed), (6, 73, 76))):
        return None
    status, gates, feed = (r['result'] for r in (status, gates, feed))
    if status.get('_status_wire_valid') is not True or feed.get('_feed_wire_valid') is not True:
        return None
    slots = [x for x in status.get('slots', []) if x.get('index') == slot]
    rows = [x for x in feed.get('feed_info', []) if x.get('index') == slot]
    mask = gates.get('fields', {}).get('1')
    if len(slots) != 1 or len(rows) != 1 or type(mask) is not int:
        return None
    s, r = slots[0], rows[0]
    if s.get('_wire_valid') is not True or r.get('_wire_valid') is not True or r.get('_decoder_present') is not True:
        return None
    enc, motor = r.get('decoder'), r.get('steps')
    if type(enc) is not int or not 0 <= enc < 2**64 or type(motor) is not int or motor < 0:
        return None
    state = s.get('slot_status')
    if not isinstance(state, str) or not state or state == 'unknown':
        return None
    if enc >= 2**63:
        enc -= 2**64
    return LaneSample(bool(mask & (1 << (4 * slot))), state, enc, motor)


def still(previous, current):
    """Only a new native report can count as another observation of stillness."""
    if previous is None or current is None:
        return False
    if previous.stamp or current.stamp:
        if not previous.stamp or not current.stamp or len(previous.stamp) != len(current.stamp):
            return False
        if any(b <= a for a, b in zip(previous.stamp, current.stamp)):
            return False
    return (previous.present, previous.state, previous.encoder, previous.motor) == (
        current.present, current.state, current.encoder, current.motor) and current.state == 'ready'
