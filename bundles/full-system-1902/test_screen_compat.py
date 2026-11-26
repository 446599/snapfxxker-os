"""Execute patched methods from the pinned field archive, not mock patches."""
import ast
import asyncio
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from screen_compat import patch_engine, patch_web
from test_compose import field_text


def extract(source, names, env):
    nodes = {}
    for n in ast.walk(ast.parse(source)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names:
            if n.name in nodes:
                raise AssertionError("ambiguous method " + n.name)
            n.decorator_list = []
            nodes[n.name] = n
    if set(nodes) != set(names):
        raise AssertionError(set(names) - set(nodes))
    exec(compile(ast.Module(body=list(nodes.values()), type_ignores=[]),
                 '<field-archive>', 'exec'), env)


class FakeAce:
    def __init__(self):
        self._head_source = {0: {'ace_index': 0, 'slot': 0}, 1: None, 2: None, 3: None}
        self._loading_heads = set()
        self._active_device_index = 1
        self._ace_devices = ['ace0', 'ace1']
        self._connected_per_ace = {0: True, 1: True}
        self._feed_assist_per_ace = {0: -1, 1: -1}
        self._auto_feed_enabled = True
        self._fa_context = 'idle'
        self._last_switch_auto_ts = None
        self.timers, self.armed, self.disarmed = [], [], []
        self.toolhead = SimpleNamespace(get_extruder=lambda: SimpleNamespace(extruder_index=0))
        self.printer = SimpleNamespace(lookup_object=lambda *args: None)
        self.reactor = SimpleNamespace(
            register_timer=lambda fn, when: self.timers.append(fn),
            monotonic=lambda: 0.0, NEVER=float('inf'))
    def _job_note_active(self): pass
    def _staged_head_watch(self): pass
    def _fa_trace(self, *args): pass
    def _audit_state(self, *args): pass
    def _telemetry(self, *args): pass
    def _apply_spool_pa(self, *args, **kwargs): pass
    def head_uses_ace(self, head): return True
    def _set_active_idx(self, idx): self._active_device_index = idx
    def _arm_fa_for(self, idx, slot): self.armed.append((idx, slot))
    def _disarm_fa_for(self, idx): self.disarmed.append(idx)


class ScreenRoutingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = field_text('klipper/extras/ace.py')
        cls.raw = raw
        cls.patched = patch_engine(raw)
        for source, attr in ((raw, 'before'), (cls.patched, 'after')):
            env = {'logging': logging, 'time': time}
            extract(source, ['_on_extruder_change'], env)
            setattr(cls, attr, staticmethod(env['_on_extruder_change']))

    def test_tool_activation_during_load_owns_explicit_route(self):
        a = FakeAce()
        a._loading_heads.add(0)
        self.before(a)
        self.assertEqual(a._active_device_index, 0)
        self.assertEqual(len(a.timers), 1)
        b = FakeAce()
        b._loading_heads.add(0)
        self.after(b)
        self.assertEqual(b._active_device_index, 1)
        self.assertEqual(b.timers, [])

    def test_delayed_assist_cannot_follow_old_route(self):
        a = FakeAce()
        self.before(a)
        a._head_source[0] = {'ace_index': 1, 'slot': 0}
        a.timers[0](0.1)
        self.assertEqual(a.armed, [(0, 0)])
        b = FakeAce()
        self.after(b)
        b._head_source[0] = {'ace_index': 1, 'slot': 0}
        b.timers[0](0.1)
        self.assertEqual(b.armed, [])

    def test_delayed_assist_still_arms_matching_route(self):
        a = FakeAce()
        self.after(a)
        a.timers[0](0.1)
        self.assertEqual(a.armed, [(0, 0)])

    def test_pending_assist_aborts_if_load_started(self):
        a = FakeAce()
        self.after(a)
        a._loading_heads.add(0)
        a.timers[0](0.1)
        self.assertEqual(a.armed, [])

    def test_route_rechecked_before_provisional_source(self):
        tree = ast.parse(self.patched)
        method = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == '_cmd_ACE_LOAD_HEAD_body')
        node = next(n for n in method.body if isinstance(n, ast.If)
                    and ast.unparse(n.test) == 'self.head_uses_ace(head) and self._active_device_index != ace_index')
        # Extract and run the actual new branch without running a printer.
        code = compile(ast.Module(body=[node], type_ignores=[]), '<route-recheck>', 'exec')
        a = FakeAce()
        a._active_device_index = 0
        def switch(idx):
            a._set_active_idx(idx)
            return True
        a._switch_ace_for_head_target = switch
        exec(code, {'self': a, 'head': 0, 'ace_index': 1, 'gcmd': None})
        self.assertEqual(a._active_device_index, 1)
        a._active_device_index = 0
        a._switch_ace_for_head_target = lambda idx: False
        a._last_load_ok = True
        a._disp = lambda idx: idx+1
        a._ace_error = lambda *args, **kwargs: RuntimeError('no route')
        with self.assertRaisesRegex(RuntimeError, 'no route'):
            exec(code, {'self': a, 'head': 0, 'ace_index': 1, 'gcmd': None})
        self.assertFalse(a._last_load_ok)


