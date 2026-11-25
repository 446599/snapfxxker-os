// SPDX-License-Identifier: GPL-3.0-or-later
#include "material_workspace.h"

#include "app_globals.h"
#include "core/http_executor.h"
#include "core/update_queue.h"
#include "hv/requests.h"
#include "i_moonraker_api.h"
#include "moonraker_error.h"
#include "moonraker_validation.h"
#include "runtime_config.h"

#include <algorithm>
#include <cstdlib>
#include <map>

namespace helix::material_workspace {
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

std::string request_url(const std::string& path, bool post) {
    // State and spool sync already live in the MultiACE API. Query it directly so
    // the screen does not depend on the optional 7136 companion forwarding service.
    if ((!post && path == "/state") || (post && path == "/spoolman/sync")) {
        return "http://127.0.0.1:7126/api" + path;
    }
    // Local firmware staging/upgrade must remain behind the device service, which
    // owns file validation, idle checks, and the one-shot confirmation token.
    return "http://127.0.0.1:7136" + path;
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
            data["aces"] = nlohmann::json::array();
            for (int u = 0; u < 4; ++u)
                data["aces"].push_back({{"idx", u},
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
    (path == "/firmware/prepare" ? http::HttpExecutor::slow() : http::HttpExecutor::fast())
        .submit([path, body, post, reply = std::move(reply)]() {
            nlohmann::json data;
            std::string error;
            try {
                auto req = std::make_shared<HttpRequest>();
                req->url = request_url(path, post);
                req->method = post ? HTTP_POST : HTTP_GET;
                req->timeout = path == "/firmware/prepare" ? 180 : 30;
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
        {"read_tag", "ACE_TAG_READ"},
        {"write_tag", "ACE_TAG_WRITE"},
        {"spool_set", "ACE_SPOOL_SET"},
        {"spool_add", "ACE_SPOOL_ADD"},
        {"spool_assign", "ACE_SPOOL_ASSIGN"},
        {"spool_delete", "ACE_SPOOL_DELETE"},
        {"auto_dry", "ACE_SET_AUTO_DRY"},
        {"calibrate_pa", "ACE_PA_CALIBRATE"}};
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
