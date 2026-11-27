"""Run the real pinned field driver with the integration patch, no hardware."""
import importlib
from pathlib import Path
import struct
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import Mock

from native_eject_contract import Operation
from native_unload_wire import NativeUnloadWire
from patch_native_link import stage
from patch_scoped_runtime import patch_driver


class NativeDriverGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        archive = (Path(__file__).resolve().parents[1] / 'multiace-190' / 'artifacts' /
                   'multiace-1.90.2-233.20261126.tar.gz')
        with tarfile.open(archive, 'r:gz') as t:
            for name in ('ace_protocol.py', 'ace_protocol_ace2k.py'):
                (root / name).write_bytes(t.extractfile(
                    'multiace-1.90.2-233.20261126/klipper/extras/' + name).read())
        stage(root)
        driver = root / "ace_protocol_ace2k.py"
        driver.write_text(patch_driver(driver.read_text()))
        pkg = types.ModuleType('field_native_guard_test')
        pkg.__path__ = [str(root)]
        sys.modules[pkg.__name__] = pkg
        cls.driver = importlib.import_module(pkg.__name__ + '.ace_protocol_ace2k')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.now = [10.0]
        self.raw = types.SimpleNamespace(is_open=True, write=Mock(side_effect=len), close=Mock())
        self.link = self.driver.Ace2kLink(self.raw, clock=lambda: self.now[0])
        self.link._ready = True
        self.link.uid = struct.pack('<3I', 1, 2, 3)
        self.link._in_use = 0
        self.link.dictionary = types.SimpleNamespace(has=lambda n: True,
            encode=Mock(return_value=b'payload'), constant=lambda n,d:d)
        self.link._xfer = Mock(return_value=True)
        self.link._wait = Mock(return_value=({'lane': 0, 'accepted': 1, 'reason': 0}, None))
        self.guard = self.link._native_exit
        self.report(10)

    def report(self, t, modes=(0,0,0,0), switches=15, enc=(55,66,77,88), fg=(3,4,5,6)):
        self.now[0] = t
        self.link._dispatch('ace2k_sensors_state', {'switches': switches, 'age_ms': 0})
        self.link._dispatch('ace2k_feed_state', {'mode': bytes(modes), 'seq': bytes([16]*4)})
        self.link._dispatch('ace2k_lane_counters', {'encoder_um': struct.pack('<4i', *enc),
                                                  'fg': struct.pack('<4I', *fg)})

    def wire(self, lane=0, valid=lambda: True, epoch=None):
        op = Operation('uid:00000001-00000002-00000003', epoch or self.guard.epoch,
                       lane, 17, self.now[0])
        return NativeUnloadWire(self.link, op, valid, self.driver.klipper_block)

    def test_prior_fault_reports_kind_but_allows_existing_driver_recovery(self):
        self.report(10, modes=(0, 0, 7, 0))
        self.link._feed[1]['error'] = bytearray((0, 0, 9, 0))
        self.link._call = Mock(return_value=None)
        self.link._pump = Mock()
        self.link._tick = self.link._tag_tick = self.link._dryer_tick = Mock()
        request = {'method': 'get_lane_state', 'params': {'index': 2}}
        state = self.link._execute(request)['result']
        self.assertEqual((state['state'], state['fault_kind'], state['move_admissible']),
                         ('feed_error', 'motor_stalled', True))
        self.link._jobs[2] = {'kind': 'feed'}
        self.assertFalse(self.link._execute(request)['result']['move_admissible'])
        self.link._jobs.pop(2)
        self.link._tag_reads[2] = {}
        self.assertFalse(self.link._execute(request)['result']['move_admissible'])
        self.link._tag_reads.pop(2)
        self.report(10, modes=(0, 0, 0, 0))
        hold = self.guard.reserve(self.wire(2))
        self.report(10, modes=(0, 0, 7, 0))
        self.link._feed[1]['error'] = bytearray((0, 0, 9, 0))
        self.assertFalse(self.link._execute(request)['result']['move_admissible'])
        self.guard.release_before_start(hold)

        self.link._start = Mock(side_effect=[(None, 'in_error'), (17, 'ok')])
        self.link._clear_lane = Mock()
        self.link._move_max_mm = Mock(return_value=2000)
        job = {'slot': 2, 'kind': 'feed', 'target': 100, 'done': 0,
               'speed': 40, 'free_tip': 0, 'stop_when': False}
        self.assertEqual(self.link._start_piece(job), 'ok')
        self.link._clear_lane.assert_called_once_with(2, 'feed start')
        self.assertEqual(self.link._start.call_count, 2)

    def test_tag_blocked_event_records_distance_without_changing_verdict(self):
        self.link._tag_reads[2] = {'seq': 33}
        self.link._end_tag_read = Mock()
        event = {'lane': 2, 'seq': 33, 'mode': 4, 'kind': 18,
                 'motor_um': 61000, 'filament_um': 23000}
        with self.assertLogs(level='WARNING') as captured:
            self.link._on_event(event)
        self.assertIn('tag lane 2 ended blocked seq=33 mode=4', captured.output[0])
        self.assertIn('motor_um=61000 filament_um=23000', captured.output[0])
        self.assertIn('plunger', captured.output[0])
        self.link._end_tag_read.assert_called_once_with(2, 'blocked', 23.0)
        self.assertEqual(self.link._tag_reads[2]['seq'], 33)

    def test_unsolicited_fault_is_logged_without_clearing_or_restarting(self):
        self.link._clear_lane = Mock()
        event = {'lane': 3, 'seq': 0, 'mode': 4, 'kind': 7,
                 'motor_um': 80000, 'filament_um': 56000}
        with self.assertLogs(level='WARNING') as captured:
            self.link._on_event(event)
        self.assertIn('unmatched lane 3 fault stuck seq=0 mode=4', captured.output[0])
        self.assertIn('motor_um=80000 filament_um=56000', captured.output[0])
        self.link._clear_lane.assert_not_called()
        self.assertEqual(self.link._jobs, {})

    def test_real_receive_path_no_fake_event_callback(self):
        wire = self.wire()
        hold = self.guard.reserve(wire)
        self.report(11)
        self.assertTrue(self.guard.capture(hold))
        self.report(12)
        self.assertEqual(self.guard.start(hold, 60000), 'started')
        self.raw.write.assert_called_once()
        self.link.dictionary.encode.assert_any_call('ace2k_feed_start', lane=0, mode=4,
                      length_um=0, speed_um_s=60000, seq=17, free_tip=0)
        self.report(13, modes=(3,0,0,0))
        self.link._dispatch('ace2k_feed_event', {'lane':0,'seq':16,'mode':3,'kind':6})
        self.assertIsNone(wire.event)
        self.link._dispatch('ace2k_feed_event', {'lane':0,'seq':17,'mode':3,'kind':6})
        self.assertEqual(wire.event['kind'], 6)
        self.assertEqual(wire.event_at, 13)
        self.assertFalse(self.guard.blocked(1))
        self.assertFalse(self.guard.blocked(2))
        with self.assertRaises(RuntimeError):
            self.guard.release_before_start(hold)

    def test_same_lane_conflicts_but_four_independent_lanes_reserve(self):
        a = self.guard.reserve(self.wire(0))
        self.assertEqual(self.guard.mask(), 1)
        with self.assertRaises(RuntimeError):
            self.guard.reserve(self.wire(0))
        holds = [self.guard.reserve(self.wire(lane)) for lane in (1,2,3)]
        self.assertEqual(self.guard.mask(), 15)
        self.guard.release_before_start(a)
        self.assertEqual(self.guard.mask(), 14)
        for hold in holds:
            self.guard.release_before_start(hold)
        self.assertEqual(self.guard.mask(), 0)

    def test_normal_host_and_auto_motion_blocked_on_lane_only(self):
        self.guard.reserve(self.wire(0))
        self.link._xfer.reset_mock()
        for lane in (0,):
            self.assertEqual(self.link._start(lane,0,100,30), (None,'busy'))
            self.assertEqual(self.link._motion('start_feed_assist',{'index':lane},1)['code'],1)
            self.assertEqual(self.link._tag_read_load({'index':lane},1)['code'],1)
            self.assertEqual(self.link._tag_read_motion({'index':lane},1)['code'],1)
            self.assertEqual(self.link._start_piece({'slot':lane}),'busy')
            self.assertIsNone(self.link._send('ace2k_feed_clear',lane=lane))
        self.link._xfer.assert_not_called()
        self.link._xfer.return_value = {'accepted':1,'lane':2,'reason':0}
        self.assertEqual(self.link._start(2,0,100,30), (17,'ok'))
        self.link._xfer.return_value = True
        self.assertTrue(self.link._stop_lane(0))

    def test_four_native_starts_before_any_terminal_event(self):
        holds = [self.guard.reserve(self.wire(n)) for n in range(4)]
        self.report(11)
        for hold in holds:
            self.assertTrue(self.guard.capture(hold))
        self.report(12)
        for n, hold in enumerate(holds):
            self.link._wait.return_value = ({'lane':n,'accepted':1,'reason':0},None)
            self.assertEqual(self.guard.start(hold,60000),'started')
        self.assertEqual(self.raw.write.call_count,4)
        self.assertEqual(self.guard.mask(),15)
        self.assertTrue(all(h.wire.event is None for h in holds))
        self.link._dispatch('ace2k_feed_event', {'lane':1,'seq':17,'mode':3,'kind':6})
        self.assertEqual(holds[1].wire.event['kind'],6)
        self.assertTrue(all(holds[n].wire.event is None for n in (0,2,3)))

    def test_neighbour_motion_does_not_block_unload_or_completion(self):
        hold = self.guard.reserve(self.wire())
        self.report(11, modes=(0,1,0,0))
        self.guard.capture(hold)
        self.report(12, modes=(0,1,0,0),fg=(3,99,5,6))
        self.assertEqual(self.guard.start(hold,60000),'started')
        self.link._dispatch('ace2k_feed_event', {'lane':0,'seq':17,'mode':3,'kind':6})
        self.report(13, switches=14,modes=(0,1,0,0),fg=(3,100,5,6))
        self.assertTrue(self.guard.capture_after_event(hold))
        self.report(14, switches=14,modes=(0,1,0,0),fg=(3,101,5,6))
        self.assertTrue(self.guard.finish(hold,Mock(return_value=True)))

    def test_lost_mask_ack_no_start(self):
        self.link._xfer.return_value = None
        with self.assertRaises(RuntimeError):
            self.guard.reserve(self.wire())
        self.assertEqual(self.guard.mask(), 0)
        self.raw.write.assert_not_called()

    def test_selected_lane_movement_or_reused_reports_never_starts(self):
        hold = self.guard.reserve(self.wire())
        self.report(11)
        self.guard.capture(hold)
        with self.assertRaises(RuntimeError):
            self.guard.start(hold,60000)
        self.report(12,fg=(4,4,5,6))
        with self.assertRaises(RuntimeError):
            self.guard.start(hold,60000)
        self.raw.write.assert_not_called()

    def test_queued_selected_lane_and_wrong_connection_refused(self):
        self.link._follow_want.add(0)
        with self.assertRaises(RuntimeError):
            self.guard.reserve(self.wire())
        self.link._follow_want.clear()
        with self.assertRaises(RuntimeError):
            self.guard.reserve(self.wire(epoch='old'))
        self.assertEqual(self.guard.mask(),0)

    def test_route_loss_and_disconnect_no_start(self):
        route = [True]
        hold = self.guard.reserve(self.wire(valid=lambda:route[0]))
        self.report(11)
        self.guard.capture(hold)
        self.report(12)
        route[0] = False
        with self.assertRaises(RuntimeError):
            self.guard.start(hold,60000)
        route[0] = True
        self.guard.invalidate()
        with self.assertRaises(RuntimeError):
            self.guard.start(hold,60000)
        self.raw.write.assert_not_called()

    def test_release_needs_real_unloaded_and_independent_reports(self):
        hold = self.guard.reserve(self.wire())
        self.report(11)
        self.guard.capture(hold)
        self.report(12)
        self.guard.start(hold, 60000)
        commit = Mock(return_value=True)
        self.report(13, switches=14)
        self.assertFalse(self.guard.capture_after_event(hold))
        self.assertFalse(self.guard.finish(hold,commit))
        self.link._dispatch('ace2k_feed_event', {'lane':0,'seq':17,'mode':3,'kind':6})
        self.report(14,switches=14)
        self.assertTrue(self.guard.capture_after_event(hold))
        self.assertFalse(self.guard.finish(hold,commit))
        self.report(15,switches=14,fg=(4,4,5,6))
        self.assertFalse(self.guard.finish(hold,commit))
        self.report(16,switches=14,fg=(3,4,5,6))
        commit.return_value = False
        self.assertFalse(self.guard.finish(hold,commit))
        self.assertTrue(self.guard.blocked(0))
        self.guard.capture_after_event(hold)
        self.report(17,switches=14,fg=(3,4,5,6))
        commit.return_value = True
        self.assertTrue(self.guard.finish(hold,commit))
        self.assertFalse(self.guard.blocked(0))
        self.assertNotIn(0,self.link._native_unload_owners)

    def test_cancel_targets_current_operation_even_if_route_lost(self):
        route = [True]
        hold = self.guard.reserve(self.wire(valid=lambda:route[0]))
        self.report(11)
        self.guard.capture(hold)
        self.report(12)
        self.guard.start(hold,60000)
        route[0] = False
        self.link._xfer.reset_mock()
        self.assertFalse(self.guard.cancel(hold,lane=1,seq=17,epoch=self.guard.epoch))
        self.assertFalse(self.guard.cancel(hold,lane=0,seq=16,epoch=self.guard.epoch))
        self.link._xfer.assert_not_called()
        self.assertTrue(self.guard.cancel(hold,lane=0,seq=17,epoch=self.guard.epoch))
        self.link.dictionary.encode.assert_called_with('ace2k_feed_stop',lane=0)
        self.assertTrue(self.guard.blocked(0))

    def test_other_unit_same_head_is_quiet_without_blocking_peer(self):
        hold = self.guard.reserve_quiet_lane('task-A', 0)
        self.assertEqual(self.guard.mask(), 1)
        self.assertTrue(self.guard.blocked(0))
        self.assertFalse(self.guard.blocked(1))
        self.link._xfer.reset_mock()
        self.assertEqual(self.link._start(0,0,100,30), (None,'busy'))
        self.assertEqual(self.link._tag_read_load({'index':0},1)['code'],1)
        self.link._xfer.assert_not_called()
        self.assertIsNone(self.guard.quiet_sample(hold))
        self.report(11)
        a = self.guard.quiet_sample(hold)
        self.report(12)
        b = self.guard.quiet_sample(hold)
        from field_native_guard_test.ace_protocol import still
        self.assertTrue(still(a,b))
        self.assertFalse(self.guard.observe({'lane':0,'kind':6},12))
        self.link._xfer.return_value = {'accepted':1,'lane':1,'reason':0}
        self.assertEqual(self.link._start(1,0,100,30),(17,'ok'))
        with self.assertRaises(RuntimeError):
            self.guard.release_quiet_lane(hold,token='wrong')
        self.guard.release_quiet_lane(hold,token='task-A')
        self.assertFalse(self.guard.blocked(0))

    def test_shared_head_hold_conflicts_only_with_native_same_lane(self):
        hold = self.guard.reserve_quiet_lane('task-A', 0)
        with self.assertRaises(RuntimeError):
            self.guard.reserve(self.wire(0))
        with self.assertRaises(RuntimeError):
            self.guard.reserve_quiet_lane('task-B',0)
        other = self.guard.reserve(self.wire(1))
        self.assertEqual(self.guard.mask(),3)
        self.guard.release_before_start(other)
        self.guard.release_quiet_lane(hold,token='task-A')

    def test_quiet_hold_lost_mask_ack_and_replacement(self):
        self.link._xfer.return_value = None
        with self.assertRaises(RuntimeError):
            self.guard.reserve_quiet_lane('task-A',0)
        self.assertEqual(self.guard.mask(),0)
        self.link._xfer.return_value = True
        hold = self.guard.reserve_quiet_lane('task-A',0)
        self.report(11)
        self.assertIsNotNone(self.guard.quiet_sample(hold))
        self.link._raw = types.SimpleNamespace(is_open=True)
        self.assertIsNone(self.guard.quiet_sample(hold))
        with self.assertRaises(RuntimeError):
            self.guard.release_quiet_lane(hold,token='task-A')

    def test_last_query_is_same_lane_and_does_not_fake_success(self):
        hold = self.guard.reserve(self.wire())
        self.report(11)
        self.guard.capture(hold)
        self.report(12)
        self.guard.start(hold,60000)
        self.report(13,switches=14)
        self.link._xfer.return_value = {'lane':0,'seq':17,'mode':3,'kind':6}
        self.assertTrue(self.guard.query_last(hold))
        self.link.dictionary.encode.assert_called_with('ace2k_feed_last_query',lane=0)
        self.assertFalse(self.guard.query_last(hold))
        self.assertFalse(self.guard.capture_after_event(hold))
        self.assertTrue(self.guard.blocked(0))


if __name__ == '__main__':
    unittest.main()
