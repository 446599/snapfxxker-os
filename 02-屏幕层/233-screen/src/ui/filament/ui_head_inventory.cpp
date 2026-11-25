// SPDX-License-Identifier: GPL-3.0-or-later
#include "ui_head_inventory.h"

#include "ui_callback_helpers.h"
#include "ui_material_workspace.h"
#include "ui_path_calibration.h"
#include "ui_nav.h"
#include "ui_nav_manager.h"
#include "ui_panel_ams_overview.h"

#include "ams_backend.h"
#include "ams_state.h"
#include "app_globals.h"
#include "core/data_root_resolver.h"
#include "core/observer_factory.h"
#include "core/static_panel_registry.h"
#include "printer_state.h"
#include "material_workspace.h"
#include "theme_manager.h"

#include <spdlog/fmt/fmt.h>

#include <algorithm>
#include <cmath>
#include <cstdlib>

namespace helix::ui {

HeadInventory::~HeadInventory() {
    revision_observer_.reset();
    connection_observer_.reset();
    job_observer_.reset();
    subjects_.deinit_all();
}

void HeadInventory::init_subjects() {
    init_subjects_guarded([this] {
        UI_MANAGED_SUBJECT_INT(view_, 0, "inventory_view", subjects_);
        UI_MANAGED_SUBJECT_INT(card_count_, 0, "inventory_card_count", subjects_);
        UI_MANAGED_SUBJECT_INT(group_count_, 0, "inventory_group_count", subjects_);
        UI_MANAGED_SUBJECT_INT(selected_group_, 0, "inventory_selected_group", subjects_);
        UI_MANAGED_SUBJECT_INT(selected_card_, 0, "inventory_selected_card", subjects_);
        groups_.ensure_size(4);
        UI_MANAGED_SUBJECT_INT(selected_head_, 0, "inventory_selected_head", subjects_);
        UI_MANAGED_SUBJECT_INT(selected_unit_subject_, -1, "inventory_selected_unit", subjects_);
        UI_MANAGED_SUBJECT_INT(unit_count_, 0, "inventory_unit_count", subjects_);
        UI_MANAGED_SUBJECT_INT(hold_, 0, "inventory_hold", subjects_);
        UI_MANAGED_SUBJECT_INT(online_, 0, "inventory_online", subjects_);
        UI_MANAGED_SUBJECT_INT(external_, 0, "inventory_external", subjects_);
        UI_MANAGED_SUBJECT_INT(can_manage_, 0, "inventory_can_manage", subjects_);
        UI_MANAGED_SUBJECT_INT(external_loaded_, 0, "inventory_external_loaded", subjects_);
        UI_MANAGED_SUBJECT_INT(can_load_, 0, "inventory_can_load", subjects_);
        UI_MANAGED_SUBJECT_INT(can_read_, 0, "inventory_can_read", subjects_);
        UI_MANAGED_SUBJECT_COLOR(external_color_, lv_color_hex(kAmsDefaultSlotColor),
                                "inventory_external_color", subjects_);
        UI_MANAGED_SUBJECT_STRING(external_material_, external_material_buf_, "等待耗材信息",
                                  "inventory_external_material", subjects_);
        UI_MANAGED_SUBJECT_STRING(external_state_, external_state_buf_, "等待装载状态",
                                  "inventory_external_state", subjects_);
        UI_MANAGED_SUBJECT_STRING(external_name_, external_name_buf_, "1号外置料卷",
                                  "inventory_external_name", subjects_);
        UI_MANAGED_SUBJECT_STRING(route_, route_buf_, "1槽 → 1号喷头", "inventory_route", subjects_);
        UI_MANAGED_SUBJECT_STRING(load_label_, load_label_buf_, "换料", "inventory_load_label",
                                  subjects_);
        UI_MANAGED_SUBJECT_STRING(source_, source_buf_, "等待设备状态", "inventory_source",
                                  subjects_);
        UI_MANAGED_SUBJECT_STRING(selection_, selection_buf_, "选择耗材查看详情",
                                  "inventory_selection", subjects_);
        UI_MANAGED_SUBJECT_STRING(head_title_, head_title_buf_, "喷头 1", "inventory_head_title",
                                  subjects_);
        UI_MANAGED_SUBJECT_STRING(notice_, notice_buf_, "", "inventory_notice", subjects_);
        UI_MANAGED_SUBJECT_STRING(toolhead_image_, toolhead_image_buf_, "",
                                  "inventory_toolhead_image", subjects_);
        lv_subject_copy_string(
            &toolhead_image_,
            asset_component_uri("assets/images/u1-toolhead-front-2d-ui.png").c_str());
        for (auto* pool : {&head_names_, &names_, &env_, &materials_, &weights_, &percentages_,
                           &colors_, &fills_, &current_, &external_head_materials_,
                           &external_head_states_, &external_head_colors_, &external_head_loaded_}) {
            pool->ensure_size(4);
        }
    });
}

void HeadInventory::register_callbacks() {
    register_xml_callbacks({
        {"inventory_select_head",
         [](lv_event_t* e) {
             if (auto* self = from_event<HeadInventory>(e)) {
                 const auto* index = static_cast<const char*>(lv_event_get_user_data(e));
                 if (index) {
                     const int selected = static_cast<int>(std::strtol(index, nullptr, 10));
                     if (self->view_mode_ == 0 && !lv_subject_get_int(&self->external_))
                         self->select_unit(selected);
                     else
                         self->select_head(selected);
                 }
             }
         }},
        {"inventory_select_unit",
         [](lv_event_t* e) {
             if (auto* self = from_event<HeadInventory>(e)) {
                 const auto* index = static_cast<const char*>(lv_event_get_user_data(e));
                 if (index)
                     self->select_card(static_cast<int>(std::strtol(index, nullptr, 10)));
             }
         }},
        {"inventory_view_select", [](lv_event_t* event) {
             if (auto* self = from_event<HeadInventory>(event)) {
                 const auto* value = static_cast<const char*>(lv_event_get_user_data(event));
                 if (!value) return;
                 self->view_mode_ = std::atoi(value) == 1 ? 1 : 0;
                 self->refresh();
             }
         }},
        {"inventory_calibrate", [](lv_event_t* event) {
             if (auto* self = from_event<HeadInventory>(event);
                 self && self->selected_unit_ >= 0)
                 if (auto* backend = AmsState::instance().get_backend()) {
                     const auto ace = material_workspace::ace_index(backend->get_system_info(), self->selected_unit_);
                     if (ace) get_path_calibration().open(*ace);
                 }
         }},
        {"inventory_library",
         [](lv_event_t* e) {
             if (auto* self = from_event<HeadInventory>(e);
                 self && lv_subject_get_int(&self->online_) &&
                 !lv_subject_get_int(&self->external_)) {
                 auto token = self->object_lifetime_.token();
                 get_material_workspace().open(self->head_, self->selected_unit_, true,
                     [self, token](int) { if (!token.expired()) self->refresh(); },
                     self->local_slot_);
             }
         }},
        {"inventory_back",
         [](lv_event_t*) {
             NavigationManager::instance().request_panel(PanelId::Home,
                                                         NavigationManager::SwitchDispatch::Queued);
         }},
        {"inventory_manage",
         [](lv_event_t* e) {
             if (auto* self = from_event<HeadInventory>(e);
                 self && lv_subject_get_int(&self->can_manage_)) {
                 auto token = self->object_lifetime_.token();
                 get_material_workspace().open(self->head_, self->selected_unit_, false,
                     [self, token](int) { if (!token.expired()) self->refresh(); },
                     self->local_slot_);
             }
         }},
        {"inventory_action",
         [](lv_event_t* e) {
             if (auto* self = from_event<HeadInventory>(e))
                 if (const auto* action = static_cast<const char*>(lv_event_get_user_data(e)))
                     self->open_action(action);
         }},
        {"inventory_draw_routes",
         [](lv_event_t* e) {
             if (auto* self = from_event<HeadInventory>(e))
                 self->draw_routes(e);
         }},
    });
}

lv_obj_t* HeadInventory::create(lv_obj_t* parent) {
    lv_xml_register_component_from_file(asset_component_uri("ui_xml/head_inventory.xml").c_str());
    return OverlayBase::create(parent);
}

void HeadInventory::on_activate() {
    OverlayBase::on_activate();
    auto& state = AmsState::instance();
    revision_observer_ = observe<int>(
        state.get_ams_data_revision_subject(), this,
        [](HeadInventory* self, int) { self->refresh(); }, state.get_subjects_lifetime());
    auto& network = get_printer_state().network_state();
    connection_observer_ = observe<int>(
        network.get_nav_buttons_enabled_subject(), this,
        [](HeadInventory* self, int) { self->refresh(); },
        get_printer_state().get_subjects_lifetime());
    job_observer_ = observe<int>(
        get_printer_state().print_state().get_job_holds_machine_subject(), this,
        [](HeadInventory* self, int) { self->refresh(); },
        get_printer_state().get_subjects_lifetime());
    refresh();
}

void HeadInventory::select_head(int head) {
    if (head < 0 || head >= 4)
        return;
    if (head != head_) local_slot_ = head;
    head_ = head;
    refresh();
}

void HeadInventory::on_deactivating(DeactivateReason) {
    revision_observer_.reset();
    connection_observer_.reset();
    job_observer_.reset();
}

void HeadInventory::open_action(const std::string& action) {
    // Re-read the backend even for forced or stale widget callbacks.
    refresh();
    if (!lv_subject_get_int(&can_manage_) ||
        (action == "load" && !lv_subject_get_int(&can_load_)) ||
        (action == "read_tag" && !lv_subject_get_int(&can_read_)))
        return;
    auto token = object_lifetime_.token();
    get_material_workspace().open_action(head_, selected_unit_, action,
        [this, token](int) { if (!token.expired()) refresh(); }, local_slot_);
}

void HeadInventory::select_unit(int unit) {
    if (unit < 0 || unit >= lv_subject_get_int(&unit_count_))
        return;
    selected_unit_ = unit;
    refresh();
}

void HeadInventory::select_card(int index) {
    auto* backend = AmsState::instance().get_backend();
    if (!backend || index < 0 || index >= lv_subject_get_int(&card_count_)) return;
    const auto info = backend->get_system_info();
    if (static_cast<size_t>(index) >= routes_.size()) return;
    const auto [unit_index, local_slot] = routes_[static_cast<size_t>(index)];
    if (unit_index < 0 || static_cast<size_t>(unit_index + 1) >= info.units.size()) return;
    const auto& slots = info.units[static_cast<size_t>(unit_index + 1)].slots;
    const auto found = std::find_if(slots.begin(), slots.end(), [local_slot](const SlotInfo& slot) {
        return slot.slot_index == local_slot;
    });
    if (found == slots.end() || (view_mode_ == 1 && found->mapped_tool != head_)) return;
    selected_unit_ = unit_index;
    local_slot_ = local_slot;
    head_ = found->mapped_tool >= 0 && found->mapped_tool < 4 ? found->mapped_tool : local_slot;
    refresh();
    if (!lv_subject_get_int(&can_manage_)) return;
    auto token = object_lifetime_.token();
    get_material_workspace().open(head_, selected_unit_, false,
        [this, token](int) { if (!token.expired()) refresh(); }, local_slot_);
}

void HeadInventory::refresh() {
    auto* backend = AmsState::instance().get_backend();
    const AmsSystemInfo info = backend ? backend->get_system_info() : AmsSystemInfo{};
    const bool connected =
        lv_subject_get_int(get_printer_state().network_state().get_nav_buttons_enabled_subject()) ==
        1;
    const bool topology_known = backend && material_workspace::supports_head_inventory(info);
    // A lost connection is no evidence that ACE appeared. Retain only the layout;
    // every live value and action below still requires a real connected snapshot.
    const bool external = topology_known ? material_workspace::uses_external_spools(info)
                                         : lv_subject_get_int(&external_) != 0;
    const bool ready = connected && topology_known;
    const int count = ready && !external
                          ? std::min(4, std::max(0, static_cast<int>(info.units.size()) - 1)) : 0;
    if (selected_unit_ < 0 || selected_unit_ >= count)
        selected_unit_ = count > 0 ? 0 : -1;
    const bool by_ace = view_mode_ == 0 && !external;
    if (by_ace && selected_unit_ >= 0) {
        const auto& slots = info.units[static_cast<size_t>(selected_unit_ + 1)].slots;
        const auto viewed = std::find_if(slots.begin(), slots.end(), [this](const SlotInfo& slot) {
            return slot.slot_index == local_slot_;
        });
        if (viewed != slots.end() && viewed->mapped_tool >= 0 && viewed->mapped_tool < 4)
            head_ = viewed->mapped_tool;
    }
    routes_.clear();
    if (by_ace && selected_unit_ >= 0) {
        for (int slot = 0; slot < 4; ++slot) routes_.emplace_back(selected_unit_, slot);
    } else if (!external) {
        for (int unit = 0; unit < count; ++unit)
            for (const auto& slot : info.units[static_cast<size_t>(unit + 1)].slots)
                if (slot.mapped_tool == head_) routes_.emplace_back(unit, slot.slot_index);
        std::sort(routes_.begin(), routes_.end());
    }
    auto selected = std::find(routes_.begin(), routes_.end(), std::make_pair(selected_unit_, local_slot_));
    if (!by_ace && selected == routes_.end() && !routes_.empty()) {
        selected_unit_ = routes_.front().first;
        local_slot_ = routes_.front().second;
        selected = routes_.begin();
    }
    const int card_count = static_cast<int>(routes_.size());
    for (auto* pool : {&names_, &env_, &materials_, &weights_, &percentages_, &colors_, &fills_, &current_})
        pool->ensure_size(routes_.size());
    lv_subject_set_int(&view_, view_mode_);
    lv_subject_set_int(&group_count_, by_ace ? count : 4);
    lv_subject_set_int(&selected_group_, by_ace ? selected_unit_ : head_);
    lv_subject_set_int(&selected_card_, selected == routes_.end() ? -1 : static_cast<int>(selected - routes_.begin()));
    lv_subject_set_int(&card_count_, card_count);
    for (int index = 0; index < 4; ++index)
        groups_.set_string(index, by_ace && index < count
            ? info.units[static_cast<size_t>(index + 1)].name
            : fmt::format("喷头 {}", index + 1));
    lv_subject_set_int(&external_, external);
    lv_subject_set_int(&online_, ready);
    lv_subject_set_int(&unit_count_, count);
    lv_subject_set_int(&hold_, !info.path_hold_reason.empty());
    lv_subject_set_int(&selected_head_, head_);
    lv_subject_copy_string(&head_title_, fmt::format("喷头 {}", head_ + 1).c_str());
    lv_subject_copy_string(&route_, fmt::format("{}槽 → {}号喷头", head_ + 1, head_ + 1).c_str());
    lv_subject_set_int(&can_load_, 0);
    lv_subject_set_int(&can_read_, 0);

    // All four external spools are independent. Browsing a card changes only
    // the selected head; live load state always comes from the backend.
    for (int head = 0; head < 4; ++head) {
        const auto* slot = material_workspace::head_slot(info, head);
        const bool available = ready && slot && info.units.front().connected;
        const bool loaded = available && backend->slot_is_actively_loaded(slot->global_index);
        external_head_materials_.set_string(head, !available ? "等待设备"
            : slot->material.empty() ? "未设置耗材" : slot->material);
        const auto presence = material_workspace::head_physical_presence(backend, head);
        external_head_states_.set_string(head, !available ? "状态待确认"
            : loaded ? "装载完成" : !presence ? "到料待核"
            : *presence ? "已到喷头" : "喷头无料");
        external_head_colors_.set_color(head, available ? slot->color_rgb : kAmsDefaultSlotColor);
        external_head_loaded_.set_int(head, loaded);
    }

    if (external) {
        selected_unit_ = -1;
        lv_subject_set_int(&selected_unit_subject_, -1);
        const auto* slot = material_workspace::head_slot(info, head_);
        const bool available = ready && slot && info.units.front().connected;
        const bool loaded = available && backend->slot_is_actively_loaded(slot->global_index);
        lv_subject_set_int(&can_manage_, available);
        lv_subject_set_int(&external_loaded_, loaded);
        lv_subject_set_color(&external_color_, lv_color_hex(available ? slot->color_rgb
                                                                     : kAmsDefaultSlotColor));
        lv_subject_copy_string(&external_material_, !available ? "等待耗材信息"
                                  : slot->material.empty() ? "未设置耗材" : slot->material.c_str());
        const auto presence = material_workspace::head_physical_presence(backend, head_);
        const char* state = !available ? "等待装载状态"
                            : loaded ? "装载完成"
                            : !presence ? "到料待核"
                            : *presence ? "已到喷头 · 等待装载" : "喷头无料";
        lv_subject_copy_string(&external_state_, state);
        lv_subject_copy_string(&external_name_, fmt::format("{}号外置料卷", head_ + 1).c_str());
        lv_subject_copy_string(&source_, fmt::format("喷头 {} · 外置料卷", head_ + 1).c_str());
        lv_subject_copy_string(&selection_, "编辑耗材或手动进退料");
        lv_subject_copy_string(&notice_, !ready ? "设备离线 · 等待连接"
                                 : !info.path_hold_reason.empty() ? "料路位置待确认"
                                 : "外置供料 · 装载状态以设备反馈为准");
        return;
    }

    auto source_name = [&](int head) -> std::string {
        if (!ready)
            return "等待状态";
        if (info.path_held(head))
            return "位置待确认";
        const auto* physical = material_workspace::head_slot(info, head);
        const auto presence = material_workspace::head_physical_presence(backend, head);
        if (!physical || !presence) return "喷头到料待核";
        if (!*presence) return "喷头无料";
        const auto source = material_workspace::head_physical_source(backend, head);
        if (source) {
            for (size_t u = 1; u < info.units.size(); ++u) {
                for (const auto& slot : info.units[u].slots) {
                    if (slot.global_index == *source) {
                        if (!info.units[u].connected)
                            return info.units[u].name + " 离线";
                        return fmt::format("{} · {}槽", info.units[u].name, slot.slot_index + 1);
                    }
                }
            }
        }
        if (backend->head_source_slot(head, info) == physical->global_index) return "手动／侧供料";
        return "已到喷头 · 来源待确认";
    };
    for (int h = 0; h < 4; ++h)
        head_names_.set_string(h, source_name(h));
    lv_subject_copy_string(&source_, fmt::format("当前供料  {}", source_name(head_)).c_str());
    const std::string notice = !ready ? "等待设备耗材状态"
                               : info.path_held(head_)
                                   ? "料路位置待确认 · 请先完成位置恢复"
                               : info.action != AmsAction::IDLE
                                   ? "设备正在处理耗材 · " + info.operation_detail
                                   : fmt::format("各 ACE 的 {} 槽接喷头 {}", head_ + 1, head_ + 1);
    lv_subject_copy_string(&notice_, notice.c_str());

    const auto source = ready ? backend->upstream_slot(head_) : std::nullopt;
    for (int card = 0; card < card_count; ++card) {
        const auto [unit_index, local_slot] = routes_[static_cast<size_t>(card)];
        const auto& unit = info.units[static_cast<size_t>(unit_index + 1)];
        const auto slot = std::find_if(unit.slots.begin(), unit.slots.end(),
            [local_slot](const SlotInfo& value) { return value.slot_index == local_slot; });
        const bool present = slot != unit.slots.end();
        const auto active_source = present ? material_workspace::head_physical_source(backend, slot->mapped_tool) : std::nullopt;
        const auto* physical = present ? material_workspace::head_slot(info, slot->mapped_tool) : nullptr;
        const auto presence = present ? material_workspace::head_physical_presence(backend, slot->mapped_tool)
                                      : std::nullopt;
        const bool current = present && active_source && *active_source == slot->global_index &&
            physical && presence == true && unit.connected;
        const bool mapped = present && slot->mapped_tool >= 0 && slot->mapped_tool < 4;
        names_.set_string(card, by_ace
            ? mapped ? fmt::format("{}槽 → {}头", local_slot + 1, slot->mapped_tool + 1)
                     : fmt::format("{}槽 · 未接入", local_slot + 1)
            : fmt::format("{} · {}槽", unit.name, local_slot + 1));
        materials_.set_string(card, !unit.connected ? "离线" : !present ? "未知路线"
            : slot->status == SlotStatus::EMPTY ? "空槽"
            : slot->material.empty() ? "待识别" : slot->material);
        const bool known = present && unit.connected && slot->remaining_weight_g >= 0;
        const float percent = known ? slot->get_remaining_percent() : -1;
        weights_.set_string(card, known ? fmt::format("约 {:.0f} g", slot->remaining_weight_g)
                                       : "");
        percentages_.set_string(card, percent >= 0 ? fmt::format("{:.0f}%", percent) : "—");
        fills_.set_int(card, percent >= 0 ? static_cast<int>(std::clamp(percent, 0.0f, 100.0f)) : 0);
        colors_.set_color(card, present && unit.connected ? slot->color_rgb : kAmsDefaultSlotColor);
        current_.set_int(card, current);
    }
    lv_subject_set_int(&selected_unit_subject_, selected_unit_);
    lv_subject_set_int(&can_manage_, ready && selected_unit_ >= 0);
    bool selected_is_source = false;
    if (selected_unit_ >= 0) {
        const auto& unit = info.units[static_cast<size_t>(selected_unit_) + 1];
        const auto slot = std::find_if(unit.slots.begin(), unit.slots.end(), [this](const SlotInfo& s) {
            return s.mapped_tool == head_ && s.slot_index == local_slot_;
        });
        const bool present = slot != unit.slots.end();
        const auto* head_slot = material_workspace::head_slot(info, head_);
        const bool head_loaded = head_slot && backend->slot_is_actively_loaded(head_slot->global_index);
        const bool source_matches = present && source && *source == slot->global_index;
        const bool current = source_matches && head_loaded;
        selected_is_source = present && material_workspace::head_physical_source(backend, head_) == slot->global_index;
        const bool empty = present && slot->status == SlotStatus::EMPTY;
        const bool occupied = present && (slot->status == SlotStatus::AVAILABLE ||
            slot->status == SlotStatus::FROM_BUFFER || slot->status == SlotStatus::LOADED);
        const bool idle = ready && unit.connected && info.action == AmsAction::IDLE &&
            !info.path_held(head_) &&
            !lv_subject_get_int(get_printer_state().print_state().get_job_holds_machine_subject());
        const bool unrecorded_loaded = head_slot &&
            backend->slot_is_actively_loaded(head_slot->global_index) && !source;
        lv_subject_set_int(&can_load_, idle && occupied && !current && !unrecorded_loaded);
        lv_subject_set_int(&can_read_, idle && present);
        lv_subject_copy_string(&load_label_, current ? "当前供料" : empty ? "空槽" : head_loaded ? "换料" : "装载");
    } else
        lv_subject_copy_string(&load_label_, "等待设备");
    const std::string selection =
        selected_unit_ >= 0
            ? fmt::format("查看 {} · {}槽{}",
                          info.units[static_cast<size_t>(selected_unit_) + 1].name, local_slot_ + 1,
                          selected_is_source ? " · 当前供料" : " · 候选耗材")
            : "设备上线后显示耗材";
    lv_subject_copy_string(&selection_, selection.c_str());
    if (overlay_root_) {
        // DECLARATIVE_OK: the procedural routing diagram redraws from the new snapshot.
        if (auto* routes = lv_obj_find_by_name(overlay_root_, "inventory_routes"))
            lv_obj_invalidate(routes);
    }
}

void HeadInventory::draw_routes(lv_event_t* event) {
    const int count = lv_subject_get_int(&unit_count_);
    if (count == 0)
        return;
    auto* object = lv_event_get_current_target_obj(event);
    auto* layer = lv_event_get_layer(event);
    lv_area_t area;
    lv_obj_get_coords(object, &area);
    const int center = (area.x1 + area.x2) / 2;
    const int junction = (area.y1 + area.y2) / 2;
    auto draw = [&](int index, bool active) {
        auto* card =
            lv_obj_find_by_name(overlay_root_, fmt::format("inventory_card_{}", index).c_str());
        if (!card)
            return;
        lv_area_t card_area;
        lv_obj_get_coords(card, &card_area);
        const int x = (card_area.x1 + card_area.x2) / 2;
        lv_draw_line_dsc_t line;
        lv_draw_line_dsc_init(&line);
        line.color = active ? lv_subject_get_color(colors_.at(index))
                            : theme_manager_get_color("text_muted");
        line.opa = active ? LV_OPA_COVER : LV_OPA_30;
        line.width = active ? 3 : 1;
        line.round_start = true;
        line.round_end = true;
        const lv_point_precise_t points[] = {
            {static_cast<lv_value_precise_t>(x), static_cast<lv_value_precise_t>(area.y1)},
            {static_cast<lv_value_precise_t>(x), static_cast<lv_value_precise_t>(junction)},
            {static_cast<lv_value_precise_t>(center), static_cast<lv_value_precise_t>(junction)},
            {static_cast<lv_value_precise_t>(center), static_cast<lv_value_precise_t>(area.y2)}};
        for (int p = 0; p < 3; ++p) {
            line.p1 = points[p];
            line.p2 = points[p + 1];
            if (active) {
                auto outline = line;
                outline.color = theme_manager_get_color("text_muted");
                outline.opa = LV_OPA_50;
                outline.width = 5;
                lv_draw_line(layer, &outline);
            }
            lv_draw_line(layer, &line);
        }
    };
    for (int i = 0; i < count; ++i)
        draw(i, false);
    for (int i = 0; i < count; ++i)
        if (lv_subject_get_int(current_.at(i)))
            draw(i, true);
}

HeadInventory& get_head_inventory() {
    return helix::lazy_global<HeadInventory>("HeadInventory");
}
} // namespace helix::ui
