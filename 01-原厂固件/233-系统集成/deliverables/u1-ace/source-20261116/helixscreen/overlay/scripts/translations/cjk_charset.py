#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The CJK codepoint set the runtime font must bake.

regen_text_fonts.sh bakes assets/fonts/cjk/*.bin from this set and records it
in .cjk_codepoints.manifest; check_cjk_font_staleness.sh diffs the same set
against that manifest. Both call needed_codepoints() so the bake and the
staleness gate can never disagree about what counts as "needed".

Scans every translations/*.yml locale (any locale can carry CJK — restricting
to zh/ja would let a stray CJK char in another catalog render tofu) plus the
C++ sources, XML layouts, printer database and optional sibling device-service
Python files, for hardcoded strings displayed by the native UI. A pinned
LC_TIME sample supplies the date names and AM/PM strings produced by libc,
which never appear as literals in translated UI source. The runtime font
also supplies U+2192 for the settings change summary.

Prints one 0xXXXX codepoint per line, sorted — the format the staleness gate
and the manifest both use.
"""

from __future__ import annotations

import argparse
import glob
import re
import sys
from pathlib import Path

# Unicode ranges supplied by the runtime CJK fallback, including the settings
# summary arrow that is absent from the compiled Latin font range.
CJK_RANGES = [
    r'[\u2192]',  # Rightwards arrow in settings change summaries
    r'[　-〿]',   # CJK Symbols and Punctuation
    r'[぀-ゟ]',   # Hiragana
    r'[゠-ヿ]',   # Katakana
    r'[㐀-䶿]',   # CJK Unified Ideographs Extension A
    r'[一-鿿]',   # CJK Unified Ideographs
    r'[＀-￯]',   # Halfwidth and Fullwidth Forms
]

# Latin / general punctuation used on the U1 display (units, minus, dashes,
# middle dots, chevrons). These are absent from CJK_RANGES, and the compiled
# Latin Noto subset may also omit them — the result is a tofu box because
# CjkFontManager fallback has nothing to draw. Keep them in the runtime
# Source Han Sans SC face, same as U+2192.
EXTRA_CODEPOINTS = {
    0x00B0,  # °
    0x00B7,  # ·
    0x00D7,  # ×
    0x2013,  # –
    0x2014,  # —
    0x2026,  # …
    0x2039,  # ‹
    0x203A,  # ›
    0x2212,  # −
}

# Hardcoded CJK can live in any compiled source, not only src/ui, and in the
# hand-authored runtime data the same font renders: XML layouts (the wizard's
# language chooser, and the generated ui_xml/translations/*.xml, which is what
# actually renders), the printer database, and the optional companion service
# whose labels and errors are shown by the native settings UI.
SOURCE_GLOBS = ['src/**/*.cpp', 'src/**/*.h', 'include/**/*.h',
                'ui_xml/**/*.xml', 'assets/config/printer_database.json',
                'scripts/translations/runtime_locale_samples.json',
                'scripts/translations/provider_ui_messages.json',
                '../device-service/*.py']


def needed_codepoints(root: Path, paths: list[str] | None = None) -> list[int]:
    """Codepoints used under root: the translations plus SOURCE_GLOBS, or only
    the given root-relative globs when paths is set."""
    chars: set[str] = set()

    def extract(content: str) -> None:
        for pattern in CJK_RANGES:
            chars.update(re.findall(pattern, content))

    if paths is None:
        for path in sorted(glob.glob(str(root / 'translations' / '*.yml'))):
            extract(Path(path).read_text(encoding='utf-8'))

    for pattern in paths if paths is not None else SOURCE_GLOBS:
        for path in glob.glob(str(root / pattern), recursive=True):
            try:
                extract(Path(path).read_text(encoding='utf-8'))
            except (OSError, UnicodeDecodeError):
                pass

    codes = {ord(c) for c in chars}
    # Punctuation extras belong in the runtime .bin fallback. Do not inject
    # them into a path-restricted scan (language chooser / locale fixtures),
    # which is checked against compiled WIZARD_CJK rather than the .bins.
    if paths is None:
        codes.update(EXTRA_CODEPOINTS)
    return sorted(codes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='.', help='repository root to scan')
    parser.add_argument('--paths', nargs='+', metavar='GLOB',
                        help='scan only these root-relative globs')
    args = parser.parse_args()

    root = Path(args.root)
    codepoints = needed_codepoints(root, args.paths)
    if not codepoints:
        # An empty result means the scan matched nothing — for this repo that
        # is a broken scan, not an empty language. Refuse to print an empty
        # set that a caller would bake as "no CJK needed".
        print('ERROR: no CJK codepoints found under {} — broken scan?'.format(root),
              file=sys.stderr)
        return 1
    for cp in codepoints:
        print(f'0x{cp:04x}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
