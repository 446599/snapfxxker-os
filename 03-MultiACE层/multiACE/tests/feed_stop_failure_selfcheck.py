"""Exercise the real load sensor branch without starting Klipper or motion."""
import ast
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest


class LoadStopFailureTest(unittest.TestCase):
    def test_stop_acceptance_controls_load_success(self):
        source = Path(__file__).resolve().parents[1] / 'multiace/klipper/extras/filament_feed_ace.py'
        tree = ast.parse(source.read_text())
        branch = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.If)
                      and ast.unparse(node.test) == 'runout_detect == True'
                      and '_stop_feeding' in ast.unparse(node))
        self.assertIsInstance(branch.body[-1], ast.Break)
        fn = ast.FunctionDef(
            name='run',
            args=ast.arguments(posonlyargs=[], args=[ast.arg(arg='self')],
                               kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=[ast.Assign(targets=[ast.Name(id='ch', ctx=ast.Store())], value=ast.Constant(0)),
                  ast.Assign(targets=[ast.Name(id='_ace_slot', ctx=ast.Store())], value=ast.Constant(1)),
                  ast.Assign(targets=[ast.Name(id='load_found', ctx=ast.Store())], value=ast.Constant(False)),
                  ast.Assign(targets=[ast.Name(id='_feed_ace_idx', ctx=ast.Store())], value=ast.Constant(2))]
                 + branch.body[:-1]
                 + [ast.Return(value=ast.Name(id='load_found', ctx=ast.Load()))],
            decorator_list=[])
        module = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
        scope = {'FEED_OK': 'ok', 'FEED_ERR_MOVE': 'move', 'logging': logging}
        exec(compile(module, str(source), 'exec'), scope)
        for accepted in (True, False):
            calls = []
            def stop(slot, idx=None):
                calls.append((slot, idx))
                return accepted
            obj = SimpleNamespace(ace=SimpleNamespace(_stop_feeding=stop),
                                  channel_error=['initial'])
            if accepted:
                self.assertTrue(scope['run'](obj))
                self.assertEqual(obj.channel_error[0], 'ok')
            else:
                with self.assertRaises(ValueError):
                    scope['run'](obj)
                self.assertEqual(obj.channel_error[0], 'move')
            self.assertEqual(calls, [(1, 2)])

    def test_deadline_stops_frozen_device_and_aborts(self):
        source = Path(__file__).resolve().parents[1] / 'multiace/klipper/extras/filament_feed_ace.py'
        tree = ast.parse(source.read_text())
        branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                      and ast.unparse(node.test) == 'self.reactor.monotonic() > _feed_deadline')
        module = ast.fix_missing_locations(ast.Module(body=branch.body, type_ignores=[]))
        calls = []
        obj = SimpleNamespace(ace=SimpleNamespace(_stop_feeding=lambda slot, idx=None: calls.append((slot, idx))),
                              channel_error=['initial'], exception_code=[0])
        scope = {'self': obj, 'ch': 0, '_ace_slot': 1, '_feed_ace_idx': 2,
                 'FEED_ERR_TIMEOUT': 'timeout', 'logging': logging}
        with self.assertRaises(ValueError):
            exec(compile(module, str(source), 'exec'), scope)
        self.assertEqual(calls, [(1, 2)])
        self.assertEqual(obj.channel_error, ['timeout'])
        self.assertEqual(obj.exception_code, [34])

    def test_mapping_change_stops_original_device_and_aborts(self):
        source = Path(__file__).resolve().parents[1] / 'multiace/klipper/extras/filament_feed_ace.py'
        tree = ast.parse(source.read_text())
        branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                      and "_active_device_index != _feed_ace_idx" in ast.unparse(node.test)
                      and any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                              and n.func.attr == '_stop_feeding' for n in ast.walk(node)))
        module = ast.fix_missing_locations(ast.Module(body=branch.body, type_ignores=[]))
        calls = []
        obj = SimpleNamespace(ace=SimpleNamespace(_stop_feeding=lambda slot, idx=None: calls.append((slot, idx))),
                              channel_error=['initial'])
        scope = {'self': obj, 'ch': 0, '_ace_slot': 1, '_feed_ace_idx': 2,
                 'FEED_ERR_STATE_MISMATCH': 'state_mismatch'}
        with self.assertRaises(ValueError): exec(compile(module, str(source), 'exec'), scope)
        self.assertEqual(calls, [(1, 2)])
        self.assertEqual(obj.channel_error, ['state_mismatch'])


if __name__ == '__main__':
    unittest.main()
