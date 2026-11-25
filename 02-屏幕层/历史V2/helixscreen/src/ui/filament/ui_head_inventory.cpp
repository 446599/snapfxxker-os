// SPDX-License-Identifier: GPL-3.0-or-later
#include "ui_head_inventory.h"

#include "ui_callback_helpers.h"
#include "ui_material_workspace.h"
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
#include "theme_manager.h"

#include <spdlog/fmt/fmt.h>

#include <algorithm>
#include <cmath>
#include <cstdlib>

namespace helix::ui {

HeadInventory::~HeadInventory() {
    revision_observer_.reset();
    connection_observer_.reset();
    subjects_.deinit_all();
}

void HeadInventory::init_subjects() {
    init_subjects_guarded([this] {
        UI_MANAGED_SUBJECT_INT(selected_head_, 0, "inventory_selected_head", subjects_);
        UI_MANAGED_SUBJECT_INT(selected_unit_subject_, -1, "inventory_selected_unit", subjects_);
        UI_MANAGED_SUBJECT_INT(unit_count_, 0, "inventory_unit_count", subjects_);
        UI_MANAGED_SUBJECT_INT(hold_, 0, "inventory_hold", subjects_);
        UI_MANAGED_SUBJECT_INT(online_, 0, "inventory_online", subjects_);
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
                           &colors_, &fills_, &current_}) {
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
                 if (index)
                     self->select_head(static_cast<int>(std::strtol(index, nullptr, 10)));
             }
         }},
        {"inventory_select_unit",
         [](lv_event_t* e) {
             if (auto* self = from_event<HeadInventory>(e)) {
                 const auto* index = static_cast<const char*>(lv_event_get_user_data(e));
                 if (index)
                     self->select_unit(static_cast<int>(std::strtol(index, nullptr, 10)));
             }
         }},
        {"inventory_library",
         [](lv_event_t* e) {
             if (auto* self = from_event<HeadInventory>(e))
                 get_material_workspace().open(self->head_, self->selected_unit_, true);
         }},
        {"inventory_back",
         [](lv_event_t*) {
             NavigationManager::instance().request_panel(PanelId::Home,
                                                         NavigationManager::SwitchDispatch::Queued);
         }},
        {"inventory_manage",
         [](lv_event_t* e) {
             if (auto* self = from_event<HeadInventory>(e); self && self->selected_unit_ >= 0)
                 get_material_workspace().open(self->head_, self->selected_unit_);
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
    refresh();
}

void HeadInventory::select_head(int head) {
    if (head < 0 || head >= 4)
        return;
    head_ = head;
    selected_unit_ = -1;
    refresh();
}

void HeadInventory::on_deactivating(DeactivateReason) {
    revision_observer_.reset();
    connection_observer_.reset();
}

void HeadInventory::select_unit(int unit) {
    if (unit < 0 || unit >= lv_subject_get_int(&unit_count_))
        return;
    selected_unit_ = unit;
    refresh();
}

void HeadInventory::refresh() {
    auto* backend = AmsState::instance().get_backend();
    const AmsSystemInfo info = backend ? backend->get_system_info() : AmsSystemInfo{};
    const bool connected =
        lv_subject_get_int(get_printer_state().network_state().get_nav_buttons_enabled_subject()) ==
        1;
    const bool ready = connected && backend && info.head_focused_inventory;
    const int count = ready ? std::min(4, std::max(0, static_cast<int>(info.units.size()) - 1)) : 0;
    lv_subject_set_int(&online_, ready);
    lv_subject_set_int(&unit_count_, count);
    lv_subject_set_int(&hold_, !info.path_hold_reason.empty());
    lv_subject_set_int(&selected_head_, head_);
    lv_subject_copy_string(&head_title_, fmt::format("喷头 {}", head_ + 1).c_str());

    auto source_name = [&](int head) -> std::string {
        if (!ready)
            return "等待状态";
        if (!info.path_hold_reason.empty() &&
            (info.path_hold_head < 0 || info.path_hold_head == head))
            return "位置待确认";
        const auto source = backend->upstream_slot(head);
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
        return "未确认供料";
    };
    for (int h = 0; h < 4; ++h)
        head_names_.set_string(h, source_name(h));
    lv_subject_copy_string(&source_, fmt::format("当前供料  {}", source_name(head_)).c_str());
    const std::string notice = !ready ? "等待 multi 模式设备状态"
                               : !info.path_hold_reason.empty()
                                   ? "料路位置待确认 · 请先完成位置恢复"
                               : info.action != AmsAction::IDLE
                                   ? "设备正在处理耗材 · " + info.operation_detail
                                   : fmt::format("各 ACE 的 {} 槽接喷头 {}", head_ + 1, head_ + 1);
    lv_subject_copy_string(&notice_, notice.c_str());

    const auto source = ready ? backend->upstream_slot(head_) : std::nullopt;
    int source_unit = -1;
    for (int u = 0; u < count; ++u) {
        const auto& unit = info.units[static_cast<size_t>(u) + 1];
        const auto slot =
            std::find_if(unit.slots.begin(), unit.slots.end(), [this](const SlotInfo& s) {
                return s.mapped_tool == head_ && s.slot_index == head_;
            });
        const bool present = slot != unit.slots.end();
        const bool current = present && source && *source == slot->global_index && unit.connected;
        if (current)
            source_unit = u;
        names_.set_string(u, unit.name);
        std::string temperature = "—°C", humidity = "—%";
        if (unit.connected && unit.environment) {
            if (unit.environment->has_temperature())
                temperature = fmt::format("{:.0f}°C", unit.environment->temperature_c);
            if (unit.environment->has_humidity)
                humidity = fmt::format("{:.0f}%", unit.environment->humidity_pct);
        }
        env_.set_string(u, temperature + "  " + humidity);
        materials_.set_string(u, !unit.connected                     ? "离线"
                                 : !present                          ? "未知"
                                 : slot->status == SlotStatus::EMPTY ? "空槽"
                                 : slot->material.empty()            ? "待识别"
                                                                     : slot->material);
        const bool known = present && unit.connected && slot->remaining_weight_g >= 0;
        const float percent = known ? slot->get_remaining_percent() : -1;
        weights_.set_string(u, known ? fmt::format("约 {:.0f} g", slot->remaining_weight_g)
                                     : "余量未知");
        percentages_.set_string(
            u, percent >= 0 ? fmt::format("{:.0f}%", std::clamp(percent, 0.0f, 100.0f)) : "—");
        fills_.set_int(u, percent >= 0 ? static_cast<int>(std::clamp(percent, 0.0f, 100.0f)) : 0);
        colors_.set_color(u, present ? slot->color_rgb : 0x888888);
        current_.set_int(u, current);
    }
    if (selected_unit_ < 0 || selected_unit_ >= count)
        selected_unit_ = source_unit >= 0 ? source_unit : count > 0 ? 0 : -1;
    lv_subject_set_int(&selected_unit_subject_, selected_unit_);
    const std::string selection =
        selected_unit_ >= 0
            ? fmt::format("查看 {} · {}槽{}",
                          info.units[static_cast<size_t>(selected_unit_) + 1].name, head_ + 1,
                          selected_unit_ == source_unit ? " · 当前供料" : " · 候选耗材")
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
