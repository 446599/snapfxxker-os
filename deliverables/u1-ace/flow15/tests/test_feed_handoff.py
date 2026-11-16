"""Real STOP, admission and measured-return behavior with a deterministic wire."""
import ast
import copy
import importlib
import logging
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
PKG = '_flow15_feed_tests'
package = types.ModuleType(PKG)
package.__path__ = [str(ROOT / 'runtime/klipper/extras'),
    str(ROOT.parent / 'source-20261116/multiace/runtime/klipper/extras')]
sys.modules[PKG] = package
standstill = importlib.import_module(PKG + '.ace_v2_standstill')
feed = importlib.import_module(PKG + '.ace_feed_supervisor')
travel = importlib.import_module(PKG + '.ace_filament_travel')
ready = importlib.import_module(PKG + '.ace_ready_wait')


class Reactor:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def pause(self, target):
        self.now = max(self.now, target)


class Wire:
    def __init__(self):
        self.reactor = Reactor()
        self._serials = {0: object()}
        self._connected_per_ace = {0: True}
        self._reconnecting_per_ace = {}
        self._gate_status_per_ace = {0: [1, 1, 1, 1]}
        self._insert_epochs = {(0, 0): 7}
        self.uid = (1, 2, 3)
        self.calls = []
        self.faults = []
        self.motor = 100.0
        self.decoder = 100.0
        self.steps = 200
        self.state = 'feeding'
        self.others = ['ready', 'assisting', 'ready', 'ready']
        self.stops = 0
        self.head = False
        self.drop_first_stop = False
        self.ignore_stops = False
        self.bad_id = False
        self.change_uid = False
        self.armed = set()

    def _location_identity(self, idx):
        return '/dev/ace0', self.uid

    def _latch_stop_fault(self, idx, slot, reason, identity=None):
        self.faults.append((idx, slot, reason, identity))

    def _clear_fa_cache_for(self, idx, slot):
        self.armed.discard(slot)

    def _native_assist_needs_stop(self, idx, slot):
        return slot in self.armed

    def _native_assist_is_armed(self, idx, slot):
        return slot in self.armed

    def send_request_to(self, idx, request, callback):
        request['id'] = len(self.calls) + 1
        method = request['method']
        self.calls.append((method, request.get('params', {}).get('index'), self.reactor.now))
        command = {'stop_feed_filament': 9, 'get_status': 6, 'get_feed_info': 76}[method]
        response = {'id': request['id'], 'code': 0, '_v2_command': command,
                    '_v2_frame_valid': True, '_v2_payload_valid': True, 'result': {}}
        if method == 'stop_feed_filament':
            self.stops += 1
            # Reproduce the firmware's setup race: the early ACK does not stop.
            if self.reactor.now >= .2 and not self.ignore_stops and self.state != 'feed_error':
                self.state = 'ready'
            if self.change_uid:
                self.uid = (9, 9, 9)
            if self.drop_first_stop and self.stops == 1:
                return True
        elif method == 'get_status':
            states = self.others[:]
            states[0] = self.state
            response['result'] = {'_status_wire_valid': True, 'status': 'busy',
                'slots': [{'index': i, 'slot_status': state, '_wire_valid': True}
                          for i, state in enumerate(states)]}
        elif method == 'get_feed_info':
            decoder = int(self.decoder) % (1 << 64)
            response['result'] = {'_feed_wire_valid': True, 'feed_info': [
                {'index': 0, 'length': int(self.motor), 'steps': self.steps,
                 'decoder': decoder, '_wire_valid': True, '_decoder_present': True}]}
        if self.bad_id and method != 'stop_feed_filament':
            response['id'] += 1
        callback(self, response)
        return True


