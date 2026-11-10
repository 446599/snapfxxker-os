#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Restore the exact first-party native UI source from a fixed upstream and patches."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import urllib.error
import urllib.request

REPO = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def restore_font_sources(output, source):
    inventory_record = source["third_party_manifest"]
    inventory_path = REPO / inventory_record["path"]
    if digest(inventory_path) != inventory_record["sha256"]:
        raise ValueError("Third-party source manifest checksum mismatch")
    fonts = {item["file"]: item for item in json.loads(inventory_path.read_text())["font_assets"]}
    for name, expected in source["font_sources"].items():
        target = output / name
        if not target.is_file():
            record = fonts[name]
            if record["sha256"] != expected:
                raise ValueError("Source font manifests disagree: " + name)
            commit = record["source_commit"]
            prefix = "https://github.com/notofonts/noto-cjk/blob/" + commit + "/"
            if len(commit) != 40 or not record["source_url"].startswith(prefix):
                raise ValueError("Source font URL must use its pinned commit: " + name)
            url = "https://raw.githubusercontent.com/notofonts/noto-cjk/" + commit + "/" + \
                record["source_url"][len(prefix):]
            try:
                with urllib.request.urlopen(url, timeout=60) as response:
                    content = response.read()
            except (urllib.error.URLError, TimeoutError) as error:
                raise RuntimeError("Unable to download pinned source font " + name + ": " + url) from error
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError("Downloaded source font checksum mismatch: " + name)
            target.write_bytes(content)
        if digest(target) != expected:
            raise ValueError("Source font checksum mismatch: " + name)


def prepare(output, no_submodules=False):
    manifest = json.loads((REPO / "bundles/native-ui/manifest.json").read_text())
    source = manifest["source"]
    for patch in source["patches"]:
        if digest(REPO / patch["path"]) != patch["sha256"]:
            raise ValueError("Patch checksum mismatch: " + patch["path"])
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty source output directory: " + str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout",
                    source["repository"], str(output)], check=True)
    def git(*arguments):
        return subprocess.check_output(["git", "-C", str(output), *arguments], text=True).strip()
    git("checkout", "--detach", source["commit"])
    if git("rev-parse", "HEAD") != source["commit"]:
        raise ValueError("Wrong upstream commit")
    for module in source["submodules"]:
        actual = git("ls-tree", "HEAD", module["path"]).split()[2]
        if actual != module["commit"]:
            raise ValueError("Unexpected submodule pin: " + module["path"])
    for patch in source["patches"]:
        git("apply", "--check", str(REPO / patch["path"]))
        git("apply", str(REPO / patch["path"]))
    # Upstream intentionally ignores the large CJK source OTFs. Fetch the exact
    # reviewed font revision before checking or rebuilding generated glyphs.
    restore_font_sources(output, source)
    if not no_submodules:
        git("submodule", "update", "--init", "--recursive")
    service = REPO / manifest["device_service"]["path"]
    if digest(service / "device_service.py") != manifest["device_service"]["sha256"]:
        raise ValueError("Companion service checksum mismatch")
    sibling = output.parent / "device-service"
    if sibling.exists():
        raise ValueError("Refusing to replace an existing sibling device-service directory")
    shutil.copytree(service, sibling, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (output.parent / "source-provenance.json").write_text(json.dumps({
        "upstream": source, "device_service": manifest["device_service"],
        "submodules_initialized": not no_submodules,
        "fonts": "Regenerate with the locked lv_font_conv before packaging",
    }, ensure_ascii=False, indent=2) + "\n")
    print("Native UI source restored: " + str(output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--no-submodules", action="store_true",
                        help="Validate source patches and pins only; not a compilable tree")
    args = parser.parse_args()
    prepare(args.output.resolve(), args.no_submodules)
