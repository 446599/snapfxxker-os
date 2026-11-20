"""Conservative UID passage intervals for a bounded measurement owner.

No device I/O, movement, position restoration or attribution is performed here.
"""
class EdgeError(ValueError):
    pass

class EdgeAmbiguous(EdgeError):
    pass

class PassageIntervals:
    def __init__(self, uid):
        if not isinstance(uid, str) or not uid:
            raise EdgeError('UID required')
        self.uid = uid
        self.last_pos = None
        self.last_present = None
        self.absent_pos = None
        self.edges = []

    def observe(self, before, after, observed_uid):
        if type(before) is not int or type(after) is not int or before < 0 or after < before:
            raise EdgeError('invalid encoder position')
        if self.last_pos is not None and before < self.last_pos:
            raise EdgeError('encoder direction changed')
        if observed_uid is None:
            raise EdgeError('RF result ambiguous')
        if observed_uid not in ('', self.uid):
            raise EdgeError('different UID')
        if observed_uid == '':
            self.absent_pos = before
        elif self.absent_pos is not None:
            if after - self.absent_pos > 20:
                raise EdgeAmbiguous('UID entry bracket exceeds 20 mm')
            self.edges.append((self.absent_pos, after))
            self.absent_pos = None
        self.last_pos = after
        self.last_present = bool(observed_uid)

    def circumference(self):
        if len(self.edges) < 2:
            return None
        a, b = self.edges[-2:]
        low, high = b[0] - a[1], b[1] - a[0]
        # Entire interval must be in the native BB00 acceptance range.
        if not (180 < low <= high < 600) or high - low > 20:
            raise EdgeAmbiguous('cycle interval not consecutive or wider than 20 mm')
        return {'min_mm': low, 'max_mm': high, 'mid_mm': (low + high) / 2.0}
