#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the multiACE application without changing firmware or a printer."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import tarfile


REPO = Path(__file__).resolve().parents[2]
RUNTIME = Path(__file__).resolve().parent / "runtime"
DEPENDENCY_SOURCE = "deliverables/u1-ace/source-20261116/multiace"
SNAPSHOT = REPO / DEPENDENCY_SOURCE
DEPENDENCY_MANIFEST_SHA256 = "ca0cccdbbf7037f82e6aeddb353640d560e582a67d3f36898d6267fd5317eee2"
SOURCE_COMMITS = {
    "main": "629555c33ecd569f68bfd8e87d31d28b437df892",
    "flow15": "a887123946320767db44e8848602651f3f60b37a",
}
VENDOR_PREFIX = "web/backend/vendor/"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_version():
    version = (RUNTIME / "VERSION").read_text().strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", version):
        raise ValueError("runtime/VERSION must contain a single package version")
    return version


def read_file(path):
    if path.is_symlink() and not path.resolve().is_relative_to(REPO.resolve()):
        raise ValueError("Linked package source must stay inside the repository: " + str(path))
    return path.read_bytes(), 0o755 if path.stat().st_mode & 0o111 else 0o644


def add_dependencies(files):
    raw_manifest = (SNAPSHOT / "manifest.json").read_bytes()
    if sha256(raw_manifest) != DEPENDENCY_MANIFEST_SHA256:
        raise ValueError("Pinned dependency manifest SHA256 mismatch")
    records = {
        name: record for name, record in json.loads(raw_manifest)["files"].items()
        if name.startswith(VENDOR_PREFIX)
    }
    if not records:
        raise ValueError("Pinned dependency manifest has no web/backend/vendor files")
    missing = [name for name in sorted(records)
               if not (SNAPSHOT / "runtime" / name).is_file()]
    if missing:
        raise FileNotFoundError(
            "Missing %d pinned dependency files (first: %s). Restore %s/runtime/"
            "web/backend/vendor from the source snapshot, or use --code-only for an"
            " update that requires dependencies already installed."
            % (len(missing), missing[0], DEPENDENCY_SOURCE))
    for name, record in sorted(records.items()):
        data, mode = read_file(SNAPSHOT / "runtime" / name)
        if len(data) != record["bytes"] or sha256(data) != record["sha256"]:
            raise ValueError("Pinned dependency file SHA256/size mismatch: " + name)
        files[name] = data, mode


def build(output, *, code_only=False):
    output = Path(output)
    if output.resolve().is_relative_to(RUNTIME.resolve()):
        raise ValueError("Output must be outside runtime/ to avoid packaging prior outputs")
    version = read_version()
    # Require the main license even if a caller has an incomplete checkout.
    (RUNTIME / "LICENSE").read_bytes()
    files = {}
    for path in sorted(RUNTIME.rglob("*")):
        name = path.relative_to(RUNTIME).as_posix()
        if ("__pycache__" in path.parts or path.suffix in (".pyc", ".pyo")
                or name.startswith(VENDOR_PREFIX)):
            continue
        if path.is_symlink() and not path.is_file():
            raise ValueError("Linked package source is missing or not a file: " + name)
        if path.is_file() and name != "PACKAGE.json":
            files[name] = read_file(path)
    if not code_only:
        # Only dependencies come from the historical snapshot. Its older ACE
        # source, web backend and configuration must never replace this runtime.
        add_dependencies(files)
    metadata = {
        "schema": 1,
        "package": "multiace",
        "version": version,
        "package_kind": "code-only-update" if code_only else "complete-managed-application",
        "includes_dependencies": not code_only,
        "requires_existing_dependencies": code_only,
        "hardware_tested": False,
        "source_commits": SOURCE_COMMITS,
        "runtime_source": "packages/multiace/runtime",
        "dependency_source": DEPENDENCY_SOURCE,
        "dependency_manifest_sha256": DEPENDENCY_MANIFEST_SHA256,
        "files": {
            name: {"sha256": sha256(data), "bytes": len(data), "mode": oct(mode)}
            for name, (data, mode) in sorted(files.items())
        },
    }
    files["PACKAGE.json"] = (
        (json.dumps(metadata, sort_keys=True, indent=2) + "\n").encode(), 0o644)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as stream:
        with gzip.GzipFile(filename="", fileobj=stream, mode="wb", mtime=0, compresslevel=9) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for name, (data, mode) in sorted(files.items()):
                    member = tarfile.TarInfo("multiace-" + version + "/" + name)
                    member.size, member.mode, member.mtime = len(data), mode, 0
                    member.uid = member.gid = 0
                    member.uname = member.gname = ""
                    archive.addfile(member, io.BytesIO(data))
    digest = sha256(output.read_bytes())
    output.with_name(output.name + ".sha256").write_text(digest + "  " + output.name + "\n")
    return metadata, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code-only", action="store_true",
                        help="omit Python dependencies; requires an existing dependency installation")
    parser.add_argument("--output", type=Path, help="archive path (default: repository dist/)")
    args = parser.parse_args()
    try:
        prefix = "multiace-code-" if args.code_only else "multiace-managed-"
        output = args.output or REPO / "dist" / (prefix + read_version() + ".tar.gz")
        metadata, digest = build(output, code_only=args.code_only)
    except (OSError, ValueError, KeyError, tarfile.TarError) as error:
        print("multiACE package build failed: " + str(error), file=sys.stderr)
        return 1
    print("Created " + str(output))
    print("Package kind: " + metadata["package_kind"])
    print("Files: %d; includes dependencies: %s; hardware tested: false"
          % (len(metadata["files"]), str(metadata["includes_dependencies"]).lower()))
    print("SHA256 " + digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
