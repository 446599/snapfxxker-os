#!/usr/bin/env python3
"""Apply the U1 source overlay to its pinned HelixScreen checkout."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess


def git(path, *args):
    return subprocess.check_output(
        ['git', '-C', str(path), *args], text=True
    ).strip()


def safe_path(root, name):
    relative = PurePosixPath(name)
    if relative.is_absolute() or not relative.parts or '..' in relative.parts or '.git' in relative.parts:
        raise ValueError(f'Invalid source path: {name}')
    path = root.joinpath(*relative.parts)
    if not path.resolve().is_relative_to(root):
        raise ValueError(f'Path escapes checkout: {name}')
    return path


def apply(checkout, snapshot):
    manifest = json.loads((snapshot / 'manifest.json').read_text(encoding='utf-8'))
    top = Path(git(checkout, 'rev-parse', '--show-toplevel')).resolve()
    if top != checkout or git(checkout, 'rev-parse', 'HEAD') != manifest['base_commit']:
        raise ValueError('Use the HelixScreen commit listed in manifest.json.')
    if git(checkout, 'status', '--porcelain', '--ignore-submodules=all'):
        raise ValueError('Use a clean checkout so the overlay cannot overwrite local changes.')

    for name, pin in manifest['submodules'].items():
        submodule = safe_path(checkout, name)
        if (not submodule.is_dir()
                or Path(git(submodule, 'rev-parse', '--show-toplevel')).resolve() != submodule
                or git(submodule, 'rev-parse', 'HEAD') != pin['commit']):
            raise ValueError(f'Initialize the pinned submodule: {name}')

    sources = (snapshot / 'overlay').resolve()
    copies = []
    for name, metadata in manifest['overlay_files'].items():
        source = safe_path(sources, name)
        destination = safe_path(checkout, name)
        if hashlib.sha256(source.read_bytes()).hexdigest() != metadata['sha256']:
            raise ValueError(f'Overlay hash mismatch: {name}')
        mode = metadata['mode']
        if mode not in (0o644, 0o755):
            raise ValueError(f'Unexpected file mode: {name}')
        copies.append((source, destination, mode))

    removed = [safe_path(checkout, name) for name in manifest['deleted_paths']]
    for path in removed:
        if not path.is_file() and not path.is_symlink():
            raise ValueError(f'Expected tracked file to remove: {path}')

    for source, destination, mode in copies:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(mode)
    for path in removed:
        path.unlink()
    print(f'Applied {len(copies)} files and {len(removed)} deletions.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkout', type=Path)
    args = parser.parse_args()
    apply(args.checkout.resolve(), Path(__file__).resolve().parent)


if __name__ == '__main__':
    main()
