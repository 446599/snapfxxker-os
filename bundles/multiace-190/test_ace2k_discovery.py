import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ace2k_discovery import SharedAce2Discovery, probe_shared_ace2


class Reactor:
    monotonic = staticmethod(time.monotonic)

    def pause(self, until):
        time.sleep(max(0, until - time.monotonic()))


def codec(name, category='verified', uid=(1, 2, 3)):
    obj = Mock()
    obj.NAME = name
    obj.serial_port_in_use.return_value = False
    obj.uid_key.side_effect = lambda words: 'uid' if len(words) == 3 and any(words) else ''
    obj.probe_device.return_value = {'ok': category == 'verified',
                                     'category': category, 'uid': uid}
    return obj


class DiscoveryTests(unittest.TestCase):
    def test_stock_wins_without_second_probe(self):
        v2, k = codec('v2'), codec('ace2k')
        self.assertIs(probe_shared_ace2('p', v2, k)[0], v2)
        k.probe_device.assert_not_called()

    def test_ace2k_after_stock_silence(self):
        v2, k = codec('v2', 'no_response'), codec('ace2k')
        self.assertIs(probe_shared_ace2('p', v2, k)[0], k)

    def test_foreign_frames(self):
        v2, k = codec('v2', 'invalid_response'), codec('ace2k')
        self.assertIs(probe_shared_ace2('p', v2, k)[0], k)

    def test_no_fallback_after_access_or_identity_error(self):
        for reason in ('permission_denied', 'port_in_use', 'device_disconnected',
                       'serial_error', 'invalid_identity'):
            with self.subTest(reason=reason):
                v2, k = codec('v2', reason), codec('ace2k')
                self.assertIsNone(probe_shared_ace2('p', v2, k)[0])
                k.probe_device.assert_not_called()

    def test_both_silent(self):
        v2, k = codec('v2', 'no_response'), codec('ace2k', 'no_response')
        self.assertIsNone(probe_shared_ace2('p', v2, k)[0])

    def test_zero_uid_not_claimed(self):
        v2, k = codec('v2', 'no_response'), codec('ace2k', uid=(0, 0, 0))
        self.assertIsNone(probe_shared_ace2('p', v2, k)[0])

    def test_already_open(self):
        v2, k = codec('v2'), codec('ace2k')
        v2.serial_port_in_use.return_value = True
        self.assertIsNone(probe_shared_ace2('p', v2, k)[0])
        v2.probe_device.assert_not_called()
        k.probe_device.assert_not_called()

    def test_port_taken_between_probes(self):
        v2, k = codec('v2', 'no_response'), codec('ace2k')
        v2.serial_port_in_use.side_effect = [False, True]
        self.assertIsNone(probe_shared_ace2('p', v2, k)[0])
        k.probe_device.assert_not_called()

    def test_cache_budget_and_active_invalidation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'tty')
            Path(path).touch()
            detector, v2, k = SharedAce2Discovery(), codec('v2'), codec('ace2k')
            budget = [1]
            self.assertIs(detector.identify(path, Reactor(), budget, v2, k)[0], v2)
            self.assertEqual(budget, [0])
            self.assertIs(detector.identify(path, Reactor(), budget, v2, k)[0], v2)
            self.assertEqual(v2.probe_device.call_count, 1)
            detector.forget(path)
            self.assertIsNone(detector.identify(path, Reactor(), budget, v2, k))
            self.assertIs(detector.identify(path, Reactor(), [1], v2, k)[0], v2)
            self.assertEqual(v2.probe_device.call_count, 2)

    def test_cached_port_taken_by_another_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'tty')
            Path(path).touch()
            detector, v2, k = SharedAce2Discovery(), codec('v2'), codec('ace2k')
            self.assertIs(detector.identify(path, Reactor(), [1], v2, k)[0], v2)
            v2.serial_port_in_use.return_value = True
            self.assertIsNone(detector.identify(path, Reactor(), [1], v2, k))

    def test_changed_device_token_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'tty')
            Path(path).touch()
            detector, v2, k = SharedAce2Discovery(), codec('v2'), codec('ace2k')
            with patch.object(detector, '_token', return_value=(1, 2, 3, 4)):
                self.assertIs(detector.identify(path, Reactor(), [1], v2, k)[0], v2)
            with patch.object(detector, '_token', return_value=(1, 2, 3, 5)):
                v2.probe_device.return_value = {'ok': False, 'category': 'no_response'}
                self.assertIs(detector.identify(path, Reactor(), [1], v2, k)[0], k)

    def test_negative_cache_does_not_consume_next_scan_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'tty')
            Path(path).touch()
            detector = SharedAce2Discovery()
            v2, k = codec('v2', 'no_response'), codec('ace2k', 'no_response')
            self.assertIsNone(detector.identify(path, Reactor(), [1], v2, k))
            budget = [1]
            self.assertIsNone(detector.identify(path, Reactor(), budget, v2, k))
            self.assertEqual(budget, [1])

    def test_pending_probe_is_not_restarted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'tty')
            Path(path).touch()
            detector, v2, k = SharedAce2Discovery(), codec('v2'), codec('ace2k')
            import threading
            done = threading.Event()
            # identify() keys pending/cache by realpath; on macOS /var is a
            # symlink to /private/var, so seeding with the raw path would miss.
            key = os.path.realpath(path)
            detector.pending[key] = {'done': done, 'token': detector._token(path), 'result': None}
            budget = [1]
            self.assertIsNone(detector.identify(path, Reactor(), budget, v2, k))
            self.assertEqual(budget, [1])
            v2.probe_device.assert_not_called()


if __name__ == '__main__':
    unittest.main()
