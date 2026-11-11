#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check source integration, or require the exact ARM64 payload for firmware packaging."""
import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import xml.etree.ElementTree as ET

from build_inputs import current_inputs, verify_runtime_inputs

REPO = Path(__file__).resolve().parents[2]
PROVIDER = {
    "version": "1.27b-233.20261111-single.2",
    "path": "overlays/233-dev.2-perms.1/root/usr/local/share/233-firmware/multiace-managed-1.27b-233.20261111-single.2.tar.gz",
    "sha256": "811398431b13acc78502c9ede4d8fe105d85408095ea048a528846d5bd9621b9",
}
SUBMODULES = {
    "providers/multiACE": "a1a7b91b1fd297c1486a13ea22c61e01499a196d",
    "deps/screen-apps": "4a81b50d9b6ef7a2c253b45dd00fe6f732c9d203",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_source(manifest, partial_tree):
    current_inputs()
    overlay = REPO / "overlays/233-native-ui/root"
    project = json.loads((REPO / "233-firmware.json").read_text())
    require(project["version"] == manifest["firmware_version"], "Firmware versions differ")
    require(project["base_firmware"]["sha256"] ==
            "6000de25703921113fb8665a82234bdc7ab25bd3030bc70dd1a307a18d90c02f",
            "Existing rsyncfix base pin changed")
    provider = project["managed_provider_pin"]
    require(all(provider[key] == value for key, value in PROVIDER.items()),
            "Existing multiACE package pin changed")
    if not partial_tree:
        require(digest(REPO / provider["path"]) == provider["sha256"], "Existing multiACE archive changed")
        for path, expected in SUBMODULES.items():
            actual = subprocess.check_output(["git", "-C", str(REPO), "ls-tree", "HEAD", path], text=True)
            require(len(actual.split()) >= 3 and actual.split()[2] == expected,
                    "Existing submodule pin changed: " + path)
    plan = subprocess.check_output(["bash", str(REPO / "build-233.sh"), "--plan"], text=True)
    require(plan.index("overlays/233-dev.2-perms.1") < plan.index("overlays/233-native-ui"),
            "Native overlay must follow the verified multiACE overlay")
    for path in [*(REPO / "scripts/native-ui").glob("*.py"),
                 *(overlay / "usr/local").rglob("*.py"),
                 *(overlay / "home/lava/klipper/klippy/extras").glob("*.py"),
                 *(REPO / "tests/native-ui").glob("*.py")]:
        ast.parse(path.read_text(), filename=str(path))
    shell_paths = [REPO / "build-233.sh", REPO / "scripts/create_firmware.sh",
                   *(REPO / "scripts/native-ui").glob("*.sh"),
                   *(REPO / "overlays/233-native-ui/pre-scripts").glob("*.sh"),
                   *(overlay / "etc/init.d").glob("S*"),
                   *(overlay / "etc/hooks/klipper.d").glob("*.sh"),
                   *(overlay / "etc/hooks/moonraker.d").glob("*.sh"),
                   *(overlay / "etc/hooks/lmd.d").glob("*.sh")]
    for path in shell_paths:
        subprocess.run(["bash", "-n", str(path)], check=True)
    for path in (overlay / "etc/init.d").glob("S*"):
        require(bool(path.stat().st_mode & 0o111), "Init script must be executable: " + str(path))


def validate_runtime(manifest):
    runtime = manifest["runtime"]
    archive = REPO / runtime["path"]
    record_path = REPO / runtime["file_manifest"]
    require(archive.is_file() and record_path.is_file(),
            "No current native runtime is staged. Build with scripts/native-ui/build.sh or download "
            "the matching Native UI CI artifact, then run scripts/native-ui/pin_runtime.py. "
            "An old runtime cannot be used for new source patches.")
    recorded = json.loads(record_path.read_text())
    build = verify_runtime_inputs(recorded)
    require(runtime.get("build_input_fingerprint") == build["input_fingerprint"], "Runtime pin fingerprint differs")
    require(digest(archive) == runtime["sha256"], "Runtime archive checksum mismatch")
    require(archive.stat().st_size == runtime["bytes"] == recorded["bytes"], "Runtime archive size mismatch")
    require(recorded["sha256"] == runtime["sha256"], "Runtime/file inventory checksums differ")
    names = set()
    xml_count = 0
    fonts = 0
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            path = PurePosixPath(member.name)
            require(not path.is_absolute() and ".." not in path.parts and
                    bool(path.parts) and path.parts[0] == "helixscreen", "Unsafe archive member: " + member.name)
            require(member.uid == member.gid == 0, "Runtime member ownership differs")
            require(member.mode in (0o644, 0o755), "Unexpected permissions: " + member.name)
            if member.isdir():
                continue
            require(member.isfile(), "Only regular payload files are supported")
            relative = str(PurePosixPath(*path.parts[1:]))
            require(relative not in names, "Duplicate archive member")
            names.add(relative)
            data = tar.extractfile(member).read()
            info = recorded["files"][relative]
            require(hashlib.sha256(data).hexdigest() == info["sha256"], "File checksum differs: " + relative)
            require(len(data) == info["bytes"] and oct(member.mode) == info["mode"], "File metadata differs: " + relative)
            if relative.endswith(".xml") and relative.startswith("ui_xml/"):
                ET.fromstring(data)
                xml_count += 1
            if relative.startswith("assets/fonts/cjk/") and relative.endswith(".bin"):
                fonts += 1
            if relative.startswith("bin/helix-"):
                require(data[:6] == bytes.fromhex("7f454c460201") and int.from_bytes(data[18:20], "little") == 183,
                        "AArch64 ELF required: " + relative)
                require(member.mode == 0o755, "Runtime binary must be executable")
            if relative == "bin/helix-screen":
                require(hashlib.sha256(data).hexdigest() == runtime["binary_sha256"], "Native executable checksum differs")
                require("高级设置".encode() in data, "Expected native UI source text missing")
            if relative == "ui_xml/settings_panel.xml":
                require("高级设置".encode() in data and "PAXX 设置".encode() not in data,
                        "Expected native settings layout missing")
    require(names == set(recorded["files"]), "Archive/file inventory differ")
    require(fonts == 35 and xml_count > 400, "Incomplete CJK font or XML runtime")
    require({"moonraker-plugin/install.sh", "licenses/native-third-party-manifest.json"} <= names,
            "Missing runtime support or source notices")
    overlay = REPO / "overlays/233-native-ui/root"
    package = (overlay / "usr/local/share/extended-pkg/helixscreen").read_text()
    require(runtime["sha256"] in package and 'PKG_PREFIX="helixscreen-233"' in package,
            "Offline package pin differs")
    require("file:///usr/local/share/233-firmware/" + archive.name in package,
            "Offline package URL differs")
    require((overlay / "etc/FULLVERSION").read_text().strip() == manifest["firmware_version"],
            "Overlay firmware version differs")
    mirror = json.loads((overlay / "usr/local/share/233-firmware/native-ui.json").read_text())
    require(mirror == manifest, "Runtime/source manifest mirror differs")
    print(f"Native payload verified: {len(names)} files, {xml_count} XML layouts, {fonts} CJK faces")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-only", action="store_true", help="Check source integration without a generated runtime")
    parser.add_argument("--partial-tree", action="store_true",
                        help="Source-only staging review; omit gitlink and existing multiACE archive checks")
    args = parser.parse_args()
    if args.partial_tree and not args.source_only:
        parser.error("--partial-tree is only allowed with --source-only, never for firmware packaging")
    try:
        manifest = json.loads((REPO / "bundles/native-ui/manifest.json").read_text())
        if not args.source_only:
            validate_runtime(manifest)
        validate_source(manifest, args.partial_tree)
        print("Native source/build pins, script syntax, overlay order and retained multiACE metadata verified")
        if args.partial_tree:
            print("Partial staging only: gitlinks and the existing multiACE archive were not verified")
        elif args.source_only:
            print("Source-only check: no claim that a native runtime or firmware image has been built")
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError, ET.ParseError, tarfile.TarError) as error:
        parser.exit(1, "Native UI validation failed: " + str(error) + "\n")
