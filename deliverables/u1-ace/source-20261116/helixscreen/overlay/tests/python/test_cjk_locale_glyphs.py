# SPDX-License-Identifier: GPL-3.0-or-later
"""Libc's localized date/time strings need real runtime glyphs too."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


charset = load_script('cjk_charset', 'scripts/translations/cjk_charset.py')
coverage = load_script('cjk_coverage', 'scripts/check_cjk_font_coverage.py')


def write_locale_fixture(root):
    path = root / 'scripts/translations/runtime_locale_samples.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'am_pm': ['上午', '下午', '午前', '午後']},
                               ensure_ascii=False), encoding='utf-8')


class CjkLocaleGlyphTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='cjk-locale-test-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_locale_ampm_is_scanned_without_ui_or_translation_literals(self):
        write_locale_fixture(self.root)
        self.assertIn(0x5348, charset.needed_codepoints(self.root))

    def test_staleness_gate_rejects_an_unbaked_locale_ampm_glyph(self):
        write_locale_fixture(self.root)
        (self.root / 'translations').mkdir()
        (self.root / 'translations/zh.yml').write_text("label: '热'\n", encoding='utf-8')
        (self.root / 'ui_xml').mkdir()
        (self.root / 'ui_xml/wizard_language_chooser.xml').write_text(
            '<text_body text="热"/>', encoding='utf-8')
        (self.root / 'scripts/regen_text_fonts.sh').write_text('WIZARD_CJK="0x70ed"\n')
        fonts = self.root / 'assets/fonts/cjk'
        fonts.mkdir(parents=True)
        # Include every required codepoint except the libc-produced noon character.
        needed = charset.needed_codepoints(self.root)
        (fonts / '.cjk_codepoints.manifest').write_text(
            ''.join(f'0x{cp:04x}\n' for cp in needed if cp != 0x5348))
        result = subprocess.run(
            ['bash', str(ROOT / 'scripts/check_cjk_font_staleness.sh'),
             '--root', str(self.root)], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('0x5348', result.stdout + result.stderr)

    def test_provider_dynamic_messages_are_scanned(self):
        path = self.root / 'scripts/translations/provider_ui_messages.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'characters': '握'}, ensure_ascii=False), encoding='utf-8')
        self.assertIn(ord('握'), charset.needed_codepoints(self.root))

    def test_every_runtime_face_has_provider_dynamic_glyphs(self):
        catalog = json.loads((ROOT / 'scripts/translations/provider_ui_messages.json').read_text())
        needed = {ord(character) for character in catalog['characters']}
        self.assertIn(ord('握'), needed)
        fonts = sorted((ROOT / 'assets/fonts/cjk').glob('*.bin'))
        self.assertEqual(len(fonts), 35)
        for font in fonts:
            self.assertFalse(needed - coverage.covered_codepoints(font.read_bytes()), font.name)

    def test_every_runtime_face_has_the_pinned_locale_strings(self):
        needed = set(charset.needed_codepoints(
            ROOT, ['scripts/translations/runtime_locale_samples.json']))
        self.assertIn(0x5348, needed)
        fonts = sorted((ROOT / 'assets/fonts/cjk').glob('*.bin'))
        self.assertEqual(len(fonts), 35)
        for font in fonts:
            self.assertFalse(needed - coverage.covered_codepoints(font.read_bytes()), font.name)


if __name__ == '__main__':
    unittest.main()


    def test_u1_punctuation_codepoints_are_required(self):
        # Degree, minus, dashes, middle dot and chevrons must be in the
        # runtime CJK face or they render as tofu on U1 labels.
        needed = set(charset.needed_codepoints(ROOT))
        for cp in (0x00B0, 0x00B7, 0x2014, 0x2039, 0x2212):
            self.assertIn(cp, needed, f'U+{cp:04X}')
