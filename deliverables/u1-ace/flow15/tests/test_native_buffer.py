"""Native buffer contracts: ready is idle-armed, STOP revokes old work."""
import ast
import importlib
import logging
import os
from pathlib import Path
import sys
import types
import time
import threading
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
    namespace = {'logging': logging, 'time': time, 'os': os, 'threading': threading,
                 'GATE_AVAILABLE': 1, 'FA_EXTRUDE_IDLE_GRACE': 2,
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
                         '_v2_arm_fa_for_unload', '_source_insertion_matches',
                         '_capture_source_origin', '_rfid_identity_status',
                         '_inherit_prev_capture', '_restore_head_source',
                         'notify_external_load', '_retire_transport', 'cmd_ACE_LOAD_HEAD')


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
            _connected_per_ace={0: True},
            _insert_session='this-process', _insert_epochs={(0, 0): 3},
            _head_source={0: {'ace_index': 0, 'slot': 0, 'insert_epoch': 3,
                             'insert_session': 'this-process',
                             'connection_identity': {'port': '/dev/ace0', 'uid': [1, 2, 3]}}},
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
        self.a._rfid_identity_status = types.MethodType(METHODS._rfid_identity_status, self.a)
        self.a._source_insertion_matches = types.MethodType(METHODS._source_insertion_matches, self.a)

    def test_actual_arm_method_does_not_resend_accepted_ready(self):
        for _ in range(25):
            METHODS._arm_fa_for(self.a, 0, 0)
        self.assertEqual(self.sent, [])

    def test_actual_post_load_verify_keeps_acknowledged_ready(self):
        self.assertFalse(METHODS._v2_schedule_fa_rearm(self.a, 0, 0, 'post-load-verify'))
        self.assertEqual(self.sent, [])

    def test_print_start_does_not_arm_a_persisted_source_without_scope(self):
        self.c.invalidate(0, 0)
        self.a._feed_assist_per_ace[0] = -1
        self.a._head_source[0]['insert_session'] = 'previous-process'
        warnings = []
        self.a.log_warn = warnings.append
        self.a._maybe_pause_fa_exhausted = lambda *args: None
        self.assertIsNone(self.c.hold_reason(0, 0))
        self.assertFalse(METHODS._arm_fa_for(self.a, 0, 0))
        self.assertEqual(self.sent, [])
        self.assertTrue(self.c.hold_reason(0, 0))
        self.assertTrue(warnings)

    def test_expired_same_slot_arm_is_stopped_before_retry(self):
        request = {'method': 'start_feed_assist', 'params': {'index': 0}}
        self.c.begin_request(0, request, self.transport, (1, 2, 3), 0)
        self.a._fa_context = 'load'
        # The supervised load retains failure ownership until grip/prime
        # finishes. Its current origin still permits buffer assistance.
        self.a._head_source[0]['load_failed'] = True
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
        self.a._insert_session = 'this-process'
        self.a._insert_epochs = {(0, 0): 3}
        self.a._source_insertion_matches = types.MethodType(METHODS._source_insertion_matches, self.a)
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
                             'insert_session': 'this-process', 'insert_epoch': 3,
                             'connection_identity': {'port': '/dev/ace0', 'uid': [1, 2, 3]}})
        METHODS._rearm_fa_after_reconnect(self.a, 0)
        self.assertEqual(self.sent, [('arm', 0, 0)])
        self.assertEqual(self.pauses, [])

    def test_reconnect_never_assists_empty_or_unknown_head(self):
        for state in (False, None):
            with self.subTest(state=state):
                self.sent.clear()
                self.reconnect_fake({'ace_index': 0, 'slot': 0,
                                     'insert_session': 'this-process', 'insert_epoch': 3,
                                     'connection_identity': {'port': '/dev/ace0', 'uid': [1, 2, 3]}}, state)
                METHODS._rearm_fa_after_reconnect(self.a, 0)
                self.assertEqual(self.sent, [])
                self.assertTrue(self.pauses)

    def test_matching_counter_after_restart_does_not_rearm(self):
        self.reconnect_fake({'ace_index': 0, 'slot': 0,
                             'insert_session': 'previous-process', 'insert_epoch': 3,
                             'connection_identity': {'port': '/dev/ace0', 'uid': [1, 2, 3]}})
        METHODS._rearm_fa_after_reconnect(self.a, 0)
        self.assertEqual(self.sent, [])
        self.assertTrue(self.pauses)

    def test_same_uid_moved_to_different_port_requires_confirmation(self):
        self.reconnect_fake({'ace_index': 0, 'slot': 0,
                             'insert_session': 'this-process', 'insert_epoch': 3,
                             'connection_identity': {'port': '/dev/old-port', 'uid': [1, 2, 3]}})
        METHODS._rearm_fa_after_reconnect(self.a, 0)
        self.assertEqual(self.sent, [])
        self.assertTrue(self.pauses)


