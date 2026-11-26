"""Check the two rootfs slots in the verified RKAF unpack output.

Rockchip parameter nand_addr/nand_size fields are 512-byte sectors.
The afptool payload padding count is in 2048-byte archive blocks.
"""
import argparse
import json
from pathlib import Path
import re

SECTOR_BYTES = 512
ARCHIVE_BLOCK_BYTES = 2048
SLOTS = ('system_a', 'system_b')
ROW = re.compile(r'^Unpacking: name=(\S+) filename=(\S+)\s+nand_addr=(\d+)/(\d+)\s+pos=(\d+)/(\d+)/(\d+)\s*$', re.M)


def read_slots(log, image_size):
    slots = {}
    for name, filename, start, count, pos, size, blocks in ROW.findall(log):
        if name not in SLOTS:
            continue
        if name in slots or filename != 'rootfs.img':
            raise ValueError(f'invalid or duplicate {name} rootfs mapping')
        start, count, pos, size, blocks = map(int, (start, count, pos, size, blocks))
        if start <= 0 or count <= 0 or count == 0xffffffff or start + count > 0xffffffff:
            raise ValueError(f'invalid {name} geometry')
        if size != image_size or blocks != (size + ARCHIVE_BLOCK_BYTES - 1) // ARCHIVE_BLOCK_BYTES:
            raise ValueError(f'{name} archive metadata does not match rootfs bytes')
        slots[name] = {'start_sector': start, 'sector_count': count,
                       'capacity_bytes': count * SECTOR_BYTES, 'archive_offset': pos}
    if set(slots) != set(SLOTS):
        raise ValueError('both system_a and system_b mappings are required')
    if slots['system_a']['archive_offset'] != slots['system_b']['archive_offset']:
        raise ValueError('system slots must reference the same rootfs payload')
    a, b = sorted(slots.values(), key=lambda s: s['start_sector'])
    if a['start_sector'] + a['sector_count'] > b['start_sector']:
        raise ValueError('system partitions overlap')
    return slots


def check_capacity(base_log, base_size, image_size, readback_log=None):
    if base_size <= 0 or image_size <= 0:
        raise ValueError('empty rootfs image')
    slots = read_slots(base_log, base_size)
    capacity = min(s['capacity_bytes'] for s in slots.values())
    padded = ((image_size + ARCHIVE_BLOCK_BYTES - 1) // ARCHIVE_BLOCK_BYTES) * ARCHIVE_BLOCK_BYTES
    if base_size > capacity or padded > capacity:
        raise ValueError(f'rootfs does not fit both slots: base={base_size}, new_padded={padded}, limit={capacity}')
    if readback_log is not None:
        final = read_slots(readback_log, image_size)
        for name in SLOTS:
            for field in ('start_sector', 'sector_count'):
                if final[name][field] != slots[name][field]:
                    raise ValueError(f'{name} partition geometry changed on repack')
    return {'sector_bytes': SECTOR_BYTES, 'base_bytes': base_size,
            'image_bytes': image_size, 'padded_bytes': padded,
            'limit_bytes': capacity, 'free_bytes': capacity - padded,
            'slots': slots, 'readback_checked': readback_log is not None}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('base_log', type=Path)
    p.add_argument('base_size', type=int)
    p.add_argument('image', type=Path)
    p.add_argument('--readback-log', type=Path)
    p.add_argument('--report', type=Path)
    a = p.parse_args()
    report = check_capacity(a.base_log.read_text(), a.base_size, a.image.stat().st_size,
                            a.readback_log.read_text() if a.readback_log else None)
    text = json.dumps(report, indent=2) + '\n'
    print(text, end='')
    if a.report:
        a.report.write_text(text)


if __name__ == '__main__':
    main()
