// Copyright (C) 2025-2026 356C LLC
// SPDX-License-Identifier: GPL-3.0-or-later

#include "ui_panel_notification_history.h"

#include "ui_event_safety.h"
#include "ui_format_utils.h"
#include "ui_nav.h"
#include "ui_notification_manager.h"
#include "ui_panel_common.h"
#include "ui_subject_registry.h"
#include "ui_utils.h"

#include "app_globals.h"
#include "core/observer_factory.h"
#include "core/static_panel_registry.h"
#include "lvgl.h"
#include "printer_state.h"
#include "system/update_checker.h"
#include "ui/ui_widget_helpers.h"

#include <spdlog/fmt/fmt.h>
#include <spdlog/spdlog.h>

#include <cstring>
#include <cstdlib>
#include <map>
#include <memory>

using namespace helix;

// ============================================================================
// CONSTRUCTOR
// ============================================================================

NotificationHistoryPanel::NotificationHistoryPanel(PrinterState& printer_state, IMoonrakerAPI* api,
                                                   NotificationHistory& history)
    : PanelBase(printer_state, api), history_(history) {
    // Dependencies stored for use in refresh()
}

NotificationHistoryPanel::~NotificationHistoryPanel() {
    deinit_subjects();
}

// ============================================================================
// PANELBASE IMPLEMENTATION
// ============================================================================

void NotificationHistoryPanel::init_subjects() {
    if (subjects_initialized_) {
        spdlog::warn("[{}] init_subjects() called twice - ignoring", get_name());
        return;
    }

    // Has entries subject: 1 = has entries (show content), 0 = empty (show empty state)
    UI_MANAGED_SUBJECT_INT(has_entries_subject_, 0, "notification_has_entries", subjects_);
    UI_MANAGED_SUBJECT_INT(row_count_subject_, 0, "notification_row_count", subjects_);

    subjects_initialized_ = true;
    spdlog::debug("[{}] Subjects initialized (2 subjects)", get_name());
}

void NotificationHistoryPanel::deinit_subjects() {
    if (!subjects_initialized_) {
        return;
    }
    history_version_observer_.reset();
    subjects_.deinit_all();
    for (auto* pool : {&severity_pool_, &title_pool_, &message_pool_, &has_message_pool_,
                       &time_pool_, &has_action_pool_, &action_label_pool_}) {
        pool->reclaim();
    }
    subjects_initialized_ = false;
    spdlog::debug("[{}] Subjects deinitialized", get_name());
}

