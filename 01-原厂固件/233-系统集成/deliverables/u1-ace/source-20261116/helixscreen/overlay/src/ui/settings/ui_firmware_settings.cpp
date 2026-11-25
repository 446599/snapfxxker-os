// SPDX-License-Identifier: GPL-3.0-or-later
#include "ui_firmware_settings.h"

#include "ui_callback_helpers.h"
#include "ui_nav.h"
#include "app_globals.h"
#include "core/data_root_resolver.h"
#include "core/static_panel_registry.h"
#include "core/static_subject_registry.h"
#include "json_utils.h"
#include "material_workspace.h"
#include "printer_state.h"
#include "runtime_config.h"

#include <spdlog/fmt/fmt.h>
#include <algorithm>
#include <cstdlib>

namespace helix::ui {
using json_util::safe_bool;
using json_util::safe_string;

FirmwareSettings::~FirmwareSettings() {
    object_lifetime_.invalidate();
    deinit_subjects();
}

void FirmwareSettings::deinit_subjects() {
    group_labels_.reclaim();
    setting_labels_.reclaim();
    setting_values_.reclaim();
    option_labels_.reclaim();
    input_labels_.reclaim();
    input_values_.reclaim();
    deinit_subjects_base(subjects_);
}

void FirmwareSettings::init_subjects() {
    init_subjects_guarded([this] {
        UI_MANAGED_SUBJECT_INT(page_, 0, "firmware_page", subjects_);
        UI_MANAGED_SUBJECT_INT(busy_, 0, "firmware_busy", subjects_);
        UI_MANAGED_SUBJECT_INT(ready_, 0, "firmware_ready", subjects_);
        UI_MANAGED_SUBJECT_INT(group_count_, 0, "firmware_group_count", subjects_);
        UI_MANAGED_SUBJECT_INT(setting_count_, 0, "firmware_setting_count", subjects_);
        UI_MANAGED_SUBJECT_INT(option_count_, 0, "firmware_option_count", subjects_);
        UI_MANAGED_SUBJECT_INT(input_count_, 0, "firmware_input_count", subjects_);
        UI_MANAGED_SUBJECT_INT(selected_option_, -1, "firmware_selected_option", subjects_);
        UI_MANAGED_SUBJECT_INT(dirty_, 0, "firmware_dirty", subjects_);
        UI_MANAGED_SUBJECT_INT(has_output_, 0, "firmware_has_output", subjects_);
        UI_MANAGED_SUBJECT_STRING(title_, title_buf_, "高级设置", "firmware_title", subjects_);
        UI_MANAGED_SUBJECT_STRING(status_, status_buf_, "正在读取设置", "firmware_status", subjects_);
        UI_MANAGED_SUBJECT_STRING(description_, description_buf_, "", "firmware_description", subjects_);
        UI_MANAGED_SUBJECT_STRING(current_, current_buf_, "", "firmware_current", subjects_);
        UI_MANAGED_SUBJECT_STRING(review_, review_buf_, "", "firmware_review", subjects_);
        UI_MANAGED_SUBJECT_STRING(output_text_, output_buf_, "", "firmware_output", subjects_);
        StaticSubjectRegistry::instance().register_deinit("FirmwareSettings", [] {
            if (auto* view = helix::lazy_global_if_exists<FirmwareSettings>())
                view->deinit_subjects();
        });
    });
}

lv_obj_t* FirmwareSettings::create(lv_obj_t* parent) {
    lv_xml_register_component_from_file(asset_component_uri("ui_xml/firmware_settings.xml").c_str());
    return OverlayBase::create(parent);
}

void FirmwareSettings::register_callbacks() {
    register_xml_callbacks({
        {"firmware_back", [](lv_event_t* e) {
             if (auto* self = from_event<FirmwareSettings>(e)) self->back();
         }},
        {"firmware_refresh", [](lv_event_t* e) {
             if (auto* self = from_event<FirmwareSettings>(e)) self->refresh();
         }},
        {"firmware_group_select", [](lv_event_t* e) {
             if (auto* self = from_event<FirmwareSettings>(e))
                 if (auto* index = static_cast<const char*>(lv_event_get_user_data(e)))
                     self->choose_group(std::atoi(index));
         }},
        {"firmware_setting_select", [](lv_event_t* e) {
             if (auto* self = from_event<FirmwareSettings>(e))
                 if (auto* index = static_cast<const char*>(lv_event_get_user_data(e)))
                     self->choose_setting(std::atoi(index));
         }},
        {"firmware_option_select", [](lv_event_t* e) {
             if (auto* self = from_event<FirmwareSettings>(e))
                 if (auto* index = static_cast<const char*>(lv_event_get_user_data(e)))
                     self->choose_option(std::atoi(index));
         }},
        {"firmware_review_change", [](lv_event_t* e) {
             if (auto* self = from_event<FirmwareSettings>(e)) self->review();
         }},
        {"firmware_apply_change", [](lv_event_t* e) {
             if (auto* self = from_event<FirmwareSettings>(e)) self->apply();
         }},
        {"firmware_show_output", [](lv_event_t* e) {
             if (auto* self = from_event<FirmwareSettings>(e)) self->show_output();
         }},
    });
}

void FirmwareSettings::open() {
    if (!are_subjects_initialized()) init_subjects();
    if (!show(lv_screen_active())) return;
    if (applying_) return;
    lv_subject_set_int(&busy_, 0);
    set_page(0, "高级设置");
    refresh();
}

void FirmwareSettings::set_page(int page, const std::string& title) {
    lv_subject_set_int(&page_, page);
    lv_subject_copy_string(&title_, title.c_str());
    // Each step starts at its heading, even when an input on the prior step was scrolled into view.
    if (auto* scroll = overlay_root_ ? lv_obj_find_by_name(overlay_root_, "firmware_scroll") : nullptr) {
        lv_obj_update_layout(scroll);
        lv_obj_scroll_to_y(scroll, 0, LV_ANIM_OFF);
    }
}

std::string FirmwareSettings::current_label(const nlohmann::json& setting) const {
    const auto value = safe_string(setting, "current");
    const auto options = setting.find("options");
    if (options != setting.end() && options->is_array())
        for (const auto& option : *options)
            if (safe_string(option, "value") == value)
                return safe_string(option, "label", value);
    return value.empty() ? "未知" : value;
}

void FirmwareSettings::refresh(bool after_apply) {
    if (applying_ || lv_subject_get_int(&busy_)) return;
    const std::string retained_group = after_apply && group_index_ >= 0 &&
            static_cast<size_t>(group_index_) < groups_.size()
        ? safe_string(groups_[group_index_], "id") : "";
    lv_subject_set_int(&busy_, 1);
    lv_subject_set_int(&ready_, !settings_.empty());
    lv_subject_copy_string(&status_, settings_.empty() ? "正在读取设备设置" : "已显示缓存 · 正在刷新设备设置");
    lifetime_.invalidate();
    auto token = lifetime_.token();
    material_workspace::request("/firmware/settings", {}, [this, token, retained_group, after_apply](auto data, std::string error) {
        // material_workspace delivers every callback on the LVGL thread.
        if (token.expired()) return;
        lv_subject_set_int(&busy_, 0);
        if (!error.empty()) {
            lv_subject_set_int(&ready_, 0);
            lv_subject_copy_string(&status_, error.c_str());
            return;
        }
        if (!safe_bool(data, "available") || !data.contains("settings") ||
            !data["settings"].is_array() || !data.contains("groups") || !data["groups"].is_array()) {
            lv_subject_set_int(&ready_, 0);
            lv_subject_copy_string(&status_, "当前设备未提供扩展设置，请检查高级模式与配置服务");
            return;
        }
        const int previous_page = lv_subject_get_int(&page_);
        const std::string previous_group = group_index_ >= 0 && static_cast<size_t>(group_index_) < groups_.size()
            ? safe_string(groups_[group_index_], "id") : "";
        const std::string previous_setting = setting_index_ >= 0 && static_cast<size_t>(setting_index_) < settings_.size()
            ? safe_string(settings_[setting_index_], "id") : "";
        settings_ = nlohmann::json::array();
        for (const auto& setting : data["settings"])
            if (setting.is_object() && !safe_bool(setting, "hidden")) settings_.push_back(setting);
        groups_ = nlohmann::json::array();
        for (const auto& group : data["groups"]) {
            const auto id = safe_string(group, "id");
            if (std::any_of(settings_.begin(), settings_.end(), [&](const auto& setting) {
                    return safe_string(setting, "group") == id;
                })) groups_.push_back(group);
        }
        rebuild_groups();
        lv_subject_set_int(&ready_, 1);
        const auto* runtime = get_runtime_config();
        const bool mock = runtime && runtime->should_mock_moonraker();
        lv_subject_copy_string(&status_, after_apply
            ? (mock ? "模拟已应用 · 已刷新全部设置" : "已生效 · 已刷新全部设备设置")
            : (mock ? "模拟预览 · 未连接打印机" : "按用途分组 · 读取自当前固件"));
        set_page(0, "高级设置");
        const std::string group_to_restore = after_apply ? retained_group : previous_page > 0 ? previous_group : "";
        for (size_t i = 0; i < groups_.size(); ++i) {
            if (safe_string(groups_[i], "id") != group_to_restore) continue;
            choose_group(static_cast<int>(i));
            if (!after_apply && previous_page == 2)
                for (size_t j = 0; j < visible_settings_.size(); ++j)
                    if (safe_string(settings_[visible_settings_[j]], "id") == previous_setting) {
                        choose_setting(static_cast<int>(j));
                        break;
                    }
            break;
        }
    });
}

void FirmwareSettings::rebuild_groups() {
    group_labels_.ensure_size(groups_.size());
    for (size_t i = 0; i < groups_.size(); ++i)
        group_labels_.set_string(i, safe_string(groups_[i], "label"));
    lv_subject_set_int(&group_count_, static_cast<int>(groups_.size()));
}

void FirmwareSettings::choose_group(int index) {
    if (applying_ || index < 0 || static_cast<size_t>(index) >= groups_.size()) return;
    group_index_ = index;
    const auto id = safe_string(groups_[index], "id");
    visible_settings_.clear();
    for (size_t i = 0; i < settings_.size(); ++i)
        if (safe_string(settings_[i], "group") == id) visible_settings_.push_back(static_cast<int>(i));
    setting_labels_.ensure_size(visible_settings_.size());
    setting_values_.ensure_size(visible_settings_.size());
    for (size_t i = 0; i < visible_settings_.size(); ++i) {
        const auto& setting = settings_[visible_settings_[i]];
        setting_labels_.set_string(i, safe_string(setting, "label"));
        setting_values_.set_string(i, current_label(setting));
    }
    lv_subject_set_int(&setting_count_, static_cast<int>(visible_settings_.size()));
    set_page(1, safe_string(groups_[index], "label"));
}

void FirmwareSettings::choose_setting(int index) {
    if (applying_ || index < 0 || static_cast<size_t>(index) >= visible_settings_.size()) return;
    setting_index_ = visible_settings_[index];
    const auto& setting = settings_[setting_index_];
    options_ = nlohmann::json::array();
    if (setting.contains("options") && setting["options"].is_array())
        for (const auto& option : setting["options"])
            if (option.is_object() && !safe_bool(option, "hidden")) options_.push_back(option);
    option_labels_.ensure_size(options_.size());
    int current_index = -1;
    for (size_t i = 0; i < options_.size(); ++i) {
        option_labels_.set_string(i, safe_string(options_[i], "label"));
        if (safe_string(options_[i], "value") == safe_string(setting, "current"))
            current_index = static_cast<int>(i);
    }
    lv_subject_set_int(&option_count_, static_cast<int>(options_.size()));
    lv_subject_set_int(&selected_option_, -1);
    lv_subject_set_int(&input_count_, 0);
    lv_subject_set_int(&dirty_, 0);
    lv_subject_copy_string(&description_, safe_string(setting, "description").c_str());
    lv_subject_copy_string(&current_, ("当前：" + current_label(setting)).c_str());
    set_page(2, safe_string(setting, "label"));
    if (current_index >= 0) choose_option(current_index);
}

void FirmwareSettings::choose_option(int index) {
    if (lv_subject_get_int(&busy_) || index < 0 || static_cast<size_t>(index) >= options_.size()) return;
    const auto& option = options_[index];
    inputs_ = nlohmann::json::array();
    if (option.contains("inputs") && option["inputs"].is_array()) inputs_ = option["inputs"];
    input_labels_.ensure_size(inputs_.size());
    input_values_.ensure_size(inputs_.size());
    for (size_t i = 0; i < inputs_.size(); ++i) {
        input_labels_.set_string(i, safe_string(inputs_[i], "label", safe_string(inputs_[i], "name")));
        input_values_.set_string(i, safe_string(inputs_[i], "value", safe_string(inputs_[i], "default")));
    }
    lv_subject_set_int(&selected_option_, index);
    lv_subject_set_int(&input_count_, static_cast<int>(inputs_.size()));
    const bool changed = safe_string(option, "value") != safe_string(settings_[setting_index_], "current");
    lv_subject_set_int(&dirty_, changed || !inputs_.empty());
}

std::string FirmwareSettings::input(const std::string& name) const {
    auto* field = overlay_root_ ? lv_obj_find_by_name(overlay_root_, name.c_str()) : nullptr;
    return field ? lv_textarea_get_text(field) : "";
}

void FirmwareSettings::review() {
    const int selected = lv_subject_get_int(&selected_option_);
    if (lv_subject_get_int(&busy_) || !lv_subject_get_int(&ready_) || selected < 0 ||
        static_cast<size_t>(selected) >= options_.size() || setting_index_ < 0) return;
    const auto& setting = settings_[setting_index_];
    const auto& option = options_[selected];
    nlohmann::json values = nlohmann::json::object();
    std::string input_summary;
    for (size_t i = 0; i < inputs_.size(); ++i) {
        const auto value = input(fmt::format("firmware_input_{}", i));
        if (safe_bool(inputs_[i], "required", true) && value.empty()) {
            lv_subject_copy_string(&status_, ("请填写：" + safe_string(inputs_[i], "label")).c_str());
            return;
        }
        const auto name = safe_string(inputs_[i], "name");
        values[name] = value;
        const bool secret = safe_bool(inputs_[i], "sensitive") ||
                            safe_string(inputs_[i], "type") == "password" ||
                            name.find("PASSWORD") != std::string::npos ||
                            name.find("TOKEN") != std::string::npos ||
                            name.find("API_KEY") != std::string::npos;
        input_summary += "\n" + safe_string(inputs_[i], "label", name) + "\n" +
                         (secret ? "已填写" : value) + "\n";
    }
    pending_ = {{"id", safe_string(setting, "id")}, {"value", safe_string(option, "value")},
                {"inputs", values}, {"confirm", true}};
    const std::string warning = safe_string(option, "confirm");
    const auto review = fmt::format("{}\n{} → {}\n{}\n{}{}", safe_string(setting, "label"),
                                    current_label(setting), safe_string(option, "label"), input_summary,
                                    warning.empty() ? "设置将立即应用，相关服务可能重启。" : warning,
                                    warning.empty() ? "" : "\n\n应用后会重新读取设备状态。");
    lv_subject_copy_string(&review_, review.c_str());
    lv_subject_copy_string(&status_, "确认这项修改后应用");
    set_page(3, "确认修改");
}

void FirmwareSettings::apply() {
    if (applying_ || lv_subject_get_int(&busy_) || !lv_subject_get_int(&ready_) ||
        lv_subject_get_int(&page_) != 3 || !pending_.contains("id")) return;
    if (lv_subject_get_int(get_printer_state().print_state().get_job_holds_machine_subject())) {
        lv_subject_copy_string(&status_, "请等待打印结束后再应用设置");
        return;
    }
    applying_ = true;
    lv_subject_set_int(&busy_, 1);
    lv_subject_copy_string(&status_, "正在应用 · 请等待设备确认");
    const auto token = object_lifetime_.token();
    material_workspace::request("/firmware/settings", pending_, [this, token](auto data, std::string error) {
        // Writes outlive navigation; the request helper marshals onto the UI thread.
        if (token.expired()) return;
        applying_ = false;
        lv_subject_set_int(&busy_, 0);
        output_ = safe_string(data, "output");
        lv_subject_set_int(&has_output_, !output_.empty());
        if (!error.empty()) {
            if (!data.is_object()) {
                lv_subject_copy_string(&status_, "连接中断，结果待确认。请刷新状态，不要重复应用。");
                lv_subject_set_int(&ready_, 0);
            } else {
                lv_subject_copy_string(&status_, error.c_str());
            }
            return;
        }
        const auto state = safe_string(data, "state");
        if (safe_bool(data, "ok") && state == "applied" && data.contains("setting") &&
            data["setting"].is_object()) {
            if (setting_index_ >= 0 && static_cast<size_t>(setting_index_) < settings_.size())
                settings_[setting_index_] = data["setting"];
            lv_subject_set_int(&dirty_, 0);
            lv_subject_copy_string(&status_, "已生效 · 已重新读取设备状态");
            pending_.clear();
            if (is_visible()) refresh(true);
            else lv_subject_set_int(&ready_, 0);
        } else if (state == "unverified") {
            lv_subject_copy_string(&status_, safe_string(data, "message", "结果待确认，请重新读取设备状态").c_str());
            lv_subject_set_int(&ready_, 0);
        } else {
            lv_subject_copy_string(&status_, safe_string(data, "error", safe_string(data, "message", "设置未能应用")).c_str());
        }
    }, true);
}

void FirmwareSettings::show_output() {
    if (output_.empty()) return;
    lv_subject_copy_string(&output_text_, output_.c_str());
    set_page(4, "执行详情");
}

void FirmwareSettings::back() {
    if (applying_) {
        nav::go_back();
        return;
    }
    const int page = lv_subject_get_int(&page_);
    if (page == 0) nav::go_back();
    else if (page == 1 || page == 4) set_page(0, "高级设置");
    else if (page == 3 && setting_index_ >= 0)
        set_page(2, safe_string(settings_[setting_index_], "label"));
    else choose_group(group_index_);
}

FirmwareSettings& get_firmware_settings() {
    return helix::lazy_global<FirmwareSettings>("FirmwareSettings");
}
} // namespace helix::ui
