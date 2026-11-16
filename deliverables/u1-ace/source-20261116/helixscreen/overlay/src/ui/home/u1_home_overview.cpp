// SPDX-License-Identifier: GPL-3.0-or-later
#include "u1_home_overview.h"

#include "printer_state.h"
#include "tool_state.h"
#include "app_globals.h"
#include "ui_overlay_temp_graph.h"
#include "ui_material_workspace.h"
#include "ui_fan_control_overlay.h"
#include "ui_print_tune_overlay.h"

#include "core/observer_factory.h"

#include "lvgl/lvgl.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

namespace helix {
namespace {

std::string format_remaining_zh(int seconds) {
    if (seconds <= 0) {
        return "剩余 --";
    }
    const int hours = seconds / 3600;
    const int minutes = (seconds % 3600) / 60;
    char buf[48];
    if (hours > 0 && minutes > 0) {
        std::snprintf(buf, sizeof(buf), "剩余 %d小时 %d分", hours, minutes);
    } else if (hours > 0) {
        std::snprintf(buf, sizeof(buf), "剩余 %d小时", hours);
    } else if (minutes > 0) {
        std::snprintf(buf, sizeof(buf), "剩余 %d分", minutes);
    } else {
        std::snprintf(buf, sizeof(buf), "剩余 %d秒", seconds);
    }
    return buf;
}

void copy_text(lv_subject_t* subject, char* buf, size_t buf_size, const char* text) {
    if (std::strncmp(buf, text, buf_size) == 0) {
        return;
    }
    std::snprintf(buf, buf_size, "%s", text);
    lv_subject_copy_string(subject, buf);
}

} // namespace

U1HomeOverview::U1HomeOverview(PrinterState& printer_state) : printer_state_(printer_state) {
    init_subjects();
    lv_xml_register_event_cb(nullptr, "u1_home_bed", [](lv_event_t*) {
        get_global_temp_graph_overlay().open(TempGraphOverlay::Mode::Bed, lv_screen_active());
    });
    lv_xml_register_event_cb(nullptr, "u1_home_speed", [](lv_event_t*) {
        get_print_tune_overlay().show(lv_screen_active(), get_moonraker_api(), get_printer_state());
    });
    lv_xml_register_event_cb(nullptr, "u1_home_fan", [](lv_event_t*) {
        helix::open_fan_control_overlay(lv_screen_active());
    });
    lv_xml_register_event_cb(nullptr, "u1_home_nozzle", [](lv_event_t* e) {
        const char* data = static_cast<const char*>(lv_event_get_user_data(e));
        const int i = data ? std::atoi(data) : -1;
        const auto heads = ToolState::instance().physical_heads();
        if (i >= 0 && i < static_cast<int>(heads.size()) && heads[i].extruder_name)
            ui::get_material_workspace().open_nozzle(i);
    });
}

U1HomeOverview::~U1HomeOverview() {
    bed_observer_.reset();
    speed_observer_.reset();
    fan_observer_.reset();
    remaining_observer_.reset();
    active_tool_observer_.reset();
    tools_observer_.reset();
    for (auto& observer : nozzle_temp_observers_) {
        observer.reset();
    }
    if (subjects_initialized_) {
        subjects_.deinit_all();
        subjects_initialized_ = false;
    }
}

void U1HomeOverview::setup(lv_obj_t* root) {
    root_ = root;
    observe();
    update_bed();
    update_speed();
    update_fan();
    update_remaining();
    update_nozzles();
    update_active_tool();
}

void U1HomeOverview::init_subjects() {
    if (subjects_initialized_) {
        return;
    }
    UI_MANAGED_SUBJECT_STRING(bed_text_subject_, bed_text_buf_, "—°C", "u1_home_bed_text",
                              subjects_);
    UI_MANAGED_SUBJECT_STRING(fan_text_subject_, fan_text_buf_, "0%", "u1_home_fan_text", subjects_);
    UI_MANAGED_SUBJECT_STRING(speed_text_subject_, speed_text_buf_, "100%", "u1_home_speed_text",
                              subjects_);
    UI_MANAGED_SUBJECT_STRING(remaining_text_subject_, remaining_text_buf_, "剩余 --",
                              "u1_home_remaining_text", subjects_);
    UI_MANAGED_SUBJECT_INT(active_tool_subject_, -1, "u1_home_active_tool", subjects_);
    for (int i = 0; i < kNozzleCount; ++i) {
        char name[32];
        std::snprintf(name, sizeof(name), "u1_home_nozzle_temp_%d", i);
        UI_MANAGED_SUBJECT_STRING_N(nozzle_temp_subjects_[static_cast<size_t>(i)],
                                    nozzle_temp_bufs_[static_cast<size_t>(i)].data(),
                                    nozzle_temp_bufs_[static_cast<size_t>(i)].size(), "—", name,
                                    subjects_);
    }
    subjects_initialized_ = true;
}

void U1HomeOverview::observe() {
    const auto lifetime = subjects_.get_subjects_lifetime();
    if (lv_subject_t* bed = printer_state_.temperature_state().get_bed_temp_subject()) {
        bed_observer_ = helix::ui::observe<int>(
            bed, this, [](U1HomeOverview* self, int) { self->update_bed(); }, lifetime);
    }
    if (lv_subject_t* speed = printer_state_.motion_state().get_speed_factor_subject()) {
        speed_observer_ = helix::ui::observe<int>(
            speed, this, [](U1HomeOverview* self, int) { self->update_speed(); }, lifetime);
    }
    fan_observer_ = helix::ui::observe<int>(
        printer_state_.fan_state().get_fan_speed_subject(), this,
        [](U1HomeOverview* self, int) { self->update_fan(); }, lifetime);
    if (lv_subject_t* remain = printer_state_.print_state().get_print_time_left_subject()) {
        remaining_observer_ = helix::ui::observe<int>(
            remain, this, [](U1HomeOverview* self, int) { self->update_remaining(); }, lifetime);
    }
    if (lv_subject_t* active = ToolState::instance().get_active_tool_subject()) {
        active_tool_observer_ = helix::ui::observe<int>(
            active, this, [](U1HomeOverview* self, int) { self->update_active_tool(); }, lifetime);
    }
    if (lv_subject_t* tools = ToolState::instance().get_tools_version_subject()) {
        tools_observer_ = helix::ui::observe<int>(
            tools, this,
            [](U1HomeOverview* self, int) {
                self->update_nozzles();
                self->update_active_tool();
            },
            lifetime);
    } else {
        // tools version may not exist on every build; still bind per-head temps.
        update_nozzles();
    }
}

void U1HomeOverview::update_bed() {
    lv_subject_t* bed = printer_state_.temperature_state().get_bed_temp_subject();
    const int deci = bed ? lv_subject_get_int(bed) : 0;
    char buf[16];
    std::snprintf(buf, sizeof(buf), "%.1f°C", static_cast<double>(deci) / 10.0);
    copy_text(&bed_text_subject_, bed_text_buf_, sizeof(bed_text_buf_), buf);
}

void U1HomeOverview::update_speed() {
    lv_subject_t* speed = printer_state_.motion_state().get_speed_factor_subject();
    int percent = speed ? lv_subject_get_int(speed) : 100;
    if (percent <= 0) {
        percent = 100;
    }
    char buf[12];
    std::snprintf(buf, sizeof(buf), "%d%%", percent);
    copy_text(&speed_text_subject_, speed_text_buf_, sizeof(speed_text_buf_), buf);
}

void U1HomeOverview::update_fan() {
    char buf[12];
    std::snprintf(buf, sizeof(buf), "%d%%",
                  lv_subject_get_int(printer_state_.fan_state().get_fan_speed_subject()));
    copy_text(&fan_text_subject_, fan_text_buf_, sizeof(fan_text_buf_), buf);
}

void U1HomeOverview::update_remaining() {
    lv_subject_t* remain = printer_state_.print_state().get_print_time_left_subject();
    const int seconds = remain ? lv_subject_get_int(remain) : 0;
    const std::string text = format_remaining_zh(seconds);
    copy_text(&remaining_text_subject_, remaining_text_buf_, sizeof(remaining_text_buf_),
              text.c_str());
}

void U1HomeOverview::update_active_tool() {
    lv_subject_t* active = ToolState::instance().get_active_tool_subject();
    const int tool = active ? lv_subject_get_int(active) : -1;
    const int index = head_index_for_tool(ToolState::instance().physical_heads(), tool);
    if (lv_subject_get_int(&active_tool_subject_) != index) {
        lv_subject_set_int(&active_tool_subject_, index);
    }
}

void U1HomeOverview::update_nozzles() {
    const auto lifetime = subjects_.get_subjects_lifetime();
    auto& temps = printer_state_.temperature_state();
    const auto heads = ToolState::instance().physical_heads();
    for (int i = 0; i < kNozzleCount; ++i) {
        nozzle_temp_observers_[static_cast<size_t>(i)].reset();
        lv_subject_t* temp_subject = nullptr;
        if (i < static_cast<int>(heads.size()) && heads[static_cast<size_t>(i)].extruder_name) {
            temp_subject = temps.get_extruder_temp_subject(*heads[static_cast<size_t>(i)].extruder_name);
        }
        if (temp_subject) {
            nozzle_temp_observers_[static_cast<size_t>(i)] = helix::ui::observe<int>(
                temp_subject, this,
                [i](U1HomeOverview* self, int deci) {
                    char buf[16];
                    if (deci <= -999) {
                        std::snprintf(buf, sizeof(buf), "—");
                    } else {
                        const int deg = deci >= 0 ? (deci + 5) / 10 : (deci - 5) / 10;
                        std::snprintf(buf, sizeof(buf), "%d°C", deg);
                    }
                    copy_text(&self->nozzle_temp_subjects_[static_cast<size_t>(i)],
                              self->nozzle_temp_bufs_[static_cast<size_t>(i)].data(),
                              self->nozzle_temp_bufs_[static_cast<size_t>(i)].size(), buf);
                },
                lifetime);
            // Seed immediately; observe() may not fire until the next change.
            const int deci = lv_subject_get_int(temp_subject);
            char buf[16];
            if (deci <= -999) {
                std::snprintf(buf, sizeof(buf), "—");
            } else {
                const int deg = deci >= 0 ? (deci + 5) / 10 : (deci - 5) / 10;
                std::snprintf(buf, sizeof(buf), "%d°C", deg);
            }
            copy_text(&nozzle_temp_subjects_[static_cast<size_t>(i)],
                      nozzle_temp_bufs_[static_cast<size_t>(i)].data(),
                      nozzle_temp_bufs_[static_cast<size_t>(i)].size(), buf);
        } else {
            copy_text(&nozzle_temp_subjects_[static_cast<size_t>(i)],
                      nozzle_temp_bufs_[static_cast<size_t>(i)].data(),
                      nozzle_temp_bufs_[static_cast<size_t>(i)].size(), "—");
        }
    }
}

} // namespace helix