class ActualSourceScopeTest(unittest.TestCase):
    def setUp(self):
        self.transport = types.SimpleNamespace(close=lambda: None)
        self.saved = []
        sensor = types.SimpleNamespace(runout_buttun_state=True)
        self.a = types.SimpleNamespace(
            _head_source={}, _serials={0: self.transport}, _connected_per_ace={0: True},
            _is_v2_idx=lambda idx: idx == 0,
            _location_identity=lambda idx: ('/dev/ace0', (1, 2, 3)),
            _gate_status_per_ace={0: [1, 1, 1, 1]}, _insert_epochs={(0, 0): 4},
            _insert_session='this-process', _active_device_index=0,
            _in_internal_load_head=False, head_uses_ace=lambda head: True,
            _ace_slot_for_head=lambda head: 0,
            printer=types.SimpleNamespace(lookup_object=lambda *args: sensor),
            _save_head_source=lambda: self.saved.append(True),
        )
        for name in ('_capture_source_origin', '_source_insertion_matches',
                     '_rfid_identity_status', '_inherit_prev_capture'):
            setattr(self.a, name, types.MethodType(getattr(METHODS, name), self.a))

    def source(self, **extra):
        return dict(ace_index=0, slot=0, type='PLA',
                    **self.a._capture_source_origin(0, 0), **extra)

    def test_capture_is_read_only_and_session_does_not_change(self):
        del self.a._insert_session
        first = self.a._capture_source_origin(0, 0)
        second = self.a._capture_source_origin(0, 0)
        self.assertEqual(first, second)
        self.assertEqual(len(first['insert_session']), 32)
        self.assertEqual(self.a._insert_epochs, {(0, 0): 4})
        self.assertEqual(self.saved, [])

    def test_capture_refuses_unverified_uid_and_gate(self):
        for uid, gate, connected in ((None, 1, True), ((), 1, True),
                                     ((0, 0, 0), 1, True), ((1, 2, 3), -1, True),
                                     ((1, 2, 3), 0, True), ((1, 2, 3), 1, False)):
            with self.subTest(uid=uid, gate=gate, connected=connected):
                self.a._location_identity = lambda idx, uid=uid: ('/dev/ace0', uid)
                self.a._gate_status_per_ace[0][0] = gate
                self.a._connected_per_ace[0] = connected
                with self.assertRaises(RuntimeError):
                    self.a._capture_source_origin(0, 0)
        self.assertEqual(self.saved, [])

    def test_successful_material_refresh_preserves_all_origin_fields(self):
        previous = self.source(load_failed=True)
        self.a._head_source[0] = previous
        # Material is already populated, exercising the early return too.
        result = self.a._inherit_prev_capture(0, 0, 0,
                    dict(ace_index=0, slot=0, type='PETG', load_failed=False))
        self.assertFalse(result['load_failed'])
        self.assertEqual(result['type'], 'PETG')
        for key in ('connection_identity', 'insert_epoch', 'insert_session'):
            self.assertEqual(result[key], previous[key])
        self.assertTrue(self.a._source_insertion_matches(result, 0, 0))

    def test_origin_is_never_inherited_to_a_different_slot(self):
        self.a._head_source[0] = self.source()
        result = self.a._inherit_prev_capture(0, 0, 1, {'ace_index': 0, 'slot': 1, 'type': 'PLA'})
        self.assertNotIn('insert_session', result)
        self.assertNotIn('connection_identity', result)

    def test_restore_does_not_refresh_a_persisted_scope(self):
        previous = self.source()
        previous['insert_session'] = 'previous-process'
        self.a.VARS_ACE_HEAD_SOURCE = 'sources'
        self.a.save_variables = types.SimpleNamespace(allVariables={'sources': {'0': previous}})
        METHODS._restore_head_source(self.a)
        restored = self.a._head_source[0]
        self.assertEqual(restored, previous)
        self.assertIsNot(restored, previous)
        self.assertFalse(self.a._source_insertion_matches(restored, 0, 0))
        self.assertEqual(self.saved, [])

    def test_retire_invalidates_all_four_slots_once_and_keeps_other_ace(self):
        self.a._insert_epochs[(0, 1)] = None
        self.a._insert_epochs[(1, 0)] = 9
        sources = [self.a._capture_source_origin(0, slot) for slot in range(4)]
        for name in ('_transport_retiring', '_thread_stop_flags', '_writer_queues',
                     '_ace_dev_fds', '_reader_threads', '_writer_threads', '_cb_locks'):
            setattr(self.a, name, {})
        done = METHODS._retire_transport(self.a, 0)
        self.assertTrue(done.wait(.5))
        epochs = dict(self.a._insert_epochs)
        for slot in range(4):
            self.assertFalse(self.a._source_insertion_matches(sources[slot], 0, slot))
        self.assertEqual(epochs[(1, 0)], 9)
        self.assertTrue(METHODS._retire_transport(self.a, 0).is_set())
        self.assertEqual(self.a._insert_epochs, epochs)
        self.assertEqual(self.a._insert_session, 'this-process')

    def test_ui_success_notification_cannot_create_origin_or_clear_failure(self):
        METHODS.notify_external_load(self.a, 'feed0', 0, 0)
        self.assertEqual(self.a._head_source, {})
        previous = self.source(load_failed=True)
        self.a._head_source[0] = previous
        METHODS.notify_external_load(self.a, 'feed0', 0, 0)
        self.assertTrue(previous['load_failed'])
        self.assertEqual(self.saved, [])

    def test_ui_success_preserves_supervised_origin(self):
        previous = self.source(load_failed=False)
        self.a._head_source[0] = previous
        METHODS.notify_external_load(self.a, 'feed0', 0, 0)
        self.assertIs(self.a._head_source[0], previous)
        self.assertTrue(self.a._source_insertion_matches(previous, 0, 0))
        self.assertEqual(self.saved, [])

    def test_command_does_not_refresh_old_scope_before_occupied_head_return(self):
        previous = self.source()
        previous['insert_session'] = 'previous-process'
        self.a._head_source[0] = previous
        for name in ('_require_slot_eject_idle', '_require_no_stop_fault',
                     '_require_location_clear', '_refuse_during_manual_load', '_wait_bg_op'):
            setattr(self.a, name, lambda *args, **kwargs: None)
        self.a._ensure_ace_available = lambda idx: True
        self.a._toolhead_filament_present = lambda *args, **kwargs: True
        self.a._ace_error = lambda gcmd, message, **kwargs: RuntimeError(message)
        gcmd = types.SimpleNamespace(get_int=lambda name, default=None:
                                     {'HEAD': 0, 'ACE': 0, 'SLOT': 0}.get(name, default))
        with self.assertRaisesRegex(RuntimeError, '原料路尚未确认'):
            METHODS.cmd_ACE_LOAD_HEAD(self.a, gcmd)
        self.assertIs(self.a._head_source[0], previous)
        self.assertEqual(self.saved, [])


if __name__ == '__main__':
    unittest.main()
