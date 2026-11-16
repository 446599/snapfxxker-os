// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "json_fwd.h"
#include <string>
#include <vector>

namespace helix::material_workspace {
struct MaterialOperation {
    int ace = -1, slot = -1, seq = 0, percent = -1;
    bool active = false, estimated = false;
    std::string kind, epoch, state, message;
    bool same_task(const MaterialOperation& other) const;
    nlohmann::json stop_request() const;
};
// Invalid or legacy snapshots never supply a stop target.
std::vector<MaterialOperation> material_operations(const nlohmann::json& state);
} // namespace helix::material_workspace
