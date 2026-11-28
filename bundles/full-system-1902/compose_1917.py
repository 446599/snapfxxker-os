"""Repack the pinned 1902.16 firmware with automatic park capture."""
import argparse
import json
from pathlib import Path
import tarfile
import tempfile

from compose_1916 import repin, require, sha
from patch_thermal_park import patch_engine, patch_feed

BASE_VERSION = '2.1.2-233-1902.16'
BASE_COMMIT = 'aa8a76573b11f910d3a37de0e9bba2d57fc4c987'
VERSION = '2.1.2-233-1902.17'
PROVIDER = '1.90.2-233.20261126-paxx.17'
SOURCE = Path(__file__).resolve().parent.parent / 'ace2k-native-slot-exit'
HOME = 'klipper/extras/'


def patch_provider(provider):
    extras = provider / HOME
    for name, patch in (('ace.py', patch_engine), ('filament_feed_ace.py', patch_feed)):
        file = extras / name
        file.write_text(patch(file.read_text()))
    for name in ('material_batch_runtime.py', 'park_return.py'):
        source = (SOURCE / name).read_text()
        source = source.replace('from host_native_exit import', 'from .host_native_exit import')
        source = source.replace('from park_return import', 'from .park_return import')
        compile(source, name, 'exec')
        (extras / name).write_text(source)


def compose(root, previous, output, commit):
    report = json.loads(previous.read_text())
    require(report['firmware_version'] == BASE_VERSION
            and report['integration_commit'] == BASE_COMMIT
            and (root / 'etc/FULLVERSION').read_text().strip() == BASE_VERSION,
            'expected the verified 1902.16 image')
    for path, digest in report['runtime_file_sha256'].items():
        require(sha(root / path) == digest, 'base runtime mismatch: ' + path)
    for key in ('screen', 'provider'):
        require(sha(root / report[key + '_archive']) == report[key + '_sha256'],
                'base archive mismatch: ' + key)
    old = root / report['provider_archive']
    new = old.with_name('multiace-managed-' + PROVIDER + '.tar.gz')
    require(not new.exists(), 'new provider already exists')
    with tempfile.TemporaryDirectory(prefix='1917-provider-') as temp:
        work = Path(temp)
        with tarfile.open(old, 'r:gz') as archive:
            archive.extractall(work, filter='data')
        roots = list(work.iterdir())
        require(len(roots) == 1 and roots[0].is_dir(), 'unexpected provider root')
        provider = roots[0]
        for path, digest in report['route_file_sha256'].items():
            require(sha(provider / path) == digest, 'base provider mismatch: ' + path)
        patch_provider(provider)
        (provider / 'VERSION').write_text(PROVIDER + '\n')
        for path in report['required_files']:
            require((provider / path).is_file(), 'missing provider runtime: ' + path)
        route_hashes = {path: sha(provider / path) for path in report['route_file_sha256']}
        with tarfile.open(new, 'w:gz', compresslevel=6) as archive:
            archive.add(provider, arcname='multiace-' + PROVIDER)

    new_archive = str(new.relative_to(root))
    new_sha = sha(new)
    repin(root, 'multiace', report['provider'], PROVIDER, report['provider_archive'],
          new_archive, report['provider_sha256'], new_sha)
    old.unlink()
    (root / 'etc/FULLVERSION').write_text(VERSION + '\n')
    for path in ('etc/FULLVERSION', 'usr/local/share/extended-pkg/multiace'):
        report['runtime_file_sha256'][path] = sha(root / path)
    report.update(firmware_version=VERSION, integration_commit=commit, provider=PROVIDER,
                  provider_archive=new_archive, provider_sha256=new_sha,
                  engine_sha256=route_hashes[HOME + 'ace.py'], route_file_sha256=route_hashes,
                  hardware_verified=False, scope='Manual ACE2K Multi load records the park origin; '
                  'ordinary head unload returns to it with native encoder and MCU terminal evidence.')
    output.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'previous', 'output'):
        parser.add_argument(name, type=Path)
    parser.add_argument('commit')
    args = parser.parse_args()
    compose(args.root, args.previous, args.output, args.commit)
