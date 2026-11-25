// SPDX-License-Identifier: GPL-3.0-or-later

#include "light_button_config.h"

#include "ui_toast_manager.h"

#include "app_globals.h"
#include "connection_state.h"
#include "core/update_queue.h"
#include "i_moonraker_client.h"
#include "printer_state.h"
#include "ui_modal.h"
#include "config.h"
#include "json_utils.h"
#include "led/led_controller.h"
#include "led/led_devices.h"
#include "panel_widget_config.h"
#include "panel_widget_manager.h"
#include "panel_widget_registry.h"
#include "src/ui/panel_widgets/led_widget.h"

#include <spdlog/spdlog.h>

#include <algorithm>
#include <cstring>

namespace helix {

namespace {

bool is_light_button(const PanelWidgetEntry& e) {
    const PanelWidgetDef* def = find_widget_def(e.id);
    return def != nullptr && std::strcmp(def->id, "led") == 0;
}

std::string pending_value(const Config& cfg) {
    const nlohmann::json* p = cfg.try_get_json(cfg.df() + led::kLightButtonPendingPath);
    return (p != nullptr && p->is_string()) ? p->get<std::string>() : std::string();
}

} // namespace

std::vector<std::string> home_light_button_keys(const PanelWidgetConfig& home,
                                                const std::string& pending) {
    std::vector<std::string> keys;
    for (size_t p = 0; p < home.page_count(); ++p) {
        for (const auto& e : home.page_entries(p)) {
            if (!e.is_placed() || !is_light_button(e)) {
                continue;
            }
            const std::string key = json_util::safe_string(e.config, "led");
            keys.push_back(key.empty() ? pending : key);
        }
    }
    return keys;
}

bool adopt_pending_light_button(Config& cfg, PanelWidgetConfig& home) {
    const std::string value = pending_value(cfg);
    if (value.empty()) {
        return false;
    }
    std::vector<std::string> ids;
    for (size_t p = 0; p < home.page_count(); ++p) {
        for (const auto& e : home.page_entries(p)) {
            if (e.is_placed() && is_light_button(e) &&
                json_util::safe_string(e.config, "led").empty()) {
                ids.push_back(e.id);
            }
        }
    }
    for (const auto& id : ids) {
        nlohmann::json c = home.get_widget_config(id);
        if (!c.is_object()) {
            c = nlohmann::json::object();
        }
        c["led"] = value;
        home.set_widget_config(id, c);
    }
    cfg.set(cfg.df() + led::kLightButtonPendingPath, nlohmann::json());
    cfg.save();
    return !ids.empty();
}

std::vector<std::string> home_light_button_targets() {
    auto& ctrl = led::LedController::instance();
    std::vector<std::string> keys;
    auto* cfg = Config::get_instance();
    keys = home_light_button_keys(PanelWidgetManager::instance().get_widget_config("home"),
                                  pending_value(*cfg));

    return led::union_light_targets(keys, ctrl.switchable_ids(), ctrl.chamber_light());
}

bool home_light_buttons_lit() {
    auto& ctrl = led::LedController::instance();
    std::vector<led::DeviceState> states;
    for (const auto& id : home_light_button_targets()) {
        states.push_back(ctrl.device_state(id));
    }
    return light_icon_look(states).brightness > 0;
}

bool check_u1_cavity_light(const std::vector<std::string>& ids, bool off) {
    constexpr const char* lamp = "led cavity_led";
    if (std::find(ids.begin(),ids.end(),lamp) == ids.end()) return false;
    static uint64_t last_intent = 0; // all input and UI continuations run on the LVGL thread
    const auto intent = ++last_intent;
    auto* client = get_moonraker_client();
    auto& printer = get_printer_state();
    auto& lights = led::LedController::instance();
    if (!off || !client || !printer.get_discovery().has_snapmaker() ||
        printer.print_state().get_print_job_state() != PrintJobState::PRINTING ||
        lights.device_state(lamp).power != led::PowerState::On) return false;
    const auto connection = client->connection_generation();
    auto still_current = [client,connection,intent] {
        return last_intent == intent && client == get_moonraker_client() &&
            client->get_connection_state() == ConnectionState::CONNECTED &&
            client->connection_generation() == connection &&
            led::LedController::instance().device_state("led cavity_led").power == led::PowerState::On;
    };
    auto finish = [still_current, ids](std::optional<bool> enabled) {
        ui::queue_update("LightButtons::check_ai",[enabled,still_current,ids] {
            if (!still_current()) return;
            auto turn_off = [still_current,ids] {
                if (still_current()) led::LedController::instance().set_power(ids,false);
            };
            if (enabled == false || get_printer_state().print_state().get_print_job_state() != PrintJobState::PRINTING) {
                turn_off();
                return;
            }
            const char* prompt = enabled ? "设备已开启 AI 监控，关闭腔体照明会影响检测效果。确定关闭吗？"
                                         : "未能读取 AI 监控状态。仍要关闭打印机腔体照明吗？";
            ui::modal_confirm("关闭腔体照明",prompt,ModalSeverity::Warning,"关闭",turn_off);
        });
    };
    const auto id = client->send_jsonrpc("printer.objects.query",
        {{"objects",{{"defect_detection",{"main_enable"}}}}},
        [finish](const nlohmann::json& response) {
            static const nlohmann::json::json_pointer field("/result/status/defect_detection/main_enable");
            if (response.contains(field) && response[field].is_boolean()) finish(response[field].get<bool>());
            else finish(std::nullopt);
        }, [finish](const MoonrakerError&) { finish(std::nullopt); }, 4000, true);
    if (!id) finish(std::nullopt);
    return true;
}

LightToggleResult toggle_lights(const std::vector<std::string>& ids, const char* caller) {
    auto& ctrl = led::LedController::instance();
    if (ctrl.light_command_in_flight()) {
        spdlog::debug("[LightButtons] {}: ignoring toggle - LED command already in flight", caller);
        ToastManager::instance().show(
            ToastSeverity::INFO, lv_tr("Light will switch when the current operation finishes"));
        return {LightToggle::Busy, false};
    }
    if (ids.empty()) {
        spdlog::warn("[LightButtons] {}: light toggle with no light to switch", caller);
        return {LightToggle::NoTarget, false};
    }
    if (check_u1_cavity_light(ids, true)) return {LightToggle::AwaitingCheck,false};
    const bool on = ctrl.toggle_power(ids);
    spdlog::info("[LightButtons] {}: toggled {} device(s) {}", caller, ids.size(),
                 on ? "ON" : "OFF");
    const bool unconfirmed = std::all_of(ids.begin(), ids.end(), [&](const std::string& id) {
        return ctrl.device_state(id).power == led::PowerState::Unknown;
    });
    return {on ? LightToggle::On : LightToggle::Off, unconfirmed};
}

void settle_light_buttons() {
    auto* cfg = Config::get_instance();
    adopt_pending_light_button(*cfg, PanelWidgetManager::instance().get_widget_config("home"));

    auto& ctrl = led::LedController::instance();
    // A button may name a WLED strip that has not arrived yet, and falling back
    // to the chamber light would spend the startup attempt on the wrong light.
    // The discovery settling runs this again.
    if (ctrl.wled_discovery_pending()) {
        spdlog::debug("[LightButtons] WLED discovery in flight - startup preference waits");
        return;
    }
    ctrl.apply_startup_preference(home_light_button_targets());
}

} // namespace helix
