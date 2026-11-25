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
VERSION = '1.27b-233.20261116-flow.13'
OLD_VERSION = '1.27b-233.20261109'
BASE = HERE / 'base' / ('multiace-managed-' + OLD_VERSION + '.tar.gz')
BASE_SHA = 'a18a762e4cb1061fe778bb919311a076bbfdfc445e2bb73bf72b5ae26544fc3f'
EXPECTED = {'i18n/en.json': '4aea352aa8e08b3684050139842441a301a6ae85ef90ac2b01e42ceaa15c98fa',
 'i18n/de.json': '5aeb1d46dad44a183c8f968d22c8110de7fedaacbfea4672ad6a010247ec0253',
 'i18n/zh.json': '2427167bca4a315aa41b61b3bbdbdd5c979b451e83f69c2aac2f581e44991fb6',
 'klipper/extras/ace_latch_recovery.py': '259a2a5a702b356561b87ff3eadc8e989cf9d8ba2b10099f951c12be7f70d54f',
 'klipper/extras/ace_manual_measure.py': '9e8834d0c5a76a61147d8c2f2684b6e599b440fa6563ccf01a7ec9de261592a8',
 'klipper/extras/ace_manual_remaining.py': '0eb8b8f81fa04190bb413b8e9d70d0e0b4eb2ba0888bee7f38db5788075a647c',
 'klipper/extras/ace.py': '31abd9c63fdea37d9d6d2cfba82dcd8cccacb530faf8a9bdaa63041210deb401',
 'klipper/extras/ace_bg_swap.py': 'ebc4a1e050b0aa0f56cee2bba13ba2d228937661dca6c658f17a0f87039a9567',
 'klipper/extras/ace_location_hold.py': 'bd87b856b096777b01b359acea35341840bc76f96be5d298d401d53e1201da65',
 'klipper/extras/ace_protocol_v2.py': 'fd97691d45fa02b422863a890f5794cb6c73ba1f870dbd00b2b1b38e1c10f9c0',
 'klipper/extras/ace_rc522.py': 'e866db7db2d09b74cdd635eaddb582f4eb4e28e1ead8b2912adc957f40c6959f',
 'klipper/extras/ace_slot_actions.py': 'bc7f6fb0c87faa666e4514fa07e5712848aaf114748040572fe90e1ec3b371ef',
 'klipper/extras/ace_stop_guard.py': '5448a8fcf2f1eabc27e3fa69047435b01d213364c030208ca85b5f8f8a704a98',
 'klipper/extras/filament_feed_ace.py': 'b67f957a47651c0ef6d342a6637fd3abcec2e7768cc058db2938596f15bbe7e4',
 'web/backend/main.py': '18abcaa1c97ce2c3d258580c290818c1d7fcb24e3a8c34aeb680685d8a5e665e',
 'web/frontend/app.js': 'cd2a25eee524b988fac46cba9fa5d154c45aa63cf0f19f668575c258e6a06a45',
 'web/frontend/index.html': '0cbf6e48ceb23b0a507daa5fd9a0b25b53e9fa43adc8c4bf0276678ffb41b985'}

SOURCES = {'ace_filament_options.py': 'b36729a1360f6560eaf1151e75f5dfec47df3340ee11a0de39702f8efc3d6d15',
 'ace_error_records.py': 'bf44562f5ff1461eca1f710586b88b4b84856997f230ded642d020028b5f7790',
 'ace_error_recovery.py': '06fa7340c880ffe8875eefb71a42e0b781aaaec0219ff5d140d4107e88717905',
 'ace_insert_policy.py': 'b9840995d2b6adff54bdd649d5fec9a62a9f7f92f2515858fe7b842f0431a50b',
 'ace_controlled_motion.py': 'fa96e12982fc893a42318b482fedeca63a711217b68e12848d45f551612420e7',
 'ace_path_calibration.py': 'f060a496392c240408d444e1f79167ed6ec303f8944c91be5510b41bd4265b72',
 'ace_path_operations.py': 'b7d781d4123929a6a13e803ec3a4492540b4cfd0a8e87aa40e5eca151b55bf87',
 'ace_path_store.py': '5aa0e90a167ec7ab1baaca8f6a7a55c8b5d11ec776d8b88d79d0da943620e339',
 'measurement_candidate.py': '4886a0720dcdc884f76ed873e60d93ad40b3ed054a1a8477cfe4e4c9742621d1',
 'ace_filament_travel.py': '183db469a63888332962acda30091d4ab7474ebdde73939aeb06e7f491042859'}

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
    for name, digest in SOURCES.items():
        source = (HERE / 'sources' / name).read_bytes()
        if sha(source) != digest:
            raise ValueError('packaged source SHA mismatch: ' + name)
        target = 'klipper/extras/' + name
        files[target] = source
        modes[target] = 0o644
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
