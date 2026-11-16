#!/usr/bin/env python3
"""Build the reproducible Flow15 module update from the pinned Flow13 tree.

No network access, activation, service restart or machine command is performed.
The update contains the complete Flow14 changes plus the Flow15 changes.
"""
import argparse
import ast
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile


ROOT = Path(__file__).resolve().parent
VERSION = '1.27b-233.20261117-flow.15'
SOURCE_COMMIT = 'adb1dda5935c227ab1ba40d5bd842763ea0921ba'


def digest(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()


def collect(base_runtime, base_device_service):
    base_manifest = json.loads((ROOT / 'BASE-MANIFEST.json').read_text())
    baseline_files = base_manifest['files']
    if base_manifest['version'] != '1.27b-233.20261116-flow.13':
        raise ValueError('The source baseline is not Flow13')
    payload = {}
    entries = {}
    for subtree, baseline in [('runtime', base_runtime), ('device-service', base_device_service)]:
        for path in sorted((ROOT / subtree).rglob('*')):
            if not path.is_file() or '__pycache__' in path.parts or path.suffix == '.pyc':
                continue
            relative = path.relative_to(ROOT).as_posix()
            inner = path.relative_to(ROOT / subtree).as_posix()
            data = path.read_bytes()
            if path.suffix == '.py':
                ast.parse(data, filename=relative)
            expected = (baseline_files.get(inner) if subtree == 'runtime' else
                        {'bytes': 8556, 'sha256': '8089485422cdddc8dcadfa58ef7b3971712b4e6481b2b772e829d1aad8579a94'}
                        if inner == 'insert_rfid_settings.py' else None)
            original = baseline / inner
            if expected is not None:
                if not original.is_file() or digest(original.read_bytes()) != {
                        'bytes': expected['bytes'], 'sha256': expected['sha256']}:
                    raise ValueError('Baseline hash mismatch: ' + str(original))
            elif original.exists():
                raise ValueError('New module already exists in the pinned baseline: ' + str(original))
            payload[relative] = data
            entries[relative] = dict(digest(data), mode=0o644, baseline=expected)
    if payload['runtime/VERSION'].decode().strip() != VERSION:
        raise ValueError('runtime/VERSION does not match the build version')
    # All sibling imports added by this overlay must resolve in the overlay or
    # the pinned source tree. Third-party and printer-native modules stay with
    # the existing platform; this archive never represents a complete firmware.
    extras = ROOT / 'runtime/klipper/extras'
    base_extras = base_runtime / 'klipper/extras'
    for path in extras.glob('*.py'):
        for node in ast.walk(ast.parse(path.read_bytes())):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                name = node.module.split('.')[0]
                if name.startswith('ace_') or name == 'measurement_candidate':
                    if not (extras / (name + '.py')).exists() and not (base_extras / (name + '.py')).exists():
                        raise ValueError('Missing local dependency: ' + name)
    return payload, entries, base_manifest


def build(base_runtime, base_device_service, output):
    payload, entries, base_manifest = collect(base_runtime, base_device_service)
    baseline = {'version': base_manifest['version'],
                'archive_sha256': base_manifest['archive_sha256'],
                'manifest_sha256': hashlib.sha256((ROOT / 'BASE-MANIFEST.json').read_bytes()).hexdigest()}
    overlay = {'schema_version': 1, 'version': VERSION, 'source_commit': SOURCE_COMMIT,
               'base': baseline, 'files': entries}
    overlay_data = json_bytes(overlay)
    package = {'schema_version': 1, 'version': VERSION, 'source_commit': SOURCE_COMMIT,
               'package_kind': 'module-update', 'mode': 'update',
               'complete_runtime': False, 'is_printer_firmware': False,
               'runtime_path': 'runtime/', 'device_service_path': 'device-service/',
               'base': baseline, 'baseline_requirements_verified': True,
               'base_runtime_verified': False,
               'base_runtime_file_count': len(base_manifest['files']),
               'baseline_requirements': {key: value['baseline'] for key, value in entries.items()},
               'overlay_manifest_sha256': hashlib.sha256(overlay_data).hexdigest(),
               'validation_scope': 'Offline behavioral tests and replaced-baseline hash verification; no hardware validation or installation'}
    # Keep the checked-in manifests identical to the built archive.
    (ROOT / 'OVERLAY-MANIFEST.json').write_bytes(overlay_data)
    (ROOT / 'PACKAGE.json').write_bytes(json_bytes(package))
    for name in ('BASE-MANIFEST.json', 'OVERLAY-MANIFEST.json', 'PACKAGE.json', 'LICENSE', 'README.md', 'VALIDATION.json'):
        payload[name] = (ROOT / name).read_bytes()
    stream = io.BytesIO()
    prefix = 'multiace-' + VERSION
    with tarfile.open(fileobj=stream, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for name, data in sorted(payload.items()):
            info = tarfile.TarInfo(prefix + '/' + name)
            info.size = len(data)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    output.parent.mkdir(parents=True, exist_ok=True)
    result = gzip.compress(stream.getvalue(), compresslevel=9, mtime=0)
    output.write_bytes(result)
    summary = dict(digest(result), version=VERSION, overlay_files=len(entries),
                   baseline_requirements_verified=True, hardware_tested=False)
    output.with_suffix(output.suffix + '.manifest.json').write_bytes(json_bytes(summary))
    output.with_suffix(output.suffix + '.sha256').write_text(summary['sha256'] + '  ' + output.name + '\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-runtime', type=Path,
                        default=ROOT.parent / 'source-20261116/multiace/runtime')
    parser.add_argument('--base-device-service', type=Path,
                        default=ROOT.parent / 'source-20261116/device-service')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.base_runtime, args.base_device_service, args.output), ensure_ascii=False))


if __name__ == '__main__':
    main()
