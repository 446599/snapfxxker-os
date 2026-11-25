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


def previous_ui4_home_layout():
    """Exact UI.4 dashboard, used to recognize an unmodified shipped layout."""
    widgets = [
        {"id": "tool_switcher", "enabled": True, "col": 0, "row": 0,
         "colspan": 10, "rowspan": 2},
        {"id": "print_status", "enabled": True, "col": 0, "row": 2,
         "colspan": 10, "rowspan": 4, "config": {"layout_style": "detailed"}},
        {"id": "bed_temperature", "enabled": True, "col": 0, "row": 6,
         "colspan": 5, "rowspan": 2},
        {"id": "speed_flow", "enabled": True, "col": 5, "row": 6,
         "colspan": 5, "rowspan": 2},
    ]
    widgets.extend({"id": name, "enabled": False} for name in
                   ("printer_image", "notifications", "led", "fan_stack",
                    "temperature", "filament", "tips"))
    return {"grid": "10x8", "main_page_index": 0, "next_page_id": 1,
            "pages": [{"id": "main", "widgets": widgets}]}


def fresh_home_layout():
    """UI.5 task card above one shared head, speed, and bed status strip."""
    widgets = [
        {"id": "print_status", "enabled": True, "col": 0, "row": 0,
         "colspan": 12, "rowspan": 4, "config": {"layout_style": "detailed"}},
        {"id": "tool_switcher", "enabled": True, "col": 0, "row": 4,
         "colspan": 6, "rowspan": 2},
        {"id": "speed_flow", "enabled": True, "col": 6, "row": 4,
         "colspan": 3, "rowspan": 2},
        {"id": "bed_temperature", "enabled": True, "col": 9, "row": 4,
         "colspan": 3, "rowspan": 2},
    ]
    widgets.extend({"id": name, "enabled": False} for name in
                   ("printer_image", "notifications", "led", "fan_stack",
                    "temperature", "filament", "tips"))
    return {"grid": "12x6", "main_page_index": 0, "next_page_id": 1,
            "pages": [{"id": "main", "widgets": widgets}]}


def runtime_has_overview_strip(runtime):
    """Only the reconstructed runtime can render the compact UI.5 dashboard."""
    component = Path(runtime) / "ui_xml/components/panel_widget_tool_switcher.xml"
    try:
        return 'name="tool_switcher_status_strip"' in component.read_text(encoding="utf-8")
    except OSError:
        return False


def migrate_shipped_dashboard(settings, overview=True):
    """Upgrade exact shipped layouts; custom widgets, pages, and settings survive."""
    changed = False
    printers = settings.get("printers", {})
    if not isinstance(printers, dict):
        return False
    recognized = [previous_home_layout()]
    if overview:
        recognized.append(previous_ui4_home_layout())
    for printer in printers.values():
        if not isinstance(printer, dict):
            continue
        panels = printer.get("panel_widgets", {})
        if isinstance(panels, dict) and panels.get("home") in recognized:
            panels["home"] = fresh_home_layout() if overview else previous_ui4_home_layout()
            changed = True
    return changed


def previous_home_layout():
    """Exact UI.3 dashboard, used only to identify an unmodified shipped layout."""
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
    home = fresh_home_layout() if runtime_has_overview_strip(runtime) else previous_ui4_home_layout()
    printer.setdefault("panel_widgets", {}).setdefault("home", home)
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

    # A new application layout must also reach users booting from the prior
    # UI slot. Compare its shipped dashboard exactly before migrating it.
    settings_path = extended / "helixscreen/settings.json"
    if initialized and settings_path.is_file():
        settings = json.loads(settings_path.read_text())
        if not isinstance(settings, dict):
            raise ValueError("Existing UI settings must be a JSON object")
        overview = runtime_has_overview_strip(runtime)
        if migrate_shipped_dashboard(settings, overview=overview):
            revision = 5 if overview else 4
            backup = settings_path.with_name(f"settings.json.pre-233-ui{revision}-dashboard")
            if not backup.exists():
                shutil.copy2(settings_path, backup)
            atomic_json(settings_path, settings, owner)

    atomic_json(marker, {"runtime": os.path.realpath(runtime),
                         "theme_initialized": True,
                         "layout_revision": 5 if runtime_has_overview_strip(runtime) else 4}, owner)
    return "Bundled native UI ready"


if __name__ == "__main__":
    try:
        print("[233-native-ui] " + setup())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print("[233-native-ui] " + str(error), file=sys.stderr)
        sys.exit(1)
