// SPDX-License-Identifier: GPL-3.0-or-later
#include "ui_path_calibration.h"
#include "ui_nav.h"
#include "ui_toast_manager.h"
#include "core/data_root_resolver.h"
#include "core/static_panel_registry.h"
#include "json_utils.h"
#include "material_workspace.h"
#include "printer_state.h"
#include "app_globals.h"
#include <spdlog/fmt/fmt.h>
#include <algorithm>
#include "random_bytes.h"
#include <map>
#include <cstdlib>

namespace helix::ui {
using json_util::safe_bool;
using json_util::safe_int;
using json_util::safe_string;
using json_util::safe_double;

PathCalibration::~PathCalibration() {
    deinit_subjects();
}

void PathCalibration::deinit_subjects() {
    ++generation_;
    poll_.reset();
    reading_ = sending_ = fresh_ = false;
    state_ = nlohmann::json::object();
    buttons_.reclaim();
    head_choices_.reclaim();
    deinit_subjects_base(subjects_);
}

void PathCalibration::init_subjects() {
    init_subjects_guarded([this] {
        UI_MANAGED_SUBJECT_INT(active_, 0, "path_calibration_active", subjects_);
        UI_MANAGED_SUBJECT_INT(available_, 0, "path_calibration_available", subjects_);
        UI_MANAGED_SUBJECT_INT(busy_, 0, "path_calibration_busy", subjects_);
        UI_MANAGED_SUBJECT_INT(stage_, 0, "path_calibration_stage", subjects_);
        UI_MANAGED_SUBJECT_INT(waiting_, 0, "path_calibration_waiting", subjects_);
        UI_MANAGED_SUBJECT_INT(selected_, 0, "path_calibration_selected", subjects_);
        UI_MANAGED_SUBJECT_INT(step_, 10, "path_calibration_step", subjects_);
        UI_MANAGED_SUBJECT_INT(forward_ready_, 0, "path_calibration_forward_ready", subjects_);
        UI_MANAGED_SUBJECT_INT(back_ready_, 0, "path_calibration_back_ready", subjects_);
        UI_MANAGED_SUBJECT_INT(confirm_ready_, 0, "path_calibration_confirm_ready", subjects_);
        UI_MANAGED_SUBJECT_INT(head_choices_visible_, 0, "path_calibration_head_choices_visible", subjects_);
        UI_MANAGED_SUBJECT_INT(selected_head_, -1, "path_calibration_selected_head", subjects_);
        UI_MANAGED_SUBJECT_INT(source_confirm_ready_, 0, "path_calibration_source_confirm_ready", subjects_);
        UI_MANAGED_SUBJECT_INT(can_save_, 0, "path_calibration_can_save", subjects_);
        UI_MANAGED_SUBJECT_INT(can_start_, 0, "path_calibration_can_start", subjects_);
        UI_MANAGED_SUBJECT_INT(can_continue_, 0, "path_calibration_can_continue", subjects_);
        UI_MANAGED_SUBJECT_STRING(title_, title_buf_, "ACE 1 · 料管校准",
                                  "path_calibration_title", subjects_);
        UI_MANAGED_SUBJECT_STRING(message_, message_buf_, "正在读取校准能力",
                                  "path_calibration_message", subjects_);
        UI_MANAGED_SUBJECT_STRING(position_, position_buf_, "位置待确认",
                                  "path_calibration_position", subjects_);
        UI_MANAGED_SUBJECT_STRING(banner_, banner_buf_, "料管校准",
                                  "path_calibration_banner_text", subjects_);
        UI_MANAGED_SUBJECT_STRING(route_detail_, route_detail_buf_, "等待当前路线资料",
                                  "path_calibration_route_detail", subjects_);
        buttons_.ensure_size(4);
        head_choices_.ensure_size(4);
        for (int index = 0; index < 4; ++index) {
            buttons_.set_string(index, fmt::format("{} · 待核", index + 1));
        }
        unsigned char random[16]{};
        fill_random_bytes(random, sizeof(random));
        client_id_ = "ui";
        for (unsigned char byte : random) client_id_ += fmt::format("{:02x}", byte);
        register_callbacks();
        lv_xml_register_component_from_file(
            asset_component_uri("ui_xml/path_calibration_banner.xml").c_str());
        if (material_workspace::supports_local_upgrade()) {
            poll_.reset(lv_timer_create([](lv_timer_t*) {
                get_path_calibration().refresh();
            }, 750, nullptr));
        }
    });
}

void PathCalibration::register_callbacks() {
    lv_xml_register_event_cb(nullptr, "path_calibration_return", [](lv_event_t*) {
        get_path_calibration().open();
    });
    lv_xml_register_event_cb(nullptr, "path_calibration_back", [](lv_event_t*) {
        nav::go_back();
    });
    lv_xml_register_event_cb(nullptr, "path_calibration_action", [](lv_event_t* event) {
        const auto* operation = static_cast<const char*>(lv_event_get_user_data(event));
        if (operation) get_path_calibration().act(operation);
    });
    lv_xml_register_event_cb(nullptr, "path_calibration_slot", [](lv_event_t* event) {
        const auto* value = static_cast<const char*>(lv_event_get_user_data(event));
        auto& self = get_path_calibration();
        if (!value || self.sending_ || lv_subject_get_int(&self.busy_)) return;
        self.slot_ = std::clamp(std::atoi(value), 0, 3);
        if (lv_subject_get_int(&self.active_)) self.act("select");
        else self.publish(self.state_);
    });
    lv_xml_register_event_cb(nullptr, "path_calibration_head_select", [](lv_event_t* event) {
        const auto* value = static_cast<const char*>(lv_event_get_user_data(event));
        auto& self = get_path_calibration();
        if (!value || self.sending_ || lv_subject_get_int(&self.active_)) return;
        const int head = std::atoi(value);
        if (head < 0 || head >= 4 || !lv_subject_get_int(self.head_choices_.at(head))) return;
        self.head_hint_ = head;
        self.publish(self.state_);
    });
    lv_xml_register_event_cb(nullptr, "path_calibration_step_select", [](lv_event_t* event) {
        const auto* value = static_cast<const char*>(lv_event_get_user_data(event));
        if (!value) return;
        const int step = std::atoi(value);
        if (step == 10 || step == 50 || step == 100)
            lv_subject_set_int(&get_path_calibration().step_, step);
    });
}

lv_obj_t* PathCalibration::create(lv_obj_t* parent) {
    lv_xml_register_component_from_file(asset_component_uri("ui_xml/path_calibration.xml").c_str());
    return OverlayBase::create(parent);
}

void PathCalibration::open(int ace) {
    if (!subjects_initialized_) init_subjects();
    if (ace >= 0 && !lv_subject_get_int(&active_)) {
        if (ace != ace_) head_hint_ = -1;
        ace_ = ace;
    }
    lv_subject_copy_string(&title_, fmt::format("ACE {} · 料管校准", ace_ + 1).c_str());
    if (!is_visible()) show(lv_screen_active());
    refresh();
}

std::string PathCalibration::next_id() {
    return client_id_ + "_" + std::to_string(++sequence_);
}

void PathCalibration::refresh() {
    if (reading_ || !material_workspace::supports_local_upgrade()) return;
    reading_ = true;
    auto token = object_lifetime_.token();
    const auto generation = generation_;
    material_workspace::request("/state", {}, [this, token, generation](nlohmann::json data, std::string error) {
        if (token.expired() || generation != generation_) return;
        reading_ = false;
        fresh_ = error.empty() && data.contains("path_calibration") &&
                 data["path_calibration"].is_object();
        if (!fresh_) {
            lv_subject_set_int(&can_start_, 0);
            lv_subject_set_int(&can_save_, 0);
            lv_subject_set_int(&forward_ready_, 0);
            lv_subject_set_int(&back_ready_, 0);
            lv_subject_set_int(&confirm_ready_, 0);
            lv_subject_set_int(&source_confirm_ready_, 0);
            lv_subject_set_int(&can_continue_, 0);
            lv_subject_copy_string(&message_, error.empty()
                ? "当前主机尚未提供料管校准能力" : error.c_str());
            if (lv_subject_get_int(&active_))
                lv_subject_copy_string(&banner_, "校准连接待恢复 · 返回查看");
            return;
        }
        publish(data["path_calibration"]);
    });
}

void PathCalibration::publish(const nlohmann::json& snapshot) {
    state_ = snapshot;
    const bool active = safe_bool(state_, "active");
    if (poll_) lv_timer_set_period(poll_.get(), active ? 250 : 1500);
    bool available = safe_bool(state_, "available");
    const bool busy = safe_bool(state_, "busy") || sending_;
    if (active) {
        ace_ = safe_int(state_, "ace", ace_);
        slot_ = std::clamp(safe_int(state_, "slot", slot_), 0, 3);
    }
    if (state_.contains("capable_aces") && state_["capable_aces"].is_array()) {
        const auto& capable = state_["capable_aces"];
        available = available && std::find(capable.begin(), capable.end(), ace_) != capable.end();
    }
    const bool head_mode = safe_string(state_, "mode") == "head";
    nlohmann::json heads = nlohmann::json::array();
    if (head_mode && state_.contains("heads_by_ace") && state_["heads_by_ace"].is_object()) {
        const auto found = state_["heads_by_ace"].find(std::to_string(ace_));
        if (found != state_["heads_by_ace"].end() && found->is_array()) heads = *found;
    }
    if (heads.size() == 1) head_hint_ = json_util::as_int(heads.front(), -1);
    if (std::find(heads.begin(), heads.end(), head_hint_) == heads.end()) head_hint_ = -1;
    const bool choose_head = head_mode && heads.size() > 1 && !active;
    lv_subject_set_int(&head_choices_visible_, choose_head);
    lv_subject_set_int(&selected_head_, head_hint_);
    for (int head = 0; head < 4; ++head)
        head_choices_.set_int(head, std::find(heads.begin(), heads.end(), head) != heads.end());
    const std::string phase = safe_string(state_, "stage", "idle");
    const bool marking = phase == "marking";
    const std::string wait_reason = safe_string(state_, "wait_reason");
    const bool waiting = wait_reason == "insert";
    const bool trusted = safe_bool(state_, "position_trusted");
    lv_subject_set_int(&active_, active);
    lv_subject_set_int(&available_, available);
    lv_subject_set_int(&busy_, busy);
    lv_subject_set_int(&selected_, slot_);
    lv_subject_set_int(&stage_, marking ? 2 : phase == "complete" ? 3 : 1);
    lv_subject_set_int(&waiting_, waiting);
    lv_subject_set_int(&can_start_, available && !active && !busy && (!choose_head || head_hint_ >= 0));
    lv_subject_set_int(&can_continue_, available && active && waiting && !busy);
    const bool can_jog = available && active && marking && trusted && !sending_;
    lv_subject_set_int(&forward_ready_, can_jog && safe_bool(state_, "can_jog_forward", !busy));
    lv_subject_set_int(&back_ready_, can_jog && safe_bool(state_, "can_jog_back", !busy));
    lv_subject_set_int(&confirm_ready_, available && active && wait_reason == "confirm_route" && !busy);
    const bool margin_valid = state_.contains("minimum_margin_mm") &&
        state_["minimum_margin_mm"].is_number() && safe_double(state_, "minimum_margin_mm") > 0;
    lv_subject_set_int(&can_save_, available && active && marking && trusted && !busy &&
        margin_valid && safe_bool(state_, "can_save", true));
    lv_subject_set_int(&source_confirm_ready_, available && active && wait_reason == "confirm_source" && !busy);
    lv_subject_copy_string(&title_, fmt::format("ACE {} · 料管校准", ace_ + 1).c_str());
    std::string message = available ? safe_string(state_, "message", "等待操作")
        : safe_string(state_, "capability_reason", "等待受控入料能力验证");
    if (!available && message.empty()) message = "所选 ACE 等待受控运动能力";
    if (choose_head && head_hint_ < 0) message = "这台 ACE 绑定多个喷头，请明确选择本次料管目标";
    lv_subject_copy_string(&message_, message.c_str());
    const std::string position = trusted
        ? fmt::format("{}槽 → 喷头 {} · {:.1f} mm", slot_ + 1,
                      safe_int(state_, "head", slot_) + 1, safe_double(state_, "position_mm"))
        : fmt::format("{}槽 · 当前位置待确认", slot_ + 1);
    const std::string position_with_margin = margin_valid
        ? position + fmt::format(" · Y 前间隙至少 {:.0f} mm", safe_double(state_, "minimum_margin_mm"))
        : position + " · 停车间隙待核";
    lv_subject_copy_string(&position_, position_with_margin.c_str());
    const std::string summary = fmt::format("ACE {} · {}槽 · {}", ace_ + 1, slot_ + 1, message);
    lv_subject_copy_string(&banner_, summary.c_str());
    nlohmann::json lanes = nlohmann::json::array();
    if (safe_int(state_, "ace", -1) == ace_ && state_.contains("lanes") && state_["lanes"].is_array())
        lanes = state_["lanes"];
    if (!active && state_.contains("lanes_by_ace") && state_["lanes_by_ace"].is_object()) {
        const auto found = state_["lanes_by_ace"].find(std::to_string(ace_));
        if (found != state_["lanes_by_ace"].end() && found->is_array()) lanes = *found;
    }
    static const std::map<std::string, std::string> names = {
        {"empty", "空槽"}, {"pending_insert", "等待重插"}, {"measuring", "测量中"},
        {"measured", "已测长"}, {"marking", "标记停车点"}, {"complete", "已完成"},
        {"position_unknown", "位置待核"}, {"skipped", "待补测"}, {"unmapped", "未接入 ACE"}};
    for (int index = 0; index < 4; ++index) {
        nlohmann::json lane = nlohmann::json::object();
        for (const auto& candidate : lanes)
            if (safe_int(candidate, "slot", -1) == index) lane = candidate;
        if (!active && head_mode && head_hint_ >= 0 &&
            safe_int(lane, "head", head_hint_) != head_hint_)
            lane = nlohmann::json::object();
        const int route_head = !active && head_mode ? head_hint_ : safe_int(lane, "head", index);
        const std::string status = safe_string(lane, "status");
        const auto found = names.find(status);
        std::string label = route_head >= 0
            ? fmt::format("{}槽 → 喷头 {}  {}", index + 1, route_head + 1,
                          found == names.end() ? "待测量" : found->second)
            : fmt::format("{}槽 · 请先选择目标喷头", index + 1);
        if (lane.contains("length_mm") && lane["length_mm"].is_number())
            label += fmt::format(" · {:.0f} mm", safe_double(lane, "length_mm"));
        if (lane.contains("park_mm") && lane["park_mm"].is_number())
            label += fmt::format(" · 停车 {:.0f} mm", safe_double(lane, "park_mm"));
        if (index == slot_) lv_subject_copy_string(&route_detail_, label.c_str());
        static const std::map<std::string, std::string> short_names = {
            {"empty", "空"}, {"pending_insert", "待插"}, {"measuring", "测量"},
            {"measured", "已测"}, {"marking", "定位"}, {"complete", "完成"},
            {"position_unknown", "待核"}, {"skipped", "待补"}, {"unmapped", "未接"}};
        const auto short_name = short_names.find(status);
        buttons_.set_string(index, fmt::format("{} · {}", index + 1,
            short_name == short_names.end() ? "待测" : short_name->second));
    }
}

void PathCalibration::act(const std::string& operation) {
    if (!subjects_initialized_) init_subjects();
    const bool prepare_single = operation == "prepare_single";
    const bool prepare = operation == "prepare" || prepare_single;
    const bool control = operation == "stop" || operation == "cancel";
    if ((sending_ || !fresh_) && !control) return;
    const bool forward = operation == "jog_forward";
    const bool back = operation == "jog_back";
    const bool continuing_jog = (forward && lv_subject_get_int(&forward_ready_)) ||
                                (back && lv_subject_get_int(&back_ready_));
    if ((!lv_subject_get_int(&available_) && !control) ||
        (lv_subject_get_int(&busy_) && !control && !continuing_jog) ||
        (prepare && !lv_subject_get_int(&can_start_)) ||
        ((forward || back) && !continuing_jog) ||
        (operation == "confirm_route" && !lv_subject_get_int(&confirm_ready_)) ||
        (operation == "confirm_source" && !lv_subject_get_int(&source_confirm_ready_)) ||
        (operation == "save" && !lv_subject_get_int(&can_save_))) return;
    const std::string task = prepare ? next_id() : safe_string(state_, "task_id");
    if (task.empty()) return;
    nlohmann::json arguments = {{"OP", prepare ? "prepare" : operation}, {"TASK_ID", task},
        {"ACTION_ID", next_id()}, {"EXPECTED_VERSION", safe_int(state_, "version")},
        {"ACE", ace_}, {"SLOT", prepare && !prepare_single ? -1 : slot_},
        {"HEAD", prepare ? (safe_string(state_, "mode") == "head" ? head_hint_ : -1)
                         : safe_int(state_, "head", slot_)},
        {"STEP", lv_subject_get_int(&step_)}};
    sending_ = true;
    lv_subject_set_int(&busy_, 1);
    lv_subject_set_int(&forward_ready_, 0);
    lv_subject_set_int(&back_ready_, 0);
    lv_subject_set_int(&can_save_, 0);
    auto token = object_lifetime_.token();
    const auto generation = generation_;
    auto reply = [this, token, generation](nlohmann::json, std::string error) {
        if (token.expired() || generation != generation_) return;
        sending_ = false;
        if (!error.empty()) {
            lv_subject_copy_string(&message_, error.c_str());
            ToastManager::instance().show(ToastSeverity::ERROR, error.c_str(), 3000);
        }
        refresh();
    };
    if (control) {
        material_workspace::request("/path-calibration/" + operation,
            {{"task_id", task}, {"action_id", arguments["ACTION_ID"]},
             {"expected_version", arguments["EXPECTED_VERSION"]}}, std::move(reply), true);
    } else {
        material_workspace::command("path_calibration", arguments, std::move(reply));
    }
}

PathCalibration& get_path_calibration() {
    return helix::lazy_global<PathCalibration>("PathCalibration");
}
} // namespace helix::ui
