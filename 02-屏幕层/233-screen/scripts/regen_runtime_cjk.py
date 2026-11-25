#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rebake the existing runtime CJK faces without changing compiled text fonts.

Run through `make regen-runtime-cjk-fonts`. Requires lv_font_conv 1.5.3 and
the Source Han Sans SC (思源黑体) sources in assets/fonts — Noto Sans SC is
the same family under Google branding. LV_FONT_CONV can name a private
converter install; otherwise use node_modules/.bin or PATH.

Weight is taken from each face filename (regular / bold / light). Output
bin names stay noto_sans_cjk_*.bin so CjkFontManager needs no C++ change.

The shared scanner includes translations, native UI text, the optional sibling
device-service and U+2192. Validate every generated binary's real cmap before
replacing the shipped files; only then write the matching manifest.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from check_cjk_font_coverage import covered_codepoints


WEIGHT_FILES = {
    'regular': 'SourceHanSansSC-Regular.ttf',
    'bold': 'SourceHanSansSC-Bold.ttf',
    'light': 'SourceHanSansSC-Light.ttf',
}


def subset_font(src: Path, dst: Path, codepoints: list[int]) -> None:
    unicodes = ','.join(f'U+{cp:04X}' for cp in codepoints)
    subprocess.run(
        [
            sys.executable, '-m', 'fontTools.subset', str(src),
            f'--unicodes={unicodes}',
            f'--output-file={str(dst)}',
            '--legacy-cmap',
            '--notdef-glyph',
            '--notdef-outline',
            '--recommended-glyphs',
            '--no-hinting',
        ],
        check=True,
    )


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    fonts = root / 'assets/fonts'
    runtime = fonts / 'cjk'
    converter = os.environ.get('LV_FONT_CONV')
    if not converter:
        local = root / 'node_modules/.bin/lv_font_conv'
        converter = str(local) if local.is_file() else shutil.which('lv_font_conv')
    if not converter:
        raise RuntimeError('lv_font_conv not found; install 1.5.3 or set LV_FONT_CONV')

    sources = {weight: fonts / name for weight, name in WEIGHT_FILES.items()}
    for path in sources.values():
        if not path.is_file():
            raise RuntimeError(f'Missing font source: {path}')
    targets = sorted(runtime.glob('*.bin'))
    scan = subprocess.check_output(
        [sys.executable, str(root / 'scripts/translations/cjk_charset.py'), '--root', str(root)],
        text=True)
    codepoints = [int(line, 16) for line in scan.splitlines()]
    if not targets or not codepoints:
        raise RuntimeError('No runtime font targets or codepoints found; refusing an empty bake')
    needed = set(codepoints)
    ranges = ','.join(f'0x{cp:04x}' for cp in codepoints)
    print(f'Rebuilding {len(targets)} runtime CJK faces with {len(codepoints)} codepoints', flush=True)
    print('Source: Source Han Sans SC / Noto Sans SC (思源黑体)', flush=True)

    with tempfile.TemporaryDirectory(prefix='.cjk-bake-', dir=runtime) as staging:
        staging_path = Path(staging)
        subsets = {}
        for weight, src in sources.items():
            dest = staging_path / f'{weight}-subset.ttf'
            print(f'  subsetting {src.name} -> {dest.name}', flush=True)
            subset_font(src, dest, codepoints)
            subsets[weight] = dest

        for target in targets:
            match = re.fullmatch(r'noto_sans_cjk_(?:(bold|light)_)?(\d+)\.bin', target.name)
            if not match:
                raise RuntimeError(f'Unrecognized runtime face: {target.name}')
            weight = match.group(1) or 'regular'
            size = match.group(2)
            output = staging_path / target.name
            command = [
                converter,
                '--font', str(subsets[weight]),
                '--size', size,
                '--range', ranges,
                '--bpp', '4',
                '--format', 'bin',
                '-o', str(output),
            ]
            subprocess.run(command, check=True)
            missing = needed - covered_codepoints(output.read_bytes())
            if missing:
                codes = ', '.join(f'U+{cp:04X}' for cp in sorted(missing))
                raise RuntimeError(f'{target.name} lacks {codes}')
            print(f'  {target.name}: {output.stat().st_size} bytes ({weight} {size}px)', flush=True)
        for target in targets:
            (staging_path / target.name).replace(target)
    (runtime / '.cjk_codepoints.manifest').write_text(
        ''.join(f'0x{cp:04x}\n' for cp in codepoints), encoding='utf-8')
    subprocess.run(['bash', 'scripts/check_cjk_font_staleness.sh'], cwd=root, check=True)
    subprocess.run([sys.executable, 'scripts/check_cjk_font_coverage.py'], cwd=root, check=True)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        sys.exit(1)
