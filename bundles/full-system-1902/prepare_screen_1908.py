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
    # Single-slot exit must use the same new endpoint as multi-select, rather
    # than the old material workspace endpoint that lacked native slot exit.
    p=root/'src/ui/filament/u1_filament_overview.cpp'
    s=replace(p.read_text(),'''    ui::get_material_workspace().open_action(slot->head, slot->backend_unit - 1, "eject", {},
                                              slot->local_slot, {}, slot->ace);
''','''    auto* backend = AmsState::instance().get_backend();
    auto* client = get_moonraker_client();
    if (!backend || !client) { show_feedback("设备连接不可用"); return; }
    const auto info = backend->get_system_info();
    const auto* unit = info.unit_for_slot(slot->global_index);
    if (!unit || unit->serial_number.empty()) { show_feedback("无法确认 ACE 身份"); return; }
    batch_selection_.clear();
    batch_backend_ = backend;
    batch_connection_ = client->connection_generation();
    batch_mode_ = true;
    if (!batch_selection_.toggle({slot->ace, slot->local_slot, slot->global_index, unit->serial_number})) {
        batch_mode_ = false; show_feedback("槽位不可用"); return;
    }
    preview_batch("eject");
''')
    p.write_text(s)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);prepare(p.parse_args().root)
