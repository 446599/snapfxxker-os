#!/usr/bin/env python3
"""Verify and optionally copy the exact stock image; never patch or flash it."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--input', type=Path, help='Original stock .bin file')
    parser.add_argument('--output', type=Path, help='Optional byte-identical copy')
    args = parser.parse_args()
    manifest = json.loads((HERE / 'manifest.json').read_text())
    if args.plan:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return
    if args.input is None:
        parser.error('--input is required; obtain the original file from the manifest URL')
    with args.input.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != manifest['sha256']:
        parser.error('This is not the pinned stock image (SHA-256 mismatch)')
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.output.resolve() != args.input.resolve():
            shutil.copyfile(args.input, args.output)
    print(json.dumps({'component': 'stock', 'version': manifest['version'],
                      'sha256': digest, 'modified': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