class StandstillTests(unittest.TestCase):
    def test_setup_race_gets_immediate_and_delayed_stop(self):
        a = Wire()
        receipt = standstill.stop_and_verify(a, 0, 0)
        stops = [x for x in a.calls if x[0] == 'stop_feed_filament']
        self.assertEqual(len(stops), 2)
        self.assertEqual(stops[0][2], 0)
        self.assertGreaterEqual(stops[1][2], .25)
        self.assertEqual(receipt['slot_status'], 'ready')
        self.assertGreaterEqual(receipt['samples'], 3)
        self.assertFalse(a.faults)
        self.assertTrue(all(slot == 0 for method, slot, _ in stops))

    def test_lost_stop_ack_is_bounded_and_retried(self):
        a = Wire()
        a.drop_first_stop = True
        receipt = standstill.stop_and_verify(a, 0, 0)
        self.assertIsNotNone(receipt['first_stop_error'])
        self.assertEqual(a.stops, 2)
        self.assertLess(a.reactor.now, 3)

    def test_device_error_is_stopped_but_not_cleared(self):
        a = Wire()
        a.state = 'feed_error'
        receipt = standstill.stop_and_verify(a, 0, 0)
        self.assertEqual(receipt['slot_status'], 'feed_error')
        self.assertFalse(a.faults)

    def test_ignored_stop_retains_fault(self):
        a = Wire()
        a.ignore_stops = True
        with self.assertRaises(standstill.StopUnconfirmedError):
            standstill.stop_and_verify(a, 0, 0)
        self.assertEqual(len(a.faults), 1)
        self.assertLessEqual(a.reactor.now, 3.01)

    def test_wrong_response_id_is_not_standstill_evidence(self):
        a = Wire()
        a.bad_id = True
        with self.assertRaises(standstill.StopUnconfirmedError):
            standstill.stop_and_verify(a, 0, 0)
        self.assertTrue(a.faults)

    def test_replaced_device_cannot_receive_old_stop_sequence(self):
        a = Wire()
        a.change_uid = True
        with self.assertRaises(standstill.StopUnconfirmedError):
            standstill.stop_and_verify(a, 0, 0)
        self.assertEqual(a.stops, 1)
        self.assertEqual(a.faults[0][3], ('/dev/ace0', (1, 2, 3)))

    def test_negative_decoder_is_preserved(self):
        a = Wire()
        a.decoder = -100
        receipt = standstill.stop_and_verify(a, 0, 0)
        self.assertEqual(receipt['sample'][1], -100)


def observer(a=None):
    a = a or Wire()
    s = feed.FeedSupervisor(a, 0, 0, 0, lambda: a.head, 2100, 80)
    s.ack = s.feed_admitted = True
    return a, s


