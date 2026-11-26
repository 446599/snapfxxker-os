"""Exercise the actual 1902 web code after applying candidate patches."""
import asyncio
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock

from screen_compat import patch_web
from test_compose import field_text
from test_screen_compat import extract


HERE = Path(__file__).resolve().parent


class WebBayRoutingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.root = Path(tmp.name)
        for rel in ('web/frontend/app.js', 'web/frontend/index.html', 'web/backend/main.py'):
            path = cls.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            source = field_text(rel)
            if rel == 'web/backend/main.py':
                source = patch_web(source)
            path.write_text(source)
        subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(HERE/'web_routes.patch')],
                       check=True, cwd=cls.root, capture_output=True)

    def test_web_card_buttons_and_queue(self):
        subprocess.run(['node', str(HERE/'test_web_routes.cjs'), str(self.root/'web/frontend')],
                       check=True)

    def test_snapshot_preserves_selected_slot_and_previous_source(self):
        snapshot = self.root / 'route.json'
        snapshot.write_text(json.dumps({'toolheads': [
            {'idx': 0, 'ace': 3, 'slot': 2}, {'idx': 1, 'ace': 1, 'slot': 0},
        ]}))
        state = {'toolheads': [
            {'idx': 0, 'ace': 3, 'slot': 1, 'head_source_known': True},
            {'idx': 1, 'ace': 1, 'slot': 0, 'head_source_known': True},
        ], 'aces': [{'slots': [{'state': 'ready', 'raw': 1}] * 4} for _ in range(4)]}
        env = {'_snap_path': lambda *args: snapshot, 'json': json,
               '_query_state_gated': AsyncMock(return_value=state), '_parse_state': lambda s: s}
        source = (self.root/'web/backend/main.py').read_text()
        extract(source, ['apply_snapshot'], env)
        plan = asyncio.run(env['apply_snapshot']('route', 'head'))
        self.assertEqual(plan['errors'], [])
        self.assertEqual(plan['actions'], [
            {'name': 'ACE_UNLOAD_HEAD', 'args': {'HEAD': 0, 'ACE': 3, 'SLOT': 1}},
            {'name': 'ACE_LOAD_HEAD', 'args': {'HEAD': 0, 'ACE': 3, 'SLOT': 2}},
        ])
