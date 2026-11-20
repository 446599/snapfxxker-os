"""Flow15 recovery contracts, exercising the actual controller and UI gates."""
import ast
import copy
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = '_flow15_recovery_tests'
pkg = types.ModuleType(PACKAGE)
pkg.__path__ = [str(ROOT / 'runtime/klipper/extras')]
sys.modules[PACKAGE] = pkg
RecoveryBudget = importlib.import_module(PACKAGE + '.ace_recovery_policy').RecoveryBudget
ErrorRecovery = importlib.import_module(PACKAGE + '.ace_error_recovery').ErrorRecovery
SlotActions = importlib.import_module(PACKAGE + '.ace_slot_actions').SlotActions

# Exercise the real process-scope helpers without importing ACE's full Klipper
# plugin (which requires the printer process and hardware modules).
_ace_tree = ast.parse((ROOT / 'runtime/klipper/extras/ace.py').read_text())
_origin_methods = [method for node in _ace_tree.body if isinstance(node, ast.ClassDef)
                   for method in node.body if isinstance(method, ast.FunctionDef)
                   and method.name in ('_capture_source_origin', '_source_insertion_matches')]
_origin_env = {'os': os, 'GATE_AVAILABLE': 1}
exec(compile(ast.Module(body=_origin_methods, type_ignores=[]), '<actual ACE origin helpers>', 'exec'), _origin_env)


class Reactor:
    NEVER = 1e30

    def __init__(self):
        self.now = 0.0
        self.pending = []
        self.timers = []

    def monotonic(self):
        return self.now

    def register_async_callback(self, callback):
        self.pending.append(callback)

    def register_timer(self, callback, when):
        item = (callback, when)
        self.timers.append(item)
        return item

    def unregister_timer(self, timer):
        self.timers.remove(timer)

    def run(self):
        callback = self.pending.pop(0)
        callback(self.now)


class Guard:
    def __init__(self):
        self.generation = 1
        self.faults = [{'ace_idx': 0, 'slot': 0, 'port': '/dev/ace0',
                        'uid': [1, 2, 3], 'reason': 'STOP response lost'}]

    def recovery_snapshot(self):
        return self.generation, copy.deepcopy(self.faults)

    def snapshot(self):
        return dict(self.faults[0]) if self.faults else None


class Actions:
    def __init__(self):
        self.seq = 0
        self.state = {'state': 'idle'}
        self.calls = []
        self.cancel = False
        self.fail = False
        self.reported_state = 'ready'

    def status(self):
        return dict(self.state)

    def _check(self, *args, **kwargs):
        pass

    def recover_slot(self, idx, slot, **kwargs):
        self.calls.append((idx, slot, kwargs))
        if self.fail:
            self.state.update(state='error', stopped=False)
            raise RuntimeError('no fresh stop telemetry')
        self.state.update(state='complete', stopped=True, slot_status=self.reported_state,
                          source_retained=True, path_cleared=False)
        return dict(self.state)


def fixture():
    a = types.SimpleNamespace()
    a.reactor = Reactor()
    hooks = types.SimpleNamespace(register_endpoint=lambda *args: None)
    ps = types.SimpleNamespace(state='error')
    a.printer = types.SimpleNamespace(
        lookup_object=lambda name, default=None: {'webhooks': hooks, 'print_stats': ps}.get(name, default),
        register_event_handler=lambda *args: None)
    a._slot_actions = Actions()
    a._stop_guard = Guard()
    a._serials = {0: object()}
    a._connected_per_ace = {0: True}
    a._insert_epochs = {(0, 0): 4}
    a._insert_session = '1' * 32
    a._ace_mode = 'multi'
    a._info_per_ace = {0: {'slots': [dict(index=slot, slot_status='ready') for slot in range(4)]}}
    a._location_identity = lambda idx: ('/dev/ace0', (1, 2, 3))
    a._rfid_identity_status = lambda idx: {'port': '/dev/ace0', 'uid': [1, 2, 3]}
    a._is_v2_idx = lambda idx: True
    a._capture_source_origin = types.MethodType(_origin_env['_capture_source_origin'], a)
    a._source_insertion_matches = types.MethodType(_origin_env['_source_insertion_matches'], a)
    a._rfid_route_heads = lambda idx, slot: [slot]
    a._head_source = {0: {'ace_index': 0, 'slot': 0, 'load_failed': True, 'insert_epoch': 4,
                         'insert_session': a._insert_session,
                         'connection_identity': {'port': '/dev/ace0', 'uid': [1, 2, 3]}}}
    a._swap_in_progress = a._unload_all_active = False
    a._feed_supervisors = {}
    recovery = ErrorRecovery(a)
    a._error_recovery = recovery
    return a, recovery, ps


