#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bake the 16px filename fallback from the bundled Noto CJK source.

This covers all glyphs the source supplies in these CJK ranges, not every
Unicode character. Latin glyphs come from the compiled Noto Sans face.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from check_cjk_font_coverage import covered_codepoints
from fontTools.ttLib import TTFont

RANGES = [(0x3000,0x312f),(0x31a0,0x31bf),(0x31f0,0x31ff),
          (0x3400,0x4dbf),(0x4e00,0x9fff),(0xac00,0xd7a3),
          (0xf900,0xfaff),(0xff01,0xffee),(0x20000,0x3134f)]

def main():
    root = Path(__file__).resolve().parents[1]
    source = root/'assets/fonts/NotoSansCJKsc-Regular.otf'
    target = root/'assets/fonts/filename/noto_sans_cjk_16.bin'
    tool = os.environ.get('LV_FONT_CONV') or shutil.which('lv_font_conv')
    local = root/'node_modules/.bin/lv_font_conv'
    tool = tool or (str(local) if local.is_file() else None)
    if not tool:
        raise SystemExit('Set LV_FONT_CONV to lv_font_conv 1.5.3')
    with TTFont(source) as font:
        expected = {cp for cp in font.getBestCmap()
                    if any(lo <= cp <= hi for lo, hi in RANGES)}
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as staging:
        output = Path(staging)/target.name
        subprocess.run([tool,'--font',str(source),'--size','16',
                        '--range',','.join(f'0x{lo:x}-0x{hi:x}' for lo,hi in RANGES),
                        '--no-kerning','--bpp','2','--format','bin','-o',str(output)],check=True)
        missing = expected - covered_codepoints(output.read_bytes())
        if missing:
            raise RuntimeError(f'Filename font misses {len(missing)} source glyphs')
        output.replace(target)
    print(f'Baked and verified {len(expected)} filename codepoints')

if __name__ == '__main__':
    main()
