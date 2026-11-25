import unittest
from unittest.mock import patch

from multiace.tools import post_process_virtual_toolheads as pp
from multiace.web.backend import preflight_core as preflight


PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aJx0AAAAASUVORK5CYII='


def block(width, data=PNG, declared=None, end=True):
    lines = [f'; thumbnail begin {width}x{width} {declared or len(data)}\n']
    lines.extend(f'; {data[i:i + 60]}\n' for i in range(0, len(data), 60))
    if end:
        lines.append('; thumbnail end\n')
    return lines


def preview(lines):
    return preflight.parse_meta(pp, iter(lines))[-1]['thumbnail']


class PreflightThumbnailTests(unittest.TestCase):
    def test_largest_preview_can_extend_past_metadata_window(self):
        source = block(48) + ['; filler\n'] * 310 + block(300)
        self.assertEqual(preview(source), {'mime': 'image/png', 'w': 300, 'h': 300, 'b64': PNG})

    def test_unfinished_or_wrong_media_does_not_replace_a_good_preview(self):
        source = block(48) + block(600, 'PHN2Zy8+') + block(800, end=False)
        self.assertEqual(preview(source)['w'], 48)

    def test_actual_length_is_limited_even_if_header_claims_less(self):
        with patch.object(preflight, '_THUMB_MAX_B64', len(PNG)):
            self.assertIsNone(preview(block(300, PNG * 2, declared=1)))

    def test_files_without_a_preview_are_supported(self):
        self.assertIsNone(preview(['G28\n', 'G1 X10 Y10 E1\n']))
