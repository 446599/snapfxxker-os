"""Exercise real NTAG read/ingest paths using only a fake RF transport."""
import ast
import importlib.util
import json
import logging
from pathlib import Path
import subprocess
import sys
import types
import unittest

READER_PATH = Path(__file__).resolve().parents[1] / 'runtime/klipper/extras/ace_rc522.py'
spec = importlib.util.spec_from_file_location('rc522_ntag_test', READER_PATH)
rc522 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc522)
UID = '04225251C82A81'
OTHER_UID = '043EA551C32A81'


def ndef(payload, short=True, record_id=b'', extra_record=b''):
    """Independent NDEF fixture, including the optional ID-length field."""
    typ = b'application/json'
    flags = 0x82 | (0 if extra_record else 0x40)
    flags |= 0x10 if short else 0
    flags |= 0x08 if record_id else 0
    record = bytes([flags, len(typ)])
    record += (bytes([len(payload)]) if short
               else len(payload).to_bytes(4, 'big'))
    if record_id:
        record += bytes([len(record_id)])
    record += typ + record_id + payload + extra_record
    size = len(record)
    length = bytes([size]) if size < 255 else b'\xff' + size.to_bytes(2, 'big')
    return b'\x03' + length + record + b'\xfe'


def anycubic_data():
    data = bytearray(144)
    data[:4] = b'\x7b\x00\x65\x00'
    data[4:12] = b'PLA-SKU1'
    data[24:26] = b'AC'
    data[44:48] = b'PETG'
    data[64:68] = bytes([255, 0x33, 0x22, 0x11])
    return bytes(data)


class FakeAce:
    def __init__(self):
        self.stored = []
        self.bound = []
        self.prebound = None

    def _v2_store_filament_read(self, idx, slot, identity, **kwargs):
        self.stored.append((idx, slot, identity, kwargs))

    def _persist_tag_reads(self):
        pass

    def _spool_by_sku(self, uid):
        return (1, self.prebound) if self.prebound else (None, None)

    def _spool_bind_by_tag(self, idx, slot, uid, unbind=True):
        self.bound.append((idx, slot, uid, unbind))
        return None


class TagReader(rc522.AceTagReader):
    def __init__(self, data, before=UID, after=UID):
        super().__init__(FakeAce())
        self.data, self.before, self.after = data, before, after
        self.page_reads = 0
        self.notes = []

    def _rc_stable_uid(self, *args):
        return self.before, None

    def _rc_read_uid(self, *args):
        return self.after

    def _rc_read_userdata(self, *args, **kwargs):
        self.page_reads += 1
        return self.data

    def _identify(self, *args):
        raise AssertionError('attributed NTAG must use its selected raw pages')

    def _manual_guard(self):
        pass

    def read(self, **kwargs):
        return self._read_card(2, 3, self.notes.append,
                               expected_uid=UID, allow_nudges=False, **kwargs)


