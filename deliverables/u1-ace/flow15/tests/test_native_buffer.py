"""Native buffer contracts: ready is idle-armed, STOP revokes old work."""
import ast
import importlib
import logging
from pathlib import Path
import sys
import types
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = '_flow15_native_buffer_tests'
pkg = types.ModuleType(PACKAGE)
pkg.__path__ = [str(ROOT / 'runtime/klipper/extras'),
                str(ROOT.parent / 'source-20261116/multiace/runtime/klipper/extras')]
sys.modules[PACKAGE] = pkg
native = importlib.import_module(PACKAGE + '.ace_native_buffer')
protocol = importlib.import_module(PACKAGE + '.ace_protocol_v2')
flow_guard = importlib.import_module(PACKAGE + '.ace_flow_guard')


def ack(command=8, **kwargs):
    return {'code': 0, 'msg': 'success', '_v2_frame_valid': True,
            '_v2_payload_valid': True, '_v2_command': command, **kwargs}


class BufferFeedbackTest(unittest.TestCase):
    def test_per_slot_inputs_keep_shared_full_unattributed(self):
        mask = (1 << 0) | (1 << 6) | (1 << 11) | (1 << 16)
        keys = native.decode_buffer_keys({1: [(0, mask)]})
        self.assertTrue(keys['slots'][0]['inserted'])
        self.assertTrue(keys['slots'][1]['rest'])
        self.assertTrue(keys['slots'][2]['pushed'])
        self.assertFalse(keys['slots'][3]['pushed'])
        self.assertTrue(keys['pulled_any'])
        self.assertIsNone(keys['full_slot'])
        self.assertTrue(all('full' not in row for row in keys['slots']))

    def test_empty_stub_is_only_diagnostic(self):
        keys = native.decode_buffer_keys({1: [(0, 2)]})
        self.assertTrue(keys['slots'][0]['empty_raw'])
        self.assertNotIn('empty', keys['slots'][0])

    def test_missing_or_ambiguous_mask_is_unknown(self):
        for fields in ({}, {1: [(0, -1)]}, {1: [(0, 1 << 17)]},
                       {1: [(2, b'\0')]}, {1: [(0, True)]},
                       {1: [(0, 0), (0, 1)]}):
            with self.subTest(fields=fields):
                self.assertIsNone(native.decode_buffer_keys(fields))

    def test_real_protocol_keeps_unknown_unknown(self):
        response = protocol.AceProtocolV2()._v2_response_to_v1(73, 1, b'')
        self.assertIsNot(response['result'].get('_key_wire_valid'), True)
        self.assertIsNone(response['result'].get('buffer_keys'))
        response = protocol.AceProtocolV2()._v2_response_to_v1(73, 2, b'\x08\x04')
        self.assertTrue(response['result']['_key_wire_valid'])
        self.assertTrue(response['result']['buffer_keys']['slots'][0]['rest'])


