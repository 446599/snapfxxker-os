// SPDX-License-Identifier: GPL-3.0-or-later
#include "material_operation.h"
#include "hv/json.hpp"
#include "json_utils.h"
#include <algorithm>

namespace helix::material_workspace {
bool MaterialOperation::same_task(const MaterialOperation& other) const {
    return ace == other.ace && slot == other.slot && seq == other.seq &&
           kind == other.kind && epoch == other.epoch;
}
nlohmann::json MaterialOperation::stop_request() const {
    return {{"kind", kind}, {"ace", ace}, {"slot", slot}, {"seq", seq}, {"epoch", epoch}};
}
std::vector<MaterialOperation> material_operations(const nlohmann::json& state) {
    using namespace json_util;
    std::vector<MaterialOperation> result;
    if (!state.is_object()) return result;
    const auto task = state.find("slot_action"), tag = state.find("tag_op");
    if (task == state.end() || !task->is_object() || tag == state.end() || !tag->is_object())
        return result;
    const auto epoch = safe_string(*task, "epoch");
    if (epoch.size() != 16 || !std::all_of(epoch.begin(), epoch.end(), [](char c) {
            return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'); })) return result;
    auto valid = [](const MaterialOperation& op) {
        return op.ace >= 0 && op.ace < 4 && op.slot >= 0 && op.slot < 4 && op.seq > 0;
    };
    MaterialOperation op;
    op.epoch = epoch;
    op.ace = safe_int(*task, "ace", -1); op.slot = safe_int(*task, "slot", -1);
    op.seq = safe_int(*task, "seq", 0); op.kind = safe_string(*task, "kind");
    op.state = safe_string(*task, "state"); op.message = safe_string(*task, "message");
    op.active = op.state == "running" || op.state == "preparing";
    if (op.kind == "insert") {
        op.percent = safe_int(*task, "progress_percent", -1);
        op.estimated = safe_bool(*task, "progress_estimated", false);
        if (op.percent < 0 || op.percent > 100) op.percent = -1;
        if (op.active && op.percent >= 100) op.percent = 95;
    }
    if (valid(op) && (op.kind == "insert" || op.kind == "eject")) result.push_back(op);
    const auto kind = safe_string(*tag, "kind");
    if (kind == "read" || kind == "insert") {
        MaterialOperation read;
        read.epoch = epoch; read.kind = "rfid";
        read.ace = safe_int(*tag, "ace", -1); read.slot = safe_int(*tag, "slot", -1);
        read.seq = safe_int(*tag, "seq", 0); read.active = safe_bool(*tag, "busy", false);
        read.state = read.active ? "running" : "unknown";
        read.message = "识别、测量或回退中";
        if (!read.active) {
            const auto done = tag->find("result");
            if (done != tag->end() && done->is_object() && safe_int(*done, "seq", 0) == read.seq) {
                read.state = safe_bool(*done, "ok", false) ? "complete" : "error";
                read.message = safe_string(*done, "message", "RFID 操作已结束");
            }
        }
        if (valid(read)) result.push_back(read);
    }
    return result;
}
} // namespace helix::material_workspace