class FeedTests(unittest.TestCase):
    def test_preflight_hands_off_only_target_assist(self):
        a = Wire()
        a.armed = {0, 1}
        s = feed.FeedSupervisor(a, 0, 0, 0, lambda: a.head, 2100, 80)
        s.wait_before_feed()
        self.assertEqual(a.armed, {1})
        self.assertEqual(s.result, 'feeding')
        self.assertFalse(s.stopping)
        self.assertIsNone(s.terminal_sample)

    def test_unowned_other_assist_is_not_admitted(self):
        a = Wire()
        a.state = 'ready'
        s = feed.FeedSupervisor(a, 0, 0, 0, lambda: a.head, 2100, 80)
        with self.assertRaises(feed.FeedSupervisionError) as caught:
            s.wait_before_feed()
        self.assertEqual(caught.exception.code, 'preflight_busy')

    def test_delayed_origin_is_only_measured_span(self):
        a, s = observer()
        s._observe((100., 100., 200), 1.0)
        s._observe((850., 850., 1700), 10.5)
        a.reactor.now = 12.0
        a.motor = a.decoder = 1000
        a.steps = 2000
        s.stop_bound()
        receipt = s.travel_receipt()
        self.assertEqual(receipt['distance_mm'], 900)
        self.assertEqual(receipt['basis'], 'observed_span_lower_bound')
        self.assertEqual(s.last_sample[1], 1000)

    def test_observed_zero_origin_gives_full_measured_span(self):
        a, s = observer()
        s._observe((0., 0., 0), 0)
        s._observe((100., 100., 200), 1.3)
        a.reactor.now = 2
        s.stop_bound()
        self.assertEqual(s.travel_receipt()['basis'], 'observed_zero_origin')
        self.assertEqual(s.travel_receipt()['distance_mm'], 100)

    def test_reverse_encoder_polarity_is_normalized(self):
        a, s = observer()
        s._observe((100., -100., 200), 1)
        s._observe((850., -850., 1700), 10.5)
        a.reactor.now = 12
        a.motor, a.decoder, a.steps = 1000, -1000, 2000
        s.stop_bound()
        self.assertEqual(s.travel_receipt()['distance_mm'], 900)

    def test_terminal_reset_does_not_create_stop_fault(self):
        a, s = observer()
        s._observe((100., 100., 200), 1)
        s._observe((850., 850., 1700), 10.5)
        a.reactor.now = 12
        a.motor = a.decoder = a.steps = 0
        s.stop_bound()
        self.assertIsNotNone(s.terminal_sample)
        self.assertIsNone(s.travel_receipt())
        self.assertTrue(s.measurement_error)
        self.assertFalse(a.faults)

    def test_single_stationary_sample_cannot_invent_distance(self):
        a, s = observer()
        a.reactor.now = 2
        s.stop_bound()
        self.assertIsNone(s.travel_receipt())

    def test_cancelled_partial_feed_has_no_success_receipt(self):
        a, s = observer()
        s._observe((0., 0., 0), 0)
        s._observe((100., 100., 200), 1.3)
        a.reactor.now = 2
        s.cancel()
        s.stop_bound()
        self.assertIsNone(s.travel_receipt())

    def test_startup_reset_discards_old_segment(self):
        a, s = observer()
        s._observe((900., -900., 1800), .1)
        s._observe((10., 10., 20), .3)
        s._observe((100., 100., 200), 1.5)
        a.reactor.now = 2
        s.stop_bound()
        self.assertEqual(s.travel_receipt()['distance_mm'], 90)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.t = travel.FilamentTravel()
        self.t.begin_load(0, 0, 0)
        self.t.motion_started('load', 0, 0, 0, 'feed')
        self.t.motion_finished('load', 900, receipt={'basis': 'observed_span_lower_bound'})
        self.t.finish_load(0, 0, 0)

    def test_measured_distance_survives_finish_load(self):
        self.assertEqual(self.t.begin_unload(0, 0, 0), 900)
        self.assertEqual(self.t.status()['0']['measurement_basis'], 'observed_span_lower_bound')

    def test_duplicate_receipt_cannot_double_distance(self):
        self.t.motion_finished('load', 900)
        self.assertEqual(self.t.remaining(0, 0, 0), 900)

    def test_old_park_reference_cannot_enlarge_current_budget(self):
        self.t.park_references['0:0:0'] = 1100
        self.t.begin_unload(0, 0, 0)
        self.t.motion_started('back', 0, 0, 0, 'retract')
        self.t.motion_finished('back', -100)
        self.assertEqual(self.t.park_distance(0, 0, 0), 800)
        self.t.finish_park(0, 0, 0)
        self.assertEqual(self.t.status()['0']['phase'], 'head_empty')
        self.assertEqual(self.t.park_references['0:0:0'], 1100)

    def test_existing_park_does_not_subtract_reserve_every_cycle(self):
        self.t.park_references['0:0:0'] = 850
        self.assertEqual(self.t.park_distance(0, 0, 0), 850)

    def test_interrupted_move_is_not_replayed_after_restart(self):
        self.t.begin_unload(0, 0, 0)
        self.t.motion_started('unfinished', 0, 0, 0, 'retract')
        restarted = travel.FilamentTravel(self.t.status())
        self.assertIsNone(restarted.remaining(0, 0, 0))
        self.assertEqual(restarted.status()['0']['reason'], 'interrupted_move')