class LeaseTest(unittest.TestCase):
    def setUp(self):
        self.c = native.NativeBufferControl()
        self.transport = object()
        self.uid = (1, 2, 3)

    def arm(self, slot=0, now=0):
        request = {'method': 'start_feed_assist', 'params': {'index': slot}}
        token = self.c.begin_request(0, request, self.transport, self.uid, now)
        self.c.accept_response(token, self.transport, self.uid, ack(), now + .1)
        return request, token

    def test_ready_does_not_disarm_or_request_recovery(self):
        self.arm()
        for now in range(1, 100):
            self.c.observe_status(0, [{'index': 0, 'slot_status': 'ready'}], self.transport)
            self.assertTrue(self.c.armed(0, 0, self.transport, self.uid))
            self.assertIsNone(self.c.recovery_token(0, 0, self.transport, self.uid, now))

    def test_stop_revokes_old_queue_callback_and_delayed_intent(self):
        intent = self.c.reserve_intent(0, 0)
        request, token = self.arm()
        self.c.begin_request(0, {'method': 'stop_feed_filament', 'params': {'index': 0}},
                             self.transport, self.uid, 1)
        self.assertFalse(self.c.allows_write(request, self.transport, self.uid))
        self.assertFalse(self.c.accept_response(token, self.transport, self.uid, ack(), 2))
        self.assertFalse(self.c.intent_current(intent))
        self.assertIsNone(self.c.recovery_token(0, 0, self.transport, self.uid, 100))

    def test_stopping_one_lane_preserves_other_native_loop(self):
        self.arm(0)
        other, _ = self.arm(1)
        self.c.begin_request(0, {'method': 'stop_feed_filament', 'params': {'index': 0}},
                             self.transport, self.uid, 1)
        self.assertTrue(self.c.armed(0, 1, self.transport, self.uid))
        self.assertTrue(self.c.allows_write(other, self.transport, self.uid))

    def test_queued_arm_cannot_move_new_device_or_new_uid(self):
        request, token = self.arm()
        self.assertFalse(self.c.allows_write(request, object(), self.uid))
        self.assertFalse(self.c.allows_write(request, self.transport, (4, 5, 6)))
        self.assertFalse(self.c.accept_response(token, object(), self.uid, ack(), 1))

    def test_late_ack_cannot_replace_new_reverse_assist(self):
        _, old = self.arm()
        request = {'method': 'feed_or_rollback_raw', 'params': {'index': 0, 'mode': 3}}
        new = self.c.begin_request(0, request, self.transport, self.uid, 1)
        self.assertFalse(self.c.accept_response(old, self.transport, self.uid, ack(), 2))
        self.assertTrue(self.c.accept_response(new, self.transport, self.uid, ack(), 2))
        self.assertEqual(self.c.direction(0, 0), 3)

    def test_explicit_move_replaces_assist_without_stopping_other_lane(self):
        self.arm(0)
        self.arm(1)
        self.c.begin_request(0, {'method': 'unwind_filament', 'params': {'index': 0}},
                             self.transport, self.uid, 1)
        self.assertFalse(self.c.armed(0, 0, self.transport, self.uid))
        self.assertTrue(self.c.armed(0, 1, self.transport, self.uid))

    def test_silent_arm_has_bounded_pending_interval(self):
        request = {'method': 'start_feed_assist', 'params': {'index': 0}}
        token = self.c.begin_request(0, request, self.transport, self.uid, 0)
        self.assertTrue(self.c.armed(0, 0, self.transport, self.uid, 1, pending=True))
        self.assertIsNone(self.c.recovery_token(0, 0, self.transport, self.uid, 1))
        self.assertEqual(self.c.recovery_token(0, 0, self.transport, self.uid, 6), token)

    def test_status_cannot_fabricate_command_ack(self):
        request = {'method': 'start_feed_assist', 'params': {'index': 0}}
        self.c.begin_request(0, request, self.transport, self.uid, 0)
        self.c.observe_status(0, [{'index': 0, 'slot_status': 'assisting'}], self.transport)
        self.c.observe_status(0, [{'index': 0, 'slot_status': 'ready'}], self.transport)
        self.assertFalse(self.c.armed(0, 0, self.transport, self.uid))
        self.assertIsNotNone(self.c.recovery_token(0, 0, self.transport, self.uid, 6))

    def test_mechanical_feed_error_does_not_automatically_rearm(self):
        self.arm()
        self.c.observe_status(0, [{'index': 0, 'slot_status': 'feed_error'}], self.transport)
        self.assertFalse(self.c.armed(0, 0, self.transport, self.uid))
        self.assertIsNone(self.c.recovery_token(0, 0, self.transport, self.uid, 20))

    def test_firmware_owned_preload_revokes_old_assist(self):
        request, token = self.arm()
        self.c.observe_status(0, [{'index': 0, 'slot_status': 'preloading'}], self.transport)
        self.c.observe_status(0, [{'index': 0, 'slot_status': 'ready'}], self.transport)
        self.assertFalse(self.c.armed(0, 0, self.transport, self.uid))
        self.assertFalse(self.c.accept_response(token, self.transport, self.uid, ack(), 2))
        self.assertTrue(self.c.automatic_control_blocked(0, 0))

    def test_stop_cancels_scheduled_assist_error_recovery(self):
        self.arm()
        self.c.observe_status(0, [{'index': 0, 'slot_status': 'assist_error'}], self.transport)
        token = self.c.recovery_token(0, 0, self.transport, self.uid, 20)
        self.assertIsNotNone(token)
        self.c.invalidate(0, 0)
        self.c.observe_status(0, [{'index': 0, 'slot_status': 'assist_error'}], self.transport)
        self.assertFalse(self.c.recovery_current(token, self.transport, self.uid))
        self.assertIsNone(self.c.recovery_token(0, 0, self.transport, self.uid, 30))
        self.assertTrue(self.c.automatic_control_blocked(0, 0))

    def test_feedback_poll_is_bounded_when_responses_are_missing(self):
        self.assertIsNotNone(self.c.poll_due(0, self.transport, 0))
        for now in (.1, .5, 1, 2.99):
            self.assertIsNone(self.c.poll_due(0, self.transport, now))
        self.assertIsNotNone(self.c.poll_due(0, self.transport, 3))

    def test_stale_feedback_never_reports_false_clear(self):
        token = self.c.poll_due(0, self.transport, 0)
        keys = native.decode_buffer_keys({1: [(0, 1 << 16)]})
        result = ack(73, result={'buffer_keys': keys, '_key_wire_valid': True})
        self.assertTrue(self.c.accept_keys(0, token, self.transport, result, .1))
        self.assertTrue(self.c.status(0, self.transport, self.uid, 1)['pulled_any'])
        stale = self.c.status(0, self.transport, self.uid, 4)
        self.assertFalse(stale['fresh'])
        self.assertIsNone(stale['pulled_any'])
        self.assertEqual(stale['slots'], [])
        self.assertIsNone(self.c.status(0, object(), self.uid, 1)['raw_mask'])


