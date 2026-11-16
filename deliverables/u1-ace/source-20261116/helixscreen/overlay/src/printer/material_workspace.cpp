// SPDX-License-Identifier: GPL-3.0-or-later
#include "material_workspace.h"
#include "material_fault_demo.h"
#include "path_calibration_demo.h"
#include "firmware_settings_demo.h"

#include "app_globals.h"
#include "ams_backend.h"
#include "ams_head_presence.h"
#include "multiace_status.h"
#include "ams_state.h"
#include "core/http_executor.h"
#include "core/update_queue.h"
#include "hv/requests.h"
#include "i_moonraker_api.h"
#include "moonraker_error.h"
#include "moonraker_validation.h"
#include "runtime_config.h"
#include "filament_motion.h"
#include "filament_op_router.h"
#include "printer_state.h"
#include "settings_manager.h"
#include "tool_state.h"
#include "ui_temperature_utils.h"

#include <algorithm>
#include <cstdlib>
#include <map>

namespace helix::material_workspace {
std::optional<bool> head_physical_presence(const AmsBackend* backend, int head) {
    auto* presence = backend ? backend->as<IHeadPresence>() : nullptr;
    return presence ? presence->head_physical_presence(head) : std::nullopt;
}

std::optional<int> head_physical_source(const AmsBackend* backend, int head) {
    auto* presence = backend ? backend->as<IHeadPresence>() : nullptr;
    return presence ? presence->head_physical_source(head) : std::nullopt;
}

std::optional<int> ace_index(const AmsSystemInfo& info, int unit) {
    if (unit < 0 || static_cast<size_t>(unit + 1) >= info.units.size()) return std::nullopt;
    const auto bay = multiace::bay_of(info.units[static_cast<size_t>(unit + 1)].first_slot_global_index);
    return bay ? std::optional<int>{bay->first} : std::nullopt;
}

const SlotInfo* head_slot(const AmsSystemInfo& info, int head) {
    if (head < 0 || head >= 4 || info.units.empty())
        return nullptr;
    const auto& slots = info.units.front().slots;
    const auto found = std::find_if(slots.begin(), slots.end(), [head](const SlotInfo& slot) {
        return slot.mapped_tool == head;
    });
    return found == slots.end() ? nullptr : &*found;
}

bool uses_external_spools(const AmsSystemInfo& info) {
    if (info.units.size() != 1 || info.units.front().slots.size() != 4 ||
        info.units.front().topology != PathTopology::PARALLEL)
        return false;
    for (int head = 0; head < 4; ++head) {
        if (!head_slot(info, head))
            return false;
    }
    return true;
}

bool supports_head_inventory(const AmsSystemInfo& info) {
    return info.head_focused_inventory || uses_external_spools(info);
}

std::string manual_move_refusal(int head) {
    auto* api = get_moonraker_api();
    auto* backend = AmsState::instance().get_backend();
    auto& state = get_printer_state();
    if (!api || !backend ||
        lv_subject_get_int(state.network_state().get_nav_buttons_enabled_subject()) != 1)
        return "设备未连接";
    const auto info = backend->get_system_info();
    const auto* slot = head_slot(info, head);
    if (!supports_head_inventory(info) || !slot || !info.units.front().connected)
        return "等待当前喷头状态";
    if (lv_subject_get_int(state.print_state().get_machine_motion_blocked_subject()) ||
        ams_action_holds_system(info.action) || !info.path_hold_reason.empty())
        return "请等待设备空闲并确认料路位置";
    const auto& tools = ToolState::instance();
    const auto* active = tools.active_tool();
    if (info.current_tool != head || !active || active->index != head || !active->extruder_name ||
        *active->extruder_name != slot->extruder_name ||
        state.temperature_state().active_extruder_name() != slot->extruder_name)
        return "请先激活此喷头";
    if (auto* motion = lv_xml_get_subject(nullptr, kFilamentMotionSubjectName);
        motion && lv_subject_get_int(motion) != static_cast<int>(FilamentMotion::Idle))
        return "请等待当前进退料结束";
    auto* temperature = state.temperature_state().get_extruder_temp_subject(slot->extruder_name);
    const int floor = ui::temperature::extrusion_floor_c(api->get_safety_limits(),
                                                        slot->extruder_name);
    if (!temperature || !ui::temperature::is_extrusion_safe(
                            ui::temperature::deci_to_degrees(lv_subject_get_int(temperature)), floor))
        return "请先加热喷头至可挤出温度";
    return {};
}

void manual_move(int head, int millimeters, Reply reply) {
    if (millimeters != 5 && millimeters != -5) {
        reply({}, "手动进退料仅支持每次 5 mm");
        return;
    }
    const auto refusal = manual_move_refusal(head);
    if (!refusal.empty()) {
        reply({}, refusal);
        return;
    }
    // Browse never mounts a tool. The existing manual-motion command applies only
    // after the live active tool, extruder, connection and heater have matched.
    const int speed = SettingsManager::instance().get_extrude_speed() * 60;
    if (speed <= 0) {
        reply({}, "挤出速度无效，请检查设置");
        return;
    }
    get_moonraker_api()->execute_gcode(
        ui::extrude_gcode(millimeters, speed),
        [reply]() { ui::queue_update([reply]() { reply({{"ok", true}}, {}); }); },
        [reply](const MoonrakerError& error) {
            const auto message = error.message;
            ui::queue_update([reply, message]() { reply({}, message); });
        }, IMoonrakerAPI::kAmsOperationTimeoutMs);
}

bool supports_local_upgrade() {
#if defined(HELIX_PLATFORM_SNAPMAKER_U1)
    return true;
#else
    const auto* runtime = get_runtime_config();
    const char* printer = std::getenv("HELIX_MOCK_PRINTER");
    return runtime && runtime->should_mock_moonraker() && printer &&
           std::string(printer) == "snapmaker_u1";
#endif
}

void request(const std::string& path, const nlohmann::json& body, Reply reply, bool post) {
    auto* runtime = get_runtime_config();
    if (runtime && runtime->should_mock_moonraker()) {
        nlohmann::json data = nlohmann::json::object();
        if (path == "/state") {
            data["spools"] = {{"demo1",
                               {{"material", "PLA"},
                                {"color", "D68459"},
                                {"vendor", "Generic"},
                                {"weight_g", 640},
                                {"weight_initial_g", 1000}}},
                              {"demo2",
                               {{"material", "PETG"},
                                {"color", "5A8174"},
                                {"vendor", "Generic"},
                                {"weight_g", 320},
                                {"weight_initial_g", 1000}}}};
            data["quad_replenish"] = true;
            data["pa_sync"] = true;
            data["airprint_detection"] = true;
            data["pickup_cleaning"] = false;
            data["purge_matrix"] = true;
            data["tag_op"] = {{"busy", false}};
            data["slot_action"] = {{"state", "idle"}};
            data["swap_in_progress"] = false;
            data["unload_all_active"] = false;
            data["aces"] = nlohmann::json::array();
            for (int u = 0; u < 4; ++u)
                data["aces"].push_back({{"idx", u},
                                        {"connected", true},
                                        {"slots", {{{"idx", 0}, {"position_unconfirmed", false}, {"eject_recovery_allowed", false}},
                                                   {{"idx", 1}, {"position_unconfirmed", false}, {"eject_recovery_allowed", false}},
                                                   {{"idx", 2}, {"position_unconfirmed", false}, {"eject_recovery_allowed", false}},
                                                   {{"idx", 3}, {"position_unconfirmed", false}, {"eject_recovery_allowed", false}}}},
                                        {"protocol", "v2"},
                                        {"auto_dry_running", false},
                                        {"auto_dry",
                                         {{"enabled", false},
                                          {"temp", 50},
                                          {"rh_start", 45},
                                          {"rh_end", 35},
                                          {"master", -1},
                                          {"add_time", 60}}}});
            data["spool_binding"] = {{"0_0", "demo1"}, {"1_0", "demo2"}};
            data["path_calibration"] = path_calibration_demo_state();
            fault_demo_state(data);
        } else if (path == "/material/progress" || (path == "/material/stop" && post)) {
            data = operation_demo_request(path, body);
        } else if (path == "/material/recover" && post) {
            data = fault_demo_recover(body);
        } else if (path == "/firmware/settings") {
            data = firmware_settings_demo(body, post);
        } else if (path == "/path-calibration/stop" || path == "/path-calibration/cancel") {
            const std::string error = path_calibration_demo_action({
                {"OP", path == "/path-calibration/stop" ? "stop" : "cancel"},
                {"TASK_ID", body.value("task_id", "")},
                {"ACTION_ID", body.value("action_id", "")},
                {"EXPECTED_VERSION", body.value("expected_version", -1)}});
            ui::queue_update([reply, error]() { reply({{"ok", error.empty()}}, error); });
            return;
        } else if (path == "/firmware/files") {
            data["files"] = {{{"name", "U1_2.1.0_upgrade.bin"},
                              {"path", "/media/usb/U1_2.1.0_upgrade.bin"},
                              {"size", 217588250}}};

        } else if (path == "/firmware/prepare") {
            data = {
                {"token", "preview-only"}, {"version", "2.1.0"}, {"sha256", std::string(64, 'a')}};
        } else if (path == "/firmware/status") {
            data = {{"state", "idle"},
                    {"pct", 0},
                    {"msg", "模拟设备 · 升级流程预览"},
                    {"current_version", "2.1.0"}};
        }
        ui::queue_update([reply = std::move(reply), data]() { reply(data, {}); });
        return;
    }
    const bool control = post &&
        (path == "/path-calibration/stop" || path == "/path-calibration/cancel" ||
         path == "/material/stop");
    auto* executor = &http::HttpExecutor::fast();
    if (control) executor = &http::HttpExecutor::control();
    else if (path == "/firmware/prepare" || (path == "/firmware/settings" && post))
        executor = &http::HttpExecutor::slow();
    executor->submit([path, body, post, control, reply = std::move(reply)]() {
            nlohmann::json data;
            std::string error;
            try {
                auto req = std::make_shared<HttpRequest>();
                req->url = "http://127.0.0.1:7136" + path;
                req->method = post ? HTTP_POST : HTTP_GET;
                req->timeout = control || path == "/material/progress" ? 2 : path == "/state" ? 4 : path == "/firmware/prepare" ? 180 :
                    (path == "/firmware/settings" && post) ? 240 : 30;
                if (post) {
                    req->content_type = APPLICATION_JSON;
                    req->body = body.dump();
                }
                auto resp = requests::request(req);
                if (!resp) {
                    error = "本机服务未连接，请检查 u1-device-service";
                } else {
                    data = nlohmann::json::parse(resp->body);
                    if (!data.is_object())
                        throw std::runtime_error("设备服务返回了无效数据");
                    if (resp->status_code < 200 || resp->status_code >= 300)
                        error = data.value("error", "设备服务请求失败");
                }
            } catch (const std::exception& e) {
                error = e.what();
            }
            ui::queue_update([reply = std::move(reply), data, error]() { reply(data, error); });
        });
}

std::optional<std::string> build_command(const std::string& action, const nlohmann::json& args) {
    // multiACE vocabulary stays in the provider. Views use capability action names.
    static const std::map<std::string, std::string> commands = {
        {"quad_replenish", "ACE_SET_QUAD_REPLENISH"},
        {"pa_sync", "ACE_SET_PA_SYNC"},
        {"airprint", "ACE_SET_AIRPRINT_DETECTION"},
        {"pickup_cleaning", "ACE_SET_PICKUP_CLEANING"},
        {"purge_matrix", "ACE_SET_PURGE"},
        {"read_tag", "ACE_SLOT_RFID"},
        {"eject", "ACE_SLOT_EXIT"},
        {"write_tag", "ACE_TAG_WRITE"},
        {"spool_set", "ACE_SPOOL_SET"},
        {"spool_add", "ACE_SPOOL_ADD"},
        {"spool_assign", "ACE_SPOOL_ASSIGN"},
        {"spool_delete", "ACE_SPOOL_DELETE"},
        {"auto_dry", "ACE_SET_AUTO_DRY"},
        {"calibrate_pa", "ACE_PA_CALIBRATE"},
        {"path_calibration", "ACE_PATH_CALIBRATION"}};
    const auto found = commands.find(action);
    if (found == commands.end() || !args.is_object())
        return std::nullopt;
    std::string gcode = found->second;
    for (auto it = args.begin(); it != args.end(); ++it) {
        const std::string value =
            it.value().is_string() ? it.value().get<std::string>() : it.value().dump();
        const bool safe_key =
            !it.key().empty() && std::all_of(it.key().begin(), it.key().end(), [](char c) {
                return (c >= 'A' && c <= 'Z') || c == '_';
            });
        // Preserve UTF-8 metadata from existing web-created spools while keeping
        // both ASCII command delimiters and Unicode whitespace out of parameters.
        std::string ascii;
        for (size_t pos = 0; pos < value.size();) {
            const auto sequence = text_io::decode_utf8(value, pos);
            if (!sequence ||
                (sequence->codepoint >= 0x80 &&
                 (sequence->codepoint < 0xA0 || is_unicode_space(sequence->codepoint))))
                return std::nullopt;
            if (sequence->codepoint < 0x80)
                ascii.push_back(static_cast<char>(sequence->codepoint));
            pos += sequence->length;
        }
        if (!safe_key || (!ascii.empty() && !IMoonrakerAPI::is_safe_material_param(ascii)))
            return std::nullopt;
        gcode += " " + it.key() + "=" + IMoonrakerAPI::gcode_param_value(value);
    }
    return gcode;
}
void command(const std::string& action, const nlohmann::json& args, Reply reply) {
    const auto* runtime = get_runtime_config();
    if (action == "path_calibration" && runtime && runtime->should_mock_moonraker()) {
        const std::string error = path_calibration_demo_action(args);
        ui::queue_update([reply, error]() { reply({{"ok", error.empty()}}, error); });
        return;
    }
    auto* api = get_moonraker_api();
    const auto gcode = build_command(action, args);
    if (!api || !gcode) {
        reply({}, "当前设备不支持此操作或参数包含不支持的字符");
        return;
    }
    api->execute_gcode(
        *gcode, [reply]() { ui::queue_update([reply]() { reply({{"ok", true}}, {}); }); },
        [reply](const MoonrakerError& error) {
            const std::string message = error.message;
            ui::queue_update([reply, message]() { reply({}, message); });
        },
        IMoonrakerAPI::kAmsOperationTimeoutMs);
}
} // namespace helix::material_workspace
