#!/usr/bin/env python3
"""Offline checks against the authenticated 2026-11-06 RC522 capture.

No device access. Run: python3 tests/bambu_classic_selfcheck.py
"""
import importlib.util
import json
from pathlib import Path

path = Path(__file__).resolve().parents[1] / 'multiace/klipper/extras/ace_rc522.py'
spec = importlib.util.spec_from_file_location('ace_rc522_test', path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
fixture = json.loads((Path(__file__).parent / 'fixture_60o_select_uid.json').read_text())
assert fixture['op'] == 9
assert [x['offset'] for x in fixture['reads']] == list(range(13,18))*2
assert fixture['reads'][:5] == fixture['reads'][5:]
CAPTURED_SELECT = bytes(x['byte'] for x in fixture['reads'][:5])
UID = CAPTURED_SELECT[:4].hex().upper()
assert UID == '35E665E5' and CAPTURED_SELECT[4] == 0x53
BLOCK4 = bytes.fromhex('504c4120426173696300000000000000')
BLOCK5 = bytes.fromhex('ffffffffe80300000000e03f00000000')

class Reader(m.AceTagReader):
    def __init__(self, fault=''):
        super().__init__(None)
        self.fault = fault
        self.writes = []
        self.block = None
        self.count = 0
    def _wait_fw_identify(self, idx): pass
    def _fw_identify_busy(self, idx): return False
    def _pause(self, delay): pass
    def _rc(self, idx, slot, op, a1=0, a2=0):
        if op == 6: return 0
        if op == 9:
            data = CAPTURED_SELECT
            if self.fault == 'uid': data = b'\x34' + data[1:]
            if self.fault == 'bcc': data = data[:4] + b'\x00'
            # Actual 60O op-9 SELECT buffer: UID+BCC at offsets 13..17.
            # Refuse other indices so a self-consistent wrong mock fails.
            if a1 not in range(13, 18):
                raise AssertionError(('unexpected SELECT offset', a1))
            return data[a1-13]
        if op == 1:
            self.writes.append((a1, a2))
            if self.fault == 'write' and (a1, a2) == (2, 0xf7): return None
            return 0
        if op == 0 and a1 == 8: return 0 if self.fault == 'auth' else 8
        if op == 0 and a1 == 4: return 0x10 if self.fault == 'auth' else 0
        if op == 2:
            if a1 == 1:
                self.block = a2
                self.count += 1
            return 0
        if op == 3: return 1 if self.fault == 'transceive' else 0
        if op == 5: return 127 if self.fault == 'short' else 128
        if op == 4:
            if self.fault == 'lost' and a1 == 3: return None
            data = BLOCK4 if self.block == 4 else BLOCK5
            if self.fault == 'mismatch' and self.count == 4 and a1 == 0: return 0
            return data[a1]
        raise AssertionError((op, a1, a2))

assert Reader._bambu_key(UID).hex() == '15f683b32a4a'
assert Reader()._selected_single_uid(3, 3) == UID
assert Reader('uid')._selected_single_uid(3, 3) == ''
assert Reader('bcc')._selected_single_uid(3, 3) == ''
r = Reader()
out = r._read_bambu_classic(3, 3, UID)
assert out == {'type': 'PLA Basic', 'brand': 'Bambu Lab', 'sku': UID,
               'color': [255, 255, 255], 'code': 0}, out
assert r.writes[-2:] == [(1, 0), (8, 0)]
assert bytes(v for reg,v in r.writes if reg == 9) == bytes.fromhex(
    '6004') + Reader._bambu_key(UID) + bytes.fromhex(UID)
for fault in ('uid', 'bcc', 'write', 'auth', 'short', 'lost', 'mismatch', 'transceive'):
    r = Reader(fault)
    assert r._read_bambu_classic(3, 3, UID) is None, fault
    assert r.writes[-2:] == [(1, 0), (8, 0)], fault
for uid in ('', '88112233', '01020304050607'):
    assert Reader()._read_bambu_classic(3, 3, uid) is None
print('PASS: authenticated capture, RGB offsets, UID/BCC, partial reads, auth failures, cleanup')

# Import the actual MultiAce store path with a serial import stub. No serial
# operation is executed, and this test does not install any package.
import sys
import types
import importlib
serial = types.ModuleType('serial')
serial.SerialException = Exception
sys.modules.setdefault('serial', serial)
pkg = types.ModuleType('bambu_selfcheck_extras')
pkg.__path__ = [str(path.parent)]
sys.modules[pkg.__name__] = pkg
ace = importlib.import_module(pkg.__name__ + '.ace')
class FakeStore:
    _uid_from_sentinel = staticmethod(ace.MultiAce._uid_from_sentinel)
    def __init__(self, host):
        self._v2_filament_info_per_ace = {3: {3: {'host': host, 'type': 'old'}}}
        self._v2_filament_info_empty = {}
        self._rescan_bind_pending = {3: {3}}
        self.flushed = False
    def _raw(self, v): return v
    def _rescan_flush_binds(self, idx): self.flushed = True
sentinel = {'sku': 'SM' + UID, 'type': 'PLA Basic',
            'color': [255, 255, 232], 'tag': {'field2': 0x0102}}
for host in (False, True):
    f = FakeStore(host)
    ace.MultiAce._v2_store_filament_read(f, 3, 3, sentinel, host=False)
    assert (3 in f._v2_filament_info_per_ace[3]) == host
    assert f._v2_filament_info_empty[3][3] > 0 and f.flushed
print('PASS: firmware defaults never ingested; verified host cache preserved')

# Malformed UID must not allow an otherwise unverified sentinel identity in.
f = FakeStore(False)
ace.MultiAce._v2_store_filament_read(f, 3, 3, dict(sentinel, sku='malformed'), host=False)
assert 3 not in f._v2_filament_info_per_ace[3]
print('PASS: malformed sentinel UID remains untrusted')

class Workflow(Reader):
    def __init__(self):
        super().__init__()
        self.calls = []
    def _rc_stable_uid(self, idx, slot, respond): return '', None
    def _selected_single_uid(self, idx, slot): return UID
    def _read_bambu_classic(self, idx, slot, uid):
        self.calls.append(('auth', uid))
        return {'type': 'PLA Basic', 'brand': 'Bambu Lab', 'sku': uid,
                'color': [255, 255, 255], 'code': 0}
    def _ingest_bambu(self, idx, slot, uid, identity, respond):
        self.calls.append(('ingest', uid))
    def _identify(self, idx, slot):
        self.calls.append(('identify',))
        return dict(sentinel, code=0)
    def _neighbour_blocked_notice(self, idx, slot, uid):
        self.calls.append(('blocked', uid))

w = Workflow()
assert w._read_card(3, 3, lambda msg: None) == 0
assert w.calls == [('auth', UID), ('ingest', UID)], w.calls
w = Workflow()
assert w._read_card(3, 3, lambda msg: None, reject_uid=UID) == 0
assert w.calls == [('identify',), ('blocked', UID)], w.calls
print('PASS: authenticated before identify; neighbour UID never ingested')
