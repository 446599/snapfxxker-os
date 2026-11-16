// SPDX-License-Identifier: GPL-3.0-or-later
#include "ui_material_operation_banner.h"
#include "ui_toast_manager.h"
#include "async_lifetime_guard.h"
#include "core/static_panel_registry.h"
#include "core/static_subject_registry.h"
#include "json_utils.h"
#include "material_operation.h"
#include "material_workspace.h"
#include "subject_managed_panel.h"
#include "ui_timer_guard.h"
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
        if (root_ && lv_is_initialized()) {
            if (StaticPanelRegistry::is_destroying_all())
                StaticPanelRegistry::instance().record_orphaned_widget(root_);
            else lv_obj_delete_async(root_);
        }
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
        const auto token = lifetime_.token();
        StaticSubjectRegistry::instance().register_deinit("MaterialOperationBanner", [this, token]() {
            if (!token.expired()) subjects_.deinit_all();
        });
        lv_xml_register_event_cb(nullptr, "material_op_stop", [](lv_event_t*) {
            if (current_banner) current_banner->press();
        });
        root_ = static_cast<lv_obj_t*>(lv_xml_create(lv_layer_top(), "material_operation_banner", nullptr));
        if (!root_) return;
        timer_.reset(lv_timer_create([](lv_timer_t* timer) {
            static_cast<MaterialOperationBanner*>(lv_timer_get_user_data(timer))->tick();
        }, 200, this));
        tick();
    }
  private:
    void set_visible(bool visible) {
        lv_subject_set_int(&visible_, visible);
        ToastManager::instance().set_bottom_task_active(visible);
    }
    void present(const material_workspace::MaterialOperation& op) {
        const bool changed = !operation_ || !operation_->same_task(op);
        if (changed) {
            stop_requested_ = false;
            lv_subject_set_int(&pending_, 0);
        }
        operation_ = op;
        const char* action = op.kind == "insert" ? "插入 10 cm" : op.kind == "rfid" ? "RFID 识别" : "退料";
        lv_subject_copy_string(&title_, fmt::format("ACE {} · {} 槽 · {}", op.ace + 1, op.slot + 1, action).c_str());
        lv_subject_copy_string(&button_, op.active ? "停止" : "关闭");
        lv_subject_set_int(&percent_, op.percent);
        std::string detail = op.message;
        if (op.active && stop_requested_) detail = "正在停止，等待设备确认";
        else if (op.active && op.estimated && op.percent >= 0)
            detail = fmt::format("估算进度 {}% · {}", op.percent, op.message);
        else if (detail.empty()) detail = op.active ? "正在进行" : "操作状态已变化";
        lv_subject_copy_string(&detail_, detail.c_str());
        set_visible(true);
        // Keep the operation control reachable above editors and dialogs.
        lv_obj_move_foreground(root_);
        if (op.active) finished_at_ = 0;
        else if (!finished_at_) finished_at_ = lv_tick_get();
    }
    void tick() {
        if (finished_at_ && operation_ && operation_->state == "complete" &&
            lv_tick_elaps(finished_at_) > 3500) set_visible(false);
        if (poll_pending_) return;
        poll_pending_ = true;
        const auto token = lifetime_.token();
        material_workspace::request("/material/progress", {}, [this, token](auto data, std::string error) {
            // request() dispatches callbacks on the LVGL thread.
            if (token.expired()) return;
            poll_pending_ = false;
            if (!error.empty()) {
                if (operation_ && operation_->active) {
                    lv_subject_copy_string(&detail_, "连接中断，停止状态未确认；可重试停止");
                    lv_obj_move_foreground(root_);
                }
                return;
            }
            const auto ops = material_workspace::material_operations(data);
            const auto active = std::find_if(ops.begin(), ops.end(), [](const auto& op) { return op.active; });
            if (active != ops.end()) present(*active);
            else if (operation_ && operation_->active) {
                const auto done = std::find_if(ops.begin(), ops.end(), [this](const auto& op) {
                    return operation_->same_task(op);
                });
                if (done != ops.end()) present(*done);
                else {
                    auto unknown = *operation_;
                    unknown.active = false; unknown.state = "unknown"; unknown.percent = -1;
                    unknown.message = "设备状态已变化，请核对耗材是否停止";
                    present(unknown);
                }
            }
        });
    }
    void press() {
        if (!operation_ || lv_subject_get_int(&pending_)) return;
        if (!operation_->active) { set_visible(false); return; }
        const auto target = *operation_;
        stop_requested_ = true;
        lv_subject_set_int(&pending_, 1);
        lv_subject_copy_string(&detail_, "正在停止，等待设备确认");
        const auto token = lifetime_.token();
        material_workspace::request("/material/stop", target.stop_request(),
            [this, token, target](auto result, std::string error) {
                if (token.expired() || !operation_ || !operation_->same_task(target)) return;
                lv_subject_set_int(&pending_, 0);
                if (!operation_->active) return;
                if (!error.empty() || !json_util::safe_bool(result, "cancelled", false)) {
                    stop_requested_ = false;
                    lv_subject_copy_string(&detail_, "停止未确认，请重试或检查设备");
                }
                // Acknowledgement is only acceptance; polling confirms the terminal state.
            }, true);
    }
    lv_obj_t* root_ = nullptr;
    SubjectManager subjects_;
    lv_subject_t visible_{}, percent_{}, pending_{}, title_{}, detail_{}, button_{};
    char title_buffer_[128]{}, detail_buffer_[384]{}, button_buffer_[32]{};
    LvglTimerGuard timer_;
    AsyncLifetimeGuard lifetime_;
    bool poll_pending_ = false, stop_requested_ = false;
    uint32_t finished_at_ = 0;
    std::optional<material_workspace::MaterialOperation> operation_;
};
} // namespace
void start_material_operation_banner() {
    if (!material_workspace::supports_local_upgrade()) return;
    helix::lazy_global<MaterialOperationBanner>("MaterialOperationBanner").start();
}
} // namespace helix::ui