void NotificationHistoryPanel::setup(lv_obj_t* panel, lv_obj_t* parent_screen) {
    // Call base class to store panel_ and parent_screen_
    PanelBase::setup(panel, parent_screen);

    if (!panel_) {
        spdlog::error("[{}] NULL panel", get_name());
        return;
    }

    // The restyled U1 list uses a custom 通知 / 关闭 chrome. Keep the older
    // header_bar wiring when that header is still present.
    if (lv_obj_find_by_name(panel_, "overlay_header")) {
        ui_overlay_panel_setup_standard(panel_, parent_screen_, "overlay_header", "overlay_content");
        lv_obj_t* action_btn =
            ui_overlay_panel_wire_action_button(panel_, on_clear_clicked, "overlay_header", this);
        if (action_btn) {
            lv_obj_bind_flag_if_eq(action_btn, &has_entries_subject_, LV_OBJ_FLAG_HIDDEN, 0);
        }
    }

    // 关闭 is an XML event_cb on a padded hit target. Make sure the control
    // itself is clickable (semantic labels default to not).
    if (lv_obj_t* close_btn = lv_obj_find_by_name(panel_, "overlay_close")) {
        lv_obj_add_flag(close_btn, LV_OBJ_FLAG_CLICKABLE);
    }

    // A tap on the 8px dim pad around the card (the panel root) dismisses,
    // matching the restyled overlay. Clicks inside the card do not bubble
    // here because the card itself is not flagged event_bubble.
    lv_obj_add_flag(panel_, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(
        panel_,
        [](lv_event_t* e) {
            if (lv_event_get_target(e) == lv_event_get_current_target(e)) {
                helix::nav::go_back();
            }
        },
        LV_EVENT_CLICKED, nullptr);

    // Refresh the list when a notification arrives while the panel is open.
    // The observer attaches at most once per panel lifetime — setup() runs
    // again on every reopen, and the subject is owned by the manager singleton
    // that outlives this panel.
    if (!history_version_observer_) {
        lv_subject_t* version_subject = helix::ui::notification_history_version_subject();
        if (version_subject) {
            history_version_observer_ = helix::ui::observe<int>(
                version_subject, this,
                [](NotificationHistoryPanel* p, int value) {
                    p->handle_history_version_change(value);
                },
                NotificationManager::instance().get_subjects_lifetime());
        } else {
            spdlog::warn("[{}] Notification history version subject not initialized", get_name());
        }
    }

    // Populate list
    refresh();

    spdlog::info("[{}] Setup complete", get_name());
}

// ============================================================================
// PUBLIC API
// ============================================================================

void NotificationHistoryPanel::refresh() {
    if (!panel_) {
        spdlog::warn("[{}] Cannot refresh - panel not created", get_name());
        return;
    }

    // Get all entries (filter buttons removed from UI for cleaner look)
    auto entries = history_.get_all();

    // Update has_entries subject - XML bindings handle visibility reactively
    bool has_entries = !entries.empty();
    lv_subject_set_int(&has_entries_subject_, has_entries ? 1 : 0);

    // Fill the rows' subjects, then publish the count: the <repeat> builds rows
    // bound to slots that already hold their values.
    const size_t n = entries.size();
    for (auto* pool : {&severity_pool_, &title_pool_, &message_pool_, &has_message_pool_,
                       &time_pool_, &has_action_pool_, &action_label_pool_}) {
        pool->ensure_size(n);
    }
    row_actions_.assign(n, std::string{});
    row_ids_.resize(n);
    for (size_t i = 0; i < n; ++i) {
        const auto& entry = entries[i];
        // An untitled entry (every plain toast) leads with its message; the icon and
        // border already carry the severity, so a placeholder title says nothing.
        const bool has_title = entry.title[0] != '\0';
        const char* message = entry.resolved ? "该错误已解除。" : has_title ? entry.message : "";
        severity_pool_.set_int(i, severity_index(entry.severity));
        title_pool_.set_string(i, has_title ? entry.title : entry.message);
        message_pool_.set_string(i, message);
        has_message_pool_.set_int(i, message[0] != '\0' ? 1 : 0);
        time_pool_.set_string(i, entry.active ? "待处理" : entry.resolved ? "已解除" : format_timestamp(entry.timestamp_ms));
        action_label_pool_.set_string(i, entry.action_label[0] ? entry.action_label : "查看");
        row_actions_[i] = entry.action;
        row_ids_[i] = entry.id;
        has_action_pool_.set_int(i, entry.action[0] != '\0' ? 1 : 0);
    }
    lv_subject_set_int(&row_count_subject_, static_cast<int>(n));
    if (auto* content = lv_obj_find_by_name(panel_, "overlay_content")) {
        // Attach to nested hit targets too, without bubbling their click actions.
        auto attach = [this](auto&& visit, lv_obj_t* obj) -> void {
            lv_obj_remove_event_cb(obj, on_row_pointer);
            lv_obj_add_event_cb(obj, on_row_pointer, LV_EVENT_ALL, this);
            for (uint32_t i = 0; i < lv_obj_get_child_count(obj); ++i)
                visit(visit, lv_obj_get_child(obj, i));
        };
        for (uint32_t i = 0; i < lv_obj_get_child_count(content); ++i)
            attach(attach, lv_obj_get_child(content, i));
    }

    // Mark all as read
    history_.mark_all_read();

    // Reading history does not dismiss a condition that its source still reports.
    helix::ui::notification_refresh_from_history();

    spdlog::debug("[{}] Refreshed: {} entries displayed", get_name(), entries.size());
}

// ============================================================================
// PRIVATE HELPERS
// ============================================================================

int NotificationHistoryPanel::severity_index(ToastSeverity severity) {
    switch (severity) {
    case ToastSeverity::ERROR:
        return 3;
    case ToastSeverity::WARNING:
        return 2;
    case ToastSeverity::SUCCESS:
        return 1;
    case ToastSeverity::INFO:
    default:
        return 0;
    }
}

std::string NotificationHistoryPanel::format_timestamp(uint64_t timestamp_ms) {
    uint64_t now = lv_tick_get();

    // Handle edge case where timestamp is in future (shouldn't happen)
    uint64_t diff_ms = (timestamp_ms > now) ? 0 : (now - timestamp_ms);

    return helix::ui::format_relative_time(diff_ms);
}

// ============================================================================
// BUTTON HANDLERS
// ============================================================================

void NotificationHistoryPanel::handle_history_version_change(int32_t version) {
    if (version == last_applied_history_version_) {
        return;
    }
    last_applied_history_version_ = version;

    // A hidden panel is not destroyed (NavigationManager::go_back() only hides
    // the overlay), so the observer stays attached and would otherwise rebuild
    // an invisible list and mark it read. setup() already does an unconditional
    // refresh() on every real (re)open, which catches up on anything that
    // arrived while hidden. A panel hidden only until its queued push lands is
    // opening, and setup() has already run, so it refreshes now.
    if (!panel_ || !helix::nav::is_showing(panel_)) {
        return;
    }
    refresh();
}

void NotificationHistoryPanel::handle_clear_clicked() {
    history_.clear();
    // clear() bumps the store revision; the manager publishes it on its next
    // badge refresh. Record it now so that publish does not rebuild the list
    // again once it lands.
    last_applied_history_version_ = static_cast<int32_t>(history_.version());
    refresh();
    spdlog::info("[{}] History cleared by user", get_name());
}

// ============================================================================
// ACTION DISPATCH
// ============================================================================

void NotificationHistoryPanel::on_row_pointer(lv_event_t* event) {
    const auto code = lv_event_get_code(event);
    if (code != LV_EVENT_PRESSED && code != LV_EVENT_PRESSING &&
        code != LV_EVENT_RELEASED && code != LV_EVENT_PRESS_LOST) return;
    // A nested label may bubble. Process only its original target once.
    if (lv_event_get_target(event) != lv_event_get_current_target(event)) return;
    auto* self = static_cast<NotificationHistoryPanel*>(lv_event_get_user_data(event));
    auto* input = lv_indev_active();
    if (!self || !input) return;
    if (code == LV_EVENT_RELEASED || code == LV_EVENT_PRESS_LOST) {
        self->swipe_id_ = 0;
        return;
    }
    lv_point_t point;
    lv_indev_get_point(input, &point);
    if (code == LV_EVENT_PRESSED) {
        auto* row = lv_event_get_target_obj(event);
        while (row && (!lv_obj_get_name(row) ||
               std::strcmp(lv_obj_get_name(row), "notification_item") != 0))
            row = lv_obj_get_parent(row);
        const auto index = row ? lv_obj_get_index(row) : -1;
        self->swipe_id_ = index >= 0 && static_cast<size_t>(index) < self->row_ids_.size()
            ? self->row_ids_[index] : 0;
        self->swipe_start_ = point;
    } else if (self->swipe_id_) {
        const int dx = std::abs(point.x - self->swipe_start_.x);
        const int dy = std::abs(point.y - self->swipe_start_.y);
        if (dy >= 20 && dy > dx) {
            self->swipe_id_ = 0; // leave vertical scrolling to the list
        } else if (dx >= 48 && dx > 2 * dy) {
            const uint64_t id = std::exchange(self->swipe_id_, 0);
            lv_indev_wait_release(input); // a swipe must never invoke the row action
            self->dismiss_entry(id);
        }
    }
}

void NotificationHistoryPanel::handle_item_swiped(size_t index) {
    if (index < row_ids_.size()) dismiss_entry(row_ids_[index]);
}

void NotificationHistoryPanel::dismiss_entry(uint64_t id) {
    // Row subjects may rebuild widgets. Do this after the input event unwinds.
    object_lifetime_.defer("NotificationHistoryPanel::dismiss", [this, id]() {
        if (history_.dismiss(id)) {
            last_applied_history_version_ = static_cast<int32_t>(history_.version());
            refresh();
        }
    });
}

void NotificationHistoryPanel::handle_item_clicked(size_t index) {
    if (index < row_actions_.size() && !row_actions_[index].empty()) {
        // Copied: dispatching closes the panel, and a refresh may replace the row.
        const std::string action = row_actions_[index];
        dispatch_action(action.c_str());
    }
}

void NotificationHistoryPanel::dispatch_action(const char* action) {
    spdlog::info("[{}] Dispatching action: {}", get_name(), action);

    if (strcmp(action, "show_update_modal") == 0) {
        // Close notification history overlay first, then show update modal
        helix::nav::go_back();
        UpdateChecker::instance().show_update_notification();
    } else if (auto handler = helix::ui::notification_action_handler(action)) {
        helix::nav::go_back();
        handler();
    } else {
        spdlog::warn("[{}] Unknown action: {}", get_name(), action);
    }
}

namespace {

using ActionHandler = std::function<void(const std::string&)>;

std::map<std::string, ActionHandler>& notification_action_handlers() {
    static std::map<std::string, ActionHandler> handlers;
    return handlers;
}

} // namespace

namespace helix::ui {

void set_notification_action_handler(const std::string& id, ActionHandler handler) {
    if (handler) {
        notification_action_handlers()[id] = std::move(handler);
    } else {
        notification_action_handlers().erase(id);
    }
}

std::function<void()> notification_action_handler(const std::string& action) {
    const auto colon = action.find(':');
    const std::string id = action.substr(0, colon);
    const std::string arg = colon == std::string::npos ? std::string{} : action.substr(colon + 1);
    auto& handlers = notification_action_handlers();
    auto it = handlers.find(id);
    if (it == handlers.end()) {
        return {};
    }
    return [handler = it->second, arg] { handler(arg); };
}

} // namespace helix::ui

// ============================================================================
// STATIC TRAMPOLINES
// ============================================================================

void NotificationHistoryPanel::on_clear_clicked(lv_event_t* e) {
    LVGL_SAFE_EVENT_CB_BEGIN("[NotificationHistoryPanel] on_clear_clicked");
    auto* self = static_cast<NotificationHistoryPanel*>(lv_event_get_user_data(e));
    if (self) {
        self->handle_clear_clicked();
    }
    LVGL_SAFE_EVENT_CB_END();
}

// ============================================================================
// GLOBAL INSTANCE (needed by main.cpp)
// ============================================================================

NotificationHistoryPanel& get_global_notification_history_panel() {
    return helix::lazy_global<NotificationHistoryPanel>("NotificationHistoryPanel",
                                                        get_printer_state(), nullptr);
}
