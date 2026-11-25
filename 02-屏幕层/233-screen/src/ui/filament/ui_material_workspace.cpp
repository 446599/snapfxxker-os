// SPDX-License-Identifier: GPL-3.0-or-later
#include "ui_material_workspace.h"

#include "ui_ams_edit_overlay.h"
#include "ui_ams_environment_overlay.h"
#include "ui_callback_helpers.h"
#include "ui_nav.h"
#include "ui_temperature_utils.h"
#include "ui_overlay_temp_graph.h"
#include "ui_error_reporting.h"
#include "ui_notification_manager.h"
#include "u1_filament_model.h"

#include "ams_backend.h"
#include "ams_state.h"
#include "app_globals.h"
#include "core/data_root_resolver.h"
#include "core/observer_factory.h"
#include "core/static_panel_registry.h"
#include "json_utils.h"
#include "i_moonraker_api.h"
#include "lane_source_store.h"
#include "filament_motion.h"
#include "material_workspace.h"
#include "printer_state.h"
#include "settings_manager.h"
#include "tool_change_request.h"
#include "tool_state.h"
#include "temperature_controller.h"

#include <spdlog/fmt/fmt.h>

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <utility>

namespace helix::ui {
using json_util::safe_bool;
using json_util::safe_int;
using json_util::safe_string;
MaterialWorkspace::~MaterialWorkspace() {
    poll_.reset();
    slot_poll_.reset();
    motion_timer_.reset();
    revision_observer_.reset();
    connection_observer_.reset();
    job_observer_.reset();
    tool_observer_.reset();
    temperature_observer_.reset();
    target_observer_.reset();
    for (auto& observer : overview_current_observers_)
        observer.reset();
    for (auto& observer : overview_target_observers_)
        observer.reset();
    subjects_.deinit_all();
}
void MaterialWorkspace::init_subjects() {
    init_subjects_guarded([this] {
        UI_MANAGED_SUBJECT_INT(page_, 0, "workspace_page", subjects_);
        UI_MANAGED_SUBJECT_INT(busy_, 0, "workspace_busy", subjects_);
        UI_MANAGED_SUBJECT_INT(ready_, 0, "workspace_ready", subjects_);
        UI_MANAGED_SUBJECT_INT(eject_ready_, 0, "workspace_eject_ready", subjects_);
        UI_MANAGED_SUBJECT_INT(unload_ready_, 0, "workspace_unload_ready", subjects_);
        UI_MANAGED_SUBJECT_INT(source_ready_, 0, "workspace_source_ready", subjects_);
        UI_MANAGED_SUBJECT_INT(task_stoppable_, 0, "workspace_task_stoppable", subjects_);
        UI_MANAGED_SUBJECT_INT(recovery_needed_, 0, "workspace_recovery_needed", subjects_);
        UI_MANAGED_SUBJECT_INT(confirm_ready_, 0, "workspace_confirm_ready", subjects_);
        UI_MANAGED_SUBJECT_STRING(confirm_text_, confirm_text_buf_, "确认操作", "workspace_confirm_text", subjects_);
        UI_MANAGED_SUBJECT_STRING(task_message_, task_message_buf_, "正在读取料槽状态",
                                  "workspace_task_message", subjects_);
        UI_MANAGED_SUBJECT_INT(route_mapped_, 0, "workspace_route_mapped", subjects_);
        UI_MANAGED_SUBJECT_INT(external_, 0, "workspace_external", subjects_);
        UI_MANAGED_SUBJECT_INT(edit_external_, 0, "workspace_edit_external", subjects_);
        UI_MANAGED_SUBJECT_INT(editable_, 0, "workspace_editable", subjects_);
        UI_MANAGED_SUBJECT_INT(manual_ready_, 0, "workspace_manual_ready", subjects_);
        UI_MANAGED_SUBJECT_INT(nozzle_head_, 0, "workspace_nozzle_head", subjects_);
        UI_MANAGED_SUBJECT_INT(nozzle_heat_ready_, 0, "workspace_nozzle_heat_ready", subjects_);
        UI_MANAGED_SUBJECT_INT(nozzle_target_dirty_, 0, "workspace_nozzle_target_dirty", subjects_);
        UI_MANAGED_SUBJECT_INT(nozzle_activation_ready_, 0, "workspace_nozzle_activation_ready", subjects_);
        UI_MANAGED_SUBJECT_INT(nozzle_active_, 0, "workspace_nozzle_active", subjects_);
        UI_MANAGED_SUBJECT_INT(nozzle_editable_, 0, "workspace_nozzle_editable", subjects_);
        overview_current_.ensure_size(5);
        overview_target_.ensure_size(5);
        UI_MANAGED_SUBJECT_INT(nozzle_has_color_, 0, "workspace_nozzle_has_color", subjects_);
        UI_MANAGED_SUBJECT_COLOR(nozzle_color_, lv_color_hex(kAmsDefaultSlotColor),
                                "workspace_nozzle_color", subjects_);
        UI_MANAGED_SUBJECT_STRING(nozzle_title_, nozzle_title_buf_, "喷头 1",
                                  "workspace_nozzle_title", subjects_);
        UI_MANAGED_SUBJECT_STRING(nozzle_temperature_, nozzle_temperature_buf_, "—°C",
                                  "workspace_nozzle_temperature", subjects_);
        UI_MANAGED_SUBJECT_STRING(nozzle_target_, nozzle_target_buf_, "—°C",
                                  "workspace_nozzle_target", subjects_);
        UI_MANAGED_SUBJECT_STRING(nozzle_material_, nozzle_material_buf_, "等待耗材信息",
                                  "workspace_nozzle_material", subjects_);
        UI_MANAGED_SUBJECT_STRING(nozzle_source_, nozzle_source_buf_, "等待供料状态",
                                  "workspace_nozzle_source", subjects_);
        UI_MANAGED_SUBJECT_STRING(manual_hint_, manual_hint_buf_, "",
                                  "workspace_manual_hint", subjects_);
        UI_MANAGED_SUBJECT_INT(list_count_, 0, "workspace_spool_count", subjects_);
        UI_MANAGED_SUBJECT_INT(file_count_, 0, "workspace_file_count", subjects_);
        UI_MANAGED_SUBJECT_INT(confirmed_, 0, "workspace_prepared", subjects_);
        UI_MANAGED_SUBJECT_INT(settings_ready_, 0, "workspace_settings_ready", subjects_);
        settings_labels_.ensure_size(5);
        settings_enabled_.ensure_size(5);
        for (int i = 0; i < 5; ++i)
            settings_enabled_.set_int(i, -1);
        UI_MANAGED_SUBJECT_INT(auto_sensor_, 1, "workspace_auto_sensor", subjects_);
        UI_MANAGED_SUBJECT_INT(auto_enabled_, -1, "workspace_auto_enabled", subjects_);
        auto_values_.ensure_size(5);
        UI_MANAGED_SUBJECT_INT(progress_, 0, "workspace_progress", subjects_);
        UI_MANAGED_SUBJECT_STRING(title_, title_buf_, "耗材操作", "workspace_title", subjects_);
        UI_MANAGED_SUBJECT_STRING(status_, status_buf_, "请选择需要的操作", "workspace_status",
                                  subjects_);
        UI_MANAGED_SUBJECT_STRING(detail_, detail_buf_, "", "workspace_detail", subjects_);
        UI_MANAGED_SUBJECT_STRING(form_material_, form_material_buf_, "PLA", "workspace_material",
                                  subjects_);
        UI_MANAGED_SUBJECT_STRING(form_color_, form_color_buf_, "D68459", "workspace_color",
                                  subjects_);
        UI_MANAGED_SUBJECT_STRING(form_weight_, form_weight_buf_, "", "workspace_weight",
                                  subjects_);
        UI_MANAGED_SUBJECT_STRING(form_vendor_, form_vendor_buf_, "Generic", "workspace_vendor",
                                  subjects_);
        UI_MANAGED_SUBJECT_STRING(file_info_, file_info_buf_, "插入 U 盘后刷新文件列表",
                                  "workspace_file_info", subjects_);
        UI_MANAGED_SUBJECT_STRING(version_, version_buf_, "", "workspace_version", subjects_);
    });
}
void MaterialWorkspace::open(int head, int unit, bool library, std::function<void(int)> on_return,
                             int local_slot) {
    if (subjects_initialized_ && lv_subject_get_int(&busy_))
        return;
    ++session_;
    slot_task_ = {};
    deferred_slot_action_.clear();
    expected_recovery_ticket_.clear();
    expected_ace_ = -1;
    slot_state_at_ = 0;
    firmware_entry_ = false;
    auto_dry_entry_ = false;
    recovery_notice_ = recovery_request_pending_ = recovery_done_ = false;
    watch_recovery_after_source_ = open_recovered_slot_ = false;
    recovery_seq_ = source_review_seq_ = 0;
    recovery_started_at_ = 0;
    pending_action_.clear();
    head_ = std::clamp(head, 0, 3);
    local_slot_ = local_slot >= 0 ? std::clamp(local_slot, 0, 3) : head_;
    nozzle_draft_dirty_ = false;
    nozzle_draft_extruder_.clear();
    unit_ = unit;
    on_return_ = std::move(on_return);
    library_origin_ = -1;
    original_slot_.reset();
    init_subjects();
    lv_subject_set_int(&edit_external_, 0);
    show(lv_screen_active());
    lv_subject_copy_string(&status_, "请选择需要的操作");
    refresh();
    page(library && !lv_subject_get_int(&external_) ? 1 : 0);
    if (library && !lv_subject_get_int(&external_))
        load_library();
}
void MaterialWorkspace::open_nozzle(int head, std::function<void(int)> on_return) {
    if (subjects_initialized_ && lv_subject_get_int(&busy_))
        return;
    open(head, -1, false, std::move(on_return));
    manual_origin_ = -1;
    page(9);
}
void MaterialWorkspace::open_action(int head, int unit, const std::string& action,
                                    std::function<void(int)> on_return, int local_slot,
                                    const std::string& expected_recovery_ticket,
                                    int expected_ace) {
    if (subjects_initialized_ && lv_subject_get_int(&busy_))
        return;
    open(head, unit, false, std::move(on_return), local_slot);
    auto_dry_entry_ = action == "auto_dry";
    recovery_notice_ = action == "recover";
    expected_recovery_ticket_ = expected_recovery_ticket;
    expected_ace_ = expected_ace;
    lv_subject_copy_string(&confirm_text_, recovery_notice_ ? "清除错误" : "确认操作");
    if (action == "eject" || action == "read_tag" || action == "recover" ||
        action == "confirm_source") {
        deferred_slot_action_ = action;
        lv_subject_copy_string(&status_, "正在核对所选料槽");
        refresh();
        poll_slot_task();
        return;
    }
    act(action);
    if (action == "edit" && lv_subject_get_int(&page_) == 2 &&
        lv_subject_get_int(&edit_external_))
        edit_origin_ = -1;
}
void MaterialWorkspace::open_temperature() {
    if (subjects_initialized_ && lv_subject_get_int(&busy_))
        return;
    open(0, -1);
    page(10);
}
void MaterialWorkspace::open_firmware() {
    if (subjects_initialized_ && lv_subject_get_int(&busy_))
        return;
    ++session_;
    on_return_ = {};
    firmware_entry_ = true;
    init_subjects();
    show(lv_screen_active());
    page(3);
    load_files();
}
lv_obj_t* MaterialWorkspace::create(lv_obj_t* parent) {
    lv_xml_register_component_from_file(
        asset_component_uri("ui_xml/material_workspace.xml").c_str());
    return OverlayBase::create(parent);
}
void MaterialWorkspace::register_callbacks() {
    register_xml_callbacks({
        {"workspace_back",
         [](lv_event_t* e) {
             if (auto* self = from_event<MaterialWorkspace>(e))
                 self->act("back");
         }},
        {"workspace_action",
         [](lv_event_t* e) {
             if (auto* self = from_event<MaterialWorkspace>(e))
                 if (auto* value = static_cast<const char*>(lv_event_get_user_data(e)))
                     self->act(value);
         }},
        {"workspace_spool_select",
         [](lv_event_t* e) {
             if (auto* self = from_event<MaterialWorkspace>(e))
                 if (auto* value = static_cast<const char*>(lv_event_get_user_data(e)))
                     self->choose_spool(std::atoi(value));
         }},
        {"workspace_file_select",
         [](lv_event_t* e) {
             if (auto* self = from_event<MaterialWorkspace>(e))
                 if (auto* value = static_cast<const char*>(lv_event_get_user_data(e)))
                     self->choose_file(std::atoi(value));
         }},

    });
}
void MaterialWorkspace::on_activate() {
    OverlayBase::on_activate();
    visible_ = true;
    observed_extruder_.clear();
    revision_observer_ = observe<int>(
        AmsState::instance().get_ams_data_revision_subject(), this,
        [](MaterialWorkspace* self, int) { self->refresh(); },
        AmsState::instance().get_subjects_lifetime());
    auto& state = get_printer_state();
    connection_observer_ = observe<int>(state.network_state().get_nav_buttons_enabled_subject(),
        this, [](MaterialWorkspace* self, int) {
            self->observed_extruder_.clear();
            self->temperature_observer_.reset();
            self->target_observer_.reset();
            self->overview_heaters_.fill("");
            for (auto& observer : self->overview_current_observers_)
                observer.reset();
            for (auto& observer : self->overview_target_observers_)
                observer.reset();
            self->refresh();
        }, state.get_subjects_lifetime());
    job_observer_ = observe<int>(state.print_state().get_print_lifecycle_subject(),
        this, [](MaterialWorkspace* self, int) { self->refresh(); }, state.get_subjects_lifetime());
    tool_observer_ = observe<int>(ToolState::instance().get_active_tool_subject(),
        this, [](MaterialWorkspace* self, int) { self->refresh_nozzle(); },
        ToolState::instance().get_subjects_lifetime());
    refresh();
    if (lv_subject_get_int(&page_) == 5)
        show_progress();
    slot_poll_pending_ = false;
    poll_slot_task();
    slot_poll_.reset(lv_timer_create([](lv_timer_t* timer) {
        auto* self = static_cast<MaterialWorkspace*>(lv_timer_get_user_data(timer));
        self->refresh();
        self->poll_slot_task();
    }, 1000, this));
}
void MaterialWorkspace::on_deactivating(DeactivateReason) {
    visible_ = false;
    deferred_slot_action_.clear();
    expected_recovery_ticket_.clear();
    if (reading_auto_dry_) {
        reading_auto_dry_ = false;
        lv_subject_set_int(&busy_, 0);
    }
    revision_observer_.reset();
    poll_.reset();
    slot_poll_.reset();
    connection_observer_.reset();
    job_observer_.reset();
    tool_observer_.reset();
    temperature_observer_.reset();
    target_observer_.reset();
    for (auto& observer : overview_current_observers_)
        observer.reset();
    for (auto& observer : overview_target_observers_)
        observer.reset();
    overview_heaters_.fill("");
    observed_extruder_.clear();
}
void MaterialWorkspace::page(int value) {
    lv_subject_set_int(&page_, value);
    const char* titles[] = {"耗材操作", "本地耗材库", "编辑线盘", "本地固件升级", "确认固件升级",
                            "升级进度", "确认操作",   "自动烘干", "耗材设置", "喷头详情", "温度"};
    lv_subject_copy_string(&title_, recovery_notice_ && value == 6 ? "处理耗材错误" : titles[value]);
    if (value != 5)
        poll_.reset();
    if (value == 0)
        refresh();
    if (auto* scroll = overlay_root_ ? lv_obj_find_by_name(overlay_root_, "overlay_content") : nullptr) {
        lv_obj_update_layout(scroll);
        lv_obj_scroll_to_y(scroll, 0, LV_ANIM_OFF);
    }
}
std::string MaterialWorkspace::input(const char* name) const {
    auto* field = overlay_root_ ? lv_obj_find_by_name(overlay_root_, name) : nullptr;
    return field ? lv_textarea_get_text(field) : "";
}
void MaterialWorkspace::refresh() {
    auto* backend = AmsState::instance().get_backend();
    auto info = backend ? backend->get_system_info() : AmsSystemInfo{};
    const bool external = material_workspace::uses_external_spools(info);
    if (material_workspace::supports_head_inventory(info))
        lv_subject_set_int(&external_, external);
    const int current_page = lv_subject_get_int(&page_);
    if (external && !firmware_entry_ &&
        (current_page == 1 || current_page == 6 || current_page == 7 || current_page == 8)) {
        page(0);
        return;
    }
    slot_ = -1;
    const int requested_unit = expected_ace_ >= 0
        ? u1_filament::ace_unit_for_address(info, expected_ace_) - 1 : unit_;
    ace_index_ = material_workspace::ace_index(info, requested_unit).value_or(-1);
    bool connected = false;
    bool bay_present = false;
    bool route_mapped = external;
    if (external) {
        if (const auto* slot = material_workspace::head_slot(info, head_)) {
            slot_ = slot->global_index;
            connected = info.units.front().connected;
            if (lv_subject_get_int(&page_) == 0) {
                const char* loaded = backend->slot_is_actively_loaded(slot_)
                                         ? "已装载 · 设备已确认"
                                     : slot->status == SlotStatus::UNKNOWN ? "装载状态待确认"
                                     : slot->status == SlotStatus::BLOCKED ? "料路异常"
                                                                          : "未装载到喷头";
                lv_subject_copy_string(&detail_,
                    fmt::format("喷头 {} · 外置料卷\n{}\n{}", head_ + 1,
                                slot->material.empty() ? "未设置耗材" : slot->material, loaded)
                        .c_str());
            }
        }
    } else if (requested_unit >= 0 && static_cast<size_t>(requested_unit + 1) < info.units.size()) {
        const auto& unit = info.units[static_cast<size_t>(requested_unit + 1)];
        connected = unit.connected;
        for (const auto& slot : unit.slots) {
            if (slot.slot_index != local_slot_) continue;
            bay_present = slot.is_present();
            route_mapped = slot.mapped_tool >= 0 && slot.mapped_tool < 4;
            if (route_mapped) head_ = slot.mapped_tool;
            slot_ = slot.global_index;
            const auto upstream = material_workspace::head_physical_source(backend, head_);
            const auto* physical = material_workspace::head_slot(info, head_);
            const bool current = upstream && *upstream == slot_ && physical &&
                material_workspace::head_physical_presence(backend, head_) == true;
            std::string quantity = slot.remaining_weight_g >= 0
                                       ? fmt::format("约 {:.0f} g", slot.remaining_weight_g)
                                       : "余量未知";
            if (lv_subject_get_int(&page_) == 0)
                lv_subject_copy_string(
                    &detail_,
                    fmt::format("{} · {} 槽{}\n{} · {}\n{}", unit.name, local_slot_ + 1,
                                route_mapped ? fmt::format(" → 喷头 {}", head_ + 1) : " · 未接入 ACE 供料",
                                slot.material.empty() ? "待识别" : slot.material, quantity,
                                current ? "当前供料已确认" : "正在查看候选耗材")
                        .c_str());
        }
    }
    lv_subject_set_int(&route_mapped_, route_mapped);
    const bool online =
        lv_subject_get_int(get_printer_state().network_state().get_nav_buttons_enabled_subject()) ==
        1;
    const bool job =
        lv_subject_get_int(get_printer_state().print_state().get_job_holds_machine_subject()) != 0;
    const bool task_fresh = slot_task_.valid && lv_tick_elaps(slot_state_at_) < 5000;
    lv_subject_set_int(&ready_, online && connected && slot_ >= 0 && (external || ace_index_ >= 0) &&
                                    (external || (task_fresh && !slot_task_.busy && !slot_task_.held && !slot_task_.stop_fault)) &&
                                    !info.path_held(head_) &&
                                    !ams_action_holds_system(info.action) && !job);
    const bool matching_ace = ace_index_ >= 0 && (expected_ace_ < 0 || expected_ace_ == ace_index_);
    lv_subject_set_int(&unload_ready_, !external && matching_ace && route_mapped &&
        online && connected && task_fresh && !ams_action_holds_system(info.action) && !job &&
        material_workspace::can_unload_ace_nozzle(slot_task_, slot_,
            material_workspace::head_physical_source(backend, head_),
            material_workspace::head_physical_presence(backend, head_)));
    lv_subject_set_int(&eject_ready_, !external && matching_ace && u1_filament::can_exit_ace(
        online && connected, bay_present, !ams_action_holds_system(info.action) && !job,
        task_fresh, slot_task_.busy, slot_task_.stop_fault, slot_task_.held, slot_task_.recovery));
    lv_subject_set_int(&source_ready_, !external && online && connected && route_mapped &&
        matching_ace && bay_present && task_fresh && !slot_task_.busy &&
        (!job || get_printer_state().print_state().get_print_lifecycle() == PrintState::Paused) &&
        material_workspace::head_physical_presence(backend, head_) == true &&
        !material_workspace::head_physical_source(backend, head_).has_value());
    lv_subject_set_int(&recovery_needed_, slot_task_.needs_clear);
    lv_subject_set_int(&task_stoppable_, !external && online && task_fresh &&
        slot_task_.stop_target.has_value() && !slot_stop_pending_);
    if (recovery_notice_ && !recovery_done_ && !recovery_request_pending_) {
        if (!recovery_seq_ && watch_recovery_after_source_ && slot_task_.kind == "recover" &&
            slot_task_.seq > 0 && slot_task_.seq != source_review_seq_) {
            recovery_seq_ = slot_task_.seq;
            recovery_started_at_ = lv_tick_get();
            lv_subject_copy_string(&detail_, "正在复核停稳，请稍候。");
            lv_subject_copy_string(&confirm_text_, "处理中");
        }
        if (recovery_seq_ > 0 && slot_task_.seq == recovery_seq_ &&
            slot_task_.terminal_result && !slot_task_.busy)
            finish_recovery_result();
        else if (recovery_seq_ > 0 && lv_tick_elaps(recovery_started_at_) >= 45000)
            finish_recovery_notice("尚未收到停稳结果，请返回通知查看当前状态。");
    }
    update_confirmation_ready();
    lv_subject_set_int(&editable_, online && connected && slot_ >= 0 && !job &&
                                       !ams_action_holds_system(info.action));
    if (!online && lv_subject_get_int(&page_) == 0)
        lv_subject_copy_string(&detail_, "设备离线 · 等待耗材状态");
    else if (slot_ < 0 && lv_subject_get_int(&page_) == 0)
        lv_subject_copy_string(&detail_, "等待所选喷头的耗材状态");
    refresh_nozzle();
    refresh_temperatures();
}
void MaterialWorkspace::poll_slot_task() {
    if (!visible_ || firmware_entry_ || ace_index_ < 0 || slot_poll_pending_)
        return;
    slot_poll_pending_ = true;
    const auto token = lifetime_.token();
    const auto session = session_;
    const int ace = ace_index_, slot = local_slot_;
    material_workspace::request("/state", {}, [this, token, session, ace, slot](auto state, std::string error) {
        if (token.expired())
            return;
        slot_poll_pending_ = false;
        if (session != session_ || ace != ace_index_ || slot != local_slot_)
            return;
        slot_task_ = error.empty() ? material_workspace::slot_task_state(state, ace, slot)
                                  : material_workspace::SlotTaskState{};
        slot_state_at_ = lv_tick_get();
        lv_subject_copy_string(&task_message_, error.empty() ? slot_task_.message.c_str()
            : "料槽状态暂不可用，正在重新连接");
        refresh();
        if (slot_task_.valid && !deferred_slot_action_.empty()) {
            const auto action = std::exchange(deferred_slot_action_, {});
            const auto expected = std::exchange(expected_recovery_ticket_, {});
            if (!expected.empty() && slot_task_.recovery_ticket != expected) {
                finish_recovery_notice("错误或设备状态已变化，请返回通知后重试。");
                return;
            }
            act(action);
        }
    });
}
void MaterialWorkspace::stop_slot_task() {
    refresh();
    if (!lv_subject_get_int(&task_stoppable_))
        return;
    slot_stop_pending_ = true;
    refresh();
    const auto token = object_lifetime_.token();
    material_workspace::request("/material/stop", slot_task_.stop_target->stop_request(),
        [this, token](auto result, std::string error) {
            if (token.expired()) return;
            slot_stop_pending_ = false;
            lv_subject_copy_string(&status_, !error.empty() ? error.c_str()
                : safe_bool(result, "cancelled", false) ? "已请求停止，等待设备确认"
                                                        : "任务已结束或已变化，请查看当前状态");
            poll_slot_task();
            refresh();
        }, true);
}
void MaterialWorkspace::refresh_temperatures() {
    if (binding_temperatures_)
        return;
    binding_temperatures_ = true;
    auto* backend = AmsState::instance().get_backend();
    const auto info = backend ? backend->get_system_info() : AmsSystemInfo{};
    auto& temperatures = get_printer_state().temperature_state();
    const bool online = lv_subject_get_int(
        get_printer_state().network_state().get_nav_buttons_enabled_subject()) == 1;
    for (int i = 0; i < 5; ++i) {
        const auto* head_slot = i < 4 ? material_workspace::head_slot(info, i) : nullptr;
        // Only backend-supplied head/heater mappings identify physical nozzles.
        const std::string name = !online ? "" : i == 4 ? "heater_bed"
                                                    : head_slot ? head_slot->extruder_name : "";
        SubjectLifetime current_lifetime, target_lifetime;
        auto* current = name.empty() ? nullptr : i == 4
            ? temperatures.get_bed_temp_subject(current_lifetime)
            : temperatures.get_extruder_temp_subject(name, current_lifetime);
        auto* target = name.empty() ? nullptr : i == 4
            ? temperatures.get_bed_target_subject(target_lifetime)
            : temperatures.get_extruder_target_subject(name, target_lifetime);
        if (overview_heaters_[i] != name ||
            (visible_ && current && !overview_current_observers_[i])) {
            overview_heaters_[i] = name;
            overview_current_observers_[i].reset();
            overview_target_observers_[i].reset();
            if (visible_ && current)
                overview_current_observers_[i] = observe<int>(current, this,
                    [](MaterialWorkspace* self, int) { self->refresh_temperatures(); },
                    current_lifetime);
            if (visible_ && target)
                overview_target_observers_[i] = observe<int>(target, this,
                    [](MaterialWorkspace* self, int) { self->refresh_temperatures(); },
                    target_lifetime);
        }
        overview_current_.set_string(i, current
            ? fmt::format("{:.0f}°C", temperature::deci_to_degrees_f(lv_subject_get_int(current)))
            : "—°C");
        overview_target_.set_string(i, target
            ? fmt::format("目标 {:.0f}°C", temperature::deci_to_degrees_f(lv_subject_get_int(target)))
            : "目标 —°C");
    }
    binding_temperatures_ = false;
}
void MaterialWorkspace::refresh_nozzle() {
    auto* backend = AmsState::instance().get_backend();
    const auto info = backend ? backend->get_system_info() : AmsSystemInfo{};
    const auto* head_slot = material_workspace::head_slot(info, head_);
    const bool online = lv_subject_get_int(
        get_printer_state().network_state().get_nav_buttons_enabled_subject()) == 1;
    lv_subject_set_int(&nozzle_head_, head_);
    lv_subject_copy_string(&nozzle_title_, fmt::format("喷头{} · {}/4", head_ + 1, head_ + 1).c_str());
    const std::string extruder = head_slot ? head_slot->extruder_name : "";
    if (observed_extruder_ != extruder) {
        observed_extruder_ = extruder;
        temperature_observer_.reset();
        target_observer_.reset();
        auto& temperatures = get_printer_state().temperature_state();
        SubjectLifetime current_lifetime, target_lifetime;
        if (visible_ && !extruder.empty()) {
            if (auto* current = temperatures.get_extruder_temp_subject(extruder, current_lifetime))
                temperature_observer_ = observe<int>(current, this,
                    [](MaterialWorkspace* self, int) { self->refresh_nozzle(); }, current_lifetime);
            if (auto* target = temperatures.get_extruder_target_subject(extruder, target_lifetime))
                target_observer_ = observe<int>(target, this,
                    [](MaterialWorkspace* self, int) { self->refresh_nozzle(); }, target_lifetime);
        }
    }
    auto& temperatures = get_printer_state().temperature_state();
    auto* current = extruder.empty() ? nullptr : temperatures.get_extruder_temp_subject(extruder);
    auto* target = extruder.empty() ? nullptr : temperatures.get_extruder_target_subject(extruder);
    lv_subject_copy_string(&nozzle_temperature_, online && current
        ? fmt::format("{:.0f}°C", temperature::deci_to_degrees_f(lv_subject_get_int(current))).c_str()
        : "—°C");
    if (nozzle_draft_extruder_ != extruder) {
        nozzle_draft_extruder_ = extruder;
        nozzle_draft_dirty_ = false;
    }
    if (online && target) {
        const int actual_target = static_cast<int>(std::lround(
            temperature::deci_to_degrees_f(lv_subject_get_int(target))));
        if (!nozzle_draft_dirty_)
            nozzle_draft_c_ = actual_target;
        else if (actual_target == nozzle_draft_c_)
            nozzle_draft_dirty_ = false;
        lv_subject_copy_string(&nozzle_target_, fmt::format("{}°C", nozzle_draft_c_).c_str());
    } else
        lv_subject_copy_string(&nozzle_target_, "—°C");
    lv_subject_set_int(&nozzle_target_dirty_, nozzle_draft_dirty_);
    const bool job = lv_subject_get_int(
        get_printer_state().print_state().get_job_holds_machine_subject()) != 0;
    const bool head_ready = online && head_slot && !info.units.empty() &&
        info.units.front().connected && !ams_action_holds_system(info.action) &&
        !info.path_held(head_) && !job;
    lv_subject_set_int(&nozzle_activation_ready_, head_ready);
    lv_subject_set_int(&nozzle_active_,
        lv_subject_get_int(ToolState::instance().get_active_tool_subject()) == head_);
    lv_subject_set_int(&nozzle_heat_ready_, online && current && target &&
        !extruder.empty() && !job && get_temperature_controller() && get_moonraker_api());
    SlotInfo material{};
    std::string source = "等待供料状态";
    bool identified = false;
    const auto presence = online && head_slot
        ? material_workspace::head_physical_presence(backend, head_) : std::nullopt;
    const bool present = presence == true;
    if (online && head_slot) {
        source = !presence ? "喷头到料待核" : present ? "有料 · 来源待确认" : "喷头无料";
        if (present && (material_workspace::uses_external_spools(info) ||
            backend->head_source_slot(head_, info) == head_slot->global_index)) {
            source = material_workspace::uses_external_spools(info) ? "外置料卷" : "手动／侧供料";
            material = *head_slot;
            identified = true;
        }
        if (present) {
            if (const auto upstream = material_workspace::head_physical_source(backend, head_)) {
                const auto* source_unit = info.unit_for_slot(*upstream);
                if (source_unit && source_unit->connected) {
                    material = backend->get_slot_info(*upstream);
                    source = fmt::format("{} · {} 槽", source_unit->name, material.slot_index + 1);
                    identified = true;
                }
            }
        }
    }
    lv_subject_copy_string(&nozzle_material_, !online || !head_slot ? "等待耗材信息"
        : !presence ? "喷头到料待核" : !present ? "喷头无料" : !identified ? "来源待确认"
        : material.material.empty() ? "未设置耗材" : material.material.c_str());
    lv_subject_copy_string(&nozzle_source_, source.c_str());
    const bool has_color = identified && (material.color_rgb != kAmsDefaultSlotColor ||
                                          !material.color_name.empty());
    if (has_color)
        lv_subject_set_color(&nozzle_color_, lv_color_hex(material.color_rgb));
    lv_subject_set_int(&nozzle_has_color_, has_color);
    lv_subject_set_int(&nozzle_editable_, head_ready &&
        (material_workspace::uses_external_spools(info) ||
         material_workspace::head_physical_source(backend, head_).has_value()));
    const auto refusal = material_workspace::manual_move_refusal(head_);
    lv_subject_set_int(&manual_ready_, refusal.empty());
    lv_subject_copy_string(&manual_hint_, refusal.empty() ? "每次 5 mm · 操作当前喷头" : refusal.c_str());
}

void MaterialWorkspace::browse_head(int delta) {
    if (lv_subject_get_int(&busy_))
        return;
    const int next = std::clamp(head_ + delta, 0, 3);
    if (next == head_)
        return;
    head_ = next;
    nozzle_draft_dirty_ = false;
    nozzle_draft_extruder_.clear();
    refresh();
}

void MaterialWorkspace::stage_nozzle_target(int delta) {
    refresh_nozzle();
    auto* controller = get_temperature_controller();
    if (!controller || !lv_subject_get_int(&nozzle_heat_ready_))
        return;
    const int ceiling = static_cast<int>(controller->keypad_max_for(nozzle_draft_extruder_,
        controller->effective_keypad_max(HeaterType::Nozzle, 300.0f)));
    const int next = std::clamp(nozzle_draft_c_ + delta, 0, ceiling);
    if (next == nozzle_draft_c_)
        return;
    nozzle_draft_c_ = next;
    nozzle_draft_dirty_ = true;
    lv_subject_set_int(&nozzle_target_dirty_, 1);
    lv_subject_copy_string(&nozzle_target_, fmt::format("{}°C", nozzle_draft_c_).c_str());
}

void MaterialWorkspace::apply_nozzle_target() {
    refresh_nozzle();
    auto* controller = get_temperature_controller();
    if (!controller || !lv_subject_get_int(&nozzle_heat_ready_) || !nozzle_draft_dirty_ ||
        nozzle_draft_extruder_.empty())
        return;
    const auto token = object_lifetime_.token();
    const unsigned session = session_;
    lv_subject_set_int(&busy_, 1);
    lv_subject_copy_string(&status_, "正在设置目标温度");
    SendOptions options;
    options.on_success = [this, token, session] {
        token.defer("MaterialWorkspace::temperature_requested", [this, session] {
            if (session != session_)
                return;
            lv_subject_set_int(&busy_, 0);
            lv_subject_copy_string(&status_, "设置已受理，温度以设备反馈为准");
            refresh_nozzle();
        });
    };
    options.on_error = [this, token, session](const MoonrakerError&) {
        token.defer("MaterialWorkspace::temperature_failed", [this, session] {
            if (session != session_)
                return;
            lv_subject_set_int(&busy_, 0);
            lv_subject_copy_string(&status_, "目标温度设置失败，请重试");
            refresh_nozzle();
        });
    };
    // This targets the browsed head by its real heater name; it does not mount it.
    controller->set_target(nozzle_draft_extruder_, nozzle_draft_c_, std::move(options));
}

void MaterialWorkspace::edit_nozzle_material() {
    refresh_nozzle();
    auto* backend = AmsState::instance().get_backend();
    if (!backend || !lv_subject_get_int(&nozzle_editable_))
        return;
    const auto info = backend->get_system_info();
    if (material_workspace::uses_external_spools(info)) {
        unit_ = -1;
        refresh();
        edit_external();
        return;
    }
    const auto source = material_workspace::head_physical_source(backend, head_);
    if (!source)
        return;
    for (size_t u = 1; u < info.units.size(); ++u) {
        for (const auto& slot : info.units[u].slots) {
            if (slot.global_index == *source) {
                unit_ = static_cast<int>(u) - 1;
                local_slot_ = slot.slot_index;
                act("edit");
                return;
            }
        }
    }
}

void MaterialWorkspace::activate_head() {
    refresh();
    auto* motion = lv_xml_get_subject(nullptr, kFilamentMotionSubjectName);
    if (!lv_subject_get_int(&nozzle_activation_ready_) ||
        (motion && lv_subject_get_int(motion) != static_cast<int>(FilamentMotion::Idle))) {
        lv_subject_copy_string(&status_, "请等待设备空闲并确认料路位置后激活喷头");
        return;
    }

    // Browsing never mounts a head. Only this explicit action enters the same
    // confirmation, print and filament-operation gates as the home switcher.
    // An accepted request is not evidence of a mounted head: refresh_nozzle()
    // continues to gate manual extrusion on the live tool/extruder readback.
    const auto token = object_lifetime_.token();
    const unsigned session = session_;
    ToolChangeHooks hooks;
    hooks.owner_token = token;
    hooks.on_success = [this, token, session] {
        token.defer("MaterialWorkspace::head_requested", [this, session] {
            if (session != session_)
                return;
            lv_subject_copy_string(&status_, "已请求激活喷头，请等待设备确认");
            refresh();
        });
    };
    hooks.on_failed = [this, token, session] {
        token.defer("MaterialWorkspace::head_failed", [this, session] {
            if (session != session_)
                return;
            lv_subject_copy_string(&status_, "喷头激活失败，请查看设备提示");
            NOTIFY_ERROR("喷头激活失败，请查看设备提示");
            refresh();
        });
    };
    const auto verdict = offer_tool_change(get_printer_state(), head_, std::move(hooks));
    if (verdict == ToolChangeVerdict::AlreadyActive)
        lv_subject_copy_string(&status_, "当前喷头已激活，温度就绪后可进退料");
}

void MaterialWorkspace::edit_external() {
    auto* backend = AmsState::instance().get_backend();
    if (!backend || !lv_subject_get_int(&editable_)) {
        lv_subject_copy_string(&status_, "请等待设备在线且空闲后编辑");
        return;
    }
    original_slot_ = backend->get_slot_info(slot_);
    edit_origin_ = lv_subject_get_int(&page_);
    lv_subject_set_int(&edit_external_, 1);
    lv_subject_copy_string(&form_material_, original_slot_->material.c_str());
    lv_subject_copy_string(&form_vendor_, original_slot_->brand.c_str());
    lv_subject_copy_string(&form_color_, fmt::format("{:06X}", original_slot_->color_rgb).c_str());
    lv_subject_copy_string(&form_weight_, "");
    page(2);
}

void MaterialWorkspace::return_from_editor() {
    if (edit_origin_ < 0) {
        if (on_return_)
            on_return_(head_);
        nav::go_back();
    } else
        page(edit_origin_);
}

void MaterialWorkspace::save_external() {
    auto* backend = AmsState::instance().get_backend();
    if (!backend || !original_slot_ || !lv_subject_get_int(&editable_) ||
        !material_workspace::uses_external_spools(backend->get_system_info()) ||
        slot_ != original_slot_->global_index) {
        lv_subject_copy_string(&status_, "设备或供料方式已变化，请返回后重新编辑");
        return;
    }
    const auto current = backend->get_slot_info(slot_);
    if (current.material != original_slot_->material || current.brand != original_slot_->brand ||
        current.color_rgb != original_slot_->color_rgb ||
        current.spoolman_id != original_slot_->spoolman_id) {
        lv_subject_copy_string(&status_, "耗材信息已变化，请返回后重新编辑");
        return;
    }
    auto edited = current;
    edited.material = input("spool_material");
    edited.brand = input("spool_vendor");
    std::string color = input("spool_color");
    if (!color.empty() && color.front() == '#')
        color.erase(0, 1);
    if (edited.material.empty() || !IMoonrakerAPI::is_safe_material_param(edited.material) ||
        color.size() != 6 ||
        !std::all_of(color.begin(), color.end(), [](unsigned char c) {
            return (c >= '0' && c <= '9') || (c >= 'A' && c <= 'F') || (c >= 'a' && c <= 'f');
        })) {
        lv_subject_copy_string(&status_, "请输入耗材类型和六位 HEX 颜色");
        return;
    }
    edited.color_rgb = static_cast<uint32_t>(std::strtoul(color.c_str(), nullptr, 16));
    // Identity only: leave the device's weight and loaded state untouched.
    const auto lane = backend->lane_id(slot_);
    const auto filed_before = helix::ams::pending_filings(lane);
    const auto result = AmsState::instance().commit_slot_edit(slot_, current, edited);
    if (!result.success() && !result.partially_applied) {
        lv_subject_copy_string(&status_, result.user_msg.c_str());
        return;
    }
    lv_subject_set_int(&busy_, 1);
    lv_subject_copy_string(&status_, "正在保存，等待设备确认");
    const auto token = object_lifetime_.token();
    const unsigned session = session_;
    const std::string partial = result.partially_applied ? result.user_msg : "";
    helix::ams::await_pending(lane, filed_before,
        [this, token, session, partial](helix::ams::PendingOutcome outcome, const std::string& reason) {
            token.defer("MaterialWorkspace::external_saved", [this, session, partial, outcome, reason] {
                if (session != session_)
                    return;
                lv_subject_set_int(&busy_, 0);
                if (outcome != helix::ams::PendingOutcome::Echoed) {
                    lv_subject_copy_string(&status_, reason.empty() ? "保存未确认，请检查连接后重试"
                                                                    : reason.c_str());
                    return;
                }
                original_slot_.reset();
                return_from_editor();
                lv_subject_copy_string(&status_, partial.empty() ? "耗材信息已保存" : partial.c_str());
                if (!partial.empty())
                    NOTIFY_WARNING("{}", partial);
                refresh();
            });
        });
}

void MaterialWorkspace::move_filament(int millimeters) {
    const auto refusal = material_workspace::manual_move_refusal(head_);
    if (!refusal.empty()) {
        lv_subject_copy_string(&status_, refusal.c_str());
        return;
    }
    lv_subject_set_int(&busy_, 1);
    motion_rpc_pending_ = true;
    lv_subject_copy_string(&status_, "正在执行手动进退料");
    auto* timer = lv_timer_create([](lv_timer_t* timer) {
        auto* self = static_cast<MaterialWorkspace*>(lv_timer_get_user_data(timer));
        self->motion_timer_.release(); // One shot; LVGL frees it after the callback.
        if (!self->motion_rpc_pending_)
            self->finish_motion();
    }, manual_motion_duration_ms(5, SettingsManager::instance().get_extrude_speed()), this);
    lv_timer_set_repeat_count(timer, 1);
    motion_timer_.reset(timer);
    const auto token = object_lifetime_.token();
    material_workspace::manual_move(head_, millimeters, [this, token](auto, std::string error) {
        if (token.expired())
            return;
        motion_rpc_pending_ = false;
        lv_subject_copy_string(&status_, error.empty() ? "命令已受理，装载状态以设备反馈为准"
                                                     : error.c_str());
        if (!error.empty())
            NOTIFY_ERROR("{}", error);
        if (!error.empty())
            motion_timer_.reset();
        if (!motion_timer_)
            finish_motion();
    });
    if (motion_rpc_pending_) {
        if (auto* motion = lv_xml_get_subject(nullptr, kFilamentMotionSubjectName)) {
            publishes_motion_ = true;
            lv_subject_set_int(motion, static_cast<int>(millimeters > 0 ? FilamentMotion::Extrude
                                                                      : FilamentMotion::Retract));
        }
    }
}

void MaterialWorkspace::finish_motion() {
    if (publishes_motion_) {
        publishes_motion_ = false;
        if (auto* motion = lv_xml_get_subject(nullptr, kFilamentMotionSubjectName))
            lv_subject_set_int(motion, static_cast<int>(FilamentMotion::Idle));
    }
    lv_subject_set_int(&busy_, 0);
    if (visible_)
        refresh_nozzle();
}

std::string MaterialWorkspace::load_refusal() {
    auto* backend = AmsState::instance().get_backend();
    if (!backend || !lv_subject_get_int(&ready_))
        return "请等待设备空闲、在线并确认料路位置";
    const auto info = backend->get_system_info();
    if (unit_ < 0 || static_cast<size_t>(unit_ + 1) >= info.units.size())
        return "请重新选择候选耗材";
    const auto& unit = info.units[static_cast<size_t>(unit_) + 1];
    const auto selected = std::find_if(unit.slots.begin(), unit.slots.end(), [this](const SlotInfo& slot) {
        return slot.global_index == slot_ && slot.mapped_tool == head_ && slot.slot_index == local_slot_;
    });
    if (!unit.connected || selected == unit.slots.end())
        return "候选耗材设备已离线或连接已变化";
    if (selected->status == SlotStatus::EMPTY)
        return "空槽不可装料";
    if (selected->status != SlotStatus::AVAILABLE && selected->status != SlotStatus::FROM_BUFFER &&
        selected->status != SlotStatus::LOADED)
        return "请先确认候选耗材状态";
    const auto source = backend->upstream_slot(head_);
    const auto* physical = material_workspace::head_slot(info, head_);
    if (source && *source == slot_ && physical &&
        backend->slot_is_actively_loaded(physical->global_index))
        return "所选耗材已是当前供料";
    const auto* head_slot = material_workspace::head_slot(info, head_);
    if (!head_slot || (backend->slot_is_actively_loaded(head_slot->global_index) && !source))
        return "请先确认当前喷头料路";
    return {};
}

void MaterialWorkspace::run(const std::string& action, const nlohmann::json& args) {
    lv_subject_set_int(&busy_, 1);
    const auto token = object_lifetime_.token();
    material_workspace::command(action, args, [this, token, action](auto, std::string error) {
        if (token.expired())
            return;
        lv_subject_set_int(&busy_, 0);
        lv_subject_copy_string(&status_,
                               error.empty() ? "命令已受理，结果以设备状态为准" : error.c_str());
        refresh();
        if (error.empty() && (action == "spool_add" || action == "spool_set") &&
            lv_subject_get_int(&page_) == 2) {
            page(edit_origin_);
            if (edit_origin_ == 1)
                load_library();
        }
        if (error.empty() && lv_subject_get_int(&page_) == 8)
            load_settings();
        if (error.empty() && action == "auto_dry" && visible_ && lv_subject_get_int(&page_) == 7)
            act("auto_dry");
    });
}
void MaterialWorkspace::load_library() {
    lv_subject_copy_string(&status_, "正在读取设备耗材库");
    auto token = lifetime_.token();
    material_workspace::request("/state", {}, [this, token](auto data, std::string error) {
        if (token.expired())
            return;
        if (!error.empty()) {
            lv_subject_copy_string(&status_, error.c_str());
            return;
        }
        spools_ = nlohmann::json::array();
        const auto library = data.contains("spools") && data["spools"].is_object()
                                 ? data["spools"]
                                 : nlohmann::json::object();
        if (library.is_object())
            for (auto it = library.begin(); it != library.end(); ++it) {
                if (!it.value().is_object())
                    continue;
                auto spool = it.value();
                spool["id"] = it.key();
                spools_.push_back(spool);
            }
        labels_.ensure_size(spools_.size());
        for (size_t i = 0; i < spools_.size(); ++i) {
            const auto& spool = spools_[i];
            const auto weight = spool.find("weight_g");
            const std::string amount =
                weight != spool.end() && weight->is_number() && weight->get<double>() >= 0
                    ? fmt::format("{:.0f} g", weight->get<double>())
                    : "余量未知";
            labels_.set_string(
                i,
                fmt::format("{}  {}  ·  {}", safe_string(spool, "material", "待识别"),
                            safe_string(spool, "label", safe_string(spool, "vendor", "")), amount));
        }
        lv_subject_set_int(&list_count_, spools_.size());
        lv_subject_copy_string(
            &status_, fmt::format("{} 卷耗材 · 点选编辑或绑定当前槽", spools_.size()).c_str());
    });
}
void MaterialWorkspace::choose_spool(int index) {
    if (index < 0 || static_cast<size_t>(index) >= spools_.size())
        return;
    const auto& spool = spools_[index];
    spool_id_ = safe_string(spool, "id", "");
    lv_subject_copy_string(&form_material_, safe_string(spool, "material", "PLA").c_str());
    lv_subject_copy_string(&form_color_, safe_string(spool, "color", "").c_str());
    lv_subject_copy_string(&form_vendor_, safe_string(spool, "vendor", "").c_str());
    const auto weight = spool.find("weight_g");
    const auto text = weight != spool.end() && weight->is_number() && weight->get<double>() >= 0
                          ? fmt::format("{:.0f}", weight->get<double>())
                          : "";
    lv_subject_copy_string(&form_weight_, text.c_str());
    edit_origin_ = 1;
    lv_subject_set_int(&edit_external_, 0);
    page(2);
}
void MaterialWorkspace::load_files() {
    auto token = lifetime_.token();
    material_workspace::request("/firmware/files", {}, [this, token](auto data, std::string error) {
        if (token.expired())
            return;
        if (!error.empty()) {
            lv_subject_copy_string(&status_, error.c_str());
            return;
        }
        files_ = data.value("files", nlohmann::json::array());
        file_labels_.ensure_size(files_.size());
        for (size_t i = 0; i < files_.size(); ++i)
            file_labels_.set_string(i, fmt::format("{}  · {:.1f} MB", files_[i].value("name", ""),
                                                   files_[i].value("size", 0.0) / 1048576));
        lv_subject_set_int(&file_count_, files_.size());
        lv_subject_copy_string(&status_, files_.empty()
                                             ? "未找到固件，请插入 U 盘或放入本地 firmware 目录"
                                             : "选择文件后会复制到本机并校验");
    });
}
void MaterialWorkspace::choose_file(int index) {
    if (lv_subject_get_int(&busy_) || index < 0 || static_cast<size_t>(index) >= files_.size())
        return;
    selected_file_ = files_[index].value("name", "");
    firmware_token_.clear();
    lv_subject_set_int(&confirmed_, 0);
    lv_subject_set_int(&busy_, 1);
    lv_subject_copy_string(&status_, "正在复制、校验固件，请稍候");
    auto token = object_lifetime_.token();
    material_workspace::request(
        "/firmware/prepare", {{"path", files_[index]["path"]}, {"target", "u1"}},
        [this, token](auto data, std::string error) {
            if (token.expired())
                return;
            lv_subject_set_int(&busy_, 0);
            if (!error.empty()) {
                lv_subject_copy_string(&status_, error.c_str());
                return;
            }
            firmware_token_ = data.value("token", "");
            lv_subject_set_int(&confirmed_, !firmware_token_.empty());
            lv_subject_copy_string(&version_, data.value("version", "").c_str());
            lv_subject_copy_string(
                &file_info_,
                fmt::format("{}\nSHA256 {}", selected_file_, data.value("sha256", "")).c_str());
            lv_subject_copy_string(&status_, "完整包分区校验通过，请核对目标版本");
        },
        true);
}
void MaterialWorkspace::show_progress() {
    page(5);
    if (!visible_)
        return;
    firmware_status();
    poll_.reset(lv_timer_create(
        [](lv_timer_t* timer) {
            static_cast<MaterialWorkspace*>(lv_timer_get_user_data(timer))->firmware_status();
        },
        1500, this));
}
void MaterialWorkspace::load_settings() {
    lv_subject_set_int(&settings_ready_, 0);
    for (int i = 0; i < 5; ++i)
        settings_enabled_.set_int(i, -1);
    auto token = lifetime_.token();
    material_workspace::request("/state", {}, [this, token](auto data, std::string error) {
        if (token.expired())
            return;
        if (!error.empty()) {
            lv_subject_copy_string(&status_, error.c_str());
            return;
        }
        const char* keys[] = {"quad_replenish", "pa_sync", "airprint_detection", "pickup_cleaning",
                              "purge_matrix"};
        const char* names[] = {"自动续料", "线盘 PA 同步", "空打检测", "取头清洁", "冲刷矩阵"};
        for (int i = 0; i < 5; ++i) {
            if (!data.contains(keys[i]) || !data[keys[i]].is_boolean()) {
                lv_subject_copy_string(&status_, "当前后端尚未提供完整耗材设置");
                return;
            }
            settings_labels_.set_string(i, names[i]);
            settings_enabled_.set_int(i, safe_bool(data, keys[i]) ? 1 : 0);
        }
        lv_subject_copy_string(&status_, "设置保存在设备端，与网页版共用");
        lv_subject_set_int(&settings_ready_, 1);
    });
}
void MaterialWorkspace::firmware_status() {
    auto token = lifetime_.token();
    material_workspace::request(
        "/firmware/status", {}, [this, token](auto data, std::string error) {
            if (token.expired())
                return;
            if (!error.empty()) {
                lv_subject_copy_string(&status_, error.c_str());
                return;
            }
            const std::string state = data.value("state", "unknown");
            const auto pct = data.find("pct");
            lv_subject_set_int(&progress_, pct != data.end() && pct->is_number()
                                               ? std::clamp(pct->template get<int>(), 0, 100)
                                               : 0);
            lv_subject_copy_string(&status_,
                                   fmt::format("{}\n{}\n当前系统 {}", data.value("msg", ""),
                                               data.value("error", ""),
                                               data.value("current_version", "未知"))
                                       .c_str());
            if (state == "verify_version" || state == "done" || state == "error" ||
                state == "idle" || state == "prepared" || state == "reboot_pending")
                poll_.reset();
        });
}
void MaterialWorkspace::finish_recovery_notice(const std::string& message) {
    recovery_done_ = true;
    lv_subject_copy_string(&detail_, fmt::format("ACE {} · {} 槽\n{}",
        ace_index_ + 1, local_slot_ + 1, message).c_str());
    lv_subject_copy_string(&confirm_text_, open_recovered_slot_ ? "打开原槽" : "返回通知");
    lv_subject_set_int(&confirm_ready_, 1);
    page(6);
}

void MaterialWorkspace::finish_recovery_result() {
    open_recovered_slot_ = slot_task_.recovered_stop && !slot_task_.stop_fault &&
        slot_task_.held && slot_task_.recovery;
    finish_recovery_notice(open_recovered_slot_
        ? "停稳保护已解除。此槽料路仍待核，请打开原槽并选择「退出 ACE」，再重新插料。"
        : slot_task_.message);
}

static bool is_slot_recovery_action(std::string_view action) {
    return action == "recover_source" || action == "recover_auto" ||
        action == "recover_check" || action == "recover_clear";
}

bool MaterialWorkspace::recovery_ready(std::string_view action) {
    auto& state = get_printer_state();
    return !lv_subject_get_int(&external_) && ace_index_ >= 0 &&
        (expected_ace_ < 0 || expected_ace_ == ace_index_) &&
        lv_subject_get_int(state.network_state().get_nav_buttons_enabled_subject()) == 1 &&
        !lv_subject_get_int(state.print_state().get_job_holds_machine_subject()) &&
        lv_tick_elaps(slot_state_at_) < 5000 &&
        material_workspace::next_slot_recovery_action(slot_task_) == action;
}

void MaterialWorkspace::update_confirmation_ready() {
    if (recovery_notice_) {
        const bool available = is_slot_recovery_action(pending_action_) &&
            recovery_ready(pending_action_) &&
            safe_string(pending_args_, "TICKET") == slot_task_.recovery_ticket;
        lv_subject_set_int(&confirm_ready_, recovery_done_ ||
            (!recovery_request_pending_ && !recovery_seq_ && available));
        return;
    }
    lv_subject_set_int(&confirm_ready_, lv_subject_get_int(
        pending_action_ == "eject" ? &eject_ready_ :
        pending_action_ == "unload" ? &unload_ready_ :
        pending_action_ == "confirm_source" ? &source_ready_ : &ready_));
}

void MaterialWorkspace::confirm_recovery() {
    refresh();
    if (recovery_request_pending_ || recovery_seq_ || recovery_done_) return;
    const int ace = ace_index_, slot = local_slot_;
    const auto ticket = safe_string(pending_args_, "TICKET");
    const bool source = pending_action_ == "recover_source";
    if (!recovery_ready(pending_action_) ||
        safe_int(pending_args_, "ACE", -1) != ace || safe_int(pending_args_, "SLOT", -1) != slot ||
        ticket != slot_task_.recovery_ticket ||
        (source && safe_int(pending_args_, "HEAD", -1) != slot_task_.source_head)) {
        finish_recovery_notice("错误或设备状态已变化，请返回通知后重试。");
        return;
    }
    nlohmann::json body = {{"ace", ace}, {"slot", slot}, {"confirmed", true}, {"ticket", ticket}};
    if (source) {
        body["retained"] = true;
        body["head"] = slot_task_.source_head;
        source_review_seq_ = slot_task_.seq;
    } else {
        body["action"] = pending_action_ == "recover_auto" ? "auto" :
                         pending_action_ == "recover_check" ? "check" : "clear";
    }
    const auto token = lifetime_.token();
    const auto session = session_;
    recovery_request_pending_ = true;
    lv_subject_copy_string(&confirm_text_, "处理中");
    update_confirmation_ready();
    material_workspace::request(source ? "/material/source" : "/material/recover", body,
        [this, token, session, ace, slot, source](auto result, std::string error) {
            if (token.expired() || !visible_ || session != session_ ||
                ace != ace_index_ || slot != local_slot_) return;
            recovery_request_pending_ = false;
            if (source && error.empty() && safe_bool(result, "saved")) {
                watch_recovery_after_source_ = true;
                pending_action_.clear();
                deferred_slot_action_ = "recover";
                lv_subject_copy_string(&detail_, "原料路已确认，正在读取停稳状态。");
            } else if (!source && error.empty() && safe_bool(result, "started") &&
                       (recovery_seq_ = safe_int(result, "seq", 0)) > 0) {
                recovery_started_at_ = lv_tick_get();
                lv_subject_copy_string(&detail_, "正在复核停稳，请稍候。");
            } else {
                finish_recovery_notice(!error.empty() ? error :
                    safe_string(result, "message", "未能开始处理，请返回通知后重试。"));
            }
            update_confirmation_ready();
            poll_slot_task();
        }, true);
}

void MaterialWorkspace::act(const std::string& action) {
    if (action == "recover") {
        if (recovery_done_ || recovery_seq_) return;
        if (slot_task_.held && slot_task_.recovery && !slot_task_.busy &&
            !slot_task_.stop_fault && !slot_task_.source_review) {
            open_recovered_slot_ = true;
            finish_recovery_notice("此槽料路仍待核。请打开原槽，选择「退出 ACE」后重新插料。");
            return;
        }
        const auto next = material_workspace::next_slot_recovery_action(slot_task_);
        if (next.empty() || !recovery_ready(next)) {
            finish_recovery_notice(slot_task_.message);
            return;
        }
        act(std::string(next));
        return;
    }
    if (recovery_notice_ && action == "confirm" && recovery_done_ && open_recovered_slot_) {
        recovery_notice_ = recovery_done_ = false;
        pending_action_.clear();
        page(0);
        return;
    }
    if (recovery_notice_ && (action == "back" || (action == "confirm" && recovery_done_))) {
        nav::go_back();
        NotificationManager::instance().show_history();
        return;
    }

    if (action == "stop_slot") {
        stop_slot_task();
        return;
    }
    if (action == "back") {
        if (lv_subject_get_int(&busy_)) {
            lv_subject_copy_string(&status_, "请等待当前操作完成");
            return;
        }
        const int current_page = lv_subject_get_int(&page_);
        lv_subject_copy_string(&status_, "请选择需要的操作");
        if (firmware_entry_) {
            if (current_page == 4)
                page(3);
            else
                nav::go_back();
            return;
        }
        if (current_page == 7 && auto_dry_entry_) {
            nav::go_back();
            return;
        }
        if (current_page == 2) {
            // Form subjects are only a draft. Back never commits them.
            original_slot_.reset();
            return_from_editor();
            return;
        }
        if (current_page == 9 && manual_origin_ >= 0) {
            page(manual_origin_);
            return;
        }
        if (current_page == 1 && library_origin_ >= 0) {
            page(library_origin_);
            return;
        }
        if (current_page == 0 || current_page == 1 || current_page == 9 || current_page == 10) {
            if (on_return_)
                on_return_(head_);
            nav::go_back();
        } else
            page(0);
        return;
    }
    if (lv_subject_get_int(&busy_))
        return;
    refresh();
    if ((action == "load" || action == "unload") && !lv_subject_get_int(&route_mapped_)) {
        lv_subject_copy_string(&status_, "当前槽尚未接入已确认的 ACE 喷头路线");
        return;
    }
    if (action == "temperature_bed") {
        get_global_temp_graph_overlay().open(TempGraphOverlay::Mode::Bed, lv_screen_active());
        return;
    }
    if (action.compare(0, 17, "temperature_head_") == 0 && action.size() == 18) {
        const int selected = action.back() - '0';
        if (selected >= 0 && selected < 4) {
            head_ = selected;
            unit_ = -1;
            manual_origin_ = 10;
            refresh();
            page(9);
        }
        return;
    }
    if (action == "previous_head" || action == "next_head") {
        browse_head(action == "previous_head" ? -1 : 1);
        return;
    }
    if (action == "nozzle_target_down" || action == "nozzle_target_up") {
        stage_nozzle_target(action == "nozzle_target_down" ? -5 : 5);
        return;
    }
    if (action == "nozzle_apply") {
        apply_nozzle_target();
        return;
    }
    if (action == "edit_nozzle_material") {
        edit_nozzle_material();
        return;
    }
    if (action == "manual") {
        manual_origin_ = lv_subject_get_int(&page_);
        page(9);
        return;
    }
    if (action == "activate_head") {
        activate_head();
        return;
    }
    if (action == "extrude" || action == "retract") {
        move_filament(action == "extrude" ? 5 : -5);
        return;
    }
    if (action == "save_spool" && lv_subject_get_int(&edit_external_)) {
        save_external();
        return;
    }
    if (!firmware_entry_ && lv_subject_get_int(&external_)) {
        if (action == "edit")
            edit_external();
        else
            lv_subject_copy_string(&status_, "外置料卷请使用编辑或手动进退料");
        return;
    }
    if (action == "read_tag" && !lv_subject_get_int(&ready_)) {
        lv_subject_copy_string(&status_, "请等待设备空闲、在线并确认料路位置");
        return;
    }
    if (action == "settings") {
        page(8);
        load_settings();
        return;
    }
    if (action.compare(0, 8, "setting_") == 0 && action.size() == 12 &&
        lv_subject_get_int(&settings_ready_)) {
        const int index = action[8] - '0';
        const char* actions[] = {"quad_replenish", "pa_sync", "airprint", "pickup_cleaning",
                                 "purge_matrix"};
        if (index < 0 || index >= 5)
            return;
        const bool on = action.substr(10) == "on";
        run(actions[index], {{index == 4 ? "MATRIX" : "ENABLE", on ? 1 : 0}});
        return;
    }
    if (action == "library") {
        if (lv_subject_get_int(&page_) != 1 && lv_subject_get_int(&page_) != 2)
            library_origin_ = lv_subject_get_int(&page_);
        page(1);
        load_library();
        return;
    }
    if (action == "refresh_library") {
        load_library();
        return;
    }
    if (action == "firmware") {
        page(3);
        load_files();
        return;
    }
    if (action == "refresh_files") {
        load_files();
        return;
    }
    if (action == "upgrade_status") {
        show_progress();
        return;
    }
    if (action == "firmware_next") {
        if (firmware_token_.empty())
            return;
        lv_subject_copy_string(&detail_,
                               fmt::format("升级范围  U1 主机系统、主控 MCU 与四个工具头 MCU\n"
                                           "不包含 ACE 固件\n文件  {}\n版本  "
                                           "{}\n升级期间请保持供电，完成后机器会重启。",
                                           selected_file_, lv_subject_get_string(&version_))
                                   .c_str());
        page(4);
        return;
    }
    if (action == "firmware_start") {
        lv_subject_set_int(&busy_, 1);
        auto token = object_lifetime_.token();
        material_workspace::request(
            "/firmware/start", {{"token", firmware_token_}},
            [this, token](auto, std::string error) {
                if (token.expired())
                    return;
                lv_subject_set_int(&busy_, 0);
                lv_subject_copy_string(&status_, error.empty() ? "升级任务已提交" : error.c_str());
                if (error.empty()) {
                    firmware_token_.clear();
                    lv_subject_set_int(&confirmed_, 0);
                    show_progress();
                }
            },
            true);
        return;
    }
    if (action == "new_spool") {
        spool_id_.clear();
        lv_subject_copy_string(&form_material_, "PLA");
        lv_subject_copy_string(&form_weight_, "");
        lv_subject_copy_string(&form_color_, "");
        lv_subject_copy_string(&form_vendor_, "Generic");
        edit_origin_ = 1;
        lv_subject_set_int(&edit_external_, 0);
        page(2);
        return;
    }
    if (action == "save_spool") {
        auto args = nlohmann::json{{"MATERIAL", input("spool_material")},
                                   {"COLOR", input("spool_color")},
                                   {"VENDOR", input("spool_vendor")}};
        std::string weight = input("spool_weight");
        if (!weight.empty()) {
            char* end = nullptr;
            const double value = std::strtod(weight.c_str(), &end);
            if (!end || *end || !std::isfinite(value) || value < 0 || value > 100000) {
                lv_subject_copy_string(&status_, "请输入有效余量，单位 g");
                return;
            }
            args["WEIGHT"] = value;
        }
        if (input("spool_material").empty()) {
            lv_subject_copy_string(&status_, "请输入耗材类型");
            return;
        }
        if (!spool_id_.empty())
            args["ID"] = spool_id_;
        run(spool_id_.empty() ? "spool_add" : "spool_set", args);
        return;
    }
    if (action == "sync") {
        auto token = object_lifetime_.token();
        lv_subject_set_int(&busy_, 1);
        material_workspace::request(
            "/spoolman/sync", {},
            [this, token](auto, std::string error) {
                if (token.expired())
                    return;
                lv_subject_set_int(&busy_, 0);
                lv_subject_copy_string(&status_,
                                       error.empty() ? "Spoolman 同步已受理" : error.c_str());
            },
            true);
        return;
    }
    if (action == "confirm") {
        if (is_slot_recovery_action(pending_action_)) {
            confirm_recovery();
            return;
        }
        if (recovery_request_pending_) return;
        if (pending_action_ == "eject" || pending_action_ == "unload") refresh();
        if (!lv_subject_get_int(&confirm_ready_)) {
            lv_subject_copy_string(&status_, "请等待设备空闲、在线并确认料路位置");
            return;
        }
        auto* backend = AmsState::instance().get_backend();
        if (pending_action_ == "eject" &&
            (safe_int(pending_args_, "ACE", -1) != ace_index_ ||
             safe_int(pending_args_, "SLOT", -1) != local_slot_)) {
            lv_subject_copy_string(&status_, "所选槽位已变化，请重新确认退出目标");
            page(0);
            return;
        }
        if (pending_action_ == "confirm_source") {
            refresh();
            if (!lv_subject_get_int(&source_ready_) ||
                safe_int(pending_args_, "ACE", -1) != ace_index_ ||
                safe_int(pending_args_, "SLOT", -1) != local_slot_ ||
                safe_int(pending_args_, "HEAD", -1) != head_) {
                lv_subject_copy_string(&status_, "供料状态已变化，请重新选择料槽");
                page(0);
                return;
            }
            const auto token = lifetime_.token();
            const auto session = session_;
            const int ace = ace_index_, slot = local_slot_, head = head_;
            lv_subject_set_int(&busy_, 1);
            material_workspace::request("/material/source",
                {{"head", head}, {"ace", ace}, {"slot", slot}},
                [this, token, session, ace, slot, head](auto result, std::string error) {
                    if (token.expired() || session != session_ || ace != ace_index_ ||
                        slot != local_slot_ || head != head_) return;
                    lv_subject_set_int(&busy_, 0);
                    lv_subject_copy_string(&status_, !error.empty() ? error.c_str() :
                        safe_bool(result, "saved") ? "当前供料槽已保存" :
                        safe_string(result, "message", "供料槽未保存，请重试").c_str());
                    refresh();
                }, true);
            page(0);
            return;
        }
        if (pending_action_ == "load" || pending_action_ == "unload") {
            if (pending_action_ == "load") {
                if (!confirmed_target_ || slot_ != *confirmed_target_) {
                    lv_subject_copy_string(&status_, "候选耗材连接已变化，请重新确认");
                    page(0);
                    return;
                }
                const auto refusal = load_refusal();
                if (!refusal.empty()) {
                    lv_subject_copy_string(&status_, refusal.c_str());
                    page(0);
                    return;
                }
            }
            const auto source = pending_action_ == "unload"
                ? material_workspace::head_physical_source(backend, head_)
                : backend->upstream_slot(head_);
            if (source != confirmed_source_) {
                lv_subject_copy_string(&status_, "当前供料已变化，请重新确认操作");
                page(0);
                return;
            }
            if (pending_action_ == "unload" && (confirmed_target_ != slot_ || source != slot_ ||
                material_workspace::head_physical_presence(backend, head_) != true)) {
                lv_subject_copy_string(&status_, "当前喷头没有已确认料路");
                return;
            }
            auto result = pending_action_ == "load" ? backend->load_filament(slot_)
                                                    : backend->unload_filament(head_);
            lv_subject_copy_string(&status_, result.success() ? "命令已受理，等待实际料路确认"
                                                              : result.user_msg.c_str());
        } else
            run(pending_action_, pending_args_);
        page(0);
        return;
    }
    if (action == "dry") {
        open_environment_for_unit(unit_ + 1);
        return;
    }
    if (action == "edit") {
        if (auto* backend = AmsState::instance().get_backend();
            backend && slot_ >= 0 && lv_subject_get_int(&editable_)) {
            const auto original = backend->get_slot_info(slot_);
            get_ams_edit_overlay().show_for_slot(lv_screen_active(), slot_,
                original, get_moonraker_api(), [original](const auto& result) {
                    if (result.saved)
                        AmsState::instance().commit_slot_edit(result.slot_index, original,
                                                              result.slot_info);
                });
        }
        return;
    }
    if (action == "auto_dry") {
        page(7);
        lv_subject_set_int(&settings_ready_, 0);
        lv_subject_set_int(&auto_enabled_, -1);
        reading_auto_dry_ = true;
        lv_subject_set_int(&busy_, 1);
        auto token = lifetime_.token();
        material_workspace::request("/state", {}, [this, token](auto data, std::string error) {
            if (token.expired())
                return;
            reading_auto_dry_ = false;
            lv_subject_set_int(&busy_, 0);
            if (!error.empty()) {
                lv_subject_copy_string(&status_, error.c_str());
                return;
            }
            const auto units = data.contains("aces") && data["aces"].is_array()
                                   ? data["aces"]
                                   : nlohmann::json::array();
            for (const auto& unit : units) {
                if (safe_int(unit, "idx", -1) != ace_index_)
                    continue;
                const auto cfg = unit.value("auto_dry", nlohmann::json::object());
                if (!cfg.is_object())
                    break;
                const bool sensor =
                    safe_string(unit, "protocol") == "v2" ||
                    (unit.contains("humidity_effective") && unit["humidity_effective"].is_number());
                lv_subject_set_int(&auto_sensor_, sensor);
                lv_subject_set_int(&auto_enabled_, safe_bool(cfg, "enabled") ? 1 : 0);
                lv_subject_set_int(&settings_ready_, 1);
                const char* keys[] = {"temp", "rh_start", "rh_end", "master", "add_time"};
                for (int i = 0; i < 5; ++i) {
                    const auto found = cfg.find(keys[i]);
                    auto_values_.set_string(
                        i, found != cfg.end() && found->is_number() ? found->dump() : "");
                }
                lv_subject_copy_string(
                    &status_, fmt::format("ACE {} · {} · {}", ace_index_ + 1,
                                          safe_bool(cfg, "enabled") ? "已启用" : "已关闭",
                                          safe_bool(unit, "auto_dry_running") ? "正在烘干" : "待机")
                                  .c_str());
                return;
            }
            lv_subject_copy_string(&status_, "当前设备尚未提供自动烘干设置");
        });
        return;
    }
    if (action == "auto_dry_save") {
        nlohmann::json args = {{"ACE", ace_index_}};
        const bool sensor = lv_subject_get_int(&auto_sensor_) != 0;
        const char* keys[] = {"TEMP", "RH_START", "RH_END", "MASTER", "ADD_TIME"};
        double values[5]{};
        for (int i = 0; i < 5; ++i) {
            if ((sensor && i >= 3) || (!sensor && (i == 1 || i == 2)))
                continue;
            const auto text = input(fmt::format("auto_value_{}", i).c_str());
            char* end = nullptr;
            values[i] = std::strtod(text.c_str(), &end);
            if (text.empty() || !end || *end || !std::isfinite(values[i])) {
                lv_subject_copy_string(&status_, "请填写有效的烘干参数");
                return;
            }
            if (i == 0 || i >= 3) {
                if (values[i] != std::floor(values[i])) {
                    lv_subject_copy_string(&status_, "温度、主设备编号和时长需要填写整数");
                    return;
                }
                const double lower = i == 0 ? 35 : i == 3 ? -1 : 0;
                const double upper = i == 0 ? 150 : i == 3 ? 3 : 600;
                if (values[i] < lower || values[i] > upper) {
                    lv_subject_copy_string(&status_, "烘干参数超出可用范围");
                    return;
                }
                args[keys[i]] = static_cast<int>(values[i]);
            } else
                args[keys[i]] = values[i];
        }
        if (sensor && values[2] >= values[1]) {
            lv_subject_copy_string(&status_, "停止湿度需要低于启动湿度");
            return;
        }
        run("auto_dry", args);
        return;
    }
    if (action == "auto_dry_on" || action == "auto_dry_off") {
        run("auto_dry", {{"ACE", ace_index_}, {"ENABLE", action == "auto_dry_on" ? 1 : 0}});
        return;
    }
    if (action == "read_tag") {
        run("read_tag", {{"ACE", ace_index_}, {"SLOT", local_slot_}});
        return;
    }
    if (action == "load") {
        const auto refusal = load_refusal();
        if (!refusal.empty()) {
            lv_subject_copy_string(&status_, refusal.c_str());
            return;
        }
    }
    pending_action_ = action;
    pending_args_ = {{"ACE", ace_index_}, {"SLOT", local_slot_}};
    std::string prompt;
    std::string target_label =
        fmt::format("ACE {} · {} 槽 连接 喷头 {}", ace_index_ + 1, local_slot_ + 1, head_ + 1);
    if (action == "load" || action == "unload") {
        auto* backend = AmsState::instance().get_backend();
        confirmed_source_ = action == "unload"
            ? material_workspace::head_physical_source(backend, head_)
            : backend ? backend->upstream_slot(head_) : std::nullopt;
        confirmed_target_ = slot_;
        if (action == "unload") {
            if (!lv_subject_get_int(&unload_ready_) || confirmed_source_ != slot_) {
                lv_subject_copy_string(&status_, "请确认设备空闲，且当前喷头由所选槽供料");
                return;
            }
            const auto info = backend->get_system_info();
            for (const auto& unit : info.units)
                for (const auto& slot : unit.slots) {
                    if (slot.global_index == *confirmed_source_)
                        target_label = fmt::format("当前供料 {} · {} 槽 连接 喷头 {}", unit.name,
                                                   slot.slot_index + 1, head_ + 1);
                }
        }
    }
    if (is_slot_recovery_action(action)) {
        if (!recovery_ready(action)) {
            finish_recovery_notice(slot_task_.message);
            return;
        }
        target_label = fmt::format("ACE {} · {} 槽", ace_index_ + 1, local_slot_ + 1);
        pending_args_["TICKET"] = slot_task_.recovery_ticket;
        if (action == "recover_source") {
            pending_args_["HEAD"] = slot_task_.source_head;
            prompt = fmt::format("请核对通往喷头 {} 的原卷和原料路，确认透明管里没有其他残料，再点确认。",
                slot_task_.source_head + 1);
            lv_subject_copy_string(&confirm_text_, "确认原料路");
        } else if (action == "recover_clear") {
            prompt = "请先将本槽耗材完全退出 ACE，并确认对应喷头无料。确认后检查并清除本槽的错误。";
            lv_subject_copy_string(&confirm_text_, "清除错误");
        } else {
            prompt = "复核原槽是否已停稳，通过后解除停稳保护。此步骤只检查停止状态。";
            lv_subject_copy_string(&confirm_text_, "复核停稳");
        }
    } else if (action == "confirm_source") {
        if (!lv_subject_get_int(&source_ready_)) {
            lv_subject_copy_string(&status_, "当前无需确认来源，请选择实际供料的 ACE 槽");
            return;
        }
        pending_args_["HEAD"] = head_;
        prompt = "确认这就是当前喷头的供料槽。只保存来源，不进退料或加热。";
    } else if (action == "load")
        prompt = "将所选耗材装入喷头，已有耗材会先退料。";
    else if (action == "unload")
        prompt = "退出当前喷头中的耗材，保留在 ACE 内待用。";
    else if (action == "eject") {
        if (!lv_subject_get_int(&eject_ready_)) {
            lv_subject_copy_string(&status_, "请等待料槽空闲，或先恢复发生异常的槽位");
            return;
        }
        target_label = fmt::format("ACE {} · {} 槽", ace_index_ + 1, local_slot_ + 1);
        prompt = "退出到 ACE 入口外，可直接取下料卷。若此槽仍连接喷头，将先加热并退出喷头。";
    }
    else if (action == "spool_assign") {
        if (spool_id_.empty()) {
            lv_subject_copy_string(&status_, "请先保存新线盘，再从耗材库选择");
            return;
        }
        pending_args_["ID"] = spool_id_;
        prompt = "把所选线盘绑定到当前 ACE 槽。";
    } else if (action == "unbind") {
        pending_action_ = "spool_assign";
        prompt = "解除当前 ACE 槽的线盘绑定，余量将重新读取设备状态。";
    } else if (action == "calibrate_pa") {
        pending_args_ = {{"HEAD", head_}, {"FORCE", 1}};
        prompt = "执行当前已装载耗材的 PA 标定，设备会加热并运动。";
    } else if (action == "write_tag") {
        auto* backend = AmsState::instance().get_backend();
        if (!backend || slot_ < 0)
            return;
        const auto slot = backend->get_slot_info(slot_);
        if (slot.material.empty()) {
            lv_subject_copy_string(&status_, "请先识别或编辑耗材信息");
            return;
        }
        pending_args_["MATERIAL"] = slot.material;
        pending_args_["COLOR"] = fmt::format("{:06X}", slot.color_rgb);
        pending_args_["FORMAT"] = "openspool";
        prompt = "将当前耗材信息写入 RFID 标签，料槽会转动。需要设备支持 OpenSpool 写卡。";
    } else
        return;
    lv_subject_copy_string(&detail_, fmt::format("{}\n{}", target_label, prompt).c_str());
    update_confirmation_ready();
    page(6);
}
MaterialWorkspace& get_material_workspace() {
    return helix::lazy_global<MaterialWorkspace>("MaterialWorkspace");
}
} // namespace helix::ui
