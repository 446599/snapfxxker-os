"""Checked-byte control-flow model, not MCU/timing/physical-motion emulation."""
import hashlib
import json
from pathlib import Path
import sys
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'firmware'))
from preload_quarantine import (apply_insert_policy, BASE_ADDR, INSERT_RISE,
                                INSERT_RETURN, STOCK_RISE, QUARANTINE_RISE, STOCK_GUARDS)
STOCK = (ROOT.parent / 'input/ACE2_V1.1.31_20260306.bin').read_bytes()
DEFAULT = (ROOT / 'build/EXPERIMENTAL-DO-NOT-FLASH.bin').read_bytes()
ENABLED = (ROOT / 'build/COMPARISON-UNSAFE-PRELOAD-DO-NOT-FLASH.bin').read_bytes()

def at(img, addr, n):
    return img[addr - BASE_ADDR:addr - BASE_ADDR + n]

def gate_model(old, new, quarantined):
    # Exact predicates at F6A8/F6AA/F6AC and F6BA/F6BC/F6BE/F6C0.
    # The asserted instruction bytes below bind this abstraction to the stock gate.
    if old == 0 and new == 1:
        return 'return' if quarantined else 'preload_guards_and_enqueue'
    if old == 1 and new == 0:
        return 'stock_falling_stop_dispatch'
    return 'return'

class TestQuarantine(unittest.TestCase):
    def test_stock_identity(self):
        self.assertEqual(len(STOCK), 71592)
        self.assertEqual(hashlib.md5(STOCK).hexdigest(), '79fb22e7914bae1dc75ac91b30739c19')
        self.assertEqual(at(STOCK, 0x0800F6A8, 6), bytes.fromhex('3ab9012b05d1'))
        self.assertEqual(at(STOCK, 0x0800F6BA, 10), bytes.fromhex('012a08bf002b00f0a180'))
        self.assertEqual(at(STOCK, INSERT_RETURN, 4), bytes.fromhex('01b0f0bd'))

    def test_checked_arm_encoding(self):
        self.assertEqual((ROOT / 'build/quarantine_gate.bin').read_bytes(), QUARANTINE_RISE)
        hw = int.from_bytes(QUARANTINE_RISE[:2], 'little')
        self.assertEqual(hw & 0xf800, 0xe000)  # Thumb B T2 immediate encoding.
        imm = hw & 0x7ff
        if imm & 0x400:
            imm -= 0x800
        self.assertEqual(INSERT_RISE + 4 + 2 * imm, INSERT_RETURN)
        self.assertEqual(QUARANTINE_RISE[2:], bytes.fromhex('00bf'))

    def test_exhaustive_edge_model(self):
        # 65,536 input-value pairs, including non-boolean noise.
        for old in range(256):
            for new in range(256):
                patched = gate_model(old, new, True)
                self.assertNotEqual(patched, 'preload_guards_and_enqueue')
                if (old, new) != (0, 1):
                    self.assertEqual(patched, gate_model(old, new, False))
        self.assertEqual(gate_model(1, 0, True), 'stock_falling_stop_dispatch')

    def test_only_rising_gate_changed_in_whole_callback(self):
        original = bytearray(at(STOCK, 0x0800F698, 0x0800F848 - 0x0800F698))
        off = INSERT_RISE - 0x0800F698
        original[off:off + 4] = QUARANTINE_RISE
        self.assertEqual(at(DEFAULT, 0x0800F698, len(original)), original)
        self.assertEqual(at(ENABLED, 0x0800F698, len(original)),
                         at(STOCK, 0x0800F698, len(original)))
        # Also preserve the entire falling-edge dispatch, tail call and status setter.
        self.assertEqual(at(DEFAULT, 0x0800F806, 0x40), at(STOCK, 0x0800F806, 0x40))
        self.assertEqual(at(DEFAULT, 0x0800F634, 0x64), at(STOCK, 0x0800F634, 0x64))
        for addr, expected in STOCK_GUARDS:
            self.assertEqual(at(DEFAULT, addr, 2), expected)
            self.assertEqual(at(ENABLED, addr, 2), expected)

    def test_enabled_vs_default_entire_image_delta(self):
        expected = bytearray(ENABLED)
        off = INSERT_RISE - BASE_ADDR
        expected[off:off + 4] = QUARANTINE_RISE
        self.assertEqual(DEFAULT, expected)
        self.assertEqual(len(DEFAULT), len(ENABLED))
        self.assertEqual(DEFAULT[-8:], STOCK[-8:])

    def test_fail_closed_patch_site_validation(self):
        for addr in [INSERT_RISE] + [x[0] for x in STOCK_GUARDS]:
            bad = bytearray(STOCK)
            bad[addr - BASE_ADDR] ^= 1
            before = bytes(bad)
            with self.assertRaises(ValueError):
                apply_insert_policy(bad)
            self.assertEqual(bytes(bad), before)

    def test_no_decoder_truthfulness_contract_change(self):
        for filename in ['native_tag_decoder.c', 'rawtag_extract_stub.s', 'uid_stub.s']:
            self.assertEqual((ROOT / 'firmware' / filename).read_bytes(),
                             (ROOT.parent / 'source/firmware' / filename).read_bytes())
        self.assertIn(b'V1.1.9QO\x00', DEFAULT)
        spec = json.loads((ROOT / 'firmware/patch.json').read_text())
        self.assertFalse(spec['autonomous_preload_enabled'])
        self.assertEqual(spec['deployment_status'], 'NOT APPROVED; DO NOT FLASH')
        self.assertIn('09e000bf', [h['replace_hex'] for h in spec['hooks']])

if __name__ == '__main__':
    unittest.main(verbosity=2)
