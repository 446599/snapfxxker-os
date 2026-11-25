"""Per-load stationary RFID observations; no hardware or database writes."""
import math
try:
    from .ace_filament_approximation import estimate_remaining
except ImportError:
    from ace_filament_approximation import estimate_remaining

class LoadEstimate:
    def __init__(self, session, ace, slot, connection):
        self.session, self.ace, self.slot, self.connection = session, ace, slot, connection
        self.last_mm = None
        self.uid = None
        self.phase = 'seeking'
        self.hits = self.misses = 0
        self.edges = []
        self.reason = 'awaiting two tag passages'
        self.invalid = False

    def invalidate(self, reason):
        self.invalid = True; self.reason = reason
        self.edges.clear()

    def observe(self, *, session, ace, slot, connection, mm, kind, uid=None):
        if self.invalid: return
        if (session != self.session or ace != self.ace or slot != self.slot
                or connection is not self.connection):
            self.invalidate('session or mapping changed'); return
        if (isinstance(mm, bool) or not isinstance(mm, (int,float)) or not math.isfinite(mm)
                or mm < 0 or (self.last_mm is not None and not 0 < mm-self.last_mm <= 30)):
            self.invalidate('encoder reversed or sampling gap'); return
        if kind not in ('uid', 'not_observed'):
            self.invalidate('RF error or collision'); return
        if kind == 'uid':
            if not isinstance(uid,str) or len(uid)!=8 or any(c not in '0123456789ABCDEF' for c in uid.upper()):
                self.invalidate('invalid UID'); return
            uid=uid.upper()
            if self.uid is not None and uid != self.uid:
                self.invalidate('multiple or changed UID'); return
            self.uid=uid; self.hits += 1; self.misses=0
            if self.hits == 2 and self.phase == 'outside':
                self.edges.append(mm); self.phase='inside'
            elif self.hits >= 2 and self.phase == 'seeking':
                self.phase='inside'
        else:
            if uid: self.invalidate('malformed RF observation'); return
            self.misses += 1; self.hits=0
            if self.misses>=2: self.phase='outside'
        self.last_mm=mm

    def result(self, *, attributed, timestamp):
        out={'state':'pending','uid':self.uid,'remaining_g':None,'reason':self.reason,
             'source':'segmented_load_rfid_encoder','timestamp':timestamp,
             'quality':'unverified_tag_coverage'}
        if self.invalid:
            out.update(state='ambiguous',reason=self.reason); return out
        if not attributed:
            out.update(state='ambiguous',reason='slot attribution unavailable'); return out
        if len(self.edges)<2: return out
        period=self.edges[-1]-self.edges[-2]
        # Physical core limit excludes impossible sub-core periods, not missed turns.
        if not math.pi*87 <= period <= 600:
            out.update(state='ambiguous',reason='period outside assumed spool geometry'); return out
        try:
            estimate=estimate_remaining(period,87,60,1.75,1.24)
        except ValueError:
            out.update(state='ambiguous',reason='incompatible spool geometry'); return out
        out.update(state='estimate',period_mm=period,remaining_g=estimate['remaining_g'],
                   reason=None, quality='estimated_unverified_coverage')
        return out
