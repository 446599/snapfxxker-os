// SPDX-License-Identifier: GPL-3.0-or-later
#include "material_fault_demo.h"
#include "hv/json.hpp"
#include "json_utils.h"
#include <cstdlib>
#include <string>

namespace helix::material_workspace {
namespace {
int sequence = 0;
bool resolved = false;
std::string mode() {
    const char* value = std::getenv("HELIX_MOCK_MATERIAL_FAULT");
    const std::string selected = value ? value : "";
    return selected == "loaded" || selected == "empty" || selected == "stop" || selected == "stale"
        ? selected : "";
}
std::string ticket() { return std::string(31, 'a') + "0123456789abcdef"[sequence % 16]; }
} // namespace

nlohmann::json operation_demo_request(const std::string& path, const nlohmann::json& body) {
    using namespace json_util;
    static bool stopped = false;
    const char* mode = std::getenv("HELIX_MOCK_MATERIAL_OPERATION");
    const std::string kind = mode ? mode : "";
    const std::string epoch(16, 'a');
    if (kind != "insert" && kind != "rfid" && kind != "eject")
        return {{"slot_action", {{"epoch", epoch}}}, {"tag_op", {{"busy", false}}}};
    if (path == "/material/stop") {
        const bool same = safe_string(body, "kind") == kind && safe_string(body, "epoch") == epoch &&
            safe_int(body, "ace", -1) == 0 && safe_int(body, "slot", -1) == 0 && safe_int(body, "seq", 0) == 1;
        if (same) stopped = true;
        return {{"cancelled", same}};
    }
    nlohmann::json task = {{"kind", kind}, {"epoch", epoch}, {"ace", 0}, {"slot", 0}, {"seq", 1},
        {"state", stopped ? "cancelled" : "running"}, {"progress_percent", 42}, {"progress_estimated", true},
        {"message", stopped ? "已停止插料" : "正在插入 10 cm"}};
    nlohmann::json tag = {{"busy", false}};
    if (kind == "rfid") tag = {{"busy", !stopped}, {"kind", "read"}, {"ace", 0}, {"slot", 0}, {"seq", 1},
        {"result", {{"seq", 1}, {"ok", false}, {"message", "已停止 RFID 操作"}}}};
    return {{"slot_action", task}, {"tag_op", tag}};
}

void fault_demo_state(nlohmann::json& state) {
    const auto selected = mode();
    if (selected.empty()) return;
    static unsigned stale_revision = 0;
    const auto recovery_ticket = selected == "stale"
        ? std::string(31, 'b') + "0123456789abcdef"[++stale_revision % 16] : ticket();
    const bool held = !resolved;
    state["error_recovery"] = {{"active", false}, {"stop_fault", nullptr}, {"slots", nlohmann::json::array()}};
    if (!resolved && selected == "stop")
        state["error_recovery"]["stop_fault"] = {{"ace_idx", 0}, {"slot", 3}, {"reason", "demo stop"}};
    for (int ace = 0; ace < 4; ++ace) {
        for (int slot = 0; slot < 4; ++slot) {
            const bool target = ace == 0 && slot == 3;
            state["error_recovery"]["slots"].push_back({{"ace", ace}, {"slot", slot},
                {"needs_clear", target && held}, {"can_check", true},
                {"can_clear", !(target && held && selected == "stop")},
                {"ticket", recovery_ticket}, {"prompt", "请先手动退空本槽，再确认清除。"},
                {"detail", target && held ? "旧的失败装料记录仍占用料路，请退空本槽后清除。" : "可重新检查设备停止状态。"}});
        }
    }
    if (sequence > 0)
        state["slot_action"] = {{"state", resolved ? "complete" : "error"}, {"kind", "recover"},
            {"ace", 0}, {"slot", 3}, {"seq", sequence},
            {"message", resolved ? "已完成检查，本槽错误已解除。" : "ACE 1 · 4 槽入口仍有料，请手动退空后重试。"}};
}

nlohmann::json fault_demo_recover(const nlohmann::json& body) {
    using namespace json_util;
    const auto selected = mode();
    if (selected.empty() || safe_int(body, "ace", -1) != 0 || safe_int(body, "slot", -1) != 3 ||
        !safe_bool(body, "confirmed") || safe_string(body, "ticket") != ticket() ||
        safe_string(body, "action") != (selected == "stop" ? "check" : "clear"))
        return {{"started", false}, {"message", "演示状态已变化，请重新确认。"}};
    ++sequence;
    resolved = selected != "loaded";
    return {{"started", true}, {"seq", sequence}};
}
} // namespace helix::material_workspace
