#!/usr/bin/env python3
"""Finish staged screen integration on the pinned vendored source."""
import argparse
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'ace2k-native-slot-exit'))
from patch_scoped_runtime import replace

def prepare(root):
    # The pinned source omits ui_print_tune_compact.h/.cpp. Keep all actual
    # tuning functions by routing to the existing maintained full panel.
    p=root/'src/ui/print/ui_panel_print_status.cpp'
    s=replace(p.read_text(),'#include "ui_print_tune_compact.h"\n','')
    s=replace(s,'''    if (lv_subject_get_int(&lcd_layout_subject_)) {
        helix::ui::get_compact_print_tune_overlay().show(parent_screen_, api_, printer_state_);
    } else {
        get_print_tune_overlay().show(parent_screen_, api_, printer_state_);
    }
''','''    get_print_tune_overlay().show(parent_screen_, api_, printer_state_);
''')
    p.write_text(s)
    # patch_batch_task_view already routes single-slot eject through preview.
    # Verify the existing connection rather than re-applying a second edit.
    p=root/'src/ui/filament/u1_filament_overview.cpp'
    source=p.read_text()
    start=source.index('void U1FilamentOverview::on_action_eject() {')
    end=source.index('\n}',start)
    if 'preview_batch("eject")' not in source[start:end]:
        raise ValueError('single-slot native eject was not integrated')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);prepare(p.parse_args().root)
