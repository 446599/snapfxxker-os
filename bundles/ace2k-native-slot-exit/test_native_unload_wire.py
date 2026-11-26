import threading
import struct
import types
import unittest
from unittest.mock import Mock

from lane_contract import LaneSample
from native_eject_contract import Operation
from native_unload_wire import NativeUnloadWire


class Dictionary:
    def __init__(self):
        self.encode = Mock(return_value=b'payload')

    def has(self, name):
        return True

    def constant(self, name, default):
        return {'ACE2K_LANE_SPEED_MIN_UM_S': 9000,
                'ACE2K_LANE_SPEED_MAX_UM_S': 80000}.get(name, default)


class Link:
    def __init__(self):
        self.uid = struct.pack('<3I', 1, 2, 3)
        self.is_open = True
        self.seq = 0
        self._raw = types.SimpleNamespace(write=Mock(side_effect=lambda data: len(data)))
        self._io = threading.RLock()
        self.dictionary = Dictionary()
        self._jobs, self._follow, self._tag_reads, self._lane_seq = {}, {}, {}, {}
        self._follow_want = set()
        self._feed = (12, {'seq': bytes([0, 0, 16, 0])})
        self._fresh = Mock(return_value=True)
        self._next_seq = Mock(return_value=17)
        self._now = Mock(return_value=12.1)
        self._wait = Mock(return_value=({'lane': 2, 'accepted': 1, 'reason': 0}, None))
        self._call = Mock()
        self._send = Mock(return_value=True)


