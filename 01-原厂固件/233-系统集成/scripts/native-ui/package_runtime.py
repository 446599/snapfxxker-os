#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Create the firmware-managed U1 runtime from a matching source/build tree."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
from build_inputs import verify_build_start

REPO = Path(__file__).resolve().parents[2]
PRINTER_IMAGES = {"snapmaker-u1.png", "snapmaker-u1-tophat.png", "generic-corexy.png",
                  "regions.json", "README.md"}
PERSONAL_CONFIG = {"settings.json", "settings-test.json", "helixconfig.json",
                   "helixconfig-test.json", "telemetry_device.json", "telemetry_queue.json",
                   "tool_spools.json", "filament_slot_overrides.json", "user_filaments.json",
                   ".crash_restart_count", "crash_history.json", "pending_remap.json",
                   ".helix-screen.lock"}
SKIP_DIRS = {".git", "build", "node_modules", "__pycache__", ".deps", ".cache"}


def permitted(relative):
    parts = relative.parts
    if any(part in SKIP_DIRS for part in parts) or relative.suffix in (".pyc", ".pyo"):
        return False
    if relative.name in (".DS_Store", "mdi-icon-metadata.json.gz"):
        return False
    if parts[:2] in (("assets", "test_gcodes"), ("assets", "test_timelapse")):
        return False
    if parts[:2] == ("assets", "fonts") and relative.suffix in (".c", ".h", ".ttf", ".otf"):
        return False
    if relative.suffix.lower() in (".ico", ".icns"):
        return False
    if parts[:3] == ("assets", "images", "printers"):
        if len(parts) == 4:
            return relative.name in PRINTER_IMAGES
        if len(parts) > 4 and parts[3] == "prerendered":
            return relative.name.startswith(("snapmaker-u1", "generic-corexy"))
    if parts[0] == "ui_xml" and relative.suffix in (".c", ".h"):
        return False
    if parts[0] == "config" and (relative.name in PERSONAL_CONFIG
                                or relative.name.startswith("crash")):
        return False
    if parts[0] == "moonraker-plugin" and (
            any(part in ("tests", "tools") for part in parts)
            or relative.name == "TESTING_PLAN.md"):
        return False
    return True


def is_license(name):
    upper = name.upper()
    return upper in ("OFL", "OFL.TXT", "COPYRIGHT", "NOTICE", "NOTICE.TXT") or \
        upper.startswith(("LICENSE", "LICENCE", "COPYING"))


def collect(source, binaries, ca_bundle, licenses):
    files = {}
    for directory in ("assets", "config", "ui_xml", "moonraker-plugin"):
        if not (source / directory).is_dir():
            raise ValueError("Missing source runtime directory: " + directory)
        for path in sorted((source / directory).rglob("*")):
            relative = path.relative_to(source)
            if path.is_file() and permitted(relative):
                files[str(relative)] = path
    for name in ("LICENSE", "COPYRIGHT", "VERSION.txt"):
        files[name] = source / name
    for name in ("helix-screen", "helix-splash", "helix-watchdog", ".build-features"):
        path = binaries / name
        if not path.is_file():
            raise ValueError("Missing matching ARM build output: " + str(path))
        files["bin/" + name] = path
    files["certs/ca-certificates.crt"] = ca_bundle
    for directory, dirs, names in os.walk(source):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for name in names:
            path = Path(directory) / name
            if path.is_file() and is_license(name):
                files["licenses/source/" + str(path.relative_to(source))] = path
    for path in licenses.rglob("*"):
        if path.is_file():
            files["licenses/" + str(path.relative_to(licenses))] = path
    return files


def package(source, binaries, ca_bundle, licenses, output, build_start):
    build = verify_build_start(source, binaries, build_start)
    files = collect(source, binaries, ca_bundle, licenses)
    header = (binaries / "helix-screen").read_bytes()[:20]
    if header[:6] != b"\x7fELF\x02\x01" or int.from_bytes(header[18:20], "little") != 183:
        raise ValueError("The runtime requires an AArch64 ELF, not a host binary or launcher")
    needed = {"assets/images/printers/snapmaker-u1.png",
              "assets/images/printers/snapmaker-u1-tophat.png",
              "assets/images/printers/generic-corexy.png", "ui_xml/firmware_settings.xml",
              "assets/config/themes/defaults/u1-warm.json",
              "licenses/native-third-party-manifest.json"}
    if not needed.issubset(files):
        raise ValueError("Missing runtime inputs: " + repr(sorted(needed - files.keys())))
    if len([name for name in files if name.startswith("assets/fonts/cjk/") and name.endswith(".bin")]) != 35:
        raise ValueError("Expected all 35 generated CJK font faces")
    records = {}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as stream:
        with gzip.GzipFile(filename="", mode="wb", fileobj=stream, mtime=0, compresslevel=9) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                directories = {"helixscreen"}
                for name in files:
                    directories.update(str(parent) for parent in (Path("helixscreen") / name).parents
                                       if str(parent) != ".")
                for name in sorted(directories):
                    info = tarfile.TarInfo(name)
                    info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o755, 0
                    archive.addfile(info)
                for name, path in sorted(files.items()):
                    content = path.read_bytes()
                    mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                    info = tarfile.TarInfo("helixscreen/" + name)
                    info.mode, info.size, info.mtime = mode, len(content), 0
                    archive.addfile(info, io.BytesIO(content))
                    records[name] = {"sha256": hashlib.sha256(content).hexdigest(),
                                     "bytes": len(content), "mode": oct(mode)}
    manifest = {"sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "bytes": output.stat().st_size, "root": "helixscreen", "files": records,
                "build": build,
                "scope": "U1 runtime; other printer artwork falls back to generic-corexy"}
    output.with_suffix(output.suffix + ".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: manifest[key] for key in ("sha256", "bytes", "root")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--binaries", required=True, type=Path)
    parser.add_argument("--ca-bundle", required=True, type=Path)
    parser.add_argument("--licenses", type=Path, default=REPO / "bundles/native-ui/licenses")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--build-start", required=True, type=Path,
                        help="Record made immediately before the native compilation")
    args = parser.parse_args()
    package(args.source, args.binaries, args.ca_bundle, args.licenses, args.output, args.build_start)
