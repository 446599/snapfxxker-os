import unittest
from lane_contract import LaneSample
from native_eject_contract import (Operation, native_start_fields, start_gate,
                                   terminal_event, outcome)


class NativeExitContractTest(unittest.TestCase):
    def setUp(self):
        self.op = Operation("uid:A", "connection-1", 2, 17, 10.0)
        self.identity = dict(device_uid="uid:A", connection_epoch="connection-1")
        self.ev = dict(lane=2, seq=17, mode=3, kind=6)
        self.a = LaneSample(False, "ready", 100, 20, (12, 12, 12, 12))
        self.b = LaneSample(False, "ready", 100, 20, (13, 13, 13, 13))

    def finish(self, event=None, a=None, b=None, **kw):
        return outcome(self.op, self.ev if event is None else event,
                       self.a if a is None else a, self.b if b is None else b,
                       **dict(dict(event_received_at=11, now=13.1, **self.identity), **kw))

    def test_native_unload_not_rollback(self):
        f = native_start_fields(self.op, 80000, 9000, 80000)
        self.assertEqual(f, dict(lane=2, mode=4, length_um=0,
                                 speed_um_s=80000, seq=17, free_tip=0))

    def test_invalid_lane_seq_speed(self):
        for lane, seq in ((-1, 1), (4, 1), (0, 0), (0, 256), (True, 1)):
            with self.assertRaises(ValueError):
                native_start_fields(Operation("uid:A", "connection-1", lane, seq, 10),
                                    80000, 9000, 80000)
        with self.assertRaises(ValueError):
            native_start_fields(self.op, 80001, 9000, 80000)

    def test_present_and_still_before_start(self):
        a = LaneSample(True, "ready", 100, 20, (11, 11, 11, 11))
        b = LaneSample(True, "ready", 100, 20, (12, 12, 12, 12))
        self.assertTrue(start_gate(self.op, a, b, now=12.1, **self.identity))
        self.assertFalse(start_gate(self.op, a, a, now=12.1, **self.identity))
        self.assertFalse(start_gate(self.op, a, self.b, now=13.1, **self.identity))
        self.assertFalse(start_gate(self.op, a, b, now=15.1, **self.identity))

    def test_moving_or_missing_evidence_cannot_start(self):
        a = LaneSample(True, "ready", 100, 20, (11, 11, 11, 11))
        b = LaneSample(True, "rollback", 100, 20, (12, 12, 12, 12))
        self.assertFalse(start_gate(self.op, a, b, now=12.1, **self.identity))
        self.assertFalse(start_gate(self.op, a, None, now=12.1, **self.identity))

    def test_native_terminal_and_empty_stillness(self):
        self.assertEqual(self.finish(), "complete")

    def test_wrong_unit_connection_lane_seq_mode(self):
        self.assertEqual(self.finish(device_uid="uid:B"), "unconfirmed")
        self.assertEqual(self.finish(connection_epoch="connection-2"), "unconfirmed")
        for key, v in (("lane", 1), ("seq", 18), ("seq", 0), ("mode", 2)):
            self.assertEqual(self.finish(event=dict(self.ev, **{key: v})), "unconfirmed")

    def test_missing_event_or_done_is_not_eject(self):
        self.assertEqual(self.finish(event={}), "unconfirmed")
        self.assertEqual(self.finish(event=dict(self.ev, kind=0)), "unconfirmed")

    def test_stale_or_duplicate_sample(self):
        self.assertEqual(self.finish(b=self.a), "unconfirmed")
        self.assertEqual(self.finish(now=20), "unconfirmed")
        self.assertEqual(self.finish(event_received_at=12.5), "unconfirmed")

    def test_inlet_still_present_or_motor_moving(self):
        a = LaneSample(True, "ready", 100, 20, self.a.stamp)
        b = LaneSample(True, "ready", 100, 20, self.b.stamp)
        self.assertEqual(self.finish(a=a, b=b), "unconfirmed")
        self.assertEqual(self.finish(b=LaneSample(False, "ready", 100, 21, self.b.stamp)),
                         "unconfirmed")

    def test_stop_is_cancel_not_success(self):
        self.assertEqual(self.finish(event=dict(self.ev, kind=1)), "cancelled")

    def test_real_error_never_success_even_if_sensor_empty(self):
        for k in (7, 9, 10, 13, 21):
            self.assertEqual(self.finish(event=dict(self.ev, kind=k)), "failed")


if __name__ == "__main__":
    unittest.main()
