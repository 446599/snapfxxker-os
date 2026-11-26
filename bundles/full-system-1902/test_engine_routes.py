"""Exercise the candidate methods against the pinned field provider, without a printer."""
import ast
import logging
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from compose import patch_preload
from screen_compat import patch_engine
from test_compose import field_text
from test_screen_compat import extract

HERE = Path(__file__).resolve().parent


class Gcmd(dict):
    error = ValueError

    def get_int(self, name, default=None):
        return self.get(name, default)


class RouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        baseline = patch_engine(patch_preload(field_text('klipper/extras/ace.py')))
        names = ['_cmd_ACE_UNLOAD_HEAD_body', '_cmd_ACE_LOAD_HEAD_body',
                 '_disable_feed_assist_all', '_retract_send_wait', '_finish_interrupted_retract']
        cls.baseline = {}
        extract(baseline, names, cls.baseline)
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / 'klipper/extras'
            tree.mkdir(parents=True)
            (tree / 'ace.py').write_text(baseline)
            (tree / 'filament_feed_ace.py').write_text(field_text('klipper/extras/filament_feed_ace.py'))
            subprocess.run(['patch', '-p1', '--batch', '--fuzz=0',
                            '-i', str(HERE / 'engine_routes.patch')], cwd=tmp, check=True)
            cls.candidate = {}
            extract((tree / 'ace.py').read_text(), names + ['_disable_feed_assist_for_head'], cls.candidate)
            cls.feed_tree = ast.parse((tree / 'filament_feed_ace.py').read_text())
        for env in (cls.candidate, cls.baseline):
            env.update(logging=logging, GATE_EMPTY=0, GATE_UNKNOWN=-1, RETRACT_SEND_RETRIES=2,
                       RETRACT_RETRY_PAUSE=0.1, RETRACT_REPLY_TIMEOUT=1.0,
                       RETRACT_PENDING_PRE_MM=100, RETRACT_PENDING_MIN_REDO_MM=10)

    def machine(self):
        a = Mock()
        a._head_source = {2: {'ace_index': 3, 'slot': 2}}
        a._bg_staged = {}
        a._bg_left_empty = set()
        a._unload_slot_hint = {}
        a._manual_op_in_running_print.return_value = False
        a._calibration_route_occupied.return_value = None
        a.head_uses_ace.return_value = True
        a._head_is_loaded.return_value = False
        a._ace_mode = 'multi'
        a._ace_devices = list(range(4))
        a._active_device_index = 3
        a._connected_per_ace = {n: True for n in range(4)}
        a._disp.side_effect = lambda n: n+1
        a._t.side_effect = lambda name, **kw: name
        a._is_actively_printing.return_value = False
        a._swap_in_progress = False
        a._gate_status_per_ace = {3: [1, 1, 0, 1]}
        a._ensure_ace_available.return_value = True
        a.printer.command_error = RuntimeError
        a.printer.lookup_object.return_value.get_status.return_value = {'filament_detected': False}
        return a

    def test_bay_unload_compares_source_after_background_finishes(self):
        for current, should_reject in [(self.baseline, False), (self.candidate, True)]:
            a = self.machine()
            a._wait_bg_op.side_effect = lambda *args: a._head_source.update({2: {'ace_index': 1, 'slot': 2}})
            cmd = Gcmd(HEAD=2, ACE=3, SLOT=2)
            if should_reject:
                with self.assertRaisesRegex(ValueError, 'source'):
                    current['_cmd_ACE_UNLOAD_HEAD_body'](a, cmd)
                a._head_is_loaded.assert_not_called()
                a.printer.lookup_object.assert_not_called()
                a.gcode.run_script_from_command.assert_not_called()
                self.assertFalse(a._last_unload_ok)
            else:
                current['_cmd_ACE_UNLOAD_HEAD_body'](a, cmd)
                a._head_is_loaded.assert_called_once_with(2)

    def test_bay_unload_requires_pair_and_real_source(self):
        for cmd, uses_ace in [(Gcmd(HEAD=2, ACE=3), True), (Gcmd(HEAD=2, SLOT=2), True),
                              (Gcmd(HEAD=2, ACE=3, SLOT=2), False), (Gcmd(HEAD=2, ACE=3, SLOT=2), True)]:
            a = self.machine()
            a._head_source = {}
            a.head_uses_ace.return_value = uses_ace
            with self.subTest(cmd=cmd, uses_ace=uses_ace), self.assertRaises(ValueError):
                self.candidate['_cmd_ACE_UNLOAD_HEAD_body'](a, cmd)
            a._head_is_loaded.assert_not_called()
            a.gcode.run_script_from_command.assert_not_called()

    def test_matching_track_stage_and_head_only_preserve_regular_empty_noop(self):
        for src, stage, cmd in [({2: {'ace_index': 3, 'slot': 2}}, {}, Gcmd(HEAD=2, ACE=3, SLOT=2)),
                                ({}, {2: (3, 2)}, Gcmd(HEAD=2, ACE=3, SLOT=2)),
                                ({}, {}, Gcmd(HEAD=2))]:
            a = self.machine()
            a._head_source, a._bg_staged = src, stage
            self.candidate['_cmd_ACE_UNLOAD_HEAD_body'](a, cmd)
            a._head_is_loaded.assert_called_once_with(2)
            a.gcode.run_script_from_command.assert_not_called()

    def test_bay_load_reports_empty_offline_foreign_and_unmapped_head(self):
        cmd = Gcmd(HEAD=2, ACE=3, SLOT=2)
        for detected, available, source in [(False, False, {}), (False, True, {}),
                                            (True, True, {2: {'ace_index': 1, 'slot': 2}}),
                                            (True, True, {})]:
            for env, rejects in [(self.baseline, False), (self.candidate, True)]:
                a = self.machine()
                a._head_source = source
                a.printer.lookup_object.return_value.get_status.return_value = {'filament_detected': detected}
                a._ensure_ace_available.return_value = available
                with self.subTest(detected=detected, available=available, source=source, rejects=rejects):
                    if rejects:
                        with self.assertRaises(ValueError):
                            env['_cmd_ACE_LOAD_HEAD_body'](a, cmd)
                        self.assertFalse(a._last_load_ok)
                    else:
                        env['_cmd_ACE_LOAD_HEAD_body'](a, cmd)
                        self.assertTrue(a._last_load_ok)
                    a.gcode.run_script_from_command.assert_not_called()

    def test_bay_load_already_matches_head(self):
        a = self.machine()
        a.printer.lookup_object.return_value.get_status.return_value = {'filament_detected': True}
        self.candidate['_cmd_ACE_LOAD_HEAD_body'](a, Gcmd(HEAD=2, ACE=3, SLOT=2))
        a.gcode.run_script_from_command.assert_not_called()
        self.assertTrue(a._last_load_ok)

    def test_loaded_foreign_source_survives_a_clear_motion_sensor(self):
        a = self.machine()
        a._head_is_loaded.return_value = True
        a._head_source[2] = {'ace_index': 1, 'slot': 2}
        with self.assertRaisesRegex(ValueError, 'different bay'):
            self.candidate['_cmd_ACE_LOAD_HEAD_body'](a, Gcmd(HEAD=2, ACE=3, SLOT=2))
        a._finish_interrupted_retract.assert_not_called()
        a.gcode.run_script_from_command.assert_not_called()
        self.assertEqual(a._head_source[2]['ace_index'], 1)

    def test_unverified_bay_unload_reports_failure_and_preserves_source(self):
        a = self.machine()
        a._head_is_loaded.return_value = True
        a._cap.return_value = True
        a.EXTRUDER_MAP = {2: ('e2', 0)}
        a._feed_assist_per_ace = {}
        a._calibration_unload = {}
        def feed(script):
            if 'UNLOAD=1 STAGE=doing' in script:
                a._last_unload_ok = False
                a._last_unload_reason = 'bowden_stall'
        a.gcode.run_script_from_command.side_effect = feed
        with self.assertRaisesRegex(ValueError, 'bowden_stall'):
            self.candidate['_cmd_ACE_UNLOAD_HEAD_body'](a, Gcmd(HEAD=2, ACE=3, SLOT=2))
        self.assertEqual(a._head_source[2], {'ace_index': 3, 'slot': 2})

    def test_assist_cleanup_preserves_unrelated_units_and_global_compatibility(self):
        for mode, route, expected in [('bay', (3, 2), [(3, 2)]), ('bay', (3, 1), []),
                                      ('global', None, [(1, 0), (3, 2)])]:
            a = self.machine()
            a._head_source = {2: {'ace_index': route[0], 'slot': route[1]}} if route else {}
            a._feed_assist_per_ace = {0: -1, 1: 0, 2: -1, 3: 2}
            a._gate_status_per_ace = {}
            a._cap.side_effect = lambda idx, cap: cap == 'fa_stop_rollback'
            a._disable_feed_assist_all.side_effect = lambda **kwargs: self.candidate['_disable_feed_assist_all'](a, **kwargs)
            if mode == 'bay':
                self.candidate['_disable_feed_assist_for_head'](a, 2)
            else:
                self.candidate['_disable_feed_assist_all'](a)
            sent = [(c.args[0], c.args[1]['params']['index']) for c in a.send_request_to.call_args_list]
            self.assertEqual(sent, expected)

    def test_no_assist_command_without_a_tracked_or_staged_route(self):
        a = self.machine()
        a._head_source.clear()
        self.candidate['_disable_feed_assist_for_head'](a, 2)
        a._disable_feed_assist_all.assert_not_called()
        a._ace_slot_for_head.assert_not_called()
        a._unload_slot_hint[2] = {'ace_index': 3, 'slot': 2}
        self.candidate['_disable_feed_assist_for_head'](a, 2)
        a._disable_feed_assist_all.assert_called_once_with(only_route=(3, 2))

    def test_interrupted_retract_keeps_old_bay_and_head_only_recovery(self):
        def machine():
            a = self.machine()
            a._retract_pending = {'1_2': {'head': 2, 'ace': 1, 'slot': 2, 'len': 300},
                                  '2_1': {'head': 1, 'ace': 2, 'slot': 1, 'len': 400}}
            a._head_sensor_present.return_value = False
            a.get_retract_length.return_value = 1000
            a.get_retract_speed.return_value = 40
            a._retract_pending_clear.side_effect = a._retract_pending.pop
            return a
        run = self.candidate['_finish_interrupted_retract']
        a = machine()
        with self.assertRaisesRegex(ValueError, 'interrupted'):
            run(a, Gcmd(HEAD=2, ACE=3, SLOT=2), 2)
        self.assertEqual(len(a._retract_pending), 2)
        a._retract.assert_not_called()
        run(a, Gcmd(HEAD=2, ACE=1, SLOT=2), 2)
        self.assertEqual(list(a._retract_pending), ['2_1'])
        a._retract.assert_not_called()
        b = machine()
        run(b, Gcmd(HEAD=2), 2)
        b._retract.assert_called_once_with(2, 300, 40, head=2, ace=1)
        self.assertEqual(list(b._retract_pending), ['2_1'])

    def test_positive_retract_rejection_is_not_success(self):
        for env, rejects in [(self.baseline, False), (self.candidate, True)]:
            a = self.machine()
            a._tipform_send.return_value = {'code': 0, 'msg': 'FORBIDDEN'}
            a._tipform_rejected.return_value = True
            a._cap.return_value = False
            a.reactor.monotonic.return_value = 0
            args = (a, 1, 2, 200, 40, {}, lambda *unused: None)
            if rejects:
                with self.assertRaises(RuntimeError):
                    env['_retract_send_wait'](*args)
            else:
                self.assertTrue(env['_retract_send_wait'](*args))
            self.assertEqual(a._tipform_send.call_count, 3)
            a._await_retract_end.assert_not_called()
        for accepted in [None, {'code': 0, 'msg': 'ok'}]:
            a = self.machine()
            a._tipform_send.return_value = accepted
            a._tipform_rejected.return_value = False
            self.assertTrue(self.candidate['_retract_send_wait'](a, 1, 2, 200, 40, {}, None))
            a._await_retract_end.assert_called_once()

    def test_both_retract_paths_stop_if_selection_fails_and_pin_moves_across_yields(self):
        nodes = list(ast.walk(self.feed_tree))
        blocks = [n for n in nodes if isinstance(n, ast.If)
                  and ast.unparse(n.test) == 'retract_ace != self.ace._active_device_index']
        moves = [n for n in nodes if isinstance(n, ast.Lambda) and isinstance(n.body, ast.Call)
                 and ast.unparse(n.body.func) == 'self.ace._retract' and
                 any(k.arg == 'ace' and ast.unparse(k.value) == 'retract_ace' for k in n.body.keywords)]
        self.assertEqual((len(blocks), len(moves)), (2, 4))
        a = self.machine()
        a._active_device_index = 1
        a._switch_ace_for_head_target.return_value = False
        env = dict(self=SimpleNamespace(ace=a, printer=a.printer), retract_ace=3,
                   _ace_slot=2, _short_retract=100, _rest=400, _retract_speed=40, head_idx=1)
        for block in blocks:
            with self.assertRaises(RuntimeError):
                exec(compile(ast.Module(body=[block], type_ignores=[]), '<feed>', 'exec'), env)
        for move in moves:
            eval(compile(ast.Expression(body=move), '<feed>', 'eval'), env)()
        for call in a._retract.call_args_list:
            self.assertEqual((call.args[0], call.kwargs['ace']), (2, 3))
