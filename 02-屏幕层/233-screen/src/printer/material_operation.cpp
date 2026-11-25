// SPDX-License-Identifier: GPL-3.0-or-later
#include "material_operation.h"
#include "hv/json.hpp"
#include "json_utils.h"
#include <algorithm>
#include <functional>
#include <string_view>
#include <spdlog/fmt/fmt.h>

namespace helix::material_workspace {
bool MaterialOperation::same_task(const MaterialOperation& other) const {
    return ace == other.ace && slot == other.slot && seq == other.seq &&
           kind == other.kind && epoch == other.epoch && id == other.id;
}
std::string MaterialOperation::label() const {
    if (kind == "calibration") return "料管校准";
    if (kind == "dry") return fmt::format("ACE {} · 烘干", ace + 1);
    if (kind == "swap") return "喷头换料";
    if (kind == "unload_all") return "全部喷头卸载";
    const char* name = kind == "insert" ? "插料" : kind == "rfid" ? "RFID 识别" :
                       kind == "recover" ? "恢复" : kind == "eject" ? "退料" :
                       kind == "path" ? "料路动作" : "喷头送料";
    return fmt::format("ACE {} · {} 槽 · {}", ace + 1, slot + 1, name);
}
nlohmann::json MaterialOperation::stop_request() const {
    if (!stoppable) return nullptr;
    if (kind == "unload_all") return {{"kind",kind},{"seq",seq},{"epoch",epoch}};
    if (kind == "dry") return {{"kind", kind}, {"ace", ace}, {"cycle_id", id}};
    if (kind == "calibration")
        return {{"kind",kind},{"task_id",id},{"action_id",fmt::format("banner-{}-{}",std::hash<std::string>{}(id),version)},
                {"expected_version",version}};
    return {{"kind", kind}, {"ace", ace}, {"slot", slot}, {"seq", seq}, {"epoch", epoch}};
}
static bool lower_hex(const std::string& id, size_t size) {
    return id.size() == size && std::all_of(id.begin(), id.end(), [](char c) {
        return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'); });
}
static void append_workflows(const nlohmann::json& state, std::vector<MaterialOperation>& result) {
    using namespace json_util;
    const auto cal = state.find("path_calibration");
    if (cal != state.end() && cal->is_object()) {
        MaterialOperation op;
        op.kind = "calibration"; op.id = safe_string(*cal, "task_id");
        op.version = safe_int(*cal, "version", -1); op.state = safe_string(*cal, "stage");
        op.active = safe_bool(*cal, "active"); op.message = safe_string(*cal, "message");
        op.stoppable = !op.id.empty() && op.version >= 0;
        if (!op.id.empty()) result.push_back(op);
    }
    for (const auto* key : {"dryers", "feeds", "paths"}) {
        const auto rows = state.find(key);
        if (rows == state.end() || !rows->is_array()) continue;
        for (const auto& row : *rows) {
            if (!row.is_object()) continue;
            MaterialOperation op;
            op.ace = safe_int(row,"ace",-1); op.slot = safe_int(row,"slot",-1);
            op.head = safe_int(row,"head",-1);
            if (op.ace < 0 || op.ace > 3) continue;
            op.message = safe_string(row,"message");
            if (std::string_view(key) == "dryers") {
                op.kind = "dry"; op.id = safe_string(row,"cycle_id"); op.state = safe_string(row,"phase");
                op.stoppable = lower_hex(op.id,32);
                op.active = op.state == "starting" || op.state == "warming" || op.state == "drying" ||
                            op.state == "venting" || op.state == "stopping" ||
                            (op.stoppable && (op.state == "unknown" || op.state == "error"));
                if (op.state == "idle" || (!op.active && op.id.empty())) continue;
            } else if (std::string_view(key) == "feeds") {
                op.kind = "feed"; op.seq = safe_int(row,"seq",0); op.epoch = safe_string(row,"epoch");
                op.state = safe_string(row,"state"); op.active = safe_bool(row,"active");
                if (!lower_hex(op.epoch,16) || op.seq < 1) continue;
                if (op.active) op.message = "单头热送料仅可查看 · " + (op.message.empty() ? op.state : op.message);
            } else {
                op.kind = "path"; op.id = safe_string(row,"action_id"); op.state = safe_string(row,"state");
                if (op.id.empty() || op.state.empty()) continue;
                op.active = op.state != "complete" && op.state != "cancelled" && op.state != "error";
                op.message = safe_string(row,"error");
                if (op.message.empty()) op.message = safe_string(row,"kind") + " · " + op.state;
            }
            if (op.message.empty()) op.message = op.state;
            result.push_back(op);
        }
    }
    const auto unload = state.find("unload_all");
    if (unload != state.end() && unload->is_object()) {
        MaterialOperation op;
        op.kind = "unload_all";
        op.epoch = safe_string(*unload,"epoch");
        op.seq = safe_int(*unload,"seq",0);
        op.state = safe_string(*unload,"state");
        if (op.seq > 0 && lower_hex(op.epoch,16) && (op.state == "running" ||
            op.state == "cancelling" || op.state == "cancelled" || op.state == "complete" || op.state == "error")) {
            op.active = op.state == "running" || op.state == "cancelling";
            op.stoppable = op.state == "running";
            op.message = op.state == "running" ? "可在当前喷头完成后停止" :
                         op.state == "cancelling" ? "等待当前喷头完成，后续喷头将跳过" :
                         op.state == "cancelled" ? "已停止全部卸载" :
                         op.state == "complete" ? "全部卸载流程已结束" : "全部卸载流程出错";
            result.push_back(op);
        }
    }
    for (const auto* key : {"unload_all_active", "swap_in_progress"}) {
        if (!safe_bool(state,key)) continue;
        if (std::string_view(key) == "unload_all_active" &&
            std::any_of(result.begin(),result.end(),[](const auto& op) { return op.active && op.kind == "unload_all"; })) continue;
        MaterialOperation op;
        op.kind = std::string_view(key) == "unload_all_active" ? "unload_all" : "swap";
        op.active = true; op.state = "running"; op.message = "进行中，可在耗材页查看喷头状态";
        result.push_back(op);
    }
}
std::vector<MaterialOperation> material_operations(const nlohmann::json& state) {
    using namespace json_util;
    std::vector<MaterialOperation> result;
    if (!state.is_object()) return result;
    append_workflows(state, result);
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
    op.stoppable = true;
    if (op.kind == "insert") {
        op.percent = safe_int(*task, "progress_percent", -1);
        op.estimated = safe_bool(*task, "progress_estimated", false);
        if (op.percent < 0 || op.percent > 100) op.percent = -1;
        if (op.active && op.percent >= 100) op.percent = 95;
    }
    if (valid(op) && (op.kind == "insert" || op.kind == "eject" || op.kind == "recover")) result.push_back(op);
    const auto kind = safe_string(*tag, "kind");
    if (kind == "read" || kind == "insert") {
        MaterialOperation read;
        read.epoch = epoch; read.kind = "rfid"; read.stoppable = true;
        read.ace = safe_int(*tag, "ace", -1); read.slot = safe_int(*tag, "slot", -1);
        read.seq = safe_int(*tag, "seq", 0); read.active = safe_bool(*tag, "busy", false);
        read.state = read.active ? "running" : "unknown";
        read.message = "识别、测量或回退中";
        if (!read.active) {
            const auto done = tag->find("result");
            if (done != tag->end() && done->is_object() && safe_int(*done, "seq", 0) == read.seq) {
                read.state = safe_bool(*done, "ok", false) ? "complete" : "error";
                read.message = safe_string(*done, "msg", safe_string(*done, "message", "RFID 操作已结束"));
            }
        }
        if (valid(read)) result.push_back(read);
    }
    return result;
}

TaskEntry* MaterialTaskList::current() {
    return index_ < tasks_.size() ? &tasks_[index_] : nullptr;
}
TaskEntry* MaterialTaskList::find(const MaterialOperation& op) {
    for (auto& entry : tasks_) if (entry.op.same_task(op)) return &entry;
    return nullptr;
}
void MaterialTaskList::next() {
    if (!tasks_.empty()) index_ = (index_ + 1) % tasks_.size();
}
void MaterialTaskList::dismiss() {
    if (!current()) return;
    tasks_.erase(tasks_.begin() + index_);
    if (index_ >= tasks_.size()) index_ = 0;
}
void MaterialTaskList::update(const std::vector<MaterialOperation>& snapshot, uint32_t now) {
    for (auto& entry : tasks_) {
        const auto row = std::find_if(snapshot.begin(), snapshot.end(), [&](const auto& op) {
            return entry.op.same_task(op);
        });
        if (row != snapshot.end()) {
            entry.missing = false;
            if (entry.op.active && !row->active) {
                entry.terminal_at = now;
                entry.awaiting_stop = false;
                entry.failure.clear();
            }
            if (row->state == "cancelling") {
                entry.awaiting_stop = false;
                entry.failure.clear();
            }
            entry.op = *row;
        } else if (entry.op.active) {
            entry.missing = true;
            entry.awaiting_stop = false;
            if (entry.op.kind == "swap" || entry.op.kind == "unload_all") {
                entry.op.active = false; entry.op.state = "complete";
                entry.op.message = "此作业已退出活动列表"; entry.terminal_at = now;
                entry.missing = false;
            }
        }
        if (entry.awaiting_stop && now - entry.stop_at >= 5000) {
            entry.awaiting_stop = false; entry.failure = "尚未确认停止，可重试";
        }
    }
    for (const auto& op : snapshot) {
        if (!op.active || find(op)) continue;
        tasks_.push_back({op, false, false, 0, 0, {}});
    }
    for (size_t i = tasks_.size(); i > 0; --i) {
        const size_t pos = i - 1;
        const auto& entry = tasks_[pos];
        if (entry.op.active || entry.op.state != "complete" || now - entry.terminal_at < 3500) continue;
        tasks_.erase(tasks_.begin() + pos);
        if (index_ > pos) --index_;
    }
    if (index_ >= tasks_.size()) index_ = 0;
}
} // namespace helix::material_workspace
