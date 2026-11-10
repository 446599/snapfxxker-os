# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for the shared config left by the original UI.3 hook."""

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "overlays/233-native-ui/root"
spec = importlib.util.spec_from_file_location(
    "upgrade_slot_config_tests", OVERLAY / "usr/local/lib/u1-device-service/slot_config.py")
slot_config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(slot_config)
LEGACY = "# Managed by the 233 native UI upgrade service.\n[u1_native_upgrade]\n"


class UpgradeSlotConfigTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.shared = self.tmp / "oem/config/extended/klipper/u1_native_upgrade.cfg"
        self.system_config = self.tmp / "slot/klipper.cfg"
        self.system_config.parent.mkdir()
        self.system_config.write_bytes(
            (OVERLAY / "usr/local/share/u1-native-upgrade/klipper.cfg").read_bytes())
        self.system_module = self.tmp / "slot/u1_native_upgrade.py"
        self.system_module.write_bytes(
            (OVERLAY / "home/lava/klipper/klippy/extras/u1_native_upgrade.py").read_bytes())

    def ensure(self):
        slot_config.ensure_slot_config(self.shared, self.system_config, self.system_module)

    def write_legacy(self, content=LEGACY):
        self.shared.parent.mkdir(parents=True, exist_ok=True)
        self.shared.write_text(content)

    def test_migrates_original_hook_output_and_keeps_non_cfg_backup(self):
        self.write_legacy()
        self.ensure()
        self.assertNotIn("[u1_native_upgrade]", self.shared.read_text())
        self.assertIn("[" + slot_config.OPTIONAL_INCLUDE + "]", self.shared.read_text())
        backups = list(self.shared.parent.glob("*.before-slot-scope.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), LEGACY)
        self.assertEqual(list(self.shared.parent.glob("*.cfg")), [self.shared])

    def test_preserves_other_sections_notes_newlines_and_permissions(self):
        before = "[gcode_macro USER]\ngcode: M117 saved\n\n"
        after = "# operator note\n; another note\n\n[respond]\ndefault_type: command\n"
        content = before + "[ u1_native_upgrade ] # keep inline note\n" + after
        self.write_legacy(content)
        self.shared.chmod(0o640)
        self.ensure()
        expected = before + "[" + slot_config.OPTIONAL_INCLUDE + "] # keep inline note\n" + after
        self.assertEqual(self.shared.read_text(), expected)
        self.assertEqual(self.shared.stat().st_mode & 0o777, 0o640)
        backups = list(self.shared.parent.glob("*.before-slot-scope.*"))
        self.assertEqual(backups[0].read_text(), content)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o640)

    def test_repeated_start_does_not_rewrite_shared_config_or_create_more_backups(self):
        self.write_legacy()
        self.ensure()
        first = self.shared.stat()
        content = self.shared.read_bytes()
        self.ensure()
        self.assertEqual(self.shared.read_bytes(), content)
        self.assertEqual(self.shared.stat().st_ino, first.st_ino)
        self.assertEqual(self.shared.stat().st_mtime_ns, first.st_mtime_ns)
        self.assertEqual(len(list(self.shared.parent.glob("*.before-slot-scope.*"))), 1)

    def test_rejects_unknown_options_without_modifying_or_hiding_user_content(self):
        content = LEGACY + "custom_option: preserve me\n\n[respond]\n"
        self.write_legacy(content)
        with self.assertRaisesRegex(ValueError, "custom options"):
            self.ensure()
        self.assertEqual(self.shared.read_text(), content)
        self.assertEqual(list(self.shared.parent.iterdir()), [self.shared])

    def test_symlink_target_and_link_remain_unchanged(self):
        target = self.tmp / "operator.cfg"
        target.write_text(LEGACY)
        self.shared.parent.mkdir(parents=True)
        self.shared.symlink_to(target)
        with self.assertRaises(OSError):
            self.ensure()
        self.assertTrue(self.shared.is_symlink())
        self.assertEqual(target.read_text(), LEGACY)

    def test_rejects_ambiguous_indented_header_without_changing_user_data(self):
        content = "[gcode_macro USER]\ngcode:\n    [u1_native_upgrade]\n"
        self.write_legacy(content)
        with self.assertRaisesRegex(ValueError, "indented native upgrade section"):
            self.ensure()
        self.assertEqual(self.shared.read_text(), content)
        self.assertEqual(list(self.shared.parent.iterdir()), [self.shared])

    def test_rejects_nonregular_config_without_blocking(self):
        self.shared.parent.mkdir(parents=True)
        os.mkfifo(self.shared)
        with self.assertRaisesRegex(ValueError, "regular file"):
            self.ensure()

    def test_creates_bridge_without_legacy_section_and_retains_existing_user_sections(self):
        self.write_legacy("[respond]\ndefault_type: command\n")
        self.ensure()
        content = self.shared.read_text()
        self.assertTrue(content.startswith("[respond]\ndefault_type: command\n"))
        self.assertIn("[" + slot_config.OPTIONAL_INCLUDE + "]", content)
        self.assertNotIn("[u1_native_upgrade]", content)
        self.assertFalse(list(self.shared.parent.glob("*.before-slot-scope.*")))


if __name__ == "__main__":
    unittest.main()
