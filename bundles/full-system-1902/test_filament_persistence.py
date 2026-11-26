"""Regression against the exact field provider source; no hardware required."""
import asyncio
import copy
import json
import logging
import os
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from filament_persistence import patch_persistence_engine, patch_persistence_web
from screen_compat import patch_engine, patch_web
from test_compose import field_text
from test_screen_compat import extract, SlotOverride, HttpError


class ManualReadbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = patch_persistence_engine(patch_engine(field_text('klipper/extras/ace.py')))
        env = {'logging': logging}
        extract(source, ['_publish_manual_slot_identity', '_refresh_slot_overrides_if_changed'], env)
        cls.publish = staticmethod(env['_publish_manual_slot_identity'])
        cls.refresh = staticmethod(env['_refresh_slot_overrides_if_changed'])
        # Test that the actual status producer invokes the projection for every bay.
        assert 'self._publish_manual_slot_identity(i, slots_out[-1])' in source

    def test_manual_label_matches_screen_without_faking_rfid(self):
        override = {'material': 'PETG', 'color': '#AABBCC', 'brand': '', 'subtype': ''}
        calls = []
        def get(idx, lane):
            calls.append((idx, lane))
            return override if (idx, lane) == (2, 3) else None
        unit = SimpleNamespace(_override_for=get)
        raw = dict(index=3, status='ready', material='PLA', brand='AC',
                   subtype='Basic', color=[1, 2, 3], rfid=2, uid='1122', sku='OLD')
        slot = copy.deepcopy(raw)
        self.publish(unit, 2, slot)
        self.assertEqual(calls, [(2, 3)])
        self.assertEqual(slot['material'], 'PETG')
        self.assertEqual(slot['color'], [170, 187, 204])
        self.assertEqual(slot['raw_tag_identity'],
                         {k: raw[k] for k in ('material', 'brand', 'subtype', 'color')})
        for k in ('status', 'rfid', 'uid', 'sku'):
            self.assertEqual(slot[k], raw[k])
        untouched = dict(raw)
        self.publish(unit, 0, untouched)
        self.assertEqual(untouched, raw)

    def test_manual_edit_does_not_claim_tag_on_untagged_bay(self):
        unit = SimpleNamespace(_override_for=lambda i, s: {'material': 'PLA', 'color': '#000000'})
        slot = dict(index=0, status='ready', rfid=0, uid='', material='', brand='',
                    subtype='', color=[0, 0, 0])
        self.publish(unit, 0, slot)
        self.assertEqual((slot['material'], slot['rfid'], slot['uid']), ('PLA', 0, ''))
        self.assertEqual(slot['color'], [0, 0, 0])

    def test_existing_key_edit_triggers_display_push(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'slot_overrides.json'
            path.write_text('{}')
            calls = []
            obj = SimpleNamespace(_slot_overrides_file=str(path), _slot_overrides_mtime=-1,
                                  _slot_overrides={'2_3': {'color': '#112233'}},
                                  _push_rfid_info=lambda: calls.append('push'),
                                  _fold_overrides_into_captures=lambda prev: None)
            def reload():
                obj._slot_overrides = {'2_3': {'color': '#AABBCC'}}
                obj._slot_overrides_mtime = path.stat().st_mtime
            obj._refresh_slot_overrides = reload
            self.refresh(obj)
            self.refresh(obj)
            self.assertEqual(calls, ['push'])


class EjectAndSaveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = patch_persistence_web(patch_web(field_text('web/backend/main.py')))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'slot_overrides.json'
        self.prior = {'2_3': {'material': 'PLA', 'color': '#112233', 'brand': 'AC'}}
        self.path.write_text(json.dumps(self.prior))
        self.t = 0.0
        self.env = dict(Path=Path, json=json, os=os, re=re,
                        SlotOverride=SlotOverride, HTTPException=HttpError,
                        OVERRIDE_FILE=str(self.path), _overrides_mtime=0.0,
                        _slot_overrides=copy.deepcopy(self.prior), _kept_slot_emptied=set(),
                        _eject_pending_since={}, _last_eject_gate={}, EJECT_DEBOUNCE_S=0.5,
                        time=SimpleNamespace(monotonic=lambda: self.t),
                        _reload_overrides_if_changed=lambda: None,
                        _klipper_readable=lambda p: None,
                        _trace=logging.getLogger('filament-persistence-test'))
        extract(self.source, ['_confirmed_slot_eject', '_override_key', '_save_overrides_to_disk',
                              'set_slot_override', 'sync_slot_override'], self.env)

    def gate(self, gate, connected=True, unit='uid:A', advance=1):
        self.t += advance
        return self.env['_confirmed_slot_eject'](2, 3, gate, connected, unit)

    def test_empty_on_boot_or_reconnect_is_not_an_eject(self):
        self.assertFalse(self.gate(0))
        self.assertFalse(self.gate(0))
        self.assertFalse(self.gate(1))
        self.assertFalse(self.gate(0, connected=False))
        self.assertFalse(self.gate(0))
        self.assertFalse(self.gate(0))

    def test_real_eject_debounced_once_and_glitch_ignored(self):
        self.assertFalse(self.gate(1))
        self.assertFalse(self.gate(0))
        self.assertFalse(self.gate(1, advance=0.1))
        self.assertFalse(self.gate(0))
        self.assertFalse(self.gate(0, advance=0.1))
        self.assertTrue(self.gate(0, advance=0.5))
        self.assertFalse(self.gate(0))

    def test_unit_index_reuse_does_not_delete_label(self):
        self.assertFalse(self.gate(1, unit='uid:A'))
        self.assertFalse(self.gate(0, unit='uid:B'))
        self.assertFalse(self.gate(0, unit='uid:B'))

    def test_fresh_manual_save_cancels_stale_eject(self):
        self.gate(1)
        self.gate(0)
        self.assertIn((2, 3), self.env['_eject_pending_since'])
        req = SlotOverride(2, 3, material='PETG', color='#AABBCC')
        result = asyncio.run(self.env['sync_slot_override'](req))
        self.assertTrue(result['ok'])
        self.assertEqual(json.loads(self.path.read_text())['2_3']['color'], '#AABBCC')
        self.assertFalse(self.gate(0))

    def test_disk_failure_does_not_cancel_eject_or_report_success(self):
        self.gate(1)
        self.gate(0)
        with patch.object(os, 'replace', side_effect=OSError(28, 'No space left on device')):
            with self.assertRaises(HttpError):
                asyncio.run(self.env['sync_slot_override'](SlotOverride(2, 3, color='#AABBCC')))
        self.assertEqual(json.loads(self.path.read_text()), self.prior)
        self.assertEqual(self.env['_slot_overrides'], self.prior)
        self.assertIn((2, 3), self.env['_eject_pending_since'])


if __name__ == '__main__':
    unittest.main()