class NtagReadTests(unittest.TestCase):
    def test_anycubic_identity_accepted_without_firmware_guess(self):
        reader = TagReader(anycubic_data())
        reader.read()
        self.assertEqual(reader.page_reads, 1)
        idx, slot, data, metadata = reader.ace.stored[0]
        self.assertEqual((idx, slot), (2, 3))
        self.assertEqual((data['type'], data['color'], data['sku']),
                         ('PETG', [17, 34, 51], 'PLA-SKU1'))
        self.assertEqual(metadata['uid'], UID)
        self.assertEqual(metadata['fmt'], 'anycubic')

    def test_uid_binding_takes_precedence_over_shared_product_sku(self):
        reader = TagReader(anycubic_data())
        reader.ace.prebound = {'id': 3}
        reader.read()
        self.assertEqual(reader.ace.stored[0][3]['prebound'], {'id': 3})

    def test_openspool_identity_accepted(self):
        payload = b'{"protocol":"openspool","type":"PLA","color_hex":"A1B2C3","brand":"Test"}'
        reader = TagReader(ndef(payload))
        reader.read()
        self.assertEqual(reader.page_reads, 1)
        data, metadata = reader.ace.stored[0][2:]
        self.assertEqual((data['type'], data['color']), ('PLA', [161, 178, 195]))
        self.assertEqual(metadata, {'uid': UID, 'fmt': 'openspool'})

    def test_uid_change_before_or_during_read_never_ingests(self):
        for before, after in ((OTHER_UID, UID), (UID, OTHER_UID), (UID, '')):
            with self.subTest(before=before, after=after):
                reader = TagReader(anycubic_data(), before, after)
                reader.read()
                self.assertEqual(reader.ace.stored, [])
                self.assertEqual(reader.ace.bound, [])

    def test_neighbour_uid_is_rejected_without_reading(self):
        reader = TagReader(anycubic_data())
        reader.read(reject_uid=UID)
        self.assertEqual(reader.page_reads, 0)
        self.assertEqual(reader.ace.stored, [])

    def test_missing_pages_returns_retryable_failure(self):
        reader = TagReader(b'')
        reader.read()
        self.assertEqual(reader.ace.stored, [])
        self.assertTrue(reader._read_unread)

    def test_continuous_read_commits_only_after_existing_return_gate(self):
        for data in (anycubic_data(), ndef(b'{"protocol":"openspool","type":"PLA"}')):
            with self.subTest(format=data[:4]):
                reader = TagReader(data)
                reader._defer_identity_commit = True
                reader.read()
                self.assertEqual(reader.ace.stored, [])
                self.assertEqual(reader.ace.bound, [])
                self.assertFalse(reader._commit_continuous_identity(2, 3, OTHER_UID, reader.notes.append))
                reader._defer_identity_commit = False
                self.assertTrue(reader._commit_continuous_identity(2, 3, UID, reader.notes.append))
                self.assertEqual(len(reader.ace.stored), 1)
                self.assertEqual(reader.page_reads, 1)


class NdefTests(unittest.TestCase):
    def test_short_record_and_id_field(self):
        data = b'{"protocol":"openspool","type":"PLA","color_hex":"00FF00"}'
        for record_id in (b'', b'spool-1'):
            with self.subTest(record_id=record_id):
                result = rc522.AceTagReader._openspool_decode(ndef(data, record_id=record_id))
                self.assertEqual(result['color'], '00FF00')

    def test_long_record_length_does_not_consume_next_record(self):
        payload = json.dumps({'protocol': 'openspool', 'type': 'PETG',
                              'brand': 'B' * 300}).encode()
        # A second empty NDEF record makes the long-length offset observable.
        data = ndef(payload, short=False, extra_record=b'\x50\x00\x00')
        self.assertEqual(rc522.AceTagReader._openspool_decode(data)['material'], 'PETG')

    def test_long_payload_encode_roundtrip(self):
        data = rc522.AceTagReader._openspool_encode('PLA', '112233', 'B' * 300)
        self.assertEqual(data[:2], b'\x03\xff')
        result = rc522.AceTagReader._openspool_decode(data)
        self.assertEqual(result['vendor'], 'B' * 300)
        self.assertEqual(result['color'], '112233')

    def test_invalid_json_shapes_and_truncated_tlv_are_not_identities(self):
        for payload in (b'[]', b'null', b'{"protocol":"openspool","type":123}'):
            with self.subTest(payload=payload):
                self.assertIsNone(rc522.AceTagReader._openspool_decode(ndef(payload)))
        data = ndef(b'{"protocol":"openspool","type":"PLA"}')
        self.assertIsNone(rc522.AceTagReader._openspool_decode(data[:-3]))


class PageReadTests(unittest.TestCase):
    def test_failed_transceive_cannot_reuse_stale_receive_bytes(self):
        class Reader(rc522.AceTagReader):
            def _rc(self, idx, slot, op, a1=0, a2=0):
                if op == 2:
                    return 0
                if op == 3:
                    return 1
                if op == 5:
                    return 128
                raise AssertionError('stale RX bytes must not be read')
        self.assertEqual(Reader(None)._rc_read_page(0, 0, 4), [])

    def test_partial_page_does_not_shift_remaining_identity_bytes(self):
        class Reader(rc522.AceTagReader):
            def _rc_read_page(self, idx, slot, page, dbg=None):
                return [] if page == 8 else [1] * 16
        self.assertEqual(Reader(None)._rc_read_userdata(0, 0), b'')


