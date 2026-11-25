#!/usr/bin/env python3
"""Build the insertion-policy-only ACE application from the pinned 98O image."""
# SPDX-License-Identifier: MIT
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('checked_ace_image', HERE / 'ace_image.py')
image = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image)
VERSION = 'V1.2.03O'
HOOKS = [
    (0x0800AE52, '416045e0', 'ip_status_hook', False),
    (0x0800B578, 'b0b582b0', 'ip_feed_hook', False),
    (0x080116A8, '04f049fd', 'ip_factory', True),
    (0x08015AF6, 'f5f755fe', 'ip_registration_hook', True),
    (0x0800F698, 'f0b581b0', 'ip_insert_hook', False),
    (0x0800ED46, '84f83400', 'ip_active_hook', False),
    (0x0800ED76, '11612179', 'ip_gpio_hook', False),
    (0x0800EECA, 'cff6ff72', 'ip_start_pwm_hook', False),
    (0x0800F012, 'cff6ff72', 'ip_control_pwm_hook', False),
]
SYMBOLS = {
    'ip_status_resume': 0x0800AEE2, 'ip_feed_resume': 0x0800B57D,
    'ip_stock_malloc': 0x080132C5, 'ip_stock_factory': 0x0801613F,
    'ip_stock_register': 0x0800B7A5, 'ip_stock_feed': 0x0800B579,
    'ip_insert_resume': 0x0800F69C,
    'ip_stock_power_stop': 0x0800F089,
    'ip_stock_active_resume': 0x0800ED4C,
    'ip_stock_gpio_resume': 0x0800ED7A,
    'ip_stock_start_return': 0x0800EED0,
    'ip_stock_control_resume': 0x0800F018,
}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--reference', type=Path, required=True, help='verified V1.2.01O image')
    args = p.parse_args()
    base = args.base.read_bytes()
    reference = args.reference
    if image.digest(reference.read_bytes()) != '1bc3f2e37f822a7924a4fdcde46b625387201e9c4c1de73df4ea042ac11f066c': raise ValueError('01O baseline mismatch')
    if len(base) != image.BASE_SIZE or image.digest(base) != image.BASE_SHA256 or base[-8:] != image.MAGIC:
        raise ValueError('98O image identity mismatch')
    for address, expected, _, _ in HOOKS:
        if base[address - image.BASE_ADDRESS:address - image.BASE_ADDRESS + 4].hex() != expected:
            raise ValueError('Native hook bytes mismatch')
    # Key mapping and native finite feed admission are part of this patch contract.
    if base[0x0800F848 - image.BASE_ADDRESS:0x0800F848 - image.BASE_ADDRESS + 16] != bytes([0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3]):
        raise ValueError('Native insert key map mismatch')
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()): raise FileExistsError('Use a new output directory')
    compiler = shutil.which('arm-none-eabi-gcc')
    if not compiler: raise FileNotFoundError('arm-none-eabi-gcc')
    prefix = str(Path(compiler).with_name('arm-none-eabi-'))
    log = []
    def run(tool, *arguments):
        command = [prefix + tool, *map(str, arguments)]
        r = subprocess.run(command, text=True, capture_output=True)
        log.append(dict(command=command, returncode=r.returncode, stdout=r.stdout, stderr=r.stderr))
        (out / 'commands.json').write_text(json.dumps(log, indent=2) + '\n')
        r.check_returncode(); return r.stdout
    origin = (image.BASE_ADDRESS + len(base) - 8 + 7) & ~7
    ld = 'ENTRY(ip_dispatch)\nSECTIONS { . = 0x%x; .text : { *(.text*) *(.rodata*) } .data : { *(.data*) } .bss (NOLOAD) : { *(.bss*) *(COMMON) }\n' % origin
    ld += 'ASSERT(SIZEOF(.data)==0,"No initialized RAM") ASSERT(SIZEOF(.bss)==0,"No unreserved RAM") ASSERT(.<0x08022000,"Application bound") /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) } }\n'
    ld += ''.join('%s = 0x%x;\n' % pair for pair in SYMBOLS.items())
    (out / 'payload.ld').write_text(ld)
    flags = ['-mcpu=cortex-m3', '-mthumb', '-Os', '-ffreestanding', '-fno-builtin', '-fno-common', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables', '-Wall', '-Wextra', '-Werror']
    for source in ('runtime.c', 'hooks.S'):
        run('gcc', *flags, '-c', HERE / source, '-o', out / (source + '.o'))
    elf = out / 'insert-policy.elf'
    run('gcc', '-mcpu=cortex-m3', '-mthumb', '-nostdlib', '-Wl,-T,' + str(out / 'payload.ld'), '-Wl,-Map,' + str(out / 'payload.map'), out / 'runtime.c.o', out / 'hooks.S.o', '-lgcc', '-o', elf)
    run('objcopy', '-O', 'binary', elf, out / 'payload.bin')
    symbols = {v[2]: int(v[0], 16) for line in run('nm', '-n', elf).splitlines() if len(v := line.split()) == 3}
    (out / 'payload.dis').write_text(run('objdump', '-d', elf))
    payload = (out / 'payload.bin').read_bytes()
    result = bytearray(base[:-8]); result.extend(b'\xff' * (origin - image.BASE_ADDRESS - len(result))); result.extend(payload)
    changes = []
    for address, expected, symbol, link in HOOKS:
        branch = image.encode_branch(address, symbols[symbol], link)
        assert image.decode_branch(address, branch) == symbols[symbol]
        result[address-image.BASE_ADDRESS:address-image.BASE_ADDRESS+4] = branch
        changes.append(dict(address=hex(address), before=expected, after=branch.hex(), target=hex(symbols[symbol])))
    offset = base.index(b'V1.1.98O\0'); assert base.count(b'V1.1.98O\0') == 1
    result[offset:offset+9] = VERSION.encode() + b'\0'
    result.extend(b'\xff' * ((8-len(result)%8)%8)); result.extend(image.MAGIC)
    assert result[:512] == base[:512] and image.BASE_ADDRESS + len(result) < image.APPLICATION_LIMIT
    path = out / 'ACE2-1.2.03O-insert-policy.bin'; path.write_bytes(result)
    manifest = dict(version=VERSION, baseline_01_sha256='1bc3f2e37f822a7924a4fdcde46b625387201e9c4c1de73df4ea042ac11f066c', image=str(path), sha256=image.digest(result), md5=hashlib.md5(result).hexdigest(), bytes=len(result), crc16_kermit_init0000='%04x' % image.crc16(result,0), base_sha256=image.BASE_SHA256, hooks=changes, payload_origin=hex(origin), payload_bytes=len(payload), fixed_insert_mm=400, motor_speed_mm_s=50, preserves_native_motor_handlers=False, native_feed_entry_guard='invalidates pending before native handler', recovery_debounce_ms=2000, recovery_min_queries=3, hardware_motion_test=False, preserves_native_removal=True, flash_writes_added=False, sources={q.name:image.digest(q.read_bytes()) for q in HERE.iterdir() if q.is_file()})
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (out / 'symbols.json').write_text(json.dumps(symbols, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))

if __name__ == '__main__': main()