def actual_methods(*names):
    source = (ROOT / 'runtime/klipper/extras/ace.py').read_text()
    tree = ast.parse(source)
    selected = [node for cls in tree.body if isinstance(cls, ast.ClassDef)
                for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if len(selected) != len(names):
        raise AssertionError('actual ACE methods not found')
    namespace = {'logging': logging, 'time': time, 'FA_EXTRUDE_IDLE_GRACE': 2,
                 'FA_STICK_CONFIRM_TIME': 15,
                 'source_route': flow_guard.source_route,
                 'source_conflict': flow_guard.source_conflict,
                 'raw_head_state': flow_guard.raw_head_state,
                 'dynamic_route_blocked': flow_guard.dynamic_route_blocked,
                 'V2_FA_RUNNING_STATES': ('assisting', 'rollback_assisting', 'feeding', 'rollback', 'preloading')}
    exec(compile(ast.Module(body=selected, type_ignores=[]), '<actual ace.py>', 'exec'), namespace)
    return types.SimpleNamespace(**{name: namespace[name] for name in names})


METHODS = actual_methods('_arm_fa_for', '_native_assist_is_armed',
                         '_make_v2_velocity_tick_for', '_v2_schedule_fa_rearm',
                         '_rearm_fa_after_reconnect', '_native_reconnect_block_reason',
                         '_v2_arm_fa_for_unload')


class ActualAssistIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.c = native.NativeBufferControl()
        self.transport = object()
        request = {'method': 'start_feed_assist', 'params': {'index': 0}}
        token = self.c.begin_request(0, request, self.transport, (1, 2, 3), 0)
        self.c.accept_response(token, self.transport, (1, 2, 3), ack(), .1)
        self.sent = []
        self.a = types.SimpleNamespace(
            _stop_guard=types.SimpleNamespace(snapshot=lambda: None),
            _native_buffer=self.c, _serials={0: self.transport},
            _location_identity=lambda idx: ('/dev/ace0', (1, 2, 3)),
            reactor=types.SimpleNamespace(monotonic=lambda: 20, NEVER=1e30),
            _fa_trace=lambda msg: None, _auto_feed_enabled=True, _fa_context='print',
            _fa_rearm_reset=lambda *args: None, _fa_print_disable=set(), _fa_load_disable=set(),
            _ace_mode='multi', head_is_manual=lambda head: False,
            _feed_assist_per_ace={0: 0}, _is_v2_idx=lambda idx: True,
            _v2_get_slot_status=lambda idx, slot: 'ready',
            send_request_to=lambda *args: self.sent.append(args),
            _fa_log=logging.getLogger('test-buffer'),
        )
        self.a._native_assist_is_armed = types.MethodType(METHODS._native_assist_is_armed, self.a)

    def test_actual_arm_method_does_not_resend_accepted_ready(self):
        for _ in range(25):
            METHODS._arm_fa_for(self.a, 0, 0)
        self.assertEqual(self.sent, [])

    def test_actual_post_load_verify_keeps_acknowledged_ready(self):
        self.assertFalse(METHODS._v2_schedule_fa_rearm(self.a, 0, 0, 'post-load-verify'))
        self.assertEqual(self.sent, [])

    def test_expired_same_slot_arm_is_stopped_before_retry(self):
        request = {'method': 'start_feed_assist', 'params': {'index': 0}}
        self.c.begin_request(0, request, self.transport, (1, 2, 3), 0)
        self.a._fa_context = 'load'
        self.a._active_device_index = 0
        self.a._v2_velocity_state = {}
        self.a._fa_start_retries = 2
        self.a._fa_start_retry_delay = .1
        self.a._fa_settle_after_stop = 0
        self.a._v1_fa_blocked_by_homing = lambda idx: False
        def clear(idx, slot):
            self.c.invalidate(idx, slot)
            self.a._feed_assist_per_ace[idx] = -1
        self.a._clear_fa_cache_for = clear
        original = METHODS._arm_fa_for.__globals__.get('stop_and_verify')
        try:
            METHODS._arm_fa_for.__globals__['stop_and_verify'] = lambda ace, idx, slot: self.sent.append(('stop', idx, slot))
            METHODS._arm_fa_for(self.a, 0, 0)
        finally:
            METHODS._arm_fa_for.__globals__['stop_and_verify'] = original
        self.assertEqual(self.sent[0], ('stop', 0, 0))
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.sent[1][1]['method'], 'start_feed_assist')

    def test_unload_keeps_acknowledged_idle_buffer_armed(self):
        self.a._head_source = {0: {'ace_index': 0, 'slot': 0}}
        self.a._protocols = {0: types.SimpleNamespace(NAME='v2')}
        self.assertTrue(METHODS._v2_arm_fa_for_unload(self.a, 0))
        self.assertTrue(self.a._v2_active_rev_assist)
        self.assertEqual(self.sent, [])

    def test_unload_requires_fresh_assist_ack(self):
        self.a._head_source = {0: {'ace_index': 0, 'slot': 0}}
        self.a._protocols = {0: types.SimpleNamespace(NAME='v2')}
        self.a._active_device_index = 0
        self.c.invalidate(0, 0)
        self.a._clear_fa_cache_for = lambda idx, slot: self.c.invalidate(idx, slot)
        self.a._tipform_rejected = lambda response: response['code'] != 0
        self.a._tipform_send = lambda *args, **kwargs: {'code': 2, 'msg': 'FORBIDDEN'}
        original = METHODS._v2_arm_fa_for_unload.__globals__.get('stop_and_verify')
        try:
            METHODS._v2_arm_fa_for_unload.__globals__['stop_and_verify'] = lambda ace, idx, slot: self.sent.append(('stop', idx, slot))
            self.assertFalse(METHODS._v2_arm_fa_for_unload(self.a, 0))
        finally:
            METHODS._v2_arm_fa_for_unload.__globals__['stop_and_verify'] = original
        self.assertFalse(self.a._v2_active_rev_assist)
        self.assertTrue(all(item == ('stop', 0, 0) for item in self.sent))

    def velocity_tick(self):
        self.a._v2_velocity_state = {}
        self.a._protocols = {0: types.SimpleNamespace(NAME='v2')}
        self.a._info_per_ace = {0: {'slots': [{'index': 0, 'slot_status': 'ready'}]}}
        motion = types.SimpleNamespace(get_status=lambda eventtime: {'live_extruder_velocity': 5})
        self.a.printer = types.SimpleNamespace(lookup_object=lambda *args: motion)
        self.a.toolhead = types.SimpleNamespace(get_extruder=lambda: types.SimpleNamespace(extruder_index=0))
        self.a._head_source = {0: {'ace_index': 0, 'slot': 0}}
        self.a._v2_quantize_velocity = lambda v, direction: 5
        self.a._v2_print_assist_mode = 'constant'
        self.a._v2_constant_assist_speed = 0
        self.a._v2_assist_confirm_time = .5
        return METHODS._make_v2_velocity_tick_for(self.a, 0)

    def test_actual_velocity_tick_lets_native_buffer_wait(self):
        tick = self.velocity_tick()
        for now in range(20, 50):
            tick(now)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.a._feed_assist_per_ace[0], 0)

    def test_actual_tick_does_not_rearm_during_stop_verification(self):
        tick = self.velocity_tick()
        self.c.begin_request(0, {'method': 'stop_feed_filament', 'params': {'index': 0}},
                             self.transport, (1, 2, 3), 21)
        # Hardware may still report assisting while the two STOPs settle.
        self.a._info_per_ace[0]['slots'][0]['slot_status'] = 'assisting'
        for now in range(21, 40):
            tick(now)
        self.assertEqual(self.sent, [])

    def reconnect_fake(self, source, head_state=True):
        self.a._head_source = {0: source}
        self.a.toolhead = types.SimpleNamespace(get_extruder=lambda: types.SimpleNamespace(extruder_index=0))
        sensor = types.SimpleNamespace(runout_buttun_state=head_state)
        self.a.printer = types.SimpleNamespace(lookup_object=lambda *args: sensor)
        self.a._native_reconnect_block_reason = types.MethodType(METHODS._native_reconnect_block_reason, self.a)
        self.a._clear_fa_cache_for = lambda idx, slot: self.c.invalidate(idx, slot)
        self.a._arm_fa_for = lambda idx, slot: self.sent.append(('arm', idx, slot))
        self.a.log_always = self.a.log_warn = lambda *args: None
        self.a._disp = lambda index: index + 1
        self.pauses = []
        self.a._maybe_pause_fa_exhausted = lambda *args: self.pauses.append(args)

    def test_reconnect_without_original_uid_does_not_arm(self):
        self.reconnect_fake({'ace_index': 0, 'slot': 0})
        METHODS._rearm_fa_after_reconnect(self.a, 0)
        self.assertEqual(self.sent, [])
        self.assertTrue(self.pauses)
        self.assertTrue(self.c.hold_reason(0, 0))

    def test_reconnect_matching_uid_and_loaded_head_restores_only_assist(self):
        self.reconnect_fake({'ace_index': 0, 'slot': 0,
                             'connection_identity': {'port': '/dev/ace0', 'uid': [1, 2, 3]}})
        METHODS._rearm_fa_after_reconnect(self.a, 0)
        self.assertEqual(self.sent, [('arm', 0, 0)])
        self.assertEqual(self.pauses, [])

    def test_reconnect_never_assists_empty_or_unknown_head(self):
        for state in (False, None):
            with self.subTest(state=state):
                self.sent.clear()
                self.reconnect_fake({'ace_index': 0, 'slot': 0,
                                     'connection_identity': {'port': '/dev/ace0', 'uid': [1, 2, 3]}}, state)
                METHODS._rearm_fa_after_reconnect(self.a, 0)
                self.assertEqual(self.sent, [])
                self.assertTrue(self.pauses)


if __name__ == '__main__':
    unittest.main()
