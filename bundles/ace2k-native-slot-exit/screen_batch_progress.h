// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include "hv/json.hpp"

#include <string>
#include <string_view>

namespace helix::u1_filament {

inline bool material_batch_slot_finished(std::string_view state) {
    return state == "complete" || state == "cancelled" || state == "rejected" ||
           state == "failed" || state == "recovery";
}

inline std::string recover_material_batch(const nlohmann::json& tasks) {
    if (!tasks.is_array()) return {};
    // Tasks are published in submission order. Ignore old terminal records
    // and recover the newest batch with work still in progress.
    for (auto task = tasks.rbegin(); task != tasks.rend(); ++task) {
        if (!task->is_object()) continue;
        const auto state = task->find("state");
        const auto batch = task->find("batch");
        if (state == task->end() || !state->is_string() || batch == task->end() ||
            !batch->is_string())
            continue;
        const auto& name = state->get_ref<const std::string&>();
        const auto& id = batch->get_ref<const std::string&>();
        if (!name.empty() && !id.empty() && !material_batch_slot_finished(name)) return id;
    }
    return {};
}

} // namespace helix::u1_filament
