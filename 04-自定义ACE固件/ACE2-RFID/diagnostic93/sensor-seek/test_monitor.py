import unittest
from monitor import Monitor

class MonitorTest(unittest.TestCase):
    def test_target_hit_latched_after_clear(self):
        t = object(); m = Monitor(0, 2, 2, t)
        m.begin(t, [False]*4); m.feed_ack(True)
        self.assertTrue(m.sample_sensors(t, [False, False, True, False]))
        self.assertFalse(m.sample_sensors(t, [False]*4))
        m.stopped(True, True, 123, 123)
        self.assertEqual((m.state, m.decoder), ('at_sensor', 123))

    def test_wrong_sensor_and_missing_ack_are_not_success(self):
        t = object(); m = Monitor(0, 2, 2, t)
        m.begin(t, [False]*4)
        m.sample_sensors(t, [True, False, False, False])
        m.stopped(True, True, 5, 5)
        self.assertEqual(m.state, 'uncertain')

    def test_sensor_and_transport_fail_stop(self):
        for transport, sensors in ((None, [False]*4), ('same', [False, False, None, False])):
            t = object(); m = Monitor(0, 2, 2, t); m.begin(t, [False]*4)
            self.assertTrue(m.sample_sensors(t if transport else object(), sensors))
            self.assertTrue(m.stop_requested)

    def test_stop_or_decoder_failure_uncertain(self):
        for args in ((False, True, 3, 3), (True, False, 3, 3), (True, True, 3, 4)):
            t = object(); m = Monitor(0, 2, 2, t); m.begin(t, [False]*4)
            m.feed_ack(True); m.sample_sensors(t, [False, False, True, False])
            m.stopped(*args); self.assertEqual(m.state, 'uncertain')

if __name__ == '__main__': unittest.main()
