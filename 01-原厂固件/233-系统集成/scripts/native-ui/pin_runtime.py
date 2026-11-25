#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage a reviewed package and update the firmware's matching local package pin."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
from build_inputs import verify_runtime_inputs

REPO = Path(__file__).resolve().parents[2]


def pin(runtime):
    path = REPO / "bundles/native-ui/manifest.json"
    manifest = json.loads(path.read_text())
    files = json.loads(runtime.with_suffix(runtime.suffix + ".manifest.json").read_text())
    build = verify_runtime_inputs(files)
    digest = hashlib.sha256(runtime.read_bytes()).hexdigest()
    if digest != files["sha256"]:
        raise ValueError("Package and file manifest do not match")
    destination = REPO / manifest["runtime"]["path"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    if runtime.resolve() != destination.resolve():
        shutil.copyfile(runtime, destination)
    manifest["runtime"].update(sha256=digest, bytes=runtime.stat().st_size,
        binary_sha256=files["files"]["bin/helix-screen"]["sha256"],
        file_manifest="bundles/native-ui/runtime-files.json",
        build_input_fingerprint=build["input_fingerprint"])
    manifest["validation"]["runtime_packaged"] = True
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    (REPO / manifest["runtime"]["file_manifest"]).write_text(
        json.dumps(files, ensure_ascii=False, indent=2) + "\n")
    overlay = REPO / "overlays/233-native-ui/root"
    package = overlay / "usr/local/share/extended-pkg/helixscreen"
    package.parent.mkdir(parents=True, exist_ok=True)
    package.write_text('# SPDX-License-Identifier: GPL-3.0-or-later\n'
        'PKG_LABEL="233 Native UI"\n'
        'PKG_PREFIX="helixscreen-233"\n'
        f'PKG_VERSION="{manifest["runtime"]["package_version"]}"\n'
        f'PKG_URL="file:///usr/local/share/233-firmware/{destination.name}"\n'
        f'PKG_SHA256="{digest}"\n'
        'PKG_BINARIES="bin/helix-screen bin/helix-splash bin/helix-watchdog"\n')
    (overlay / "usr/local/share/233-firmware/native-ui.json").write_text(path.read_text())
    (overlay / "etc/FULLVERSION").write_text(manifest["firmware_version"] + "\n")
    print(f"Pinned {destination.name}: {digest}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, type=Path)
    pin(parser.parse_args().runtime)
