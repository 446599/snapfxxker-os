"""Exact-stock INSERT policy check. Historical blanket quarantine is retired.

Preserve automatic preload and stock guards. This check is NOT a failure-stop
integration. The separate offline preload_failure module must not be flashed.
"""
BASE_ADDR = 0x08008000
INSERT_RISE = 0x0800F6AE
INSERT_RETURN = 0x0800F6C4
STOCK_RISE = bytes.fromhex('84f00100')  # eor.w r0,r4,#1
# b.n 0x0800F6C4; nop. Branch has no link and does not change flags.
QUARANTINE_RISE = bytes.fromhex('09e000bf')
STOCK_GUARDS = ((0x0800F6F8, bytes.fromhex('e4d5')),
                (0x0800F724, bytes.fromhex('ced0')))


def apply_insert_policy(body):
    """Validate an offline image body without disabling automatic preload.

    No force mode for these bytes. The stock sibling and reader-busy guards must
    remain intact even in the opt-in normal-preload comparison build.
    """
    off = INSERT_RISE - BASE_ADDR
    if bytes(body[off:off + 4]) != STOCK_RISE:
        raise ValueError('INSERT-rising hook does not match exact V1.1.31')
    for addr, expected in STOCK_GUARDS:
        o = addr - BASE_ADDR
        if bytes(body[o:o + len(expected)]) != expected:
            raise ValueError('stock INSERT guard was removed or changed at 0x%08X' % addr)
    return []
