#!/usr/bin/env python3
"""Check the narrow repair against exact stock and previously verified 60O.

This is an offline image check, not proof of boot or of an actual OTA cycle.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

def sha(data):
    return hashlib.sha256(data).hexdigest()

def main():
    base = (ROOT / 'input/ACE2_V1.1.31_20260306.bin').read_bytes()
    previous = (ROOT / 'output/baseline-local-60O.bin').read_bytes()
    candidate = (HERE / 'output/ACE2-233-V1.1.91O.candidate').read_bytes()
    assert sha(base) == 'a468e366226e7c483d3dd96b444223e89cbf0d1b10573d42b6263843ffd6be3e'
    assert sha(previous) == '35d154abb9b6e037f9c6e10640bdce05fa62e2ecaa47fc5b3b21dac9607e5360'
    assert candidate == (HERE / 'output/repeated.candidate').read_bytes()
    assert candidate[-8:] == base[-8:] == bytes.fromhex('61a5635a65a5325a')
    assert len(candidate) < 0x24000 - 0x8000
    assert candidate[:0x100] == previous[:0x100] == base[:0x100]

    # Only the six existing RFID branch destinations and version may change
    # within the original application. All other bytes must match known 60O.
    addresses = [0x0800E836, 0x0800E7DA, 0x0800E842,
                 0x0800FE3C, 0x0800E228, 0x0800FE36]
    allowed = {i for addr in addresses for i in range(addr-0x08008000, addr-0x08008000+4)}
    version_offset = previous.index(b'V1.1.60O\0')
    allowed.update(range(version_offset, version_offset+9))
    assert candidate[version_offset:version_offset+9] == b'V1.1.91O\0'
    changed = [i for i in range(len(base)-8) if candidate[i] != previous[i]]
    assert set(changed) <= allowed, 'unexpected modification of existing application'
    # The IAP request handlers and IAP task itself are covered by the stronger
    # all-other-bytes rule above; record separate byte hashes for inspection.
    regions = {'iap_handlers': (0x0800D4E4, 0x0800D600),
               'iap_task': (0x08013FB8, 0x08014300)}
    protected = {}
    for name, (start, end) in regions.items():
        lo, hi = start-0x08008000, end-0x08008000
        assert candidate[lo:hi] == previous[lo:hi] == base[lo:hi]
        protected[name] = {'start': hex(start), 'end_exclusive': hex(end), 'sha256': sha(candidate[lo:hi])}
    subprocess.run(['python3', str(HERE/'firmware/apply_patch.py'), '--base',
                    str(ROOT/'input/ACE2_V1.1.31_20260306.bin'), '--out',
                    str(HERE/'output/reconstructed.candidate')], check=True)
    assert candidate == (HERE/'output/reconstructed.candidate').read_bytes()
    module_spec = importlib.util.spec_from_file_location('ota', HERE/'live/ace2_ota.py')
    ota = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(ota)
    manifest = {
        'version': 'V1.1.91O', 'release_kind': 'private bench candidate',
        'sha256': sha(candidate), 'md5': hashlib.md5(candidate).hexdigest(),
        'size': len(candidate), 'ota_crc_init_ffff': ota.crc16_kermit(candidate),
        'announce_version': '1.1.31', 'base_sha256': sha(base),
        'baseline_60O_sha256': sha(previous), 'reproducible': True,
        'patch_roundtrip': True, 'app_vectors_unchanged': True,
        'existing_app_changed_byte_count': len(changed),
        'allowed_existing_changes': [hex(a) for a in addresses] + [hex(version_offset+0x08008000)],
        'all_other_existing_app_bytes_unchanged': True, 'protected_regions': protected,
        'bootloader_in_image': False, 'iap_trailer_preserved': True,
        'source_files': {str(p.relative_to(HERE)): sha(p.read_bytes())
                         for p in sorted((HERE/'firmware').iterdir()) if p.is_file()},
        'device_tests_run': False, 'hardware_ota_cycle_verified': False,
    }
    (HERE/'output/image-validation.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps(manifest, indent=2))

if __name__ == '__main__':
    main()
