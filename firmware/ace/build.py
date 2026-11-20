#!/usr/bin/env python3
"""Build the existing ACE2-Open image offline; no serial connection or flashing."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--base', type=Path, help='User supplied stock ACE2 V1.1.31 .bin/.swu')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/ACE2-Open-1.1.60O.bin')
    args = parser.parse_args()
    manifest = json.loads((HERE / 'manifest.json').read_text())
    if args.plan:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return
    if args.base is None:
        parser.error('--base is required; the repository does not contain the stock MCU image')
    spec = importlib.util.spec_from_file_location('ace2_image_builder', ROOT / manifest['image_library'])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        base = module.load_image(str(args.base), manifest['base_version'])
        image = module.apply_open_patch(base.data, manifest['target'])
        module.check_known(image)
    except (OSError, module.FlashError) as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(image.data)
    digest = hashlib.sha256(image.data).hexdigest()
    args.output.with_suffix(args.output.suffix + '.sha256').write_text(digest + '  ' + args.output.name + '\n')
    print(json.dumps({'component': 'ace', 'version': manifest['target'],
                      'bytes': len(image.data), 'sha256': digest, 'flashed': False}))


if __name__ == '__main__':
    main()