class NativeUnloadWireTest(unittest.TestCase):
    def setUp(self):
        self.link = Link()
        self.op = Operation('uid:00000001-00000002-00000003', 'connection-1', 2, 17, 10.0)
        self.guard = Mock(return_value=True)
        self.pack_block = lambda seq, payload: bytes([seq]) + payload
        self.wire = NativeUnloadWire(self.link, self.op, self.guard, self.pack_block)
        self.a = LaneSample(True, 'ready', 100, 20, (11, 11, 11, 11))
        self.b = LaneSample(True, 'ready', 100, 20, (12, 12, 12, 12))
        self.kw = dict(device_uid=self.op.device_uid, connection_epoch='connection-1', speed_um_s=60000)
        self.identity = dict(device_uid=self.op.device_uid, connection_epoch='connection-1')

    def start(self):
        return self.wire.start(self.a, self.b, **self.kw)

    def test_one_native_start_uses_existing_sequence(self):
        self.assertEqual(self.start(), 'started')
        self.link.dictionary.encode.assert_called_once_with('ace2k_feed_start',
            lane=2, seq=17, mode=4, length_um=0, speed_um_s=60000, free_tip=0)
        self.link._raw.write.assert_called_once_with(b'\x7e\x00payload')
        self.assertEqual(self.link._lane_seq[2], 17)
        with self.assertRaisesRegex(RuntimeError, 'already attempted'):
            self.start()
        self.assertEqual(self.link._raw.write.call_count, 1)

    def test_unknown_route_or_stale_lane_never_sends(self):
        self.guard.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'not confirmed'):
            self.start()
        self.guard.return_value = True
        with self.assertRaisesRegex(RuntimeError, 'not confirmed'):
            self.wire.start(self.a, self.a, **self.kw)
        self.link._raw.write.assert_not_called()

    def test_route_changes_after_preflight_never_sends(self):
        self.guard.side_effect = [True, False]
        with self.assertRaisesRegex(RuntimeError, 'route changed'):
            self.start()
        self.link._raw.write.assert_not_called()

    def test_missing_stale_or_inconsistent_sequence_never_sends(self):
        self.link._fresh.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'sequence report unavailable'):
            self.start()
        self.link._fresh.return_value = True
        self.link._feed = (12, {'seq': [0, 0, True, 0]})
        with self.assertRaisesRegex(RuntimeError, 'sequence report unavailable'):
            self.start()
        self.link._feed = (12, {'seq': [0, 0, 16, 0]})
        self.link._lane_seq[2] = 15
        with self.assertRaisesRegex(RuntimeError, 'sequence report not current'):
            self.start()
        self.link._raw.write.assert_not_called()

    def test_other_lane_work_does_not_block_and_target_owner_does(self):
        self.link._jobs[1] = object()
        self.assertEqual(self.start(), 'started')
        other = NativeUnloadWire(self.link, self.op, self.guard, self.pack_block)
        with self.assertRaisesRegex(RuntimeError, 'another native UNLOAD owner'):
            other.start(self.a, self.b, **self.kw)
        self.assertEqual(self.link._raw.write.call_count, 1)

    def test_existing_target_tag_read_blocks_before_reservation(self):
        self.link._tag_reads[2] = object()
        with self.assertRaisesRegex(RuntimeError, 'another owner'):
            self.start()
        self.link._raw.write.assert_not_called()

    def test_uncertain_result_retains_reservation_and_refusal_releases_it(self):
        self.link._wait.return_value = (None, None)
        self.assertEqual(self.start(), 'unconfirmed')
        self.assertIs(self.link._native_unload_owners[2], self.wire)
        self.setUp()
        self.link._wait.return_value = ({'lane': 2, 'accepted': 0, 'reason': 2}, None)
        self.assertEqual(self.start(), 'refused')
        self.assertNotIn(2, self.link._native_unload_owners)

    def test_unanswered_or_wrong_ack_stops_only_target_and_keeps_uncertain(self):
        for reply in (None, {'lane': 1, 'accepted': 1}, {'lane': 2, 'accepted': True}):
            with self.subTest(reply=reply):
                self.setUp()
                self.link._wait.return_value = (reply, None)
                self.assertEqual(self.start(), 'unconfirmed')
                self.assertEqual(self.wire.state, 'uncertain')
                self.link._send.assert_called_once_with('ace2k_feed_stop', lane=2)
                self.assertEqual(self.link._lane_seq[2], 17)
                self.assertEqual(self.link._raw.write.call_count, 1)
                with self.assertRaises(RuntimeError):
                    self.start()

    def test_refused_start_not_reported_as_success(self):
        self.link._wait.return_value = ({'lane': 2, 'accepted': 0, 'reason': 2}, None)
        self.assertEqual(self.start(), 'refused')
        self.link._send.assert_not_called()

    def test_cancel_requires_exact_lane_sequence_and_connection(self):
        self.start()
        for args in ((1, 17, 'connection-1'), (2, 18, 'connection-1'), (2, 17, 'connection-2')):
            self.assertFalse(self.wire.cancel(lane=args[0], seq=args[1], connection_epoch=args[2]))
        self.link._send.assert_not_called()
        self.assertTrue(self.wire.cancel(lane=2, seq=17, connection_epoch='connection-1'))
        self.link._send.assert_called_once_with('ace2k_feed_stop', lane=2)

    def test_lost_event_query_matches_lane_seq_and_only_once(self):
        self.start()
        self.link._call.return_value = dict(lane=2, seq=17, mode=3, kind=6)
        self.link._now.return_value = 13
        idle = LaneSample(False, 'ready', 90, 30, (12.5, 12.5, 12.5, 12.5))
        self.assertTrue(self.wire.query_last(idle, **self.identity))
        self.assertFalse(self.wire.query_last(idle, **self.identity))
        self.link._call.assert_called_with('ace2k_feed_last_query', 'ace2k_feed_last', lane=2)
        a = LaneSample(False, 'ready', 90, 30, (14, 14, 14, 14))
        b = LaneSample(False, 'ready', 90, 30, (15, 15, 15, 15))
        self.assertEqual(self.wire.verdict(a, b, now=15.1, **self.identity), 'complete')
        self.link.uid = b'other-uid'
        self.assertEqual(self.wire.verdict(a, b, now=15.1, **self.identity), 'unconfirmed')

    def test_query_reply_time_not_request_time_anchors_stop_evidence(self):
        self.start()
        self.link._now.return_value = 14.5
        self.link._call.return_value = dict(lane=2, seq=17, mode=3, kind=6)
        idle = LaneSample(False, 'ready', 90, 30, (14, 14, 14, 14))
        self.assertTrue(self.wire.query_last(idle, **self.identity))
        a = LaneSample(False, 'ready', 90, 30, (14, 14, 14, 14))
        b = LaneSample(False, 'ready', 90, 30, (15, 15, 15, 15))
        self.assertEqual(self.wire.verdict(a, b, now=15.1, **self.identity), 'unconfirmed')

    def test_last_event_query_waits_for_fresh_terminal_lane_state(self):
        self.start()
        self.link._now.return_value = 13
        moving = LaneSample(True, 'rollback', 90, 30, (12.5, 12.5, 12.5, 12.5))
        self.assertFalse(self.wire.query_last(moving, **self.identity))
        self.assertFalse(self.wire.query_last(self.a, **self.identity))
        self.assertFalse(self.wire.last_queried)
        self.assertEqual(self.link._call.call_count, 0)

    def test_failed_stop_or_exception_never_releases_unknown_lane(self):
        self.link._wait.return_value = (None, None)
        self.link._send.return_value = False
        self.assertEqual(self.start(), 'unconfirmed')
        self.assertFalse(self.wire.stop_sent)
        self.assertEqual(self.wire.state, 'uncertain')
        self.setUp()
        self.link._wait.side_effect = OSError('start link failed')
        self.link._send.side_effect = OSError('stop link failed')
        with self.assertRaisesRegex(RuntimeError, 'STOP both unconfirmed'):
            self.start()
        self.assertEqual(self.wire.state, 'uncertain')

    def test_partial_write_sends_stop_without_replaying_start(self):
        self.link._raw.write.side_effect = None
        self.link._raw.write.return_value = 1
        with self.assertRaisesRegex(IOError, 'partial native UNLOAD'):
            self.start()
        self.link._raw.write.assert_called_once()
        self.link._send.assert_called_once_with('ace2k_feed_stop', lane=2)
        self.assertEqual(self.link.seq, 1)
        self.assertEqual(self.wire.state, 'uncertain')

    def test_stale_event_and_old_connection_are_rejected(self):
        self.start()
        event = dict(lane=2, seq=17, mode=3, kind=6)
        self.assertFalse(self.wire.note_event(event, received_at=9, **self.identity))
        self.assertFalse(self.wire.note_event(event, received_at=13, device_uid=self.op.device_uid,
                                              connection_epoch='connection-2'))
        self.assertIsNone(self.wire.event)

    def test_fault_event_cannot_be_overwritten_by_later_empty_event(self):
        self.start()
        self.assertTrue(self.wire.note_event(dict(lane=2, seq=17, mode=3, kind=9),
                                             received_at=13, **self.identity))
        self.assertFalse(self.wire.note_event(dict(lane=2, seq=17, mode=3, kind=6),
                                              received_at=14, **self.identity))
        self.assertEqual(self.wire.verdict(None, None, now=15, **self.identity), 'failed')


if __name__ == '__main__':
    unittest.main()
