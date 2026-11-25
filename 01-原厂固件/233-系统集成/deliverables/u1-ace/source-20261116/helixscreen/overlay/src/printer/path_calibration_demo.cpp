// SPDX-License-Identifier: GPL-3.0-or-later
#include "path_calibration_demo.h"
#include "json_utils.h"
#include <algorithm>
#include <map>
#include <cstdlib>

namespace helix::material_workspace {
namespace {
std::map<std::string, std::string> completed_actions;
nlohmann::json state = {{"mode", "multi"}, {"heads_by_ace", nlohmann::json::object()},
    {"available", true}, {"active", false}, {"busy", false},
    {"task_id", ""}, {"version", 0}, {"ace", 0}, {"slot", 0}, {"head", 0},
    {"stage", "idle"}, {"message", "模拟设备 · 可预览完整校准流程"},
    {"position_trusted", false}, {"position_mm", 0}, {"minimum_margin_mm", 20},
    {"lanes", nlohmann::json::array()}};

void next_measurement() {
    for (const auto& lane : state["lanes"]) {
        if (lane["included"] == true && lane["status"] == "pending_insert") {
            state["slot"] = lane["slot"];
            state["head"] = lane["head"];
            state["wait_reason"] = "insert";
            state["message"] = "模拟设备 · 将当前槽退出后重新插入";
            return;
        }
    }
    state["stage"] = "marking";
    state["wait_reason"] = "none";
    state["position_trusted"] = false;
    state["message"] = "测长完成 · 选择一条已测路线标记停车点";
}
} // namespace

nlohmann::json path_calibration_demo_state() {
    auto snapshot = state;
    const char* mode = std::getenv("HELIX_MOCK_MULTIACE_MODE");
    if (mode && std::string(mode) == "head") {
        snapshot["mode"] = "head";
        for (int ace = 0; ace < 4; ++ace)
            snapshot["heads_by_ace"][std::to_string(ace)] = {ace};
    }
    return snapshot;
}

std::string path_calibration_demo_action(const nlohmann::json& arguments) {
    using namespace json_util;
    const std::string operation = safe_string(arguments, "OP");
    const std::string task = safe_string(arguments, "TASK_ID");
    const std::string action = safe_string(arguments, "ACTION_ID");
    const auto previous = completed_actions.find(action);
    if (previous != completed_actions.end())
        return previous->second == arguments.dump() ? "" : "操作标识参数冲突";
    if (action.empty()) return "缺少操作标识";
    if (task.empty()) return "缺少任务标识";
    if (operation != "prepare" && task != safe_string(state, "task_id"))
        return "校准任务已变更";
    if (operation != "stop" && operation != "cancel" &&
        safe_int(arguments, "EXPECTED_VERSION", -1) != safe_int(state, "version"))
        return "状态已更新，请重新读取";
    if (operation == "prepare") {
        const int selected_ace = safe_int(arguments, "ACE");
        const bool same_ace = safe_int(state, "ace") == selected_ace;
        const auto previous_lanes = same_ace ? state["lanes"] : nlohmann::json::array();
        state["task_id"] = task;
        state["ace"] = selected_ace;
        state["active"] = true;
        state["stage"] = "measuring";
        state["position_trusted"] = false;
        const int selected_slot = safe_int(arguments, "SLOT", -1);
        state["lanes"] = nlohmann::json::array();
        for (int index = 0; index < 4; ++index) {
            const bool included = selected_slot < 0 || selected_slot == index;
            const bool head_mode = safe_string(path_calibration_demo_state(), "mode") == "head";
            const int requested_head = safe_int(arguments, "HEAD", -1);
            const int head = head_mode ? (requested_head >= 0 ? requested_head : selected_ace) : index;
            nlohmann::json lane = {{"slot", index}, {"head", head},
                {"status", included ? "pending_insert" : "skipped"}, {"inlet", index != 2}};
            if (!included && index < static_cast<int>(previous_lanes.size())) lane = previous_lanes[index];
            lane["included"] = included;
            state["lanes"].push_back(lane);
        }
        next_measurement();
    } else if (operation == "insert" || operation == "skip") {
        const int slot = safe_int(state, "slot");
        auto& lane = state["lanes"][slot];
        lane["status"] = operation == "insert" ? "measured" : "skipped";
        if (operation == "insert") lane["length_mm"] = 850 + slot * 20;
        next_measurement();
    } else if (operation == "select") {
        const int slot = safe_int(arguments, "SLOT", -1);
        if (slot < 0 || slot >= static_cast<int>(state["lanes"].size())) return "槽位无效";
        const auto& lane = state["lanes"][slot];
        if (lane["included"] != true) return "此路线未列入本次校准";
        state["slot"] = slot;
        state["head"] = lane["head"];
        state["position_trusted"] = lane.contains("length_mm");
        state["position_mm"] = safe_double(lane, "length_mm");
        state["message"] = lane.contains("length_mm")
            ? "模拟设备 · 后退至 Y 前的独立支路，留出停车间隙" : "空槽待补测";
    } else if (operation == "jog_forward" || operation == "jog_back") {
        if (!safe_bool(state, "position_trusted")) return "位置待确认";
        const int slot = safe_int(state, "slot");
        const double delta = safe_int(arguments, "STEP", 10) * (operation == "jog_forward" ? 1 : -1);
        state["position_mm"] = std::clamp(safe_double(state, "position_mm") + delta, 0.0,
            safe_double(state["lanes"][slot], "length_mm"));
    } else if (operation == "save") {
        if (!safe_bool(state, "position_trusted")) return "位置待确认";
        const int slot = safe_int(state, "slot");
        auto& lane = state["lanes"][slot];
        lane["park_mm"] = state["position_mm"];
        lane["status"] = "complete";
        bool complete = true;
        for (const auto& item : state["lanes"])
            complete = complete && (item["included"] != true ||
                item["status"] == "complete" || item["status"] == "skipped");
        state["message"] = complete ? "模拟校准完成 · 空槽可单路补测" : "停车点已保存 · 请选择下一条路线";
        if (complete) {
            state["stage"] = "complete";
            state["active"] = false;
        }
    } else if (operation == "cancel") {
        state["active"] = false;
        state["stage"] = "idle";
        state["message"] = "模拟收尾完成 · 已完成的路线保留";
    } else if (operation == "stop") {
        state["message"] = "模拟运动已停止 · 可继续或取消";
    } else if (operation == "resume") {
        state["message"] = "模拟任务已继续";
    } else {
        return "模拟设备不支持此操作";
    }
    state["version"] = safe_int(state, "version") + 1;
    state["last_action_id"] = safe_string(arguments, "ACTION_ID");
    state["last_action_state"] = "complete";
    completed_actions[action] = arguments.dump();
    return {};
}
} // namespace helix::material_workspace
