// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "json_fwd.h"
#include <string>
#include <cstdint>
#include <vector>

namespace helix::material_workspace {
struct MaterialOperation {
    int ace = -1, slot = -1, seq = 0, percent = -1;
    bool active = false, estimated = false, stoppable = false;
    int version = -1, head = -1;
    std::string kind, epoch, state, message, id;
    std::string label() const;
    bool same_task(const MaterialOperation& other) const;
    nlohmann::json stop_request() const;
};
struct TaskEntry {
    MaterialOperation op;
    bool missing = false, awaiting_stop = false;
    uint32_t stop_at = 0, terminal_at = 0;
    std::string failure;
};
class MaterialTaskList {
  public:
    void update(const std::vector<MaterialOperation>& snapshot, uint32_t now);
    TaskEntry* current();
    TaskEntry* find(const MaterialOperation& op);
    size_t size() const { return tasks_.size(); }
    size_t position() const { return index_ + 1; }
    void next();
    void dismiss();
  private:
    std::vector<TaskEntry> tasks_;
    size_t index_ = 0;
};
// Invalid or legacy snapshots never supply a stop target.
std::vector<MaterialOperation> material_operations(const nlohmann::json& state);
} // namespace helix::material_workspace
