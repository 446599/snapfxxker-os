"""Stage the U1 inventory multiselect source against the exact screen pin.

No runtime deployment; full LVGL build and production batch endpoints must be
available before this patch can be packaged. No fallback to repeated G-code.
"""
from pathlib import Path
import hashlib
import sys

HERE=Path(__file__).resolve().parent
PIN={
    'include/u1_filament_overview.h':'a5629d4eb4561230fac245868aff424e08c587b2',
    'src/ui/filament/u1_filament_overview.cpp':'b5edabc1b6079da41389691c5be8db8b80d8896c',
}


def replace(s,old,new):
    if s.count(old)!=1: raise ValueError('screen anchor changed: '+old[:80])
    return s.replace(old,new,1)


def stage(root):
    root=Path(root)
    src={}
    for path,sha in PIN.items():
        data=(root/path).read_bytes()
        if hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()!=sha:
            raise ValueError('screen source mismatch: '+path)
        src[path]=data.decode()
    h=src['include/u1_filament_overview.h']
    h=replace(h,'#include "u1_filament_model.h"','#include "u1_filament_model.h"\n#include "screen_batch_selection.h"\n#include <memory>')
    h=replace(h,'    void on_action_load();','''    void toggle_batch_mode();
    void preview_batch(const std::string& action);
    void start_batch();
    bool batch_selection_current() const;
    void on_action_load();''')
    h=replace(h,'    lv_obj_t* mode_head_ = nullptr;','''    lv_obj_t* mode_head_ = nullptr;
    lv_obj_t* mode_multi_ = nullptr;
    bool batch_mode_ = false, batch_pending_ = false;
    uint64_t batch_connection_ = 0;
    AmsBackend* batch_backend_ = nullptr;
    u1_filament::BatchSelection batch_selection_;
    std::string batch_ticket_, batch_action_;
    std::shared_ptr<int> batch_lifetime_ = std::make_shared<int>(0);''')
    c=src['src/ui/filament/u1_filament_overview.cpp']
    c=replace(c,'U1FilamentOverview::~U1FilamentOverview() {','U1FilamentOverview::~U1FilamentOverview() {\n    batch_lifetime_.reset();')
    c=replace(c,'    for (int i = 0; i < 4; ++i) {\n        group_pills_', '''    mode_multi_ = make_soft_button(mode_bar_, "多选", false, [](lv_event_t* e) {
        if (auto* self = static_cast<U1FilamentOverview*>(lv_event_get_user_data(e)))
            self->toggle_batch_mode();
    }, this);
    for (int i = 0; i < 4; ++i) {
        group_pills_''')
    c=replace(c,'    edit_draft_.reset();\n    show_page(Page::Hub);','    edit_draft_.reset();\n    batch_ticket_.clear();\n    batch_action_.clear();\n    show_page(Page::Hub);')
    c=replace(c,'    selected_global_ = slot.global_index;\n    if (slot.ace >= 0)', '''    selected_global_ = slot.global_index;
    if (batch_mode_) {
        auto* backend = AmsState::instance().get_backend();
        if (!batch_pending_ && batch_selection_current() && backend && slot.ace >= 0 &&
            slot.connected && slot.inlet_has_filament) {
            const auto info = backend->get_system_info();
            const auto* unit = info.unit_for_slot(slot.global_index);
            if (unit) batch_selection_.toggle({slot.ace, slot.local_slot, slot.global_index,
                                               unit->serial_number});
        }
        apply_hub_data();
        return;
    }
    if (slot.ace >= 0)''')
    c=replace(c,'    const int mode_w = 160;','    const int mode_w = std::min(216, row_w * 48 / 100);')
    c=replace(c,'    place_row_cell(mode_ace_, 0, 0, mode_w - 2, kToolbarH - 2, 0, 2, 0);\n    place_row_cell(mode_head_, 0, 0, mode_w - 2, kToolbarH - 2, 1, 2, 0);','''    place_row_cell(mode_ace_, 0, 0, mode_w - 2, kToolbarH - 2, 0, 3, 0);
    place_row_cell(mode_head_, 0, 0, mode_w - 2, kToolbarH - 2, 1, 3, 0);
    place_row_cell(mode_multi_, 0, 0, mode_w - 2, kToolbarH - 2, 2, 3, 0);''')
    c=replace(c,'    paint_mode(mode_head_, !by_ace);','''    paint_mode(mode_head_, !by_ace);
    paint_mode(mode_multi_, batch_mode_);
    lv_label_set_text(lv_obj_get_child(mode_multi_, 0), batch_mode_ ?
        fmt::format("多选{}", batch_selection_.entries().size()).c_str() : "多选");
    lv_obj_set_disabled(mode_multi_, batch_pending_ || ace_groups_.empty());''')
    c=replace(c,'    const std::array<bool, 4> available{can_load, head_action, can_eject, can_dry};','''    std::array<bool, 4> available{can_load, head_action, can_eject, can_dry};
    if (batch_mode_) {
        const bool selected = !batch_selection_.empty() && !batch_pending_ &&
            batch_selection_current() && !lv_subject_get_int(
                get_printer_state().print_state().get_job_holds_machine_subject());
        available = {selected && batch_selection_.distinct_heads(), selected, selected, false};
        lv_label_set_text(lv_obj_get_child(actions_[0],0), "批量进料");
        lv_label_set_text(lv_obj_get_child(actions_[1],0), "批量退料");
        lv_label_set_text(lv_obj_get_child(actions_[2],0), "批量退出");
    } else {
        lv_label_set_text(lv_obj_get_child(actions_[1],0), "从喷头退出");
        lv_label_set_text(lv_obj_get_child(actions_[2],0), "退出 ACE");
    }''')
    c=replace(c,'        const bool selected = (i == selected_card_);','''        const bool selected = batch_mode_ ? batch_selection_.contains(slot.ace, slot.local_slot)
                                          : (i == selected_card_);''')
    c=replace(c,'        if (slot.editable) lv_obj_remove_flag(w.pencil, LV_OBJ_FLAG_HIDDEN);','        if (slot.editable && !batch_mode_) lv_obj_remove_flag(w.pencil, LV_OBJ_FLAG_HIDDEN);')
    for action in ('load','unload','eject'):
        anchor='void U1FilamentOverview::on_action_'+action+'() {\n    hide_edit_menu();'
        c=replace(c,anchor,anchor+'\n    if (batch_mode_) { preview_batch("'+action+'"); return; }')
    c=replace(c,'void U1FilamentOverview::confirm_action() {\n    refresh();','''void U1FilamentOverview::confirm_action() {
    if (!batch_action_.empty()) { start_batch(); return; }
    refresh();''')
    c=replace(c,'void U1FilamentOverview::on_action_load() {',r'''bool U1FilamentOverview::batch_selection_current() const {
    const auto* client = get_moonraker_client();
    auto* backend = AmsState::instance().get_backend();
    if (!client || !backend || backend != batch_backend_ ||
        client->connection_generation() != batch_connection_ ||
        lv_subject_get_int(get_printer_state().network_state().get_nav_buttons_enabled_subject()) != 1)
        return false;
    const auto info = backend->get_system_info();
    for (const auto& entry : batch_selection_.entries()) {
        const auto& bay = entry.second;
        const auto* unit = info.unit_for_slot(bay.global);
        const auto* slot = info.get_slot_global(bay.global);
        const auto address = multiace::bay_of(bay.global);
        if (!unit || !slot || !unit->connected || unit->absent || !slot->is_present() ||
            !address || address->first != bay.ace || address->second != bay.slot ||
            slot->mapped_tool != bay.slot || unit->serial_number != bay.serial)
            return false;
    }
    return true;
}

void U1FilamentOverview::toggle_batch_mode() {
    if (batch_pending_) return;
    batch_selection_.clear();
    batch_ticket_.clear();
    batch_action_.clear();
    batch_mode_ = !batch_mode_;
    if (batch_mode_) {
        auto* client = get_moonraker_client();
        batch_backend_ = AmsState::instance().get_backend();
        if (!client || !batch_backend_) { batch_mode_ = false; return; }
        batch_connection_ = client->connection_generation();
        view_mode_ = 0;
    }
    refresh();
}

void U1FilamentOverview::preview_batch(const std::string& action) {
    if (batch_pending_ || batch_selection_.empty() || !batch_selection_current()) {
        show_feedback("选择的耗材或设备连接已变化，请重新多选"); return;
    }
    if (action == "load" && !batch_selection_.distinct_heads()) {
        show_feedback("每个喷头只能选择一卷进料"); return;
    }
    nlohmann::json rows = nlohmann::json::array();
    std::string names;
    for (const auto& entry : batch_selection_.entries()) {
        const auto& b = entry.second;
        rows.push_back({{"ace", b.ace}, {"slot", b.slot}, {"serial", b.serial}});
        names += fmt::format("ACE {} · 槽 {}\n", b.ace + 1, b.slot + 1);
    }
    batch_pending_ = true;
    apply_hub_data();
    const std::weak_ptr<int> alive = batch_lifetime_;
    material_workspace::request("/material/batch/preview", {{"action", action}, {"slots", rows}},
        [this,alive,action,names](auto data, std::string error) {
            if (alive.expired()) return;
            batch_pending_ = false;
            if (!error.empty() || !batch_selection_current() || !data.is_object() ||
                !data.contains("ticket") || !data["ticket"].is_string() ||
                data["ticket"].template get<std::string>().empty()) {
                apply_hub_data();
                show_feedback(error.empty() ? "批量接口未就绪或料路已变化" : error); return;
            }
            batch_ticket_ = data["ticket"].template get<std::string>();
            batch_action_ = action;
            layout_confirm_page();
            lv_label_set_text(confirm_title_, action == "load" ? "确认批量进料" :
                action == "unload" ? "确认批量退料" : "确认批量退出 ACE");
            lv_label_set_text(confirm_target_, fmt::format("已选 {} 槽",batch_selection_.entries().size()).c_str());
            lv_obj_set_hidden(confirm_extra_,true);
            lv_label_set_text(confirm_body_, (names + "\n无冲突任务并行，公共料路任务按需排队。").c_str());
            lv_obj_scroll_to_y(lv_obj_get_parent(confirm_body_),0,LV_ANIM_OFF);
            lv_label_set_text(lv_obj_get_child(confirm_ok_,0),"确认操作");
            show_page(Page::Confirm);
        },true);
}

void U1FilamentOverview::start_batch() {
    if (batch_pending_ || batch_ticket_.empty() || !batch_selection_current()) return;
    batch_pending_ = true;
    lv_obj_set_disabled(confirm_ok_,true);
    const std::weak_ptr<int> alive = batch_lifetime_;
    const auto ticket = std::exchange(batch_ticket_,{});
    material_workspace::request("/material/batch/start", {{"ticket",ticket}},
        [this,alive](auto data,std::string error) {
            if (alive.expired()) return;
            batch_pending_ = false;
            lv_obj_set_disabled(confirm_ok_,false);
            if (error.empty() && data.is_object() && data.contains("batch") && data["batch"].is_string()) {
                batch_selection_.clear();
                batch_mode_ = false;
                show_hub(); refresh();
                show_feedback("批量任务已受理，完成以各槽状态为准",FeedbackKind::Info);
            } else {
                show_hub(); refresh();
                show_feedback(error.empty() ? "批量任务受理未确认，请先查看设备状态" : error);
            }
        },true);
}

void U1FilamentOverview::on_action_load() {''')
    # All anchor checks finished before writing any source.
    (root/'include/screen_batch_selection.h').write_bytes((HERE/'screen_batch_selection.h').read_bytes())
    (root/'include/u1_filament_overview.h').write_text(h)
    (root/'src/ui/filament/u1_filament_overview.cpp').write_text(c)


if __name__=='__main__': stage(sys.argv[1])
