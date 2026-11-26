import ast
import json
import logging
import os
from pathlib import Path
import tarfile
import unittest

from hub_port_order import patch_engine


ROOT = Path(__file__).resolve().parents[2]
FIELD = ROOT / 'bundles/multiace-190/artifacts/multiace-1.90.2-233.20261126.tar.gz'
PORTS = tuple('/dev/serial/by-path/platform-fed00000.usb-usb-0:1.3:1.%d' % n
              for n in (0, 2, 4, 6))


def provider_text():
    with tarfile.open(FIELD, 'r:gz') as archive:
        matches = [m for m in archive.getmembers() if m.name.endswith('/klipper/extras/ace.py')]
        if len(matches) != 1:
            raise AssertionError('field ace.py is missing or ambiguous')
        return archive.extractfile(matches[0]).read().decode()


def methods():
    tree = ast.parse(patch_engine(provider_text()))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and any(isinstance(n, ast.FunctionDef) and n.name == '_order_units' for n in node.body))
    names = {'_order_hub_ports', '_unit_devices_status', '_unit_state_join', '_unit_state_split', '_unit_state_remap', '_reconcile_unit_state'}
    cls = ast.ClassDef(name='Harness', bases=[], keywords=[], decorator_list=[],
                       body=[n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names])
    tree = ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[]))
    ns = {'MAX_ACE_UNITS': 4, 'ABSENT_UNIT_PREFIX': 'absent:', 'FREE_UNIT_PATH': 'free:',
          'os': os, 'json': json, 'logging': logging}
    exec(compile(tree, '<provider methods>', 'exec'), ns)
    return ns['Harness']


def nested_method(outer_name, inner_name):
    tree = ast.parse(patch_engine(provider_text()))
    outer = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == outer_name)
    inner = next(n for n in ast.walk(outer) if isinstance(n, ast.FunctionDef) and n.name == inner_name)
    wrapper = ast.parse('def factory(idx, serial_path):\n    pass\n').body[0]
    wrapper.body = [inner, ast.Return(value=ast.Name(id=inner_name, ctx=ast.Load()))]
    module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
    ns = {'logging': logging}
    exec(compile(module, '<provider callback>', 'exec'), ns)
    return ns['factory']


def request_guard():
    tree = ast.parse(patch_engine(provider_text()))
    method = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == 'send_request_to')
    method.body = [method.body[0], ast.Return(value=ast.Constant(value='allowed'))]
    module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    ns = {'TELEMETRY_METHODS': {'get_status', 'discover_device', 'get_lane_state', 'set_lanes'}}
    exec(compile(module, '<provider gate>', 'exec'), ns)
    return ns['send_request_to']


