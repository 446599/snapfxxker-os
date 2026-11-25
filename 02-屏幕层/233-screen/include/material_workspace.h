// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <functional>
#include <optional>
#include <string>
#include <string_view>

#include "hv/json.hpp"
#include "ams_types.h"
#include "material_operation.h"

namespace helix { class AmsBackend; }
namespace helix::material_workspace {
struct SlotTaskState {
    bool valid = false, busy = true, held = true, recovery = false;
    std::optional<MaterialOperation> stop_target;
    bool check_ready = false, clear_ready = false, stop_fault = false, needs_clear = false;
    bool has_clearable_record = false, terminal_result = false;
    bool verified_recovery = false, auto_ready = false;
    bool source_review = false, source_review_ready = false, recovered_stop = false;
    int source_head = -1;
    std::string recovery_ticket, recovery_prompt;
    int seq = 0;
    std::string kind, message = "正在读取料槽状态";
};
// Pure projection of the companion's state; missing evidence disables motion.
SlotTaskState slot_task_state(const nlohmann::json& state, int ace, int slot);
std::string_view next_slot_recovery_action(const SlotTaskState& task);
bool can_unload_ace_nozzle(const SlotTaskState& task, int selected_slot,
                           std::optional<int> physical_source, std::optional<bool> head_present);
bool supports_local_upgrade();
// A direct four-head printer has one real parallel tool unit, with no upstream bays.
bool uses_external_spools(const AmsSystemInfo& info);
bool supports_head_inventory(const AmsSystemInfo& info);
const SlotInfo* head_slot(const AmsSystemInfo& info, int head);
std::optional<bool> head_physical_presence(const AmsBackend* backend, int head);
std::optional<int> head_physical_source(const AmsBackend* backend, int head);
std::optional<int> ace_index(const AmsSystemInfo& info, int unit);
// Empty only when a 5 mm move would affect the already-active, warm, idle head.
std::string manual_move_refusal(int head);
using Reply = std::function<void(nlohmann::json, std::string)>;
// Callbacks run on the UI thread. The companion is local to the machine screen.
void request(const std::string& path, const nlohmann::json& body, Reply reply, bool post = false);
std::optional<std::string> build_command(const std::string& action, const nlohmann::json& args);
void command(const std::string& action, const nlohmann::json& args, Reply reply);
void manual_move(int head, int millimeters, Reply reply);
} // namespace helix::material_workspace
