#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Wrap the existing screen build, or archive its source inputs without compiling."""
import argparse
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile


REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / 'packages/screen/manifest.json'
SKIP_PARTS = {'.git', '__pycache__', 'node_modules'}


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def source_files(manifest):
    excluded = manifest['generated_inputs_excluded']
    files = {}
    for name in manifest['source_roots']:
        root = REPO / name
        if not root.exists():
            raise FileNotFoundError('Missing screen source input: ' + name)
        for path in sorted(root.rglob('*')) if root.is_dir() else [root]:
            relative = path.relative_to(REPO).as_posix()
            if (not path.is_file() or set(path.parts) & SKIP_PARTS
                    or path.suffix in ('.pyc', '.pyo')
                    or any(relative == item or relative.startswith(item + '/') for item in excluded)):
                continue
            files[relative] = path
    return files


def build_inputs():
    # Reuse the existing patch/service/recipe inventory. This does not inspect
    # an old binary pin or require the unrelated multiACE firmware archive.
    path = REPO / 'scripts/native-ui/build_inputs.py'
    spec = importlib.util.spec_from_file_location('_screen_build_inputs', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.current_inputs(REPO)


def write_archive(output, root_name, files, package):
    records = {}
    payload = {}
    for name, path in sorted(files.items()):
        content = path.read_bytes()
        mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
        payload[name] = (content, mode)
        records[name] = {'sha256': hashlib.sha256(content).hexdigest(),
                         'bytes': len(content), 'mode': oct(mode)}
    package = {**package, 'files': records}
    payload['PACKAGE.json'] = (encoded(package), 0o644)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('wb') as stream:
        with gzip.GzipFile(filename='', mode='wb', fileobj=stream, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as archive:
                for name, (content, mode) in sorted(payload.items()):
                    info = tarfile.TarInfo(root_name + '/' + name)
                    info.size, info.mode, info.mtime = len(content), mode, 0
                    archive.addfile(info, io.BytesIO(content))
    result = {'archive': output.name,
              'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
              'bytes': output.stat().st_size, **package}
    output.with_suffix(output.suffix + '.manifest.json').write_bytes(encoded(result))
    return result


def plan(manifest, output, source_only):
    return {
        'id': manifest['id'], 'layer': 'screen',
        'mode': 'source-only' if source_only else 'arm64-build',
        'output': str(output / manifest['artifacts']['source' if source_only else 'arm64']),
        'build_command': None if source_only else
            ['bash', str(REPO / manifest['build_entry']), str(output / 'native-build')],
        'runtime_packager': manifest['runtime_packager'],
        'companion_overlay': manifest['companion_overlay'],
        'external_build_inputs': manifest['external_build_inputs'],
        'source_only_is_runnable': False,
        'directly_flashable': False,
        'inactive_reference': manifest['inactive_reference'],
        'missing_source_roots': [name for name in manifest['source_roots'] if not (REPO / name).exists()],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', action='store_true', help='Print the build plan without downloading or writing files')
    parser.add_argument('--source-only', action='store_true', help='Archive patches, service, overlay and build inputs; no ARM binary')
    parser.add_argument('--output-dir', type=Path, default=REPO / 'dist/screen')
    args = parser.parse_args()
    try:
        manifest = json.loads(MANIFEST.read_text())
        output = args.output_dir.resolve()
        if args.plan:
            print(json.dumps(plan(manifest, output, args.source_only), ensure_ascii=False, indent=2))
            return
        files = source_files(manifest)
        inputs = build_inputs()
        subprocess.run([sys.executable, str(REPO / 'scripts/native-ui/prepare_ui5_reconstruction.py'),
                        '--check'], check=True)
        package = {
            'id': manifest['id'], 'layer': 'screen',
            'kind': 'source-only' if args.source_only else 'arm64-runtime-with-companion',
            'arm64_runtime_built': not args.source_only,
            'upstream_source_tree_included': False,
            'external_build_inputs': manifest['external_build_inputs'],
            'input_fingerprint': inputs['input_fingerprint'],
            'directly_flashable': False,
            'companion_overlay': manifest['companion_overlay'],
            'inactive_reference': manifest['inactive_reference'],
        }
        if not args.source_only:
            # build.sh requires a new empty work directory and invokes the
            # existing package_runtime.py provenance/AArch64 checks itself.
            work = output / 'native-build'
            subprocess.run(['bash', str(REPO / manifest['build_entry']), str(work)], check=True)
            runtime = work / manifest['artifacts']['inner_runtime']
            inventory = runtime.with_suffix(runtime.suffix + '.manifest.json')
            recorded = json.loads(inventory.read_text())
            files['runtime/' + runtime.name] = runtime
            files['runtime/' + inventory.name] = inventory
            package['runtime_sha256'] = recorded['sha256']
            package['runtime_build'] = recorded['build']
        name = manifest['artifacts']['source' if args.source_only else 'arm64']
        result = write_archive(output / name, 'screen-source' if args.source_only else 'screen-arm64', files, package)
        print(json.dumps({key: result[key] for key in
                          ('archive', 'sha256', 'bytes', 'kind', 'arm64_runtime_built', 'input_fingerprint')},
                         ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, 'Screen package failed: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
