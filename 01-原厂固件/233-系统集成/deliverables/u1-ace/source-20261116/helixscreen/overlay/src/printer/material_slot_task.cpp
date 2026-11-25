// SPDX-License-Identifier: GPL-3.0-or-later
#include "material_workspace.h"
#include "json_utils.h"

namespace helix::material_workspace {
SlotTaskState slot_task_state(const nlohmann::json& state, int ace, int slot) {
    using json_util::safe_bool;
    using json_util::safe_int;
    using json_util::safe_string;
    SlotTaskState out;
    if (!state.is_object() || ace < 0 || ace > 3 || slot < 0 || slot > 3)
        return out;
    const auto units = state.find("aces");
    const auto tag = state.find("tag_op");
    const auto task = state.find("slot_action");
    if (units == state.end() || !units->is_array() || tag == state.end() ||
        !tag->is_object() || task == state.end() || !task->is_object() ||
        !tag->contains("busy") || !(*tag)["busy"].is_boolean())
        return out;
    for (const auto& unit : *units) {
        if (safe_int(unit, "idx", -1) != ace || !safe_bool(unit, "connected", false) ||
            !unit.contains("slots") || !unit["slots"].is_array())
            continue;
        for (const auto& bay : unit["slots"]) {
            if (safe_int(bay, "idx", -1) != slot || !bay.contains("position_unconfirmed") ||
                !bay["position_unconfirmed"].is_boolean() ||
                !bay.contains("eject_recovery_allowed") || !bay["eject_recovery_allowed"].is_boolean())
                continue;
            out.held = bay["position_unconfirmed"].get<bool>();
            out.recovery = bay["eject_recovery_allowed"].get<bool>();
            out.valid = !out.recovery || out.held;
        }
    }
    if (!out.valid)
        return out;
    const auto phase = safe_string(*task, "state");
    const bool running = phase == "running" || phase == "preparing";
    out.busy = (*tag)["busy"].get<bool>() || running ||
               safe_bool(state, "swap_in_progress", true) ||
               safe_bool(state, "unload_all_active", true);
    if (safe_bool(*tag, "busy", false) && (safe_string(*tag, "kind") == "read" || safe_string(*tag, "kind") == "insert") &&
        safe_int(*tag, "ace", -1) == ace && safe_int(*tag, "slot", -1) == slot) {
        out.kind = "rfid";
        out.seq = safe_int(*tag, "seq", 0);
        out.message = "正在读取 RFID，可随时请求停止";
        out.stoppable = out.seq > 0;
    } else if (safe_int(*task, "ace", -1) == ace && safe_int(*task, "slot", -1) == slot) {
        out.kind = safe_string(*task, "kind");
        out.seq = safe_int(*task, "seq", 0);
        out.message = safe_string(*task, "message");
        out.stoppable = running && (out.kind == "eject" || out.kind == "insert") && out.seq > 0;
    } else {
        out.message = out.busy ? "其他料路操作正在进行，请稍候" : "请选择需要的操作";
    }
    const bool recovery_result = out.kind == "recover" && (phase == "complete" || phase == "error");
    out.terminal_result = recovery_result;
    if (out.held && !out.busy && !recovery_result)
        out.message = out.recovery ? "耗材位置待核，可用「退出 ACE」恢复后重新插入"
                                   : "此料路位置待核，请先退出发生异常的 ACE 槽位";
    const auto recovery = state.find("error_recovery");
    if (recovery != state.end() && recovery->is_object()) {
        const auto fault = recovery->find("stop_fault");
        out.stop_fault = fault != recovery->end() && fault->is_object();
        const auto slots = recovery->find("slots");
        if (slots != recovery->end() && slots->is_array()) {
            for (const auto& entry : *slots) {
                if (safe_int(entry, "ace", -1) != ace || safe_int(entry, "slot", -1) != slot)
                    continue;
                out.recovery_ticket = safe_string(entry, "ticket");
                out.recovery_prompt = safe_string(entry, "prompt");
                out.check_ready = !out.busy && out.recovery_ticket.size() == 32 && safe_bool(entry, "can_check");
                out.clear_ready = !out.busy && out.recovery_ticket.size() == 32 && safe_bool(entry, "can_clear");
                out.has_clearable_record = safe_bool(entry, "needs_clear");
                out.needs_clear = out.has_clearable_record || out.stop_fault || recovery_result;
                if (!out.busy && out.needs_clear && !recovery_result) out.message = safe_string(entry, "detail");
                break;
            }
        }
    }
    return out;
}
} // namespace helix::material_workspace
