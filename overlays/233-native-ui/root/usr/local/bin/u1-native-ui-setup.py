#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Install the bundled UI before lmd, activating it once without resetting user data."""

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def atomic_json(path, value, owner=(1000, 1000)):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        if owner is not None:
            os.chown(temporary, *owner)
            os.chown(path.parent, *owner)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def is_arm64_elf(path):
    try:
        with path.open("rb") as stream:
            header = stream.read(20)
        return (len(header) == 20 and header[:6] == b"\x7fELF\x02\x01"
                and int.from_bytes(header[18:20], "little") == 183
                and os.access(path, os.X_OK))
    except OSError:
        return False


def fresh_home_layout():
    """Seat the confirmed overview controls on the U1's 480x320 home grid."""
    widgets = [
        {"id": "tool_switcher", "enabled": True, "col": 0, "row": 0,
         "colspan": 8, "rowspan": 2},
        {"id": "notifications", "enabled": True, "col": 8, "row": 0,
         "colspan": 2, "rowspan": 2},
        {"id": "speed_flow", "enabled": True, "col": 0, "row": 2,
         "colspan": 5, "rowspan": 2},
        {"id": "bed_temperature", "enabled": True, "col": 5, "row": 2,
         "colspan": 5, "rowspan": 2},
        {"id": "print_status", "enabled": True, "col": 0, "row": 4,
         "colspan": 10, "rowspan": 4, "config": {"layout_style": "detailed"}},
    ]
    # Other registry defaults must not auto-fill the deliberately allocated grid.
    widgets.extend({"id": name, "enabled": False} for name in
                   ("printer_image", "led", "fan_stack", "temperature", "filament", "tips"))
    return {"grid": "10x8", "main_page_index": 0, "next_page_id": 1,
            "pages": [{"id": "main", "widgets": widgets}]}


def fresh_settings(runtime):
    value = json.loads((runtime / "config/settings.json.template").read_text())
    if not isinstance(value, dict) or not isinstance(value.get("config_version"), int):
        raise ValueError("Bundled UI settings template has no schema version")
    preset = json.loads((runtime / "assets/config/presets/snapmaker_u1.json").read_text())
    printer = value["printers"]["default"]
    printer.update(preset.get("printer", {}))
    printer.update(name="Snapmaker U1", type="Snapmaker U1",
                   moonraker_host="127.0.0.1", moonraker_port=7125)
    printer.setdefault("panel_widgets", {}).setdefault("home", fresh_home_layout())
    value.setdefault("display", {}).update(preset.get("display", {}))
    # The first-run wizard still owns connection confirmation and touch setup.
    return value


def setup(root=Path("/"), run=subprocess.run, owner=(1000, 1000)):
    root = Path(root)
    path = lambda absolute: root / absolute.lstrip("/")
    extended = path("/oem/printer_data/config/extended")
    config = extended / "extended2.cfg"
    marker = extended / ".233-native-ui-initialized.json"
    runtime = path("/oem/apps/helixscreen/latest")
    config_tool = str(path("/usr/local/bin/extended-config.py"))
    package_tool = str(path("/usr/local/bin/extended-pkg"))
    if not config.is_file():
        raise RuntimeError("Persistent extended2.cfg is unavailable; native UI was not activated")
    result = run([config_tool, "get", str(config), "components", "gui", "snapmaker"],
                 check=True, capture_output=True, text=True)
    selected = result.stdout.strip()
    initialized = marker.is_file()
    if initialized and selected != "helixscreen":
        return "User touchscreen selection preserved"

    result = run([package_tool, "helixscreen", "needs_upgrade"], check=False,
                 capture_output=True, text=True)
    if result.returncode not in (0, 1):
        raise RuntimeError("Cannot read native UI package version")
    action = "upgrade" if result.returncode == 0 else "download"
    run([package_tool, "helixscreen", action], check=True)
    if not is_arm64_elf(runtime / "bin/helix-screen"):
        raise RuntimeError("Installed native UI is not an executable AArch64 ELF")
    for resource in ("ui_xml/settings_panel.xml", "ui_xml/firmware_settings.xml",
                     "assets/config/themes/defaults/u1-warm.json"):
        if not (runtime / resource).is_file():
            raise RuntimeError("Installed native UI lacks " + resource)

    if not initialized:
        settings_path = extended / "helixscreen/settings.json"
        if settings_path.exists():
            settings = json.loads(settings_path.read_text())
            if not isinstance(settings, dict):
                raise ValueError("Existing UI settings must be a JSON object")
            backup = settings_path.with_name("settings.json.pre-233-native-ui")
            if not backup.exists():
                shutil.copy2(settings_path, backup)
        else:
            settings = fresh_settings(runtime)
        settings = copy.deepcopy(settings)
        display = settings.setdefault("display", {})
        if not isinstance(display, dict):
            raise ValueError("Existing UI display settings must be a JSON object")
        display["theme"] = "u1-warm"
        settings["dark_mode"] = False
        settings["language"] = "zh"
        atomic_json(settings_path, settings, owner)
        run([config_tool, "add", str(config), "components", "gui", "helixscreen"],
            check=True)

    atomic_json(marker, {"runtime": os.path.realpath(runtime),
                         "theme_initialized": True}, owner)
    return "Bundled native UI ready"


if __name__ == "__main__":
    try:
        print("[233-native-ui] " + setup())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print("[233-native-ui] " + str(error), file=sys.stderr)
        sys.exit(1)
