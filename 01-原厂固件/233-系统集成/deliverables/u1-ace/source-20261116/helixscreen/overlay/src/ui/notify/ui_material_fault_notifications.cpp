// SPDX-License-Identifier: GPL-3.0-or-later
#include "ui_material_fault_notifications.h"
#include "ui_material_operation_banner.h"
#include "ams_state.h"
#include "async_lifetime_guard.h"
#include "core/static_panel_registry.h"
#include "material_fault_notice.h"
#include "material_workspace.h"
#include "ui_material_workspace.h"
#include "ui_notification.h"
#include "ui_notification_history.h"
#include "ui_notification_manager.h"
#include "ui_panel_notification_history.h"
#include "ui_timer_guard.h"
#include <algorithm>
#include <cstring>
#include <map>

namespace helix::ui {
namespace {
template <size_t N> void copy_text(char (&out)[N], const std::string& text) {
    size_t len = std::min(text.size(), N - 1);
    while (len < text.size() && len > 0 && (static_cast<unsigned char>(text[len]) & 0xc0) == 0x80)
        --len;
    std::memcpy(out, text.data(), len);
    out[len] = '\0';
}

class MaterialFaultNotifications {
  public:
    ~MaterialFaultNotifications() {
        timer_.reset();
        lifetime_.invalidate();
        set_notification_action_handler("material_fault", {});
    }
    void start() {
        if (timer_) return;
        set_notification_action_handler("material_fault", [this](const std::string& arg) { open(arg); });
        timer_.reset(lv_timer_create([](lv_timer_t* t) {
            static_cast<MaterialFaultNotifications*>(lv_timer_get_user_data(t))->poll();
        }, 2000, this));
        poll();
    }
  private:
    struct CurrentFault {
        material_workspace::FaultNotice notice;
        int seen_sequence = 0;
        std::string action;
    };

    void open(const std::string& arg) {
        // The handler accepts only the bounded identifiers produced by this source.
        if (arg.size() != 36 || arg[1] != ':' || arg[3] != ':' ||
            arg[0] < '0' || arg[0] > '3' || arg[2] < '0' || arg[2] > '3') return;
        const auto ticket = arg.substr(4);
        if (!material_workspace::valid_recovery_ticket(ticket)) return;
        const std::string key = "material_fault:" + arg.substr(0, 3);
        const auto found = current_.find(key);
        if (found == current_.end() || found->second.action != "material_fault:" + arg) {
            NotificationManager::instance().show_history();
            ui_notification_info("耗材异常", "错误或设备状态已变化，请返回通知后重试。");
            return;
        }
        const int ace = arg[0] - '0', slot = arg[2] - '0';
        auto* backend = AmsState::instance().get_backend();
        if (backend) {
            const auto info = backend->get_system_info();
            for (int unit = 0; unit < static_cast<int>(info.units.size()); ++unit) {
                if (material_workspace::ace_index(info, unit) == ace) {
                    get_material_workspace().open_action(slot, unit, "recover", {}, slot, ticket);
                    return;
                }
            }
        }
        NotificationManager::instance().show_history();
        ui_notification_info("耗材异常", "请先连接原 ACE，再处理该槽错误。");
    }
    void poll() {
        if (pending_) return;
        pending_ = true;
        const auto token = lifetime_.token();
        material_workspace::request("/state", {}, [this, token](auto state, std::string error) {
            // material_workspace delivers this callback on the UI thread.
            if (token.expired()) return;
            pending_ = false;
            const auto snapshot = error.empty() ? material_workspace::fault_notices(state) : std::nullopt;
            if (snapshot) {
                for (auto it = current_.begin(); it != current_.end();) {
                    if (snapshot->resolved_after(it->first, it->second.seen_sequence))
                        it = current_.erase(it);
                    else
                        ++it;
                }
                for (const auto& fault : snapshot->faults) {
                    auto& current = current_[fault.key];
                    current.notice = fault;
                    // An ongoing recovery can still report the original fault.
                    if (!snapshot->recovering)
                        current.seen_sequence = snapshot->task_sequence;
                }
            }
            std::vector<NotificationHistoryEntry> entries;
            for (auto& [key, current] : current_) {
                NotificationHistoryEntry entry{};
                entry.active = true;
                entry.severity = ToastSeverity::ERROR;
                copy_text(entry.key, key);
                copy_text(entry.title, current.notice.title);
                copy_text(entry.message, current.notice.message);
                current.action.clear();
                if (snapshot && snapshot->recovering) {
                    copy_text(entry.message, "正在检查设备状态，请稍候。");
                } else if (snapshot) {
                    const auto ticket = snapshot->recovery_tickets.find(key);
                    if (ticket != snapshot->recovery_tickets.end()) {
                        current.action = key + ":" + ticket->second;
                        copy_text(entry.action, current.action);
                        copy_text(entry.action_label, "清除错误");
                    } else if (key != material_workspace::kUnknownStopFaultKey) {
                        copy_text(entry.message, "设备状态暂不可用，请检查原 ACE 连接。");
                    }
                } else {
                    copy_text(entry.message, "设备状态暂不可用，请检查原 ACE 连接。");
                }
                entries.push_back(entry);
            }
            if (NotificationHistory::instance().sync_active("material_fault:", entries, lv_tick_get()))
                notification_refresh_from_history();
        });
    }
    bool pending_ = false;
    LvglTimerGuard timer_;
    AsyncLifetimeGuard lifetime_;
    std::map<std::string, CurrentFault> current_;
};
} // namespace

void start_material_fault_notifications() {
    if (!material_workspace::supports_local_upgrade()) return;
    start_material_operation_banner();
    helix::lazy_global<MaterialFaultNotifications>("MaterialFaultNotifications").start();
}
} // namespace helix::ui