class Request:
    def __init__(self, **data):
        self.data, self.result = data, None

    def get_int(self, key, default=None):
        return self.data.get(key, default)

    get_str = get_int

    def send(self, value):
        self.result = value


class RecoveryBudgetTests(unittest.TestCase):
    def test_same_incident_cannot_repeat_forever_after_stop_relatches(self):
        budget = RecoveryBudget()
        key, context = (0, 0), ('original transport', 4)
        for now in (0, 2):
            self.assertTrue(budget.observe(key, context, True, now))
            budget.start(key)
            budget.finish(key, now, False, 'STOP timed out')
        for now in range(4, 100):
            self.assertFalse(budget.observe(key, context, True, now))
        self.assertEqual(budget.snapshot(key)['attempts'], 2)

    def test_identity_change_blocks_pending_automatic_attempt(self):
        budget = RecoveryBudget()
        budget.observe((0, 0), 'old serial', True, 0)
        self.assertFalse(budget.observe((0, 0), 'replacement serial', True, 1))
        self.assertEqual(budget.snapshot((0, 0))['state'], 'blocked')

    def test_mechanical_error_stops_after_confirmed_stop(self):
        budget = RecoveryBudget()
        budget.observe((0, 0), 'original', True, 0)
        budget.start((0, 0))
        budget.finish((0, 0), 1, True, 'feed_error', needs_attention=True)
        self.assertFalse(budget.observe((0, 0), 'original', True, 10))
        self.assertEqual(budget.snapshot((0, 0))['state'], 'needs_attention')

    def test_only_sustained_healthy_state_starts_new_incident(self):
        budget = RecoveryBudget()
        budget.observe((0, 0), 'original', True, 0)
        budget.start((0, 0))
        budget.finish((0, 0), 0, True, 'stopped')
        budget.observe((0, 0), 'original', False, 1)
        self.assertFalse(budget.observe((0, 0), 'original', True, 2))
        budget.observe((0, 0), 'original', False, 3)
        budget.observe((0, 0), 'original', False, 5)
        self.assertTrue(budget.observe((0, 0), 'original', True, 6))
        self.assertEqual(budget.snapshot((0, 0))['attempts'], 0)


