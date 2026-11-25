#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Extract the recorded Debian cross toolchain and static providers without installing them."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import urllib.error
import urllib.request

REPO = Path(__file__).resolve().parents[2]


def prepare(output, cache):
    records = json.loads((REPO / "bundles/native-ui/cross-packages.json").read_text())
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    for item in records:
        package = cache / Path(item["Filename"]).name
        if not package.exists():
            errors = []
            for server in ("https://deb.debian.org/debian/", "https://deb.debian.org/debian-security/"):
                try:
                    with urllib.request.urlopen(server + item["Filename"], timeout=60) as response:
                        content = response.read()
                    if hashlib.sha256(content).hexdigest() != item["SHA256"]:
                        raise ValueError("Downloaded package checksum mismatch: " + item["Package"])
                    package.write_bytes(content)
                    break
                except urllib.error.HTTPError as error:
                    errors.append(str(error))
            else:
                raise RuntimeError("Pinned Debian package unavailable; provide it in --cache: "
                                   + package.name + ": " + "; ".join(errors))
        if hashlib.sha256(package.read_bytes()).hexdigest() != item["SHA256"]:
            raise ValueError("Cached package checksum mismatch: " + package.name)
        subprocess.run(["dpkg-deb", "-x", str(package), str(output)], check=True)
    wrappers = output.parent / "toolchain/bin"
    wrappers.mkdir(parents=True, exist_ok=True)
    for executable in (output / "usr/bin").glob("aarch64-linux-gnu-*"):
        if not executable.is_file():
            continue
        options = ""
        if executable.name in ("aarch64-linux-gnu-gcc", "aarch64-linux-gnu-g++", "aarch64-linux-gnu-cpp"):
            options = " --sysroot=" + shlex.quote(str(output)) + \
                      " -I" + shlex.quote(str(output / "usr/include/libdrm")) + \
                      " -L" + shlex.quote(str(output / "usr/lib/aarch64-linux-gnu"))
        wrapper = wrappers / executable.name
        wrapper.write_text("#!/bin/sh\nexec " + shlex.quote(str(executable)) + options + ' "$@"\n')
        wrapper.chmod(0o755)
    print("Recorded Debian toolchain extracted to " + str(output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cache", required=True, type=Path)
    args = parser.parse_args()
    prepare(args.output.resolve(), args.cache.resolve())
