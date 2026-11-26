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
    # The missing compact print-controls module leaves two unresolved calls.
    # Select the existing responsive print-status body together with the
    # maintained full tuning panel; do not invent the omitted LCD policy.
    s=replace(s,'''    // Select the structural XML branch once, from the display rather than an
    // overlay/container whose dimensions change during navigation transitions.
    lcd_layout_for_tree_ = helix::ui::uses_compact_print_controls(printer_state_, parent);
''','''    // The pinned source has no compact print-controls module.
    lcd_layout_for_tree_ = false;
''')
    s=replace(s,'''    // A cached overlay must retain the structural branch it was created with.
    // With no tree, use a live display screen: parent_screen_ can name a former
    // navigation container that has already been deleted. create() uses its
    // explicitly supplied live parent to choose the new tree's display.
    lv_obj_t* screen = lv_display_get_default() ? lv_screen_active() : nullptr;
    const bool compact = overlay_root_
                             ? lcd_layout_for_tree_
                             : helix::ui::uses_compact_print_controls(printer_state_, screen);
''','''    // Keep the selected structural layout for an existing tree.
    const bool compact = overlay_root_ && lcd_layout_for_tree_;
''')
    if 'uses_compact_print_controls' in s or 'get_compact_print_tune_overlay' in s:
        raise ValueError('missing compact print-controls module still referenced')
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
