// SPDX-License-Identifier: GPL-3.0-or-later
#include "material_fault_notice.h"
#include "hv/json.hpp"
#include "json_utils.h"
#include <algorithm>
#include <set>

namespace helix::material_workspace {
namespace {
bool valid_bay(int ace, int slot) { return ace >= 0 && ace < 4 && slot >= 0 && slot < 4; }
std::string key(int ace, int slot) {
    return "material_fault:" + std::to_string(ace) + ":" + std::to_string(slot);
}
FaultNotice notice(int ace, int slot, const std::string& message) {
    return {ace, slot, key(ace, slot),
        "ACE " + std::to_string(ace + 1) + " · " + std::to_string(slot + 1) + " 槽耗材异常",
        message};
}
} // namespace

bool valid_recovery_ticket(std::string_view ticket) {
    return ticket.size() == 32 && std::all_of(ticket.begin(), ticket.end(), [](char ch) {
        return (ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f');
    });
}

bool FaultSnapshot::resolved_after(const std::string& key, int seen_sequence) const {
    return task_sequence != seen_sequence && !completed_recovery_key.empty() &&
        (completed_recovery_key == key || (key == kUnknownStopFaultKey && !stop_fault_present));
}

std::optional<FaultSnapshot> fault_notices(const nlohmann::json& state) {
    using json_util::safe_bool;
    using json_util::safe_int;
    using json_util::safe_string;
    if (!state.is_object()) return std::nullopt;
    const auto recovery = state.find("error_recovery"), task = state.find("slot_action");
    if (recovery == state.end() || !recovery->is_object() || task == state.end() ||
        !task->is_object() || !task->contains("state") || !(*task)["state"].is_string() ||
        !recovery->contains("slots") || !(*recovery)["slots"].is_array() ||
        !recovery->contains("active") || !(*recovery)["active"].is_boolean() ||
        !recovery->contains("stop_fault") ||
        (!(*recovery)["stop_fault"].is_null() && !(*recovery)["stop_fault"].is_object()))
        return std::nullopt;
    FaultSnapshot out;
    out.recovering = safe_bool(*recovery, "active");
    const auto phase = safe_string(*task, "state");
    const int task_ace = safe_int(*task, "ace", -1), task_slot = safe_int(*task, "slot", -1);
    const auto sequence = task->find("seq");
    if (sequence != task->end() && sequence->is_number_integer())
        out.task_sequence = safe_int(*task, "seq", 0);
    bool task_slot_can_check = false;
    std::map<std::string, FaultNotice> active;
    std::set<std::string> seen_slots;
    for (const auto& slot : (*recovery)["slots"]) {
        const int ace = safe_int(slot, "ace", -1), bay = safe_int(slot, "slot", -1);
        if (!valid_bay(ace, bay) || !slot.contains("needs_clear") ||
            !slot["needs_clear"].is_boolean()) return std::nullopt;
        const auto id = key(ace, bay);
        if (!seen_slots.insert(id).second) return std::nullopt;
        const auto ticket = safe_string(slot, "ticket");
        if (!out.recovering && valid_recovery_ticket(ticket)) {
            const bool can_check = safe_bool(slot, "can_check");
            if (can_check || safe_bool(slot, "can_clear"))
                out.recovery_tickets.emplace(id, ticket);
            if (ace == task_ace && bay == task_slot)
                task_slot_can_check = can_check;
        }
        if (safe_bool(slot, "needs_clear"))
            active[id] = notice(ace, bay, safe_string(slot, "detail", "料路状态需要确认"));
    }
    const auto& stop = (*recovery)["stop_fault"];
    out.stop_fault_present = stop.is_object();
    if (out.stop_fault_present) {
        const int ace = safe_int(stop, "ace_idx", -1), slot = safe_int(stop, "slot", -1);
        const auto id = key(ace, slot);
        if (valid_bay(ace, slot)) {
            if (!active.count(id))
                active[id] = notice(ace, slot, "尚未确认设备停止，请检查 ACE 连接后清除错误。");
        } else {
            active[kUnknownStopFaultKey] = {-1, -1, kUnknownStopFaultKey, "耗材停止异常",
                "无法确认故障对应的原 ACE，请检查连接和停止记录。"};
        }
    }
    if (valid_bay(task_ace, task_slot)) {
        const auto id = key(task_ace, task_slot);
        if (phase == "error" && !active.count(id))
            active[id] = notice(task_ace, task_slot, safe_string(*task, "message", "耗材操作未完成"));
        if (phase == "complete" && safe_string(*task, "kind") == "recover" &&
            out.task_sequence > 0 && !active.count(id) && task_slot_can_check)
            out.completed_recovery_key = id;
    }
    for (auto& [id, item] : active)
        out.faults.push_back(std::move(item));
    return out;
}
} // namespace helix::material_workspace
