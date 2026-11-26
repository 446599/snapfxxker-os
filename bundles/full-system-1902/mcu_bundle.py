"""Bundle exactly the cloud-built candidate and the released 233b rollback image.
No device I/O, no automatic flash, no changes to the upstream flash transport.
"""
import hashlib
import json
from pathlib import Path
import runpy
import shutil
import struct
import zlib

VERSIONS = ('0.16.0-233b', '0.16.0-233c')
OLD_SHA = '6f4a0e38c481fb0fc057031bb05bdb07900d4f9f64a2007d12c80c7ff59d6d18'
TRAILER = bytes.fromhex('61a5635a65a5325a')


def digest(data, algorithm='sha256'):
    return hashlib.new(algorithm, data).hexdigest()


def inspect_bundle(source):
    manifest = {}
    dictionaries = []
    for ver in VERSIONS:
        name = f'ace2k-{ver}.bin'
        data = (source / name).read_bytes()
        if len(data) < 20 or data[-8:] != TRAILER:
            raise ValueError(f'{name}: invalid image trailer')
        if struct.unpack_from('<I', data, len(data)-12)[0] != zlib.crc32(data[:-12]):
            raise ValueError(f'{name}: image CRC mismatch')
        dictionary = json.loads((source / f'ace2k-{ver}.dict').read_text())
        dictionaries.append(dictionary)
        config = dictionary['config']
        if config['ACE2K_VERSION'] != ver or int(config['ACE2K_LANES_CONCURRENT']) != 1:
            raise ValueError(f'{name}: wrong version or parallel-lane capability')
        item = {'version': ver, 'sha256': digest(data), 'md5': digest(data, 'md5'),
                'size': len(data), 'hardware_verified': ver == VERSIONS[0]}
        if ver == VERSIONS[0] and item['sha256'] != OLD_SHA:
            raise ValueError('233b rollback image does not match released bytes')
        manifest[name] = item
    # The same command/response vocabulary, MCU target and timing as 233b.
    old, new = dictionaries
    for key in ('commands', 'responses'):
        if set(old[key]) != set(new[key]):
            raise ValueError(f'MCU protocol {key} changed')
    for key in ('MCU', 'CLOCK_FREQ', 'ACE2K_LANES_CONCURRENT'):
        if old['config'][key] != new['config'][key]:
            raise ValueError(f'MCU config {key} changed')
    return manifest


def bundle(provider, source):
    manifest = inspect_bundle(source)
    backend = provider / 'web/backend'
    flasher = backend / 'ace2k_flasher.py'
    firmware = backend / 'firmware'
    firmware.mkdir(exist_ok=True)
    original = runpy.run_path(str(flasher))
    pins = {}
    for name, item in manifest.items():
        version = item['version']
        image = original['classify_image'](str(source / name))
        if image.get('kind') != 'ace2k' or image.get('version') != version:
            raise ValueError(f'{name}: embedded dictionary does not match')
        target = firmware / name
        if target.exists() and target.read_bytes() != (source / name).read_bytes():
            raise ValueError(f'{name}: conflicting existing image')
        shutil.copy2(source / name, target)
        pins[version] = {'md5': item['md5'],
                         'tested': ('233b released rollback' if version == VERSIONS[0]
                                    else '233c CI-tested candidate; hardware test pending')}
    # Keep the upstream upload gate and existing release pins. New entries
    # have no downloadable SHA because they are not tobecwb release assets.
    with flasher.open('a', encoding='utf-8') as f:
        f.write('\n# 233 local images, pinned by the CI build. Upload only.\n')
        f.write('KNOWN_ACE2K.update(' + repr(pins) + ')\n')
    patched = runpy.run_path(str(flasher))
    for name, item in manifest.items():
        image = patched['classify_image'](str(firmware / name))
        patched['check_known'](image)
        if patched['downloadable'](item['version']):
            raise ValueError('local image must be upload-only')
        image['md5'] = '0' * 32
        try:
            patched['check_known'](image)
        except patched['FlasherError']:
            pass
        else:
            raise ValueError('wrong image bytes were accepted')
    (firmware / '233-ace2k-images.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return manifest
