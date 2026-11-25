"""Rebuild the offline MultiACE component from the published flow.15.112 runtime."""

import argparse
import gzip
import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
BASE_VERSION = "1.27b-233.20261122-flow.15.112"
VERSION = "1.27b-233.20261122-flow.15.113"
ARCHIVE = f"multiace-managed-{VERSION}.tar.gz"
BASE_SHA = "0d21c8d2d4b4477c68588c075a68c9b645d3892e72b4b6befa44ccd4f6957b25"
ACE2K_SHA = "6f4a0e38c481fb0fc057031bb05bdb07900d4f9f64a2007d12c80c7ff59d6d18"
FLASHER_SHA = "290a47cc677b9000eaf6d7edc5bcefb8947a0659ca71b5367865560dfc91547b"
V2_SHA = "818200b199c1d121bd7f1919e5ca507a443b650fe0debe3ddaf6d77e3b5d3251"
LICENSE_SHA = "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986"


def check(path, expected):
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"{path}: expected {expected}, got {actual}")


def write_archive(root, output):
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as archive:
                for path in sorted(p for p in root.rglob("*") if p.is_file()):
                    member = tarfile.TarInfo(f"multiace-{VERSION}/{path.relative_to(root)}")
                    member.size = path.stat().st_size
                    member.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                    with path.open("rb") as src:
                        archive.addfile(member, src)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True, help="flow.15.112 managed tar.gz")
    parser.add_argument("--ace2k", type=Path, required=True, help="ace2k-0.16.0-233b.bin")
    parser.add_argument("--flasher", type=Path, required=True, help="v0.16.0 ace2k_flash.pyz")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    check(args.base, BASE_SHA)
    check(args.ace2k, ACE2K_SHA)
    check(args.flasher, FLASHER_SHA)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.output_dir) as temp:
        work = Path(temp)
        with tarfile.open(args.base, "r:gz") as package:
            package.extractall(work, filter="data")
        root = work / f"multiace-{BASE_VERSION}"
        backend = root / "web/backend"
        check(backend / "firmware/ACE2-1.2.04O-insert-policy.bin", V2_SHA)
        subprocess.run(
            ["patch", "--batch", "--forward", "--fuzz=0", "-p1", "-i", str(HERE / "runtime.patch")],
            cwd=root,
            check=True,
        )
        (root / "VERSION").write_text(VERSION + "\n", encoding="ascii")
        shutil.copyfile(args.ace2k, backend / "firmware/ace2k-0.16.0-233b.bin")
        shutil.copyfile(args.flasher, backend / "vendor/ace2k_flash.pyz")
        # Both upstream projects distribute the identical full GPLv3 license text.
        check(root / "LICENSE", LICENSE_SHA)
        shutil.copyfile(root / "LICENSE", backend / "vendor/ace2k-flasher-LICENSE")
        shutil.copyfile(HERE / "ace2k-flasher-NOTICE", backend / "vendor/ace2k-flasher-NOTICE")
        built = work / ARCHIVE
        write_archive(root, built)
        output = args.output_dir / ARCHIVE
        os.replace(built, output)
    print(f"{hashlib.sha256(output.read_bytes()).hexdigest()}  {output}")


if __name__ == "__main__":
    main()
