"""Bounded insert measurement engine. Hardware adapter must meet CONTRACT.md.

No module in this directory enables this engine on a real printer yet.
"""
from dataclasses import dataclass
import math


class MotionFault(Exception):
    pass


@dataclass(frozen=True)
class Result:
    status: str
    uid: str | None = None
    circumference_mm: float | None = None
    travelled_mm: float = 0
    park_verified: bool = False


class SegmentedPreload:
    def __init__(self, io, segment_mm=5, budget_mm=100, timeout_s=60):
        if not (0 < segment_mm <= 10 and 0 < budget_mm <= 700 and 0 < timeout_s <= 300):
            raise ValueError('unsafe scan limits')
        self.io, self.segment, self.budget, self.timeout = io, segment_mm, budget_mm, timeout_s

    def run(self, session):
        io = self.io
        start = io.now()
        travelled = 0.0
        entries = []
        previous_kind = None
        uid = None
        moving = False
        owned = False
        try:
            # Acquisition must serialize all direct and background RF on the ACE.
            if not io.acquire(session):
                return Result('deferred')
            owned = True
            if not io.valid(session) or not io.ready(session):
                return Result('cancelled')
            while travelled < self.budget and io.now() - start < self.timeout:
                if not io.valid(session):
                    return Result('cancelled', travelled_mm=travelled)
                if io.head_state(session) != 'clear':
                    return Result('sensor', travelled_mm=travelled)
                # RF is only allowed after the adapter has confirmed rest and
                # both readers' native workers are quiescent under the lease.
                observation = io.observe(session)
                kind, candidate = observation
                if kind == 'present':
                    if not isinstance(candidate, str) or not candidate:
                        return Result('ambiguous', travelled_mm=travelled)
                    if uid is not None and candidate != uid:
                        return Result('ambiguous', travelled_mm=travelled)
                    uid = candidate
                    if previous_kind == 'absent':
                        entries.append(travelled)
                elif kind == 'conflict':
                    return Result('ambiguous', uid, travelled_mm=travelled)
                # Only adjacent verified absence -> presence bounds an entry.
                # An unknown sample breaks the edge, rather than inventing it.
                previous_kind = kind
                if len(entries) == 2:
                    period = entries[-1] - entries[-2]
                    # A possible skipped revolution would be at least twice
                    # the core circumference. Keep a two-segment uncertainty
                    # for the sampled entry boundaries.
                    low, high = period - 2*self.segment, period + 2*self.segment
                    if low > math.pi * 87 and high < 2 * math.pi * 87:
                        return Result('interval', uid, period, travelled, False)
                if len(entries) >= 3:
                    a, b = entries[-2] - entries[-3], entries[-1] - entries[-2]
                    margin = 2 * self.segment
                    if (min(a, b) - margin > math.pi * 87
                            and max(a, b) + margin < 2 * math.pi * 87
                            and abs(a-b) <= 2 * margin):
                        return Result('interval', uid, b, travelled, False)
                    return Result('ambiguous', uid, travelled_mm=travelled)
                amount = min(self.segment, self.budget - travelled)
                moving = True
                # move_and_stop checks sensors while moving, issues STOP, then
                # checks two fresh READY samples and equal signed encoders.
                distance = io.move_and_stop(session, amount)
                moving = False
                if not isinstance(distance, (float, int)) or isinstance(distance, bool) or not math.isfinite(distance) or not 0 < distance <= amount * 1.5 + 2:
                    raise MotionFault('invalid encoder displacement')
                travelled += distance
            return Result('pending', uid, travelled_mm=travelled)
        except MotionFault as exc:
            io.latch(session, str(exc))
            return Result('motion_fault', travelled_mm=travelled)
        except Exception as exc:
            if moving:
                try:
                    io.emergency_stop(session)
                except Exception:
                    pass
                io.latch(session, 'motion outcome uncertain: %s' % exc)
                return Result('motion_fault', travelled_mm=travelled)
            return Result('ambiguous', travelled_mm=travelled)
        finally:
            if owned:
                io.release(session)
