// SPDX-License-Identifier: GPL-3.0-or-later
#include "ui_material_operation_banner.h"
#include "ui_widget_ref.h"
#include "ui_button.h"
#include "ui_toast_manager.h"
#include "async_lifetime_guard.h"
#include "core/static_panel_registry.h"
#include "core/static_subject_registry.h"
#include "json_utils.h"
#include "material_operation.h"
#include "material_workspace.h"
#include "subject_managed_panel.h"
#include "ui_timer_guard.h"
#include "ui_nav_manager.h"
#include "ui_nav.h"
#include <spdlog/fmt/fmt.h>
#include <algorithm>
#include <optional>

namespace helix::ui {
namespace {
class MaterialOperationBanner;
MaterialOperationBanner* current_banner = nullptr;
class MaterialOperationBanner {
  public:
    ~MaterialOperationBanner() {
        timer_.reset();
        lifetime_.invalidate();
        current_banner = nullptr;
        subjects_.deinit_all();
    }
    void start() {
        if (timer_) return;
        current_banner = this;
        UI_MANAGED_SUBJECT_INT(visible_, 0, "material_op_visible", subjects_);
        UI_MANAGED_SUBJECT_INT(percent_, -1, "material_op_percent", subjects_);
        UI_MANAGED_SUBJECT_INT(pending_, 0, "material_op_stop_pending", subjects_);
        UI_MANAGED_SUBJECT_STRING(title_, title_buffer_, "耗材操作", "material_op_title", subjects_);
        UI_MANAGED_SUBJECT_STRING(detail_, detail_buffer_, "", "material_op_detail", subjects_);
        UI_MANAGED_SUBJECT_STRING(button_, button_buffer_, "停止", "material_op_button", subjects_);
        UI_MANAGED_SUBJECT_INT(multiple_, 0, "material_op_multiple", subjects_);
        lv_xml_register_event_cb(nullptr, "material_op_next", [](lv_event_t*) {
            if (current_banner) { current_banner->tasks_.next(); current_banner->render(); }
        });
        const auto token = lifetime_.token();
        StaticSubjectRegistry::instance().register_deinit("MaterialOperationBanner", [this, token]() {
            if (!token.expired()) subjects_.deinit_all();
        });
        lv_xml_register_event_cb(nullptr, "material_op_stop", [](lv_event_t*) {
            if (current_banner) current_banner->press();
        });
        timer_.reset(lv_timer_create([](lv_timer_t* timer) {
            static_cast<MaterialOperationBanner*>(lv_timer_get_user_data(timer))->tick();
        }, 200, this));
        tick();
    }
  private:
    void set_visible(bool visible) {
        if (!visible) expanded_ = false;
        const auto panel = lv_subject_get_int(NavigationManager::instance().get_active_panel_subject());
        if (panel != panel_at_open_) expanded_ = false;
        if (auto* chip = chip_.get()) {
            lv_obj_set_flag(chip, LV_OBJ_FLAG_HIDDEN, !visible);
            const auto text = expanded_ ? std::string("收起") : fmt::format("任务 {}",tasks_.size());
            if (chip_label_ != text) {
                ui_button_set_text(chip,text.c_str());
                chip_label_ = text;
            }
        }
        const bool show_tasks = visible && expanded_;
        if (auto* panels = panels_.get(); panels && show_tasks != panels_hidden_) {
            lv_obj_set_flag(panels,LV_OBJ_FLAG_HIDDEN,show_tasks);
            panels_hidden_ = show_tasks;
        }
        if (lv_subject_get_int(&visible_) != static_cast<int>(show_tasks)) {
            lv_subject_set_int(&visible_,show_tasks);
            ToastManager::instance().set_bottom_task_active(show_tasks);
        }
    }
    void ensure_layout() {
        if (chip_.get()) return;
        auto* content = lv_obj_find_by_name(lv_screen_active(), "content_area");
        if (!content) return;
        auto* status = lv_obj_find_by_name(content, "u1_live_status");
        auto* panel = lv_obj_find_by_name(content, "panel_container");
        if (!status || !panel) return;
        panels_ = WeakWidget(panel);
        panels_hidden_ = false;
        const char* attrs[] = {"name","material_op_toggle","width","70","height","100%",
            "style_min_height","0","style_pad_all","0","text","任务","variant","secondary",
            nullptr};
        auto* chip = static_cast<lv_obj_t*>(lv_xml_create(lv_obj_get_parent(status), "ui_button", attrs));
        if (!chip) return;
        lv_obj_add_event_cb(chip, [](lv_event_t*) {
            if (current_banner) {
                current_banner->panel_at_open_ = lv_subject_get_int(NavigationManager::instance().get_active_panel_subject());
                current_banner->expanded_ = !current_banner->expanded_;
                current_banner->render();
            }
        }, LV_EVENT_CLICKED,nullptr);
        chip_ = WeakWidget(chip);
        lv_obj_move_to_index(chip,lv_obj_get_index(status));
        lv_obj_add_flag(chip,LV_OBJ_FLAG_HIDDEN);
        // This row participates in the regular layout. Navigation and its
        // emergency stop stay reachable, and every modal covers the row.
        auto* banner = static_cast<lv_obj_t*>(lv_xml_create(content,"material_operation_banner",nullptr));
        if (banner) lv_obj_move_to_index(banner,lv_obj_get_index(panel));
    }
    void render() {
        auto* entry = tasks_.current();
        if (!entry) { set_visible(false); return; }
        const auto& op = entry->op;
        std::string heading = op.label();
        if (tasks_.size() > 1) heading = fmt::format("{}/{} · {}", tasks_.position(), tasks_.size(), heading);
        lv_subject_copy_string(&title_, heading.c_str());
        lv_subject_set_int(&multiple_, tasks_.size() > 1);
        lv_subject_set_int(&percent_, op.percent);
        const bool close = !op.active || entry->missing;
        lv_subject_copy_string(&button_, close ? "关闭" : op.stoppable ? "停止" : "查看");
        lv_subject_set_int(&pending_, !close && entry->awaiting_stop);
        std::string detail = op.message;
        if (offline_) detail = "连接中断，等待设备确认";
        else if (entry->missing) detail = "任务已离开活动列表，停止结果未确认";
        else if (!entry->failure.empty()) detail = entry->failure;
        else if (op.state == "cancelling") detail = op.message;
        else if (entry->awaiting_stop) detail = "正在停止，等待设备确认";
        else if (op.active && op.estimated && op.percent >= 0)
            detail = fmt::format("估算进度 {}% · {}", op.percent, op.message);
        else if (detail.empty()) detail = op.active ? "正在进行" : "操作状态已变化";
        lv_subject_copy_string(&detail_, detail.c_str());
        set_visible(true);

    }
    void tick() {
        ensure_layout();
        if (poll_pending_) return;
        poll_pending_ = true;
        const auto token = lifetime_.token();
        material_workspace::request("/material/progress", {}, [this, token](auto data, std::string error) {
            if (token.expired()) return;
            poll_pending_ = false;
            offline_ = !error.empty() || !data.is_object() || !data.contains("slot_action") ||
                       !data["slot_action"].is_object();
            if (!offline_) tasks_.update(material_workspace::material_operations(data), lv_tick_get());
            else if (auto* entry = tasks_.current(); entry && entry->awaiting_stop &&
                     lv_tick_elaps(entry->stop_at) >= 5000) entry->awaiting_stop = false;
            render();
        });
    }
    void press() {
        auto* entry = tasks_.current();
        if (!entry || entry->awaiting_stop) return;
        if (!entry->op.active || entry->missing) { tasks_.dismiss(); render(); return; }
        if (!entry->op.stoppable) {
            expanded_ = false;
            render();
            NavigationManager::instance().request_panel(PanelId::Filament, NavigationManager::SwitchDispatch::Queued);
            return;
        }
        const auto target = entry->op;
        entry->awaiting_stop = true;
        entry->stop_at = lv_tick_get();
        entry->failure.clear();
        const uint32_t stamp = entry->stop_at;
        render();
        const auto token = lifetime_.token();
        material_workspace::request("/material/stop", target.stop_request(),
            [this, token, target, stamp](auto result, std::string error) {
                if (token.expired()) return;
                auto* entry = tasks_.find(target);
                if (!entry || !entry->op.active || entry->stop_at != stamp) return;
                if (!error.empty() || !json_util::safe_bool(result, "cancelled", false)) {
                    entry->awaiting_stop = false;
                    entry->failure = error.empty() ? json_util::safe_string(result,"message","停止未确认，请重试") : error;
                    render();
                }
            }, true);
    }
    WeakWidget chip_, panels_;
    bool panels_hidden_ = false;
    bool expanded_ = false;
    int panel_at_open_ = -1;
    std::string chip_label_;
    SubjectManager subjects_;
    lv_subject_t visible_{}, percent_{}, pending_{}, title_{}, detail_{}, button_{}, multiple_{};
    char title_buffer_[128]{}, detail_buffer_[384]{}, button_buffer_[32]{};
    LvglTimerGuard timer_;
    AsyncLifetimeGuard lifetime_;
    bool poll_pending_ = false, offline_ = false;
    material_workspace::MaterialTaskList tasks_;
};
} // namespace
void start_material_operation_banner() {
    if (!material_workspace::supports_local_upgrade()) return;
    helix::lazy_global<MaterialOperationBanner>("MaterialOperationBanner").start();
}
} // namespace helix::ui
