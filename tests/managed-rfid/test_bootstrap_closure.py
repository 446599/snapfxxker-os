import ast
import importlib
import os
import sys
import tarfile
import tempfile
import types
import unittest
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / 'overlays/233-dev.2-perms.1/root/usr/local/share/233-firmware/multiace-managed-1.27b-233.20261111-single.3.tar.gz'
PREFIX = 'multiace-1.27b-233.20261111-single.3/klipper/extras/'

class BootstrapClosure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tarfile.open(ARCHIVE) as t:
            cls.files = {m.name[len(PREFIX):-3]: t.extractfile(m).read() for m in t
                         if m.isfile() and m.name.startswith(PREFIX) and m.name.endswith('.py')}

    def test_recursive_relative_imports_are_packaged_or_klippy_native(self):
        todo = deque(['ace', 'ace_rc522', 'filament_feed_ace', 'ace_slot_actions'])
        seen = set()
        while todo:
            name = todo.popleft()
            if name in seen or name == 'pulse_counter':
                continue  # Klipper native extras object
            seen.add(name)
            self.assertIn(name, self.files, 'missing runtime module: ' + name)
            for node in ast.walk(ast.parse(self.files[name])):
                if isinstance(node, ast.ImportFrom) and node.level == 1:
                    todo.extend([node.module.split('.')[0]] if node.module else
                                [alias.name.split('.')[0] for alias in node.names])
        self.assertIn('measurement_candidate', seen)

    def test_import_loads_new_eject_helper_when_old_root_precedes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); old = root / 'old'; new = root / 'latest'
            old.mkdir(); new.mkdir()
            (old / 'ace_slot_actions.py').write_text('SPEED_MM_S = 5\n')
            for name, data in self.files.items():
                (new / (name + '.py')).write_bytes(data)
            pkg = types.ModuleType('loadcheck'); pkg.__path__ = [str(old)]
            sys.modules['loadcheck'] = pkg
            try:
                tree = ast.parse(self.files['filament_feed_ace'])
                nodes = []
                for node in tree.body:
                    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '_multiace_extras' for t in node.targets):
                        node.value = ast.Constant(str(new)); nodes.append(node)
                    elif isinstance(node, ast.If) and 'sys.modules' in ast.unparse(node.test) and '_multiace_extras' in ast.unparse(node.test):
                        nodes.append(node)
                    elif isinstance(node, ast.Expr) and '_multiace_extras' in ast.unparse(node):
                        nodes.append(node)
                exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), 'bootstrap', 'exec'), {'sys':sys, '__package__':'loadcheck'})
                module = importlib.import_module('loadcheck.ace_slot_actions')
                self.assertEqual(Path(module.__file__), new / 'ace_slot_actions.py')
                self.assertEqual(module.SPEED_MM_S, 80)
                measured = importlib.import_module('loadcheck.measurement_candidate')
                self.assertEqual(Path(measured.__file__), new / 'measurement_candidate.py')
            finally:
                for name in list(sys.modules):
                    if name == 'loadcheck' or name.startswith('loadcheck.'):
                        del sys.modules[name]

    def test_both_bootstraps_promote_latest_above_old_tree(self):
        for name in ('ace', 'filament_feed_ace'):
            tree = ast.parse(self.files[name])
            nodes = []
            for node in tree.body:
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '_multiace_extras' for t in node.targets):
                    nodes.append(node)
                elif isinstance(node, ast.If) and 'sys.modules' in ast.unparse(node.test) and '_multiace_extras' in ast.unparse(node.test):
                    nodes.append(node)
                elif isinstance(node, ast.Expr) and '_multiace_extras' in ast.unparse(node):
                    nodes.append(node)
            pkg = types.ModuleType('bootstrap_test'); pkg.__path__ = ['/old/tree', '/oem/apps/multiace/latest/klipper/extras']
            sys.modules[pkg.__name__] = pkg
            try:
                exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), name, 'exec'), {'sys':sys, 'os':os, '__package__':pkg.__name__})
                self.assertEqual(pkg.__path__[0], '/oem/apps/multiace/latest/klipper/extras')
                self.assertEqual(pkg.__path__.count(pkg.__path__[0]), 1)
            finally:
                del sys.modules[pkg.__name__]
            self.assertNotIn(b'/oem/apps/multiace/multiace-1.27b/klipper/extras', self.files[name])