class RecoveryControllerTests(unittest.TestCase):
    def test_error_state_allows_manual_auto_without_motor_probe(self):
        a, recovery, ps = fixture()
        ticket = recovery.status()['slots'][0]['ticket']
        request = Request(ace=0, slot=0, action='auto', ticket=ticket, confirmed=1)
        before = copy.deepcopy(a._head_source)
        recovery.endpoint(request)
        self.assertTrue(request.result['started'])
        self.assertFalse(a._slot_actions.calls)  # queued; no wire work in caller
        a.reactor.run()
        call = a._slot_actions.calls[0][2]
        self.assertFalse(call['allow_probe'])
        self.assertTrue(call['clear_latch'])
        self.assertEqual(a._head_source, before)
        self.assertEqual(ps.state, 'error')  # never resume a print
        self.assertFalse(recovery.active)

    def test_automatic_feed_error_does_not_retry_physical_jam(self):
        a, recovery, _ = fixture()
        a._info_per_ace[0]['slots'][0]['slot_status'] = 'feed_error'
        a._slot_actions.reported_state = 'feed_error'
        recovery._automatic_tick(0)
        self.assertFalse(a._slot_actions.calls)
        a.reactor.run()
        self.assertFalse(a._slot_actions.calls[0][2]['allow_probe'])
        self.assertFalse(a._slot_actions.state['recovered'])
        self.assertTrue(a._slot_actions.state['needs_attention'])
        self.assertIn('喷头前端', a._slot_actions.state['message'])
        recovery._automatic_tick(10)
        self.assertFalse(a.reactor.pending)

    def test_busy_and_printing_do_not_start_or_consume_automatic_budget(self):
        a, recovery, ps = fixture()
        for state, busy in [('printing', False), ('paused', False), ('error', True)]:
            ps.state, a._swap_in_progress = state, busy
            recovery._automatic_tick(0)
            self.assertFalse(a.reactor.pending)
            self.assertFalse(recovery._automatic.episodes)
        ps.state, a._swap_in_progress = 'error', False
        recovery._automatic_tick(1)
        self.assertEqual(len(a.reactor.pending), 1)
        recovery._automatic_tick(1.5)
        self.assertEqual(len(a.reactor.pending), 1)

    def test_two_failed_stop_checks_do_not_loop_when_fault_generation_changes(self):
        a, recovery, _ = fixture()
        a._slot_actions.fail = True
        for now in (0, 2):
            a.reactor.now = now
            a._stop_guard.generation += 1
            recovery._automatic_tick(now)
            a.reactor.run()
        for now in (4, 8, 30):
            a._stop_guard.generation += 1
            recovery._automatic_tick(now)
            self.assertFalse(a.reactor.pending)
        self.assertEqual(len(a._slot_actions.calls), 2)
        self.assertFalse(recovery.active)

    def test_connection_change_before_async_work_prevents_recovery(self):
        a, recovery, _ = fixture()
        recovery._automatic_tick(0)
        a._serials[0] = object()
        a.reactor.run()
        self.assertFalse(a._slot_actions.calls)
        self.assertEqual(recovery._automatic.snapshot((0, 0))['state'], 'blocked')
        self.assertFalse(recovery.active)

    def test_old_source_uid_never_authorizes_new_device(self):
        a, recovery, _ = fixture()
        a._head_source[0]['connection_identity']['uid'] = [9, 9, 9]
        recovery._automatic_tick(0)
        self.assertFalse(a.reactor.pending)
        self.assertEqual(recovery._automatic.snapshot((0, 0))['state'], 'blocked')

    def test_explicit_short_retract_is_the_only_action_allowing_probe(self):
        a, recovery, _ = fixture()
        request = Request(ace=0, slot=0, action='clear', ticket=recovery._ticket(0, 0), confirmed=1)
        recovery.endpoint(request)
        a.reactor.run()
        self.assertTrue(a._slot_actions.calls[0][2]['allow_probe'])

    def test_assist_and_unrelated_lanes_are_not_marked_faulty(self):
        a, recovery, _ = fixture()
        a._info_per_ace[0]['slots'][1]['slot_status'] = 'assisting'
        a._info_per_ace[0]['slots'][2]['slot_status'] = 'rollback_assisting'
        rows = recovery.status()['slots']
        self.assertEqual([row['needs_clear'] for row in rows], [True, False, False, False])

    def test_stale_ticket_is_rejected_before_commands(self):
        a, recovery, _ = fixture()
        ticket = recovery._ticket(0, 0)
        a._insert_epochs[(0, 0)] += 1
        request = Request(ace=0, slot=0, action='auto', ticket=ticket, confirmed=1)
        recovery.endpoint(request)
        self.assertFalse(request.result['started'])
        self.assertFalse(a.reactor.pending)

    def test_disconnect_unregisters_timer_and_blocks_pending_work(self):
        a, recovery, _ = fixture()
        recovery._handle_ready()
        self.assertEqual(len(a.reactor.timers), 1)
        recovery._automatic_tick(0)
        recovery._handle_disconnect()
        self.assertFalse(a.reactor.timers)
        a.reactor.run()
        self.assertFalse(a._slot_actions.calls)

    def test_transient_preflight_retries_without_spending_stop_attempts(self):
        a, recovery, _ = fixture()
        def not_ready(*args, **kwargs):
            raise RuntimeError('喷头传感器暂不可用')
        a._slot_actions._check = not_ready
        recovery._automatic_tick(0)
        self.assertFalse(a.reactor.pending)
        self.assertEqual(recovery._automatic.snapshot((0, 0))['attempts'], 0)
        self.assertEqual(recovery._automatic.snapshot((0, 0))['state'], 'pending')
        a._slot_actions._check = lambda *args, **kwargs: None
        recovery._automatic_tick(2)
        self.assertEqual(len(a.reactor.pending), 1)

    def test_wait_for_identity_then_validate_before_first_stop_attempt(self):
        a, recovery, _ = fixture()
        identity = a._rfid_identity_status
        a._rfid_identity_status = lambda idx: None
        recovery._automatic_tick(0)
        self.assertFalse(a.reactor.pending)
        a._rfid_identity_status = identity
        recovery._automatic_tick(2)
        self.assertEqual(len(a.reactor.pending), 1)

    def test_cleanup_error_cannot_leave_recovery_owner_stuck(self):
        a, recovery, _ = fixture()
        recovery._automatic_tick(0)
        a._location_identity = lambda idx: (_ for _ in ()).throw(RuntimeError('identity unavailable'))
        a.reactor.run()
        self.assertFalse(recovery.active)
        self.assertEqual(a._slot_actions.state['state'], 'error')

    def test_local_stop_recovery_does_not_clear_another_slot_fault(self):
        source = ast.parse((ROOT / 'runtime/klipper/extras/ace_slot_actions.py').read_text())
        cls = next(node for node in source.body if isinstance(node, ast.ClassDef)
                   and node.name == 'SlotActions')
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                      and node.name == '_release_verified_stop_fault')
        env = {}
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<actual stop release>', 'exec'), env)
        faults = [{'ace_idx': 0, 'slot': 0, 'reason': 'STOP lost'},
                  {'ace_idx': 1, 'slot': 2, 'reason': 'other STOP lost'}]
        def clear(generation, idx, slot, identity):
            faults[:] = [fault for fault in faults if (fault['ace_idx'], fault['slot']) != (idx, slot)]
            return True
        guard = types.SimpleNamespace(snapshot=lambda: faults[0] if faults else None,
                                      recovery_snapshot=lambda: (4, faults), clear_verified=clear)
        def require_no_fault():
            if faults:
                raise RuntimeError('another slot STOP remains unconfirmed')
        target = types.SimpleNamespace(ace=types.SimpleNamespace(_stop_guard=guard,
                                      _require_no_stop_fault=require_no_fault), _eject_identity=('port', (1, 2, 3)))
        release = env['_release_verified_stop_fault']
        release(target, 0, 0, 4, require_global_clear=False)
        self.assertEqual(faults, [{'ace_idx': 1, 'slot': 2, 'reason': 'other STOP lost'}])
        with self.assertRaises(RuntimeError):
            release(target, 0, 0, 4)


