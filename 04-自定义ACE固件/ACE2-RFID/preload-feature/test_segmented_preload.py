import unittest
from ace_segmented_preload import SegmentedPreload, MotionFault

class FakeIO:
    def __init__(self, observations, fail=None):
        self.observations = iter(observations)
        self.fail, self.t, self.moves, self.faults = fail, 0, [], []
        self.released = False
    def now(self): return self.t
    def acquire(self, session): return self.fail != 'busy'
    def valid(self, session): return self.fail != 'cancel'
    def ready(self, session): return True
    def head_state(self, session): return 'triggered' if self.fail == 'head' else 'clear'
    def observe(self, session): return next(self.observations, ('absent', None))
    def move_and_stop(self, session, amount):
        self.moves.append(amount); self.t += 1
        if self.fail == 'stop': raise MotionFault('STOP rejected')
        if self.fail == 'disconnect': raise RuntimeError('connection lost')
        return amount
    def emergency_stop(self, session): pass
    def latch(self, session, reason): self.faults.append(reason)
    def release(self, session): self.released = True

class SegmentTests(unittest.TestCase):
    def run_scan(self, observations, fail=None, **kwargs):
        io = FakeIO(observations, fail)
        return io, SegmentedPreload(io, **kwargs).run(('ace', 0, 1))
    def test_three_entries_give_repeatable_period(self):
        obs = [('absent',None),('present','A'),('absent',None),('present','A'),('absent',None),('present','A')]
        io, result = self.run_scan(obs)
        self.assertEqual(result.status, 'ambiguous')
        self.assertIsNone(result.circumference_mm)
        self.assertEqual(io.moves, [5]*5)
        self.assertTrue(io.released)
    def test_two_bounded_entries_yield_period_interval(self):
        observations = [('absent',None),('present','A')] + [('present','A')]*58 + [('absent',None),('present','A')]
        io, result = self.run_scan(observations, budget_mm=400, timeout_s=120)
        self.assertEqual(result.status, 'interval')
        self.assertEqual(result.circumference_mm, 300)
    def test_unknown_breaks_edge_pair(self):
        observations = [('absent',None),('collision',None),('present','A')]
        io, result = self.run_scan(observations, budget_mm=15)
        self.assertEqual(result.status, 'pending')
        self.assertIsNone(result.circumference_mm)
    def test_no_card_pending(self):
        io, result = self.run_scan([], budget_mm=10)
        self.assertEqual(result.status, 'pending')
        self.assertIsNone(result.uid)
    def test_collision_never_counted_as_gap(self):
        io, result = self.run_scan([('present','A'),('collision',None),('present','A')], budget_mm=15)
        self.assertEqual(result.status, 'pending')
        self.assertIsNone(result.circumference_mm)
    def test_conflicting_uid_is_ambiguous(self):
        io, result = self.run_scan([('present','A'),('absent',None),('present','B')])
        self.assertEqual(result.status, 'ambiguous')
    def test_stop_failure_latches(self):
        io, result = self.run_scan([('absent',None)], 'stop')
        self.assertEqual(result.status, 'motion_fault')
        self.assertTrue(io.faults)
    def test_disconnect_during_motion_latches(self):
        io, result = self.run_scan([('absent',None)], 'disconnect')
        self.assertEqual(result.status, 'motion_fault')
        self.assertTrue(io.faults)
    def test_cancel_head_and_busy_do_not_move(self):
        for fail in ('cancel','head','busy'):
            io, result = self.run_scan([], fail)
            self.assertEqual(io.moves, [])
            self.assertIn(result.status, ('cancelled','sensor','deferred'))

if __name__ == '__main__': unittest.main()
