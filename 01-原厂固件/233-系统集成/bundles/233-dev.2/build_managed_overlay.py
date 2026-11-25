#!/usr/bin/env python3
"""Create a deterministic, allowlisted managed payload from verified live code.

Runtime configuration, state, backups and caches are never read as payload.
"""
import argparse
import gzip
import hashlib
import io
import json
import re
import tarfile
from pathlib import Path, PurePosixPath

VERSION = '1.27b-233.20261109'
FRONTEND_EXT = {'.js', '.css', '.html', '.svg', '.webmanifest', '.png', '.woff', '.woff2'}
BACKEND_FILES = {'main.py', 'preflight_core.py', 'i18n_path.py', 'ace1_flash.py',
                 'ace2_ota.py', 'ace2_open_60_patch.json', 'requirements.txt'}


def permitted(name):
    path = PurePosixPath(name)
    parts = path.parts
    if (path.is_absolute() or '..' in parts or '__pycache__' in parts or
        any(part.startswith('.') and part != '.paxx-dependencies-ready' for part in parts) or
        any('.pre-' in part or '.bak' in part.lower() for part in parts) or
        path.suffix.lower() in ('.pyc', '.pyo', '.log')):
        return False
    if len(parts) == 3 and parts[:2] == ('klipper', 'extras'):
        return path.suffix == '.py' and path.name != 'measurement_candidate.py'
    if len(parts) == 3 and parts[:2] == ('klipper', 'kinematics'):
        return path.name == 'extruder_ace.py'
    if len(parts) == 2 and parts[0] == 'i18n':
        return path.name in ('de.json', 'en.json', 'zh.json')
    if name == 'tools/post_process_virtual_toolheads.py':
        return True
    if len(parts) == 3 and parts[:2] == ('web', 'backend'):
        return path.name in BACKEND_FILES
    if parts[:3] == ('web', 'backend', 'vendor'):
        return True
    if parts[:2] == ('web', 'frontend'):
        return path.suffix.lower() in FRONTEND_EXT
    return name == 'LICENSE'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--live-tar', required=True, type=Path)
    ap.add_argument('--deployed-manifest', required=True, type=Path)
    ap.add_argument('--provider', required=True, type=Path)
    ap.add_argument('--output', required=True, type=Path)
    args = ap.parse_args()
    files = {}
    modes = {}
    with tarfile.open(args.live_tar, 'r:gz') as src:
        for member in src:
            name = member.name.removeprefix('./')
            if not member.isfile() or not permitted(name):
                continue
            if name in files:
                raise ValueError('duplicate payload file: ' + name)
            handle = src.extractfile(member)
            if handle is None:
                raise ValueError('unreadable payload file: ' + name)
            files[name] = handle.read()
            modes[name] = 0o755 if member.mode & 0o111 else 0o644
    deployed = json.loads(args.deployed_manifest.read_text())
    for name, info in deployed.items():
        actual = hashlib.sha256(files.get(name, b'')).hexdigest()
        if actual != info['sha256']:
            raise ValueError('live/deployed mismatch: ' + name)
    # The installed 1.27b tree was hard-coded in the live bootstrap. The
    # managed package installs under a new versioned name, so resolve latest.
    entry = 'klipper/extras/ace.py'
    old = b'/oem/apps/multiace/multiace-1.27b/klipper/extras'
    new = b'/oem/apps/multiace/latest/klipper/extras'
    if files[entry].count(old) != 1:
        raise ValueError('unexpected live bootstrap path')
    files[entry] = files[entry].replace(old, new, 1)
    for name in ('config/extended/ace.cfg', 'config/extended/multiace/ace_vars.cfg'):
        files[name] = (args.provider / name).read_bytes()
        modes[name] = 0o644
    # Match managed package contract: remove sections controlled by host.
    text = files['config/extended/ace.cfg'].decode()
    excluded = {'save_variables', 'gcode_macro ACEH__Update_Check', 'gcode_macro ACEH__Update_Apply'}
    keep, skipping = [], False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            skipping = stripped[1:-1] in excluded
        if not skipping:
            keep.append(line)
    files['config/extended/ace.cfg'] = ''.join(keep).encode()
    files['VERSION'] = (VERSION + '\n').encode()
    modes['VERSION'] = 0o644
    required = {'klipper/extras/ace.py', 'klipper/extras/ace_rc522.py',
                'klipper/extras/ace_manual_measure.py', 'web/backend/requirements.txt',
                'web/backend/vendor/.paxx-dependencies-ready', 'web/frontend/index.html', 'LICENSE'}
    if not required.issubset(files):
        raise ValueError('missing required payload: ' + repr(sorted(required - files.keys())))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('wb') as target:
        with gzip.GzipFile(filename='', mode='wb', fileobj=target, mtime=0, compresslevel=9) as zipped:
            with tarfile.open(fileobj=zipped, mode='w', format=tarfile.PAX_FORMAT) as out:
                for name in sorted(files):
                    data = files[name]
                    entry = tarfile.TarInfo('multiace-' + VERSION + '/' + name)
                    entry.size, entry.mode, entry.mtime = len(data), modes[name], 0
                    entry.uid = entry.gid = 0
                    entry.uname = entry.gname = ''
                    out.addfile(entry, io.BytesIO(data))
    manifest = {
        'version': VERSION,
        'live_snapshot_sha256': hashlib.sha256(args.live_tar.read_bytes()).hexdigest(),
        'archive_sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(),
        'files': {k: {'sha256': hashlib.sha256(v).hexdigest(), 'bytes': len(v)}
                  for k, v in sorted(files.items())},
        'derived_overrides': {'klipper/extras/ace.py': 'one path change: versioned directory to latest symlink'},
        'vendor_source': 'captured running multiACE vendor; CPython 3.11 aarch64',
        'vendor_uncompressed_bytes': sum(len(v) for k, v in files.items() if k.startswith('web/backend/vendor/')),
        'vendor_distributions': sorted({PurePosixPath(k).parts[3] for k in files
                                        if k.startswith('web/backend/vendor/') and len(PurePosixPath(k).parts) > 3
                                        and PurePosixPath(k).parts[3].endswith('.dist-info')}),
        'excluded': ['live configuration', 'printer data', 'spool database', 'logs', 'backups', 'bytecode caches', 'private keys'],
    }
    args.output.with_suffix(args.output.suffix + '.manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'sha256': manifest['archive_sha256'], 'files': len(files),
                      'vendor_uncompressed_bytes': manifest['vendor_uncompressed_bytes']}))

if __name__ == '__main__':
    main()
