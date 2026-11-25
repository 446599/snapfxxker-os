// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "json_fwd.h"
#include <map>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace helix::material_workspace {
inline constexpr char kUnknownStopFaultKey[] = "material_fault:stop";

struct FaultNotice {
    int ace = -1, slot = -1;
    std::string key, title, message;
};
struct FaultSnapshot {
    std::vector<FaultNotice> faults;
    std::vector<std::string> confirmed_clear_slots;
    std::map<std::string, std::string> recovery_tickets;
    int task_sequence = 0;
    std::string completed_recovery_key;
    bool stop_fault_present = false;
    bool recovering = false;

    bool resolved_after(const std::string& key, int seen_sequence) const;
};
bool valid_recovery_ticket(std::string_view ticket);
// Missing or malformed evidence cannot resolve a previously reported condition.
std::optional<FaultSnapshot> fault_notices(const nlohmann::json& state);
} // namespace helix::material_workspace
