# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise first-boot activation and persistent user choices without a printer."""
import configparser
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
MODULE = REPO / "overlays/233-native-ui/root/usr/local/bin/u1-native-ui-setup.py"
spec = importlib.util.spec_from_file_location("native_setup", MODULE)
native_setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native_setup)


class NativeUiBootTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.extended = self.root / "oem/printer_data/config/extended"
        self.extended.mkdir(parents=True)
        self.config = self.extended / "extended2.cfg"
        self.config.write_text("[components]\ngui: snapmaker\n\n[camera]\ninternal: paxx12\n")
        self.runtime = self.root / "oem/apps/helixscreen/latest"
        for name in ("bin", "config", "ui_xml", "assets/config/themes/defaults", "assets/config/presets"):
            (self.runtime / name).mkdir(parents=True)
        elf = bytearray(64)
        elf[:6] = b"\x7fELF\x02\x01"
        elf[18:20] = (183).to_bytes(2, "little")
        binary = self.runtime / "bin/helix-screen"
        binary.write_bytes(elf)
        binary.chmod(0o755)
        for name in ("settings_panel.xml", "firmware_settings.xml"):
            (self.runtime / "ui_xml" / name).write_text("<component/>")
        (self.runtime / "assets/config/themes/defaults/u1-warm.json").write_text("{}")
        (self.runtime / "assets/config/presets/snapmaker_u1.json").write_text(
            json.dumps({"display": {"rotate": 0}, "printer": {"heaters": {"hotend": "extruder"}}}))
        (self.runtime / "config/settings.json.template").write_text(json.dumps({
            "config_version": 34, "display": {}, "printers": {"default": {}},
            "wizard_completed": False}))
        self.commands = []
        self.install_fails = False
        self.upgrade_needed = False

    def run_command(self, command, **kwargs):
        self.commands.append(command)
        if command[0].endswith("extended-config.py"):
            cfg = configparser.ConfigParser()
            cfg.read(command[2])
            if command[1] == "get":
                return subprocess.CompletedProcess(command, 0, cfg.get(command[3], command[4], fallback=command[5]))
            cfg.set(command[3], command[4], command[5])
            with open(command[2], "w") as stream:
                cfg.write(stream)
            return subprocess.CompletedProcess(command, 0, "")
        if command[-1] == "needs_upgrade":
            return subprocess.CompletedProcess(command, 0 if self.upgrade_needed else 1, "")
        if self.install_fails:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, "")

    def setup_ui(self):
        return native_setup.setup(self.root, run=self.run_command, owner=None)

    def settings_path(self):
        return self.extended / "helixscreen/settings.json"

    def test_first_boot_installs_local_ui_and_uses_current_schema(self):
        self.setup_ui()
        settings = json.loads(self.settings_path().read_text())
        self.assertEqual(settings["config_version"], 34)
        self.assertEqual(settings["display"]["theme"], "u1-warm")
        self.assertFalse(settings["dark_mode"])
        self.assertEqual(settings["printers"]["default"]["moonraker_host"], "127.0.0.1")
        self.assertFalse(settings["wizard_completed"])
        self.assertIn("helixscreen", self.config.read_text())
        self.assertIn("internal = paxx12", self.config.read_text())
        self.assertEqual(os.stat(self.settings_path()).st_mode & 0o777, 0o600)

    def test_fresh_home_places_equal_speed_and_bed_tiles_with_print_and_tools(self):
        self.setup_ui()
        settings = json.loads(self.settings_path().read_text())
        home = settings["printers"]["default"]["panel_widgets"]["home"]
        self.assertEqual(home["grid"], "10x8")
        widgets = {item["id"]: item for item in home["pages"][0]["widgets"]
                   if item["enabled"]}
        for name in ("speed_flow", "bed_temperature", "print_status", "tool_switcher"):
            self.assertIn(name, widgets)
        speed, bed = widgets["speed_flow"], widgets["bed_temperature"]
        for dimension in ("row", "colspan", "rowspan"):
            self.assertEqual(speed[dimension], bed[dimension])
        occupied = set()
        for widget in widgets.values():
            cells = {(x, y) for x in range(widget["col"], widget["col"] + widget["colspan"])
                     for y in range(widget["row"], widget["row"] + widget["rowspan"])}
            self.assertTrue(all(0 <= x < 10 and 0 <= y < 8 for x, y in cells))
            self.assertFalse(cells & occupied)
            occupied |= cells

    def test_existing_ui3_dashboard_migrates_without_touching_device_settings(self):
        self.setup_ui()
        settings = json.loads(self.settings_path().read_text())
        settings["printers"]["default"]["panel_widgets"]["home"] = native_setup.previous_home_layout()
        settings["display"]["calibration"] = {"valid": True, "a": 1.23}
        original = json.loads(json.dumps(settings))
        self.settings_path().write_text(json.dumps(settings))
        self.setup_ui()
        updated = json.loads(self.settings_path().read_text())
        self.assertEqual(updated["printers"]["default"]["panel_widgets"]["home"],
                         native_setup.previous_ui4_home_layout())
        self.assertEqual(updated["display"], original["display"])
        self.assertEqual(updated["printers"]["default"]["moonraker_host"], "127.0.0.1")
        backup = self.settings_path().with_name("settings.json.pre-233-ui4-dashboard")
        self.assertEqual(json.loads(backup.read_text()), original)
        self.setup_ui()
        self.assertEqual(json.loads(self.settings_path().read_text()), updated)
        self.assertEqual(json.loads(backup.read_text()), original)

    def test_dashboard_migration_preserves_a_modified_widget_position(self):
        settings = {"printers": {"default": {"panel_widgets": {
            "home": native_setup.previous_home_layout()}}}}
        settings["printers"]["default"]["panel_widgets"]["home"]["pages"][0]["widgets"][0]["colspan"] = 6
        original = json.loads(json.dumps(settings))
        self.assertFalse(native_setup.migrate_shipped_dashboard(settings))
        self.assertEqual(settings, original)

    def test_ui5_runtime_uses_task_above_shared_status_strip(self):
        component = self.runtime / "ui_xml/components/panel_widget_tool_switcher.xml"
        component.parent.mkdir()
        component.write_text('<component><view name="tool_switcher_status_strip"/></component>')
        self.setup_ui()
        settings = json.loads(self.settings_path().read_text())
        home = settings["printers"]["default"]["panel_widgets"]["home"]
        self.assertEqual(home, native_setup.fresh_home_layout())
        self.assertEqual(home["grid"], "12x6")
        widgets = {item["id"]: item for item in home["pages"][0]["widgets"] if item["enabled"]}
        self.assertEqual(widgets["print_status"]["row"], 0)
        self.assertEqual(widgets["print_status"]["rowspan"], 4)
        self.assertEqual([widgets[name]["row"] for name in
                          ("tool_switcher", "speed_flow", "bed_temperature")], [4, 4, 4])
        occupied = set()
        for widget in widgets.values():
            cells = {(x, y) for x in range(widget["col"], widget["col"] + widget["colspan"])
                     for y in range(widget["row"], widget["row"] + widget["rowspan"])}
            self.assertTrue(all(0 <= x < 12 and 0 <= y < 6 for x, y in cells))
            self.assertFalse(cells & occupied)
            occupied |= cells
        self.assertEqual(len(occupied), 72)

    def test_ui5_upgrade_only_replaces_exact_ui3_or_ui4_dashboards(self):
        for previous in (native_setup.previous_home_layout, native_setup.previous_ui4_home_layout):
            with self.subTest(previous=previous.__name__):
                custom = previous()
                custom["pages"].append({"id": "my-tools", "widgets": []})
                settings = {"display": {"calibration": {"a": 1.23}}, "printers": {
                    "stock": {"panel_widgets": {"home": previous()}, "material": {"keep": True}},
                    "custom": {"panel_widgets": {"home": custom}}}}
                self.assertTrue(native_setup.migrate_shipped_dashboard(settings, overview=True))
                self.assertEqual(settings["printers"]["stock"]["panel_widgets"]["home"],
                                 native_setup.fresh_home_layout())
                self.assertEqual(settings["printers"]["custom"]["panel_widgets"]["home"], custom)
                self.assertEqual(settings["printers"]["stock"]["material"], {"keep": True})
                self.assertEqual(settings["display"], {"calibration": {"a": 1.23}})
                self.assertFalse(native_setup.migrate_shipped_dashboard(settings, overview=True))

    def test_existing_connections_calibration_and_material_data_are_preserved(self):
        original = {"config_version": 32, "language": "en", "display": {
            "theme": "other", "calibration": {"valid": True, "a": 1.23}},
            "printers": {"my-printer": {"moonraker_api_key": "private-test-value",
                "panel_widgets": {"home": {"grid": "10x8", "pages": [{"id": "custom",
                    "widgets": [{"id": "clock", "enabled": True, "col": 0, "row": 0,
                                 "colspan": 4, "rowspan": 2}]}]}}}},
            "custom": {"spool_id": 42}}
        self.settings_path().parent.mkdir()
        self.settings_path().write_text(json.dumps(original))
        self.setup_ui()
        updated = json.loads(self.settings_path().read_text())
        self.assertEqual(updated["config_version"], 32)
        self.assertEqual(updated["printers"], original["printers"])
        self.assertEqual(updated["display"]["calibration"], original["display"]["calibration"])
        self.assertEqual(updated["custom"], original["custom"])
        backup = self.settings_path().with_name("settings.json.pre-233-native-ui")
        self.assertEqual(json.loads(backup.read_text()), original)

    def test_subsequent_boot_preserves_selected_theme_and_applies_new_package(self):
        self.setup_ui()
        settings = json.loads(self.settings_path().read_text())
        settings["display"]["theme"] = "user-choice"
        settings["dark_mode"] = True
        self.settings_path().write_text(json.dumps(settings))
        self.upgrade_needed = True
        self.commands.clear()
        self.setup_ui()
        self.assertEqual(json.loads(self.settings_path().read_text()), settings)
        self.assertTrue(any(command[-1] == "upgrade" for command in self.commands))

    def test_switching_back_to_stock_is_respected(self):
        self.setup_ui()
        self.config.write_text("[components]\ngui: snapmaker\n")
        self.commands.clear()
        self.assertIn("preserved", self.setup_ui())
        self.assertEqual(len(self.commands), 1)
        self.assertIn("snapmaker", self.config.read_text())

    def test_failed_package_does_not_change_gui_or_create_marker(self):
        self.install_fails = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.setup_ui()
        self.assertIn("gui: snapmaker", self.config.read_text())
        self.assertFalse((self.extended / ".233-native-ui-initialized.json").exists())
        self.assertFalse(self.settings_path().exists())

    def test_shell_launcher_or_wrong_architecture_is_not_activated(self):
        binary = self.runtime / "bin/helix-screen"
        for invalid in (b"#!/bin/sh\nexit 0\n", b"\x7fELF\x02\x01" + bytes(58)):
            with self.subTest(invalid=invalid[:8]):
                binary.write_bytes(invalid)
                with self.assertRaises(RuntimeError):
                    self.setup_ui()
                self.assertIn("gui: snapmaker", self.config.read_text())
                self.assertFalse(self.settings_path().exists())

    def test_malformed_user_settings_are_not_replaced(self):
        self.settings_path().parent.mkdir()
        self.settings_path().write_text("{broken")
        with self.assertRaises(ValueError):
            self.setup_ui()
        self.assertEqual(self.settings_path().read_text(), "{broken")
        self.assertIn("gui: snapmaker", self.config.read_text())

    def test_missing_persistent_mount_does_not_install_or_activate(self):
        self.config.unlink()
        with self.assertRaises(RuntimeError):
            self.setup_ui()
        self.assertEqual(self.commands, [])


if __name__ == "__main__":
    unittest.main()