class PublicContractTests(unittest.TestCase):
    def test_profile_api_requires_current_slot_uid_and_explicit_save_action(self):
        source = ast.parse((ROOT / 'runtime/web/backend/main.py').read_text())
        function = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                        and node.name == '_spool_profile_payload')
        class HTTPError(Exception):
            pass
        env = {'HTTPException': HTTPError}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<actual profile API>', 'exec'), env)
        validate = env['_spool_profile_payload']
        read = dict(ace=0, slot=0, uid='aabbccdd', action='read')
        save = dict(read, action='save', profile={'calibrated': True})
        self.assertEqual(validate(read), read)
        self.assertEqual(validate(save), save)
        for bad in (dict(read, ace=True), dict(read, slot=4), dict(read, uid=''),
                    dict(read, action='erase'), dict(save, profile=[])):
            with self.assertRaises(HTTPError):
                validate(bad)

    def test_backend_accepts_auto_and_keeps_strict_target_validation(self):
        source = ast.parse((ROOT / 'runtime/web/backend/main.py').read_text())
        function = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                        and node.name == '_slot_operation_payload')
        class HTTPError(Exception):
            pass
        env = {'HTTPException': HTTPError}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<actual backend function>', 'exec'), env)
        validate = env['_slot_operation_payload']
        request = dict(ace=0, slot=0, action='auto', ticket='a' * 32, confirmed=1)
        self.assertEqual(validate(request, True), request)
        for bad in (dict(request, ace=True), dict(request, slot=4), dict(request, action='resume'),
                    dict(request, confirmed=True), dict(request, ticket='old')):
            with self.assertRaises(HTTPError):
                validate(bad, True)

    def test_actual_frontend_gates(self):
        result = subprocess.run(['node', str(ROOT / 'tests/test_recovery_ui.js')],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class RetainedRouteIdentityTests(unittest.TestCase):
    def setup_route(self):
        a, recovery, ps = fixture()
        original_lookup = a.printer.lookup_object
        sensor = types.SimpleNamespace(runout_buttun_state=False)
        a._test_head_sensor = sensor
        a.printer.lookup_object = lambda name, default=None: (
            sensor if name == 'filament_motion_sensor e0_filament'
            else original_lookup(name, default))
        a._head_travel = types.SimpleNamespace(records={})
        a._path_calibration = types.SimpleNamespace(task={'active': False},
            head_sensor=lambda head: sensor.runout_buttun_state, _head=lambda idx, slot, head: head)
        a._path_operations = types.SimpleNamespace(status=lambda: [])
        a._location_hold = types.SimpleNamespace(refresh_routes=lambda: True, routes=[])
        a._ace_devices = ['ace0']
        a.EXTRUDER_MAP = {0: ('left', 1)}
        a._fw_update_hold = set()
        a._gate_status_per_ace = {0: [1, 1, 1, 1]}
        a._is_v2_idx = lambda idx: True
        a._save_head_source = lambda: None
        a._ghost_heads = set()
        actions = SlotActions.__new__(SlotActions)
        actions.ace = a
        actions._eject_lease = None
        commands = []
        def request(idx, method, params, command, monitor=None):
            commands.append(method)
            monitor()
            return {'uid1': 1, 'uid2': 2, 'uid3': 3}
        actions._request = request
        return a, recovery, actions, commands

    def test_empty_head_cannot_reassign_retained_source_to_replacement_ace(self):
        a, _, actions, commands = self.setup_route()
        a._head_source[0]['connection_identity']['uid'] = [8, 8, 8]
        with self.assertRaisesRegex(RuntimeError, '先前的设备'):
            actions._bind_legacy_route(0, 0, a._serials[0])
        self.assertIsNone(actions._eject_lease)
        self.assertEqual(commands, ['discover_device'])
        self.assertEqual(a._head_source[0]['connection_identity']['uid'], [8, 8, 8])

    def test_changed_insertion_epoch_cannot_reuse_retained_route(self):
        a, _, actions, commands = self.setup_route()
        a._insert_epochs[(0, 0)] = 5
        with self.assertRaisesRegex(RuntimeError, '插料代次已变化'):
            actions._bind_legacy_route(0, 0, a._serials[0])
        self.assertIsNone(actions._eject_lease)
        self.assertEqual(commands, ['discover_device'])

    def test_legacy_missing_uid_needs_explicit_confirmation_and_keeps_failed_path(self):
        a, recovery, actions, commands = self.setup_route()
        del a._head_source[0]['connection_identity']
        del a._head_source[0]['insert_epoch']
        with self.assertRaisesRegex(RuntimeError, '旧来源没有设备 UID'):
            actions._bind_legacy_route(0, 0, a._serials[0])
        ticket = recovery._ticket(0, 0)
        result = recovery.confirm_source(0, 0, 0, retained=True, ticket=ticket)
        self.assertTrue(result['saved'])
        self.assertTrue(result['load_failed'])
        self.assertTrue(a._head_source[0]['load_failed'])
        self.assertEqual(a._head_source[0]['connection_identity'], {'port': '/dev/ace0', 'uid': [1, 2, 3]})
        actions._bind_legacy_route(0, 0, a._serials[0])
        self.assertEqual(actions._eject_lease, (0, 0, a._serials[0]))
        self.assertTrue(a._head_source[0]['load_failed'])
        self.assertEqual(commands, ['discover_device', 'discover_device'])

    def test_restart_requires_confirmation_even_if_numeric_epoch_matches(self):
        for current_epoch in (4, None):
            with self.subTest(current_epoch=current_epoch):
                a, recovery, actions, commands = self.setup_route()
                original = copy.deepcopy(a._head_source[0])
                original['pathhold'] = {'reason': 'material retained in tube'}
                a._head_source[0] = copy.deepcopy(original)
                a._insert_session = '2' * 32
                if current_epoch is None:
                    a._insert_epochs.clear()
                a._path_calibration.store = types.SimpleNamespace(state={
                    'occupied_heads': {'0': {'route_id': 'original'}},
                    'routes': {'original': {'ace': 0, 'slot': 0}}})
                held_path = copy.deepcopy(a._path_calibration.store.state)
                recovery._automatic_tick(0)
                self.assertFalse(a.reactor.pending)
                self.assertEqual(recovery._automatic.snapshot((0, 0))['state'], 'blocked')
                with self.assertRaisesRegex(RuntimeError, '会话或插料代次'):
                    actions._bind_legacy_route(0, 0, a._serials[0])
                row = recovery.status()['slots'][0]
                self.assertTrue(row['can_confirm_source'])
                self.assertIn('重启', row['source_confirmation_prompt'])
                request = Request(head=0, ace=0, slot=0, retained=1,
                                  confirmed=1, ticket=row['ticket'])
                recovery.source_endpoint(request)
                self.assertTrue(request.result['saved'])
                expected = dict(original, insert_session=a._insert_session,
                                insert_epoch=current_epoch)
                self.assertEqual(a._head_source[0], expected)
                self.assertEqual(a._path_calibration.store.state, held_path)
                self.assertFalse(a.reactor.pending)
                self.assertFalse(a._slot_actions.calls)
                self.assertEqual(a.printer.lookup_object('print_stats').state, 'error')
                recovery._original_source(0, 0)
                actions._bind_legacy_route(0, 0, a._serials[0])
                self.assertEqual(actions._eject_lease, (0, 0, a._serials[0]))
                self.assertEqual(commands, ['discover_device', 'discover_device'])

    def test_same_uid_port_change_needs_explicit_retained_confirmation(self):
        a, recovery, actions, commands = self.setup_route()
        a._head_source[0]['connection_identity']['port'] = '/dev/previous-ace-port'
        before = copy.deepcopy(a._head_source)
        with self.assertRaisesRegex(RuntimeError, '设备或端口'):
            recovery._original_source(0, 0)
        a._test_head_sensor.runout_buttun_state = True
        with self.assertRaisesRegex(RuntimeError, '当前票据'):
            recovery.confirm_source(0, 0, 0)
        a._test_head_sensor.runout_buttun_state = False
        self.assertEqual(a._head_source, before)
        row = recovery.status()['slots'][0]
        self.assertIn('端口', row['source_confirmation_prompt'])
        recovery.confirm_source(0, 0, 0, retained=True, ticket=row['ticket'])
        self.assertTrue(a._head_source[0]['load_failed'])
        self.assertEqual(a._head_source[0]['connection_identity'],
                         {'port': '/dev/ace0', 'uid': [1, 2, 3]})
        actions._bind_legacy_route(0, 0, a._serials[0])
        self.assertEqual(commands, ['discover_device'])

    def test_new_insertion_never_automatically_rebinds_previous_spool(self):
        a, recovery, _, commands = self.setup_route()
        old_ticket = recovery._ticket(0, 0)
        a._insert_epochs[(0, 0)] += 2  # removal and a new insertion
        before = copy.deepcopy(a._head_source)
        recovery._automatic_tick(0)
        self.assertFalse(a.reactor.pending)
        self.assertEqual(a._head_source, before)
        stale = Request(head=0, ace=0, slot=0, retained=1, confirmed=1, ticket=old_ticket)
        recovery.source_endpoint(stale)
        self.assertFalse(stale.result['saved'])
        row = recovery.status()['slots'][0]
        self.assertIn('插拔', row['source_confirmation_prompt'])
        self.assertIn('更换耗材', row['source_confirmation_prompt'])
        unconfirmed = Request(head=0, ace=0, slot=0, retained=1, confirmed=0, ticket=row['ticket'])
        recovery.source_endpoint(unconfirmed)
        self.assertFalse(unconfirmed.result['saved'])
        self.assertEqual(a._head_source, before)
        confirmed = Request(head=0, ace=0, slot=0, retained=1, confirmed=1, ticket=row['ticket'])
        recovery.source_endpoint(confirmed)
        self.assertTrue(confirmed.result['saved'])
        self.assertEqual(a._head_source[0]['insert_epoch'], 6)
        self.assertTrue(a._head_source[0]['load_failed'])
        self.assertFalse(commands)
        self.assertFalse(a.reactor.pending)

    def test_confirmation_never_overwrites_known_other_device_or_slot(self):
        for mismatch in ('uid', 'slot', 'ace'):
            with self.subTest(mismatch=mismatch):
                a, recovery, _, commands = self.setup_route()
                if mismatch == 'uid':
                    a._head_source[0]['connection_identity']['uid'] = [9, 9, 9]
                else:
                    a._head_source[0]['slot' if mismatch == 'slot' else 'ace_index'] = 1
                a._insert_session = '2' * 32
                before = copy.deepcopy(a._head_source)
                row = recovery.status()['slots'][0]
                self.assertFalse(row['can_confirm_source'])
                with self.assertRaises(RuntimeError):
                    recovery.confirm_source(0, 0, 0, retained=True, ticket=row['ticket'])
                a._test_head_sensor.runout_buttun_state = True
                with self.assertRaises(RuntimeError):
                    recovery.confirm_source(0, 0, 0)
                self.assertEqual(a._head_source, before)
                self.assertFalse(commands)

    def test_confirmation_ticket_is_invalidated_by_sensor_changes(self):
        for changed in ('head', 'gate', 'unknown'):
            with self.subTest(changed=changed):
                a, recovery, _, commands = self.setup_route()
                a._insert_session = '2' * 32
                ticket = recovery._ticket(0, 0)
                before = copy.deepcopy(a._head_source)
                if changed == 'head':
                    a._test_head_sensor.runout_buttun_state = True
                elif changed == 'gate':
                    a._gate_status_per_ace[0][0] = 0
                else:
                    a._test_head_sensor.runout_buttun_state = None
                with self.assertRaisesRegex(RuntimeError, '已变化'):
                    recovery.confirm_source(0, 0, 0, retained=True, ticket=ticket)
                if changed != 'head':
                    with self.assertRaises(RuntimeError):
                        recovery.confirm_source(0, 0, 0, retained=True, ticket=recovery._ticket(0, 0))
                self.assertEqual(a._head_source, before)
                self.assertFalse(commands)

    def test_same_live_origin_does_not_ask_for_reconfirmation(self):
        a, recovery, actions, commands = self.setup_route()
        row = recovery.status()['slots'][0]
        self.assertFalse(row['needs_source_confirmation'])
        self.assertFalse(row['can_confirm_source'])
        recovery._original_source(0, 0)
        actions._bind_legacy_route(0, 0, a._serials[0])
        self.assertEqual(commands, ['discover_device'])

    def test_failed_confirmation_save_restores_original_session_and_hold(self):
        a, recovery, _, commands = self.setup_route()
        a._insert_session = '2' * 32
        a._head_source[0]['pathhold'] = {'reason': 'retained material'}
        before = copy.deepcopy(a._head_source)
        def fail_save():
            raise RuntimeError('storage unavailable')
        a._save_head_source = fail_save
        with self.assertRaisesRegex(RuntimeError, 'storage unavailable'):
            recovery.confirm_source(0, 0, 0, retained=True, ticket=recovery._ticket(0, 0))
        self.assertEqual(a._head_source, before)
        self.assertFalse(commands)

    def test_v1_confirmation_does_not_call_v2_origin_helpers(self):
        a, recovery, _, commands = self.setup_route()
        a._is_v2_idx = lambda idx: False
        a._test_head_sensor.runout_buttun_state = True
        del a._head_source[0]['insert_session']
        def v2_only(*args):
            raise AssertionError('V2 origin helper called for V1')
        a._capture_source_origin = a._source_insertion_matches = v2_only
        self.assertIsNone(recovery._retained_confirmation_head(0, 0))
        result = recovery.confirm_source(0, 0, 0)
        self.assertTrue(result['saved'])
        self.assertTrue(a._head_source[0]['load_failed'])
        self.assertNotIn('insert_session', a._head_source[0])
        self.assertFalse(commands)

    def test_port_confirmation_reports_retained_real_stop_fault_and_path_hold(self):
        StopFaultLatch = importlib.import_module(PACKAGE + '.ace_stop_guard').StopFaultLatch
        LocationHold = importlib.import_module(PACKAGE + '.ace_location_hold').LocationHold
        with tempfile.TemporaryDirectory() as tmp:
            a, recovery, actions, commands = self.setup_route()
            old_port = '/dev/original-ace-port'
            a._head_source[0]['connection_identity']['port'] = old_port
            a._stop_guard = StopFaultLatch(str(Path(tmp) / 'stop.json'))
            a._stop_guard.latch(0, 0, 'STOP response lost', (old_port, (1, 2, 3)))
            a._location_hold = LocationHold(path=str(Path(tmp) / 'marker.json'),
                                            routes_path=str(Path(tmp) / 'holds.json'))
            a._location_hold.hold_route(old_port, (1, 2, 3), 0, [0], scope='route')
            faults_before = a._stop_guard.recovery_snapshot()
            holds_before = copy.deepcopy(a._location_hold.routes)
            request = Request(head=0, ace=0, slot=0, retained=1, confirmed=1,
                              ticket=recovery._ticket(0, 0))
            recovery.source_endpoint(request)
            self.assertTrue(request.result['saved'])
            self.assertTrue(request.result['connection_review_required'])
            self.assertIn('原 USB 连接', request.result['message'])
            self.assertIn('未解锁退料', request.result['message'])
            row = recovery.status()['slots'][0]
            self.assertTrue(row['connection_review_required'])
            self.assertIn('停止故障和管内占用', row['connection_review_message'])
            with self.assertRaisesRegex(RuntimeError, '原 USB 连接'):
                recovery._original_source(0, 0)
            with self.assertRaisesRegex(RuntimeError, '另一原槽的位置异常'):
                actions._bind_legacy_route(0, 0, a._serials[0], allow_loaded=True)
            generation, _ = a._stop_guard.recovery_snapshot()
            self.assertFalse(a._stop_guard.clear_verified(generation, 0, 0, a._location_identity(0)))
            recovery._automatic_tick(0)
            self.assertFalse(a.reactor.pending)
            self.assertEqual(recovery._automatic.snapshot((0, 0))['attempts'], 0)
            self.assertEqual(recovery._automatic.snapshot((0, 0))['state'], 'blocked')
            self.assertEqual(a._stop_guard.recovery_snapshot(), faults_before)
            self.assertEqual(a._location_hold.routes, holds_before)
            self.assertEqual(commands, ['discover_device'])


if __name__ == '__main__':
    unittest.main()