class HttpError(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code


class SlotOverride:
    def __init__(self, ace, slot, **fields):
        self.ace, self.slot = ace, slot
        self.fields = fields
        for name in ('material', 'brand', 'subtype', 'color'):
            setattr(self, name, fields.get(name, ''))
    def model_dump(self, exclude_unset=False):
        return dict(ace=self.ace, slot=self.slot, **self.fields)


class ScreenOverrideTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = patch_web(field_text('web/backend/main.py'))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'slot_overrides.json'
        self.prior = {'2_3': {'ace': 2, 'slot': 3, 'material': 'PLA',
                             'brand': 'Bambu', 'subtype': 'Basic', 'color': '#112233'},
                      '1_0': {'color': '#FFFFFF'}}
        self.path.write_text(json.dumps(self.prior))
        self.env = dict(Path=Path, json=json, os=os, re=re,
                        SlotOverride=SlotOverride, HTTPException=HttpError,
                        OVERRIDE_FILE=str(self.path),
                        _overrides_mtime=0.0,
                        _slot_overrides=json.loads(self.path.read_text()),
                        _kept_slot_emptied=set(),
                        _reload_overrides_if_changed=lambda: None,
                        _klipper_readable=lambda p: None,
                        _trace=logging.getLogger('screen-override-test'))
        extract(self.source, ['_override_key','_save_overrides_to_disk',
                              'set_slot_override','sync_slot_override'], self.env)

    def sync(self, req):
        return asyncio.run(self.env['sync_slot_override'](req))

    def test_screen_post_persists_exact_slot_and_preserves_other_metadata(self):
        self.assertIn('@app.post("/api/slot-override/sync")', self.source)
        result = self.sync(SlotOverride(2, 3, material='PLA', color='#AABBCC'))
        self.assertTrue(result['ok'])
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved['2_3']['color'], '#AABBCC')
        self.assertEqual(saved['2_3']['brand'], 'Bambu')
        self.assertEqual(saved['2_3']['subtype'], 'Basic')
        self.assertEqual(saved['1_0'], self.prior['1_0'])
        self.assertEqual(len(saved), 2)

    def test_failed_save_does_not_report_success_or_mutate_ram(self):
        with patch.object(os, 'replace', side_effect=OSError(28, 'No space left on device')):
            with self.assertRaises(HttpError) as err:
                self.sync(SlotOverride(2, 3, color='#AABBCC'))
        self.assertEqual(err.exception.status_code, 500)
        self.assertEqual(json.loads(self.path.read_text()), self.prior)
        self.assertEqual(self.env['_slot_overrides'], self.prior)

    def test_invalid_screen_coordinates_or_color_do_not_modify_slots(self):
        for req in (SlotOverride(4, 0, color='#AABBCC'),
                    SlotOverride(0, -1, color='#AABBCC'),
                    SlotOverride(2, 3, color='not-a-color')):
            with self.subTest(req=req), self.assertRaises(HttpError) as err:
                self.sync(req)
            self.assertEqual(err.exception.status_code, 422)
        self.assertEqual(json.loads(self.path.read_text()), self.prior)

if __name__ == '__main__':
    unittest.main()
