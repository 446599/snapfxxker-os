#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bind a packaged runtime to this checkout's source patches and build recipe."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import time

REPO = Path(__file__).resolve().parents[2]
RECIPE_PATHS = (
    "scripts/native-ui/build.sh",
    "scripts/native-ui/build_inputs.py",
    "scripts/native-ui/prepare_source.py",
    "scripts/native-ui/prepare_toolchain.py",
    "scripts/native-ui/make-recorded.sh",
    "scripts/native-ui/package_runtime.py",
    "bundles/native-ui/cross-deps.mk",
    "bundles/native-ui/cross-packages.json",
    "bundles/native-ui/font-tools/package.json",
    "bundles/native-ui/font-tools/package-lock.json",
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def object_digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def current_inputs(repo=REPO):
    manifest = json.loads((repo / "bundles/native-ui/manifest.json").read_text())
    source = manifest["source"]
    for record in [*source["patches"], source["third_party_manifest"]]:
        if digest(repo / record["path"]) != record["sha256"]:
            raise ValueError("Source input checksum mismatch: " + record["path"])
    service = repo / manifest["device_service"]["path"]
    if digest(service / "device_service.py") != manifest["device_service"]["sha256"]:
        raise ValueError("Companion service checksum mismatch")
    files = {path: digest(repo / path) for path in RECIPE_PATHS}
    files.update({str(path.relative_to(repo)): digest(path)
                  for path in sorted(service.glob("*.py")) if not path.name.startswith("test_")})
    inputs = {"schema_version": 1, "source": source, "recipe_files": files}
    return {"input_fingerprint": object_digest(inputs), "inputs": inputs}


def source_snapshot(source, repo=REPO):
    """Record the actual edited source files, including uncommitted local changes."""
    manifest = json.loads((repo / "bundles/native-ui/manifest.json").read_text())
    names = set(manifest["source"]["font_sources"])
    for patch in manifest["source"]["patches"]:
        previous = None
        target = None
        for line in (repo / patch["path"]).read_text().splitlines():
            if line.startswith("diff --git "):
                fields = shlex.split(line)
                if len(fields) != 4 or not fields[2].startswith("a/") or not fields[3].startswith("b/"):
                    raise ValueError("Invalid Git patch file header: " + line)
                previous, target = fields[2][2:], fields[3][2:]
                # Binary patches have no +++ header. Include their actual target
                # bytes, notably every regenerated runtime CJK font face.
                names.add(target)
            elif line.startswith("deleted file mode ") and target:
                names.discard(target)
            elif line.startswith("rename from ") and previous != target:
                names.discard(previous)
    records = {name: digest(source / name) for name in sorted(names)}
    return object_digest(records)


def record_start(source, output, repo=REPO):
    source = source.resolve()
    record = current_inputs(repo)
    record.update(source_directory=str(source), source_snapshot=source_snapshot(source, repo),
                  started_at_ns=time.time_ns(),
                  started_at_utc=datetime.now(timezone.utc).isoformat())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return record


def verify_build_start(source, binaries, start_file, repo=REPO):
    if not start_file.is_file():
        raise ValueError("Missing build-start record; rebuild with scripts/native-ui/build.sh")
    record = json.loads(start_file.read_text())
    expected = current_inputs(repo)
    if record.get("input_fingerprint") != expected["input_fingerprint"]:
        raise ValueError("Source/build inputs changed since compilation started; rebuild the runtime")
    if record.get("inputs") != expected["inputs"]:
        raise ValueError("Build-start input inventory does not match this checkout")
    if record.get("source_directory") != str(source.resolve()):
        raise ValueError("Build-start record belongs to another source directory")
    if record.get("source_snapshot") != source_snapshot(source, repo):
        raise ValueError("Edited native source changed during the build; rebuild before packaging")
    if (binaries / "helix-screen").stat().st_mtime_ns < record["started_at_ns"]:
        raise ValueError("helix-screen predates this build; refusing to package an old binary")
    return {**expected, "source_snapshot": record["source_snapshot"],
            "started_at_utc": record["started_at_utc"]}


def verify_runtime_inputs(file_manifest, repo=REPO):
    build = file_manifest.get("build", {})
    expected = current_inputs(repo)
    if build.get("input_fingerprint") != expected["input_fingerprint"]:
        raise ValueError("Runtime is missing current source/build provenance; rebuild it before pinning or firmware packaging")
    if build.get("inputs") != expected["inputs"]:
        raise ValueError("Runtime build-input inventory differs from this checkout")
    if not build.get("source_snapshot") or not build.get("started_at_utc"):
        raise ValueError("Runtime is missing its native compilation record")
    return build


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--write-build-start", required=True, type=Path)
    args = parser.parse_args()
    result = record_start(args.source, args.write_build_start)
    print("Recorded native build inputs: " + result["input_fingerprint"])