class PortOrderTests(unittest.TestCase):
    def make(self, keys):
        obj = methods()()
        obj._hub_port_order = PORTS
        obj._ace_canonical = None
        obj._unit_places = [{'key': k, 'name': k, 'kind': 'ace2k'} for k in reversed(keys)]
        obj._unit_excluded = set()
        obj._unit_uid_for_path = lambda p, probe: keys[PORTS.index(p)]
        obj._unit_key_for_path = lambda p: 'port:' + os.path.basename(p)
        obj._unit_kind_of_path = lambda p: 'ace2k'
        obj._save_unit_places = lambda why: None
        return obj

    def test_order_follows_port_not_saved_uid(self):
        keys = ['uid:a', 'uid:b', 'uid:c', 'uid:d']
        obj = self.make(keys)
        self.assertEqual(obj._order_hub_ports([PORTS[2], PORTS[0], PORTS[3], PORTS[1]]), list(PORTS))
        self.assertEqual([e['key'] for e in obj._unit_places], keys)
        self.assertEqual(obj._last_scan_place_of_path, dict(zip(PORTS, range(4))))

    def test_missing_second_port_does_not_shift_later_ports(self):
        keys = ['uid:a', 'uid:b', 'uid:c', 'uid:d']
        obj = self.make(keys)
        obj._unit_places = [{'key': k, 'name': '', 'kind': 'ace2k'} for k in keys]
        result = obj._order_hub_ports([PORTS[0], PORTS[2], PORTS[3]])
        self.assertEqual(result, [PORTS[0], 'absent:port:' + os.path.basename(PORTS[1]), PORTS[2], PORTS[3]])
        self.assertEqual(obj._last_scan_place_of_path[PORTS[2]], 2)

    def test_missing_devices_do_not_inherit_previous_uid_positions(self):
        # The saved UID-based order was [A, B, C, D], while the current
        # sockets are [D, A, B, C]. Only the last two sockets respond.
        keys = ['uid:d', 'uid:a', 'uid:b', 'uid:c']
        obj = self.make(keys)
        obj._unit_places = [{'key': k, 'name': k, 'kind': 'ace2k'}
                            for k in ('uid:a', 'uid:b', 'uid:c', 'uid:d')]
        result = obj._order_hub_ports([PORTS[2], PORTS[3]])
        self.assertEqual(result[:2], ['absent:port:' + os.path.basename(PORTS[0]),
                                      'absent:port:' + os.path.basename(PORTS[1])])
        self.assertEqual(result[2:], list(PORTS[2:]))
        # Offline names are retained without using a UID as an absent socket.
        self.assertEqual({e['key'] for e in obj._unit_places if e}, set(keys))
        obj._ace_devices = result
        obj._unit_is_real = lambda idx: not result[idx].startswith('absent:')
        obj._unit_key = lambda idx: keys[idx]
        obj._connected_per_ace = {2: True, 3: True}
        status = obj._unit_devices_status()
        self.assertEqual(status[0]['key'], 'port:' + os.path.basename(PORTS[0]))
        self.assertFalse(status[0]['present'])
        self.assertEqual(status[1]['key'], 'port:' + os.path.basename(PORTS[1]))

    def test_device_swaps_follow_sockets_but_keep_uid_names(self):
        keys = ['uid:d', 'uid:a', 'uid:b', 'uid:c']
        obj = self.make(keys)
        self.assertEqual(obj._order_hub_ports(list(PORTS)), list(PORTS))
        self.assertEqual([e['key'] for e in obj._unit_places], keys)
        self.assertEqual([e['name'] for e in obj._unit_places], keys)

    def test_other_usb_devices_are_not_assigned(self):
        obj = self.make(['uid:a', 'uid:b', 'uid:c', 'uid:d'])
        extra = '/dev/serial/by-path/other'
        self.assertEqual(obj._order_hub_ports(list(PORTS) + [extra]), list(PORTS))
        self.assertEqual(obj._unit_unassigned['port:other']['reason'], 'unconfigured port')

    def test_override_payload_tracks_remapped_device(self):
        original = {'ace': 2, 'slot': 0, 'material': 'PLA', 'color': '#D32F2F'}
        key, value = methods()._unit_state_join('overrides', 3, 0, original)
        self.assertEqual(key, '3_0')
        self.assertEqual(value, {'ace': 3, 'slot': 0, 'material': 'PLA', 'color': '#D32F2F'})
        self.assertEqual(original['ace'], 2)

    def test_cyclic_reordering_preserves_all_saved_slot_records(self):
        cls = methods()
        cls._unit_state_remap.__globals__['logging'] = logging
        obj = cls()
        records = {'%d_0' % i: {'ace': i, 'slot': 0, 'material': 'M%d' % i}
                   for i in range(4)}
        writes = []
        obj._unit_state_stores = lambda: [('overrides', records, lambda: writes.append(True))]
        obj._unit_tag_reads_remap = lambda remap, parked: None
        remap = {0: ('uid:a', 1), 1: ('uid:b', 2),
                 2: ('uid:c', 3), 3: ('uid:d', 0)}
        obj._unit_state_remap(remap, {})
        self.assertEqual({k: (v['ace'], v['material']) for k, v in records.items()},
                         {'1_0': (1, 'M0'), '2_0': (2, 'M1'),
                          '3_0': (3, 'M2'), '0_0': (0, 'M3')})
        self.assertEqual(writes, [True])

    def test_partial_port_migration_parks_absent_devices_data(self):
        from types import SimpleNamespace
        obj = methods()()
        obj.VARS_UNIT_KEYS, obj.VARS_UNIT_PARKED = 'keys', 'parked'
        obj._ace_devices = ['absent:port:' + os.path.basename(PORTS[0]),
                            'absent:port:' + os.path.basename(PORTS[1]),
                            PORTS[2], PORTS[3]]
        newkeys = ['port:' + os.path.basename(PORTS[0]), 'port:' + os.path.basename(PORTS[1]),
                   'uid:b', 'uid:c']
        obj._unit_key = lambda idx: newkeys[idx]
        obj.save_variables = SimpleNamespace(allVariables={'keys': {str(i): 'uid:' + v
                                                                     for i, v in enumerate('abcd')}})
        obj.save_variable = lambda k, v, write=False: obj.save_variables.allVariables.update({k: v})
        obj.write_variables = lambda: None
        obj._unit_state_unpark = lambda *args: None
        obj._unit_tag_reads_remap = lambda *args: None
        records = {'%d_0' % i: {'ace': i, 'slot': 0, 'material': v}
                   for i, v in enumerate('ABCD')}
        obj._unit_state_stores = lambda: [('overrides', records, lambda: None)]
        obj._reconcile_unit_state('test partial hub')
        self.assertEqual(records, {'2_0': {'ace': 2, 'slot': 0, 'material': 'B'},
                                   '3_0': {'ace': 3, 'slot': 0, 'material': 'C'}})
        parked = obj.save_variables.allVariables['parked']
        self.assertEqual(parked['uid:a']['overrides']['0']['material'], 'A')
        self.assertEqual(parked['uid:d']['overrides']['0']['material'], 'D')

    def test_interrupted_retract_follows_its_device(self):
        cls = methods()
        cls._unit_state_remap.__globals__['logging'] = logging
        obj = cls()
        pending = {'0_1': {'ace': 0, 'slot': 1, 'len': 900, 'head': 1}}
        writes = []
        obj._unit_state_stores = lambda: [('retract_pending', pending, lambda: writes.append(True))]
        obj._unit_tag_reads_remap = lambda remap, parked: None
        obj._unit_state_remap({0: ('uid:a', 1)}, {})
        self.assertEqual(pending, {'1_1': {'ace': 1, 'slot': 1, 'len': 900, 'head': 1}})
        self.assertEqual(writes, [True])

    def test_reconnect_same_device_clears_identity_gate(self):
        class Device: pass
        d = Device()
        d._usb_log = type('Log', (), {'info': lambda *args: None})()
        d._hub_port_order = PORTS
        d._unit_uid_by_path = {PORTS[0]: 'uid:00000001-00000002-00000003'}
        d._hub_identity_pending = {0}
        d._lane_report_sent = {0: ((1, 0), 10)}
        callback = nested_method('_open_ace', 'uid_callback')(0, PORTS[0])
        callback(d, {'result': {'uid1': 1, 'uid2': 2, 'uid3': 3}})
        self.assertEqual(d._hub_identity_pending, set())
        self.assertEqual(d._lane_report_sent, {})
        self.assertEqual(d._unit_uid_by_path[PORTS[0]], 'uid:00000001-00000002-00000003')

    def test_reconnect_different_device_is_held_without_reassigning_state(self):
        class Device: pass
        d = Device()
        d._usb_log = type('Log', (), {'info': lambda *args: None})()
        d._hub_port_order = PORTS
        d._unit_uid_by_path = {PORTS[0]: 'uid:00000001-00000002-00000003'}
        d._hub_identity_pending, d._hub_identity_changed = {0}, set()
        d._serial_failed_per_ace, d._connect_error_per_ace = {}, {}
        calls = []
        d._disconnect_from = lambda idx: calls.append(('disconnect', idx))
        d.log_error = lambda message: calls.append(('error', message))
        callback = nested_method('_open_ace', 'uid_callback')(0, PORTS[0])
        callback(d, {'result': {'uid1': 4, 'uid2': 5, 'uid3': 6}})
        self.assertEqual(d._hub_identity_changed, {0})
        self.assertTrue(d._serial_failed_per_ace[0])
        self.assertEqual(d._unit_uid_by_path[PORTS[0]], 'uid:00000001-00000002-00000003')
        self.assertEqual(calls[0], ('disconnect', 0))
        self.assertIn('restart Klipper', d._connect_error_per_ace[0])

    def test_unverified_reconnection_blocks_motion_but_allows_identity_queries(self):
        class Device: pass
        d = Device()
        d._hub_port_order, d._hub_identity_pending = PORTS, {0}
        d._hub_identity_changed = set()
        replies = []
        cb = lambda **kw: replies.append(kw['response'])
        gate = request_guard()
        self.assertIsNone(gate(d, 0, {'method': 'feed_filament'}, cb))
        self.assertEqual(replies[0]['code'], 1)
        self.assertEqual(gate(d, 0, {'method': 'discover_device'}, cb), 'allowed')
        self.assertEqual(gate(d, 0, {'method': 'get_info'}, cb), 'allowed')
        self.assertIsNone(gate(d, 0, {'method': 'set_lanes'}, cb))
        self.assertEqual(gate(d, 1, {'method': 'feed_filament'}, cb), 'allowed')
        d._hub_identity_pending.clear()
        d._hub_identity_changed.add(0)
        self.assertIsNone(gate(d, 0, {'method': 'feed_filament'}, cb))

    def test_ace2k_handshake_explicitly_queries_identity(self):
        source = patch_engine(provider_text())
        self.assertIn("if self._hub_port_order and self._is_native_ace2k(idx):\n                handshake_requests.append({'method': 'discover_device'})", source)

    def test_reconnect_not_ready_until_identity_confirmed(self):
        class Device: pass
        d = Device()
        d._open_rx_mark, d._last_rx_ts = {0: 1}, {0: 2}
        d._hub_identity_pending, d._hub_identity_changed = {0}, set()
        tree = ast.parse(patch_engine(provider_text()))
        outer = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                     and n.name == '_open_ace_verified')
        inner = next(n for n in ast.walk(outer) if isinstance(n, ast.FunctionDef)
                     and n.name == '_answered')
        wrapper = ast.parse('def factory(self, idx):\n    pass\n').body[0]
        wrapper.body = [inner, ast.Return(value=ast.Name(id='_answered', ctx=ast.Load()))]
        module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
        ns = {}
        exec(compile(module, '<provider ready gate>', 'exec'), ns)
        answered = ns['factory'](d, 0)
        self.assertFalse(answered())
        d._hub_identity_pending.clear()
        self.assertTrue(answered())
        d._hub_identity_changed.add(0)
        self.assertFalse(answered())


if __name__ == '__main__':
    unittest.main()
