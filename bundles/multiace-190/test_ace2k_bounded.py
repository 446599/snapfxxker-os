"""Bounded-probe and ownership tests that the 13 mock-based cases do not cover.

These exercise the paths the handover flags as unverified:
  - a hung probe must not block the caller past the 5.5 s reactor budget
  - the pending job keeps port ownership after a timeout
  - a completed job is collected exactly once
  - a device token change discards the result of an in-flight probe
"""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from ace2k_discovery import SharedAce2Discovery


class Reactor:
    monotonic = staticmethod(time.monotonic)

    def pause(self, until):
        time.sleep(max(0, until - time.monotonic()))


class Codec:
    NAME = 'fake'

    def __init__(self, delay=0.0, result=None):
        self.delay = delay
        self.result = result or {'ok': True, 'category': 'verified', 'uid': (1, 2, 3)}
        self.calls = 0

    def serial_port_in_use(self, path):
        return False

    def uid_key(self, words):
        return 'uid' if words else ''

    def probe_device(self, path, query_timeout=0.35):
        self.calls += 1
        time.sleep(self.delay)
        return dict(self.result)


class BoundedProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'tty')
        Path(self.path).touch()
        self.key = os.path.realpath(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_hung_probe_returns_within_budget_and_keeps_ownership(self):
        slow = Codec(delay=8.0)
        detector = SharedAce2Discovery()
        started = time.monotonic()
        self.assertIsNone(detector.identify(self.path, Reactor(), [1], slow, slow))
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 7.0, 'caller must not wait for the worker')
        self.assertGreaterEqual(elapsed, 5.0, 'should use the 5.5s budget')
        self.assertIn(self.key, detector.pending, 'ownership retained after timeout')

        # While the worker still runs, a second call must not start another probe.
        calls_before = slow.calls
        self.assertIsNone(detector.identify(self.path, Reactor(), [1], slow, slow))
        self.assertEqual(slow.calls, calls_before)

        # Drain the worker so the test leaves no thread behind.
        done = detector.pending[self.key]['done']
        self.assertTrue(done.wait(10.0))

    def test_completed_job_is_collected_once(self):
        quick = Codec(delay=0.05)
        detector = SharedAce2Discovery()
        first = detector.identify(self.path, Reactor(), [1], quick, quick)
        self.assertIsNotNone(first)
        self.assertNotIn(self.key, detector.pending)
        self.assertEqual(quick.calls, 1)

        # Cached now: no second probe, same answer.
        second = detector.identify(self.path, Reactor(), [1], quick, quick)
        self.assertIsNotNone(second)
        self.assertEqual(quick.calls, 1)

    def test_token_change_discards_inflight_result(self):
        slow = Codec(delay=0.4)
        detector = SharedAce2Discovery()
        original = detector._token(self.path)
        result = {}

        def run():
            result['value'] = detector.identify(self.path, Reactor(), [1], slow, slow)

        worker = threading.Thread(target=run)
        worker.start()
        time.sleep(0.1)
        # Simulate the tty being replaced while the probe is in flight.
        detector.pending[self.key]['token'] = ('stale',)
        worker.join(10.0)
        self.assertIsNone(result['value'], 'result from a replaced device is discarded')

    def test_negative_cache_expires(self):
        detector = SharedAce2Discovery()
        silent = Codec(result={'ok': False, 'category': 'no_response'})
        self.assertIsNone(detector.identify(self.path, Reactor(), [1], silent, silent))
        cached = detector.cache[self.key]
        self.assertGreater(cached[1], time.monotonic(), 'negative entry carries a TTL')
        self.assertLess(cached[1] - time.monotonic(), 16.0, 'negative TTL is 15s')


if __name__ == '__main__':
    unittest.main()
