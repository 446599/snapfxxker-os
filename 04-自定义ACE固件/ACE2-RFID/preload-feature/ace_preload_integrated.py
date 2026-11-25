"""Insert adapter joining guarded movement, exclusive RF and volatile result."""
from .ace_preload_motion import PreloadMotion
from .ace_preload_rf import PreloadRf
from .ace_segmented_preload import SegmentedPreload, MotionFault
from .ace_filament_approximation import estimate_remaining

PARK_MM = 390
SCAN_TIMEOUT = 300

class InsertIO:
    def __init__(self, ace, reader, idx, slot, valid):
        self.ace, self.reader, self.idx, self.slot, self.valid_fn = ace, reader, idx, slot, valid
        self.motion = PreloadMotion(ace, reader, idx, slot, valid)
        self.rf = PreloadRf(ace, reader, idx, slot, valid)
        self.identity_data = None
        self.identity_uid = None
        self.total = 0
    def now(self): return self.ace.reactor.monotonic()
    def valid(self, session): return self.valid_fn()
    def ready(self, session):
        return self.valid_fn() and self.reader._target_motion_allowed(self.idx, self.slot) and self.motion.head_clear()
    def head_state(self, session): return 'clear' if self.motion.head_clear() else 'unknown'
    def acquire(self, session): return self.rf.acquire()
    def release(self, session): pass  # lease held until verified park
    def observe(self, session):
        kind, uid = self.rf.observe()
        if kind == 'present' and self.identity_uid is None:
            data = self.rf.identity(uid)
            # Another card or communication error during the block reads
            # invalidates both UID and identity for this observation.
            second = self.rf.observe()
            if second != ('present', uid):
                return ('ambiguous', None)
            self.identity_uid, self.identity_data = uid, data
        return (kind, uid)
    def move_and_stop(self, session, amount):
        distance = self.motion.move_and_stop(int(amount))
        self.total += distance
        return distance
    def emergency_stop(self, session): self.motion.stop()
    def latch(self, session, reason): self.ace._latch_stop_fault(self.idx, self.slot, reason)

def run_insert(ace, reader, idx, slot, depth, valid):
    if type(depth) is not int or not 0 <= depth <= PARK_MM or not valid():
        return {'state':'pending', 'park_verified':False, 'reason':'unknown starting depth'}
    io = InsertIO(ace, reader, idx, slot, valid)
    budget = PARK_MM - depth
    if budget < 3:
        return {'state':'pending', 'park_verified':True, 'reason':'insufficient scan distance'}
    try:
        result = _run_owned(ace, io, idx, slot, depth, valid, budget)
    finally:
        try:
            io.rf.release()
        except Exception as exc:
            result = {'state':'ambiguous', 'park_verified':False,
                      'depth_mm':depth + io.total, 'reason':'RF restore failed: %s' % exc}
    return result

def _run_owned(ace, io, idx, slot, depth, valid, budget):
    try:
        scan = SegmentedPreload(io, segment_mm=5, budget_mm=budget, timeout_s=SCAN_TIMEOUT).run((idx, slot))
        if scan.status in ('motion_fault','cancelled','sensor','deferred') or not valid():
            state = 'cancelled' if not valid() and scan.status != 'motion_fault' else scan.status
            return {'state':state, 'park_verified':False, 'depth_mm':depth + io.total}
        # Position is based on the verified native abort depth plus each
        # individually stopped and verified encoder delta; no guessed rewind.
        deadline = io.now() + SCAN_TIMEOUT
        while PARK_MM - depth - io.total > 2 and io.now() < deadline:
            if not valid() or not io.ready((idx,slot)):
                return {'state':'cancelled', 'park_verified':False, 'depth_mm':depth + io.total}
            remaining = PARK_MM - depth - io.total
            io.move_and_stop((idx,slot), min(5, int(remaining)))
            if depth + io.total > PARK_MM + 2:
                raise MotionFault('encoder overshot park boundary')
        parked = valid() and abs(PARK_MM - depth - io.total) <= 2
        result = {'state':scan.status, 'park_verified':parked,
                  'uid':scan.uid or io.identity_uid, 'identity':io.identity_data,
                  'depth_mm':depth + io.total, 'captured_at':io.now()}
        if scan.status == 'interval' and scan.uid == io.identity_uid and io.identity_data:
            material = io.identity_data.get('type','').upper()
            if 'PLA' in material:
                try:
                    estimate = estimate_remaining(scan.circumference_mm, 87, 60, 1.75, 1.24)
                    lo = estimate_remaining(scan.circumference_mm - 10, 87, 60, 1.75, 1.24)
                    hi = estimate_remaining(scan.circumference_mm + 10, 87, 60, 1.75, 1.24)
                    result.update(state='interval', remaining_g_min=round(lo['remaining_g']),
                                  remaining_g_max=round(hi['remaining_g']))
                    result.update(circumference_mm=scan.circumference_mm,
                                  assumptions=estimate['assumptions'])
                except ValueError:
                    result['state'] = 'ambiguous'
        return result
    except MotionFault as exc:
        ace._latch_stop_fault(idx, slot, str(exc))
        return {'state':'motion_fault', 'park_verified':False, 'reason':str(exc)}
    except Exception as exc:
        return {'state':'ambiguous', 'park_verified':False, 'reason':str(exc)}
