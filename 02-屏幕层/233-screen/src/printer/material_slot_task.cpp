// SPDX-License-Identifier: GPL-3.0-or-later
#include "material_workspace.h"
#include "json_utils.h"
#include "material_fault_notice.h"

namespace helix::material_workspace {
bool can_unload_ace_nozzle(const SlotTaskState& task, int selected_slot,
                           std::optional<int> physical_source, std::optional<bool> head_present) {
    // A parking-position hold does not invalidate a live, matching nozzle source.
    return task.valid && !task.busy && !task.stop_fault && selected_slot >= 0 &&
           head_present == true && physical_source == selected_slot;
}

std::string_view next_slot_recovery_action(const SlotTaskState& task) {
    if (!task.valid || task.busy) return {};
    if (task.source_review) return task.source_review_ready ? "recover_source" : "";
    if (task.verified_recovery) return task.auto_ready ? "recover_auto" : "";
    if (task.has_clearable_record && task.clear_ready) return "recover_clear";
    return task.check_ready ? "recover_check" : "";
}

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
    bool inlet = false;
    for (const auto& unit : *units) {
        if (safe_int(unit, "idx", -1) != ace || !safe_bool(unit, "connected", false) ||
            !unit.contains("slots") || !unit["slots"].is_array())
            continue;
        const auto gates = unit.find("gate_status");
        if (gates != unit.end() && gates->is_array() && static_cast<size_t>(slot) < gates->size()) {
            const auto& gate = (*gates)[static_cast<size_t>(slot)];
            inlet = gate.is_number_integer() && gate == 1;
        }
        for (const auto& bay : unit["slots"]) {
            if (safe_int(bay, "idx", safe_int(bay, "index", -1)) != slot || !bay.contains("position_unconfirmed") ||
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
    } else if (safe_int(*task, "ace", -1) == ace && safe_int(*task, "slot", -1) == slot) {
        out.kind = safe_string(*task, "kind");
        out.seq = safe_int(*task, "seq", 0);
        out.message = safe_string(*task, "message");
    } else {
        out.message = out.busy ? "其他料路操作正在进行，请稍候" : "请选择需要的操作";
    }
    for (const auto& op : material_operations(state)) {
        if (op.active && op.ace == ace && op.slot == slot && op.kind == out.kind && op.seq == out.seq) {
            out.stop_target = op;
            break;
        }
    }
    const bool recovery_result = out.kind == "recover" &&
        (phase == "complete" || phase == "error" || phase == "cancelled");
    out.terminal_result = recovery_result;
    out.recovered_stop = recovery_result && phase == "complete" && safe_bool(*task, "stopped");
    if (out.held && !out.busy && !recovery_result)
        out.message = out.recovery ? "耗材位置待核，可用「退出 ACE」恢复后重新插入"
                                   : "此料路位置待核，请先退出发生异常的 ACE 槽位";
    const auto recovery = state.find("error_recovery");
    if (recovery != state.end() && recovery->is_object()) {
        const auto fault = recovery->find("stop_fault");
        out.stop_fault = fault != recovery->end() && fault->is_object();
        out.busy = out.busy || safe_bool(*recovery, "active");
        const auto slots = recovery->find("slots");
        if (slots != recovery->end() && slots->is_array()) {
            for (const auto& entry : *slots) {
                if (safe_int(entry, "ace", -1) != ace || safe_int(entry, "slot", -1) != slot)
                    continue;
                const auto blocked = entry.find("blocked_by_stop");
                if (blocked != entry.end() && blocked->is_boolean())
                    out.stop_fault = blocked->get<bool>();
                out.recovery_ticket = safe_string(entry, "ticket");
                out.recovery_prompt = safe_string(entry, "prompt");
                const bool usable = !out.busy && valid_recovery_ticket(out.recovery_ticket);
                out.check_ready = usable && safe_bool(entry, "can_check");
                out.clear_ready = usable && safe_bool(entry, "can_clear");
                out.verified_recovery = entry.contains("can_auto") && entry["can_auto"].is_boolean();
                out.auto_ready = usable && safe_bool(entry, "can_auto");
                out.source_review = safe_bool(entry, "needs_source_confirmation");
                out.source_head = safe_int(entry, "source_head", -1);
                out.source_review_ready = usable && inlet && safe_bool(entry, "can_confirm_source") &&
                    out.source_head >= 0 && out.source_head < 4;
                out.has_clearable_record = safe_bool(entry, "needs_clear");
                out.needs_clear = out.has_clearable_record || out.stop_fault || out.source_review || recovery_result;
                if (!out.busy && out.source_review) {
                    out.message = inlet ? safe_string(entry, "source_confirmation_prompt", "请确认原卷和原料路。")
                                        : "原槽口未检测到耗材，请核对透明管内是否还有料。";
                } else if (!out.busy && out.needs_clear && !recovery_result) {
                    out.message = out.stop_fault ? "停稳尚未确认，送料已暂停。"
                                                 : safe_string(entry, "detail", "请检查该槽。");
                }
                break;
            }
        }
    }
    return out;
}
} // namespace helix::material_workspace
