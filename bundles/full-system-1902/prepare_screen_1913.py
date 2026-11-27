#!/usr/bin/env python3
"""Restore the missing 480x320 U1 print controls from the verified deployment source."""
import argparse
from pathlib import Path
from shutil import copyfile

HERE = Path(__file__).resolve().parent
COMPACT_XML = (
    'ui_xml/components/print_status_lcd_strip.xml',
    'ui_xml/components/print_status_preview_controls.xml',
    'ui_xml/print_status_compact_more_menu.xml',
    'ui_xml/print_tune_compact_panel.xml',
    'ui_xml/print_tune_compact_row.xml',
)
COMPACT_FILES = (
    'include/ui_print_tune_compact.h',
    'src/ui/print/ui_print_tune_compact.cpp',
    *COMPACT_XML,
)

def prepare(root):
    status = (root / 'src/ui/print/ui_panel_print_status.cpp').read_text()
    if ('lcd_layout_for_tree_ = helix::ui::uses_compact_print_controls' not in status
            or 'get_compact_print_tune_overlay().show' not in status):
        raise ValueError('original print layout and tune routes must remain active')
    overview = (root / 'src/ui/filament/u1_filament_overview.cpp').read_text()
    start = overview.index('void U1FilamentOverview::on_action_eject() {')
    if 'preview_batch("eject")' not in overview[start:overview.index('\n}', start)]:
        raise ValueError('single-slot native eject was not integrated')
    for name in COMPACT_FILES:
        src, dst = HERE / 'screen_compact' / name, root / name
        if dst.exists():
            if dst.read_bytes() != src.read_bytes():
                raise ValueError('unexpected compact source already present: ' + name)
        else:
            copyfile(src, dst)

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('root', type=Path)
    prepare(p.parse_args().root)