def command_outcome():
    """Load the actual command's result handlers without starting Klipper."""
    tree = ast.parse(READER_PATH.with_name('ace.py').read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'MultiAce')
    command = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == 'cmd_ACE_TAG_READ')
    handlers = [node for node in command.body
                if isinstance(node, ast.FunctionDef) and node.name in ('_respond', '_finish')
                or isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id in ('_OK', '_FAIL')
                        for target in node.targets)]
    actions = types.SimpleNamespace(cancel=False, state={'kind': 'rfid', 'seq': 1,
                                   'ace': 2, 'slot': 3, 'state': 'running'})
    owner = types.SimpleNamespace(_tag_op_seq=1, _tag_read_busy=True,
                                  _slot_actions=actions, log_always=lambda msg: None,
                                  _insert_drain_queue=lambda: None)
    module_name = '_ntag_outcome_remaining'
    remaining = types.ModuleType(module_name + '.ace_manual_remaining')
    remaining.refresh = lambda *args: {'state': 'unknown', 'reason': 'spool_profile_missing'}
    sys.modules[remaining.__name__] = remaining
    env = {'self': owner, 'logging': logging, '_out': {'ok': None, 'msg': ''},
           'policy': types.SimpleNamespace(options=types.SimpleNamespace(enabled=lambda *args: True)),
           'reader': types.SimpleNamespace(_scan_origin_reusable=True),
           'continuous': True, 'ace_idx': 2, 'slot': 3, '_epoch': 1,
           '_seq': 1, '_action_seq': 1, '_transport': object(),
           '_busy_flag': '_tag_read_busy', '_observed_uid': {'value': UID},
           '__package__': module_name}
    exec(compile(ast.Module(body=handlers, type_ignores=[]), '<actual RFID result handlers>', 'exec'), env)
    return owner, env['_respond'], env['_finish']


class CommandOutcomeTests(unittest.TestCase):
    def test_anycubic_success_and_unknown_remaining_release_reader(self):
        owner, respond, finish = command_outcome()
        reader = TagReader(anycubic_data())
        reader._read_card(2, 3, respond, expected_uid=UID, allow_nudges=False)
        finish()
        self.assertTrue(owner._tag_op_result['ok'])
        self.assertEqual(owner._tag_op_result['remaining']['state'], 'unknown')
        self.assertEqual(owner._slot_actions.state['state'], 'complete')
        self.assertFalse(owner._tag_read_busy)

    def test_unread_ntag_is_retryable_information(self):
        owner, respond, finish = command_outcome()
        TagReader(b'')._read_card(2, 3, respond, expected_uid=UID, allow_nudges=False)
        finish()
        self.assertTrue(owner._tag_op_result['pending'])
        self.assertEqual(owner._slot_actions.state['state'], 'complete')
        self.assertFalse(owner._tag_read_busy)

    def test_no_tag_scan_is_retryable_information(self):
        owner, respond, finish = command_outcome()
        respond('rc522: result pending: 已在 600 mm 窗口内扫描，未确认可归属标签')
        finish()
        self.assertTrue(owner._tag_op_result['pending'])
        self.assertEqual(owner._slot_actions.state['state'], 'complete')
        self.assertFalse(owner._tag_read_busy)

    def test_unconfirmed_return_remains_an_error(self):
        owner, respond, finish = command_outcome()
        respond('rc522: result pending: 标签数据暂未读到，可重试识别')
        respond('rc522: read failed: 识别回停 STOP 未确认')
        finish()
        self.assertFalse(owner._tag_op_result['pending'])
        self.assertFalse(owner._tag_op_result['ok'])
        self.assertEqual(owner._slot_actions.state['state'], 'error')
        self.assertFalse(owner._tag_read_busy)


class PendingUiTests(unittest.TestCase):
    def test_state_normalization_and_outcome_template(self):
        result = subprocess.run(['node', str(Path(__file__).with_name('test_rc522_pending_ui.js'))],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
