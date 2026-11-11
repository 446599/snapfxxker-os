#!/usr/bin/env python3
"""Rebuild the managed package from its checked base and reviewed patches."""
import gzip
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
VERSION = '1.27b-233.20261111-single.3'
OLD_VERSION = '1.27b-233.20261109'
BASE = HERE / 'base' / ('multiace-managed-' + OLD_VERSION + '.tar.gz')
BASE_SHA = 'a18a762e4cb1061fe778bb919311a076bbfdfc445e2bb73bf72b5ae26544fc3f'
EXPECTED = {
    'web/backend/main.py': '6dfc2be594507a670e58d14f16ddc72d4469198a64ad803e8fab8d1678578aef',
    'web/frontend/app.js': 'cd2a25eee524b988fac46cba9fa5d154c45aa63cf0f19f668575c258e6a06a45',
    'web/frontend/index.html': '0cbf6e48ceb23b0a507daa5fd9a0b25b53e9fa43adc8c4bf0276678ffb41b985',
    'klipper/extras/ace.py': 'c4aea9565ccc6041bb24106c6097b8b82762c48d8b086ef4a9f21bd14792787f',
    'klipper/extras/ace_rc522.py': '4ee7e0d0da5c5fd0a1cdf51c2d0ed065181d0b21844a5b52c1baf304258355b3',
    'klipper/extras/filament_feed_ace.py': '6c4ebfb8c1bc7563ac83d46f3db297b41d07ad4d262c2dc9b1c96f570df3dd21',
    'klipper/extras/ace_location_hold.py': '14486341b8cc2a2272f6fdf5719f9837f177c897d56d4740fe8f811059d9e233',
    'klipper/extras/ace_slot_actions.py': '06a0e8f0ee2a17c9a0edab94101862fe9ea5722a60c926262089b84d8093f6c0',
}

def sha(data):
    return hashlib.sha256(data).hexdigest()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=HERE / ('multiace-managed-' + VERSION + '.tar.gz'))
    parser.add_argument('--manifest', type=Path, default=HERE / 'manifest.json')
    args = parser.parse_args()
    if sha(BASE.read_bytes()) != BASE_SHA:
        raise ValueError('managed base SHA mismatch')
    files, modes = {}, {}
    prefix = 'multiace-' + OLD_VERSION + '/'
    with tarfile.open(BASE, 'r:gz') as archive:
        for member in archive:
            if not member.isfile() or not member.name.startswith(prefix):
                raise ValueError('unexpected managed archive member')
            name = member.name[len(prefix):]
            parts = PurePosixPath(name).parts
            if not parts or '..' in parts or name in files:
                raise ValueError('unsafe or duplicate managed archive member')
            files[name] = archive.extractfile(member).read()
            modes[name] = member.mode & 0o777
    with tempfile.TemporaryDirectory(prefix='managed-rfid-') as temp:
        root = Path(temp)
        for name in EXPECTED:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(files[name])
        for patch in sorted((HERE / 'patches').glob('*.patch')):
            subprocess.run(['patch', '-s', '-p1', '--batch', '--forward', '-i', str(patch)],
                           cwd=root, check=True)
        for name, digest in EXPECTED.items():
            data = (root / name).read_bytes()
            if sha(data) != digest:
                raise ValueError('patched source SHA mismatch: ' + name)
            files[name] = data
    source = (HERE / 'sources' / 'measurement_candidate.py').read_bytes()
    if sha(source) != '4886a0720dcdc884f76ed873e60d93ad40b3ed054a1a8477cfe4e4c9742621d1':
        raise ValueError('measurement source SHA mismatch')
    files['klipper/extras/measurement_candidate.py'] = source
    modes['klipper/extras/measurement_candidate.py'] = 0o644
    files['VERSION'] = (VERSION + '\n').encode()
    out = args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('wb') as target:
        with gzip.GzipFile(filename='', mode='wb', fileobj=target, mtime=0, compresslevel=9) as gz:
            with tarfile.open(fileobj=gz, mode='w', format=tarfile.PAX_FORMAT) as archive:
                for name in sorted(files):
                    data = files[name]
                    member = tarfile.TarInfo('multiace-' + VERSION + '/' + name)
                    member.size, member.mode, member.mtime = len(data), modes[name], 0
                    member.uid = member.gid = 0
                    member.uname = member.gname = ''
                    archive.addfile(member, io.BytesIO(data))
    old_manifest = json.loads((HERE / 'base' / 'manifest.json').read_text())
    manifest = dict(old_manifest)
    manifest.update(version=VERSION, base_archive_sha256=BASE_SHA, archive_sha256=sha(out.read_bytes()),
                    files={name:{'sha256':sha(data),'bytes':len(data)} for name,data in sorted(files.items())},
                    patches={p.name:sha(p.read_bytes()) for p in sorted((HERE/'patches').glob('*.patch'))},
                    derived_overrides={
                        'klipper/extras/ace.py':'single-read RFID, queue epoch, safe unload cancel, and route hold',
                        'klipper/extras/ace_rc522.py':'single-cycle RFID handling and bounded measured motion',
                        'klipper/extras/filament_feed_ace.py':'guarded errors and protected manual unload cleanup',
                        'klipper/extras/ace_slot_actions.py':'sensor-ended high-speed ACE ejection and route hold recovery',
                        'klipper/extras/ace_location_hold.py':'persistent RFID uncertain-position route protection',
                        'VERSION':'managed package version',
                    })
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest,indent=2)+'\n')
    print(manifest['archive_sha256'])

if __name__ == '__main__':
    main()