class IntegrationTests(unittest.TestCase):
    @staticmethod
    def harness(names):
        parsed = ast.parse((ROOT / 'runtime/klipper/extras/filament_feed_ace.py').read_text())
        cls = next(node for node in parsed.body if isinstance(node, ast.ClassDef)
                   and node.name == 'FilamentFeed')
        target = ast.ClassDef(name='Harness', bases=[], keywords=[],
            body=[node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names],
            decorator_list=[])
        module = ast.fix_missing_locations(ast.Module(body=[target], type_ignores=[]))
        namespace = dict(copy=copy, logging=logging, sys=sys, FEED_OK='ok', FEED_ERR='error',
            FeedSupervisor=feed.FeedSupervisor, FeedSupervisionError=feed.FeedSupervisionError,
            StopUnconfirmedError=standstill.StopUnconfirmedError,
            stop_and_verify=standstill.stop_and_verify)
        exec(compile(module, 'real-filament-feed-methods', 'exec'), namespace)
        return namespace['Harness']()

    def load_failure(self, enqueued):
        h = self.harness({'_run_supervised_ace_load', '_forget_unconfirmed_load_travel',
                          '_commit_supervised_load_travel'})
        a = Wire()
        a._head_source = {}
        a._head_travel = travel.FilamentTravel()
        a.get_load_length = lambda idx, slot: 2100
        a.get_feed_speed = lambda idx: 80
        a._save_head_source = lambda: None
        def fail(*args, **kwargs):
            if enqueued:
                kwargs['normal_load'].feed_request({})
            raise feed.FeedSupervisionError('preflight_busy' if not enqueued else 'feed_ack_timeout', 'test')
        a._feed = fail
        h.ace = a
        h.reactor = a.reactor
        h.channel_error = ['ok']
        h.exception_code = [0]
        h._toolhead_filament_present = lambda channel: False
        h._hang_neutral = lambda channel: None
        with self.assertRaises(feed.FeedSupervisionError):
            h._run_supervised_ace_load(0, 0, 0, 0)
        return h, a

    def test_preflight_failure_does_not_create_failed_source(self):
        h, a = self.load_failure(False)
        self.assertNotIn(0, a._head_source)
        self.assertEqual(a.stops, 0)
        self.assertFalse(h._active_load_supervisors)
        self.assertEqual(h._last_load_supervision[0]['state'], 'rejected_before_motion')

    def test_uncertain_enqueued_feed_keeps_recoverable_source(self):
        h, a = self.load_failure(True)
        self.assertTrue(a._head_source[0]['load_failed'])
        self.assertEqual(a._head_source[0]['connection_identity']['uid'], [1, 2, 3])
        self.assertEqual(a.stops, 2)
        self.assertFalse(h._active_load_supervisors)

    def test_load_cleanup_preserves_other_head_buffer_and_print_context(self):
        h = self.harness({'_finish_load_cleanup'})
        a = Wire()
        a.armed = {0, 1}
        a._head_source = {0: {'ace_index': 0, 'slot': 0}}
        a.head_uses_ace = lambda head: True
        a._active_device_index = 0
        a._ace_slot_for_head = lambda head: head
        a._is_v2_idx = lambda idx: True
        a._dwell_fan = lambda active: None
        a._auto_feed_enabled = True
        a._fa_context = 'load'
        a._disable_feed_assist_all = lambda: self.fail('must not stop all heads')
        h.ace = a
        h.filament_ch = [0]
        h.channel_error = ['ok']
        h.gcode = types.SimpleNamespace(run_script_from_command=lambda cmd: None)
        h._finish_load_cleanup(0, True, (True, 'print'))
        self.assertEqual(a.armed, {1})
        self.assertTrue(a._auto_feed_enabled)
        self.assertEqual(a._fa_context, 'print')

    def test_success_path_consumes_real_pending_token_once(self):
        parsed = ast.parse((ROOT / 'runtime/klipper/extras/filament_feed_ace.py').read_text())
        cls = next(node for node in parsed.body if isinstance(node, ast.ClassDef)
                   and node.name == 'FilamentFeed')
        names = {'_commit_supervised_load_travel', '_forget_unconfirmed_load_travel'}
        target = ast.ClassDef(name='Harness', bases=[], keywords=[],
            body=[node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names],
            decorator_list=[])
        module = ast.fix_missing_locations(ast.Module(body=[target], type_ignores=[]))
        namespace = {}
        exec(compile(module, 'real-filament-feed-methods', 'exec'), namespace)
        h = namespace['Harness']()
        a, s = observer()
        a._head_travel = travel.FilamentTravel()
        a._head_travel.begin_load(0, 0, 0)
        a._head_travel.motion_started('actual', 0, 0, 0, 'feed')
        a._travel_pending = {(0, 0): dict(token='actual', accepted=True,
            transport=s.transport, head=0, direction=1, method='feed_filament')}
        h.ace = a
        s._observe((100., 100., 200), 1)
        s._observe((850., 850., 1700), 10.5)
        a.reactor.now = 12
        a.motor = a.decoder = 1000
        a.steps = 2000
        s.stop_bound()
        self.assertTrue(h._commit_supervised_load_travel(s))
        self.assertFalse(h._commit_supervised_load_travel(s))
        a._head_travel.finish_load(0, 0, 0)
        self.assertEqual(a._head_travel.remaining(0, 0, 0), 900)
        self.assertFalse(a._travel_pending)


if __name__ == '__main__':
    unittest.main()
