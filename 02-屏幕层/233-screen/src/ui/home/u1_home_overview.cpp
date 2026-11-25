// SPDX-License-Identifier: GPL-3.0-or-later
#include "u1_home_overview.h"

#include "ui_fan_control_overlay.h"
#include "ui_gcode_viewer.h"
#include "ui_material_workspace.h"
#include "ui_panel_print_status.h"
#include "ui_print_tune_overlay.h"

#include "app_globals.h"
#include "core/gcode_filename.h"
#include "core/observer_factory.h"
#include "led/led_controller.h"
#include "light_button_config.h"
#include "lvgl/lvgl.h"
#include "print_lifecycle_state.h"
#include "printer_state.h"
#include "tool_state.h"

#include <spdlog/spdlog.h>

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
    lv_subject_copy_string(subject, text);
}

} // namespace

U1HomeOverview::U1HomeOverview(PrinterState& printer_state, IMoonrakerAPI* api)
    : printer_state_(printer_state), api_(api) {
    init_subjects();
    lv_xml_register_event_cb(nullptr, "u1_home_job_details", [](lv_event_t*) {
        if (lv_subject_get_int(get_printer_state().print_state().get_job_holds_machine_subject()))
            PrintStatusPanel::push_overlay(lv_screen_active());
    });
    lv_xml_register_event_cb(nullptr, "u1_home_light", [](lv_event_t*) {
        toggle_lights(home_light_button_targets(), "U1Home");
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
    preview_update_timer_.cancel();
    for (auto& observer : preview_observers_)
        observer.reset();
    clear_preview();
    light_config_observer_.reset();
    light_state_observer_.reset();
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
    clear_preview();
    root_ = root;
    preview_viewer_ = root ? lv_obj_find_by_name(root, "u1_preview_gcode") : nullptr;
    if (preview_viewer_) {
        // A small home card needs one cached software render, not a second GPU scene.
        ui::gcode_viewer_set_render_mode(preview_viewer_, GcodeViewerRenderMode::Layer2D);
        ui::gcode_viewer_set_thumbnail_parity(preview_viewer_, true);
        ui::gcode_viewer_set_print_progress(preview_viewer_, -1);
        ui::gcode_viewer_set_paused(preview_viewer_, true);
    }
    observe();
    update_light();
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
    UI_MANAGED_SUBJECT_STRING(light_text_subject_, light_text_buf_, "未知", "u1_home_light_text",
                              subjects_);
    UI_MANAGED_SUBJECT_STRING(fan_text_subject_, fan_text_buf_, "0%", "u1_home_fan_text", subjects_);
    UI_MANAGED_SUBJECT_STRING(speed_text_subject_, speed_text_buf_, "100%", "u1_home_speed_text",
                              subjects_);
    UI_MANAGED_SUBJECT_STRING(remaining_text_subject_, remaining_text_buf_, "剩余 --",
                              "u1_home_remaining_text", subjects_);
    UI_MANAGED_SUBJECT_INT(active_tool_subject_, -1, "u1_home_active_tool", subjects_);
    UI_MANAGED_SUBJECT_INT(preview_render_subject_, 0, "u1_home_preview_render", subjects_);
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
    auto& lights = led::LedController::instance();
    const auto update_light = [](U1HomeOverview* self, int) { self->update_light(); };
    light_config_observer_ = helix::ui::observe<int>(lights.get_led_config_version_subject(), this,
                                                   update_light, lights.get_subjects_lifetime());
    light_state_observer_ = helix::ui::observe<int>(lights.get_led_state_version_subject(), this,
                                                  update_light, lights.get_subjects_lifetime());
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

    auto& print = printer_state_.print_state();
    const auto print_lifetime = printer_state_.get_subjects_lifetime();
    const auto changed = [](U1HomeOverview* self, int) { self->schedule_preview(); };
    const auto path_changed = [](U1HomeOverview* self, const char*) { self->schedule_preview(); };
    preview_observers_[0] =
        ui::observe<int>(print.get_print_identity_epoch_subject(), this, changed, print_lifetime);
    preview_observers_[1] = ui::observe<const char*>(print.get_print_filename_subject(), this,
                                                     path_changed, print_lifetime);
    preview_observers_[2] = ui::observe<const char*>(print.get_print_thumbnail_path_subject(), this,
                                                     path_changed, print_lifetime);
    preview_observers_[3] =
        ui::observe<int>(print.get_print_has_thumbnail_subject(), this, changed, print_lifetime);
    preview_observers_[4] =
        ui::observe<int>(print.get_job_holds_machine_subject(), this, changed, print_lifetime);
    preview_observers_[5] =
        ui::observe<int>(printer_state_.network_state().get_nav_buttons_enabled_subject(), this,
                         changed, print_lifetime);
}

void U1HomeOverview::set_preview_active(bool active) {
    preview_active_ = active;
    if (preview_viewer_)
        ui::gcode_viewer_set_paused(preview_viewer_, !active);
    // A failed fetch may retry once on the next visit, never on progress/temperature ticks.
    if (active && preview_failed_)
        clear_preview();
    if (active)
        schedule_preview();
}

void U1HomeOverview::schedule_preview() {
    preview_update_timer_.schedule_once([this]() { update_preview(); });
}

void U1HomeOverview::clear_preview() {
    preview_fetcher_.cancel();
    if (preview_viewer_) {
        ui::gcode_viewer_set_load_callback(preview_viewer_, nullptr, nullptr);
        if (preview_loading_ || ui::gcode_viewer_has_content(preview_viewer_))
            ui::gcode_viewer_clear(preview_viewer_);
        ui::gcode_viewer_set_paused(preview_viewer_, true);
    }
    preview_loading_ = false;
    preview_failed_ = false;
    preview_file_.clear();
    preview_source_.clear();
    if (subjects_initialized_)
        lv_subject_set_int(&preview_render_subject_, 0);
    // Leave the shared print_view cache copy on disk: the details viewer may use it too.
}

bool U1HomeOverview::preview_matches_current() const {
    auto& print = printer_state_.print_state();
    return root_ && preview_viewer_ && ::job_holds_machine(print.get_print_lifecycle()) &&
           preview_file_ == print.get_effective_print_filename() &&
           preview_source_ == gcode::preview_gcode_for(
                                  print.get_effective_print_filename(),
                                  lv_subject_get_string(print.get_print_filename_subject())) &&
           !(print.get_print_thumbnail_file() == preview_file_ &&
             lv_subject_get_int(print.get_print_has_thumbnail_subject()) != 0);
}

void U1HomeOverview::update_preview() {
    if (!root_ || !preview_viewer_)
        return;
    auto& print = printer_state_.print_state();
    const auto file = print.get_effective_print_filename();
    const bool has_thumbnail = print.get_print_thumbnail_file() == file &&
                               lv_subject_get_int(print.get_print_has_thumbnail_subject()) != 0;
    if (!::job_holds_machine(print.get_print_lifecycle()) || file.empty() || has_thumbnail) {
        if (!preview_file_.empty())
            clear_preview();
        return; // The existing print_thumbnail_path image binding stays authoritative.
    }
    const auto source =
        gcode::preview_gcode_for(file, lv_subject_get_string(print.get_print_filename_subject()));
    if (file == preview_file_ && source == preview_source_ &&
        (preview_loading_ || preview_failed_ || ui::gcode_viewer_has_content(preview_viewer_)))
        return;
    // A file switch invalidates the old renderer even while the home panel is hidden.
    clear_preview();
    if (!preview_active_ ||
        lv_subject_get_int(printer_state_.network_state().get_nav_buttons_enabled_subject()) != 1)
        return;
    auto* api = api_ ? api_ : get_moonraker_api();
    if (!api)
        return;
    preview_file_ = file;
    preview_source_ = source;
    preview_loading_ = true;
    preview_fetcher_.set_api(api);
    spdlog::debug("[U1HomePreview] Loading model for '{}' from '{}'", file, source);
    preview_fetcher_.fetch(
        source,
        [this](const std::string& path) {
            if (!preview_matches_current()) {
                schedule_preview();
                return;
            }
            lv_subject_set_int(&preview_render_subject_, 1);
            ui::gcode_viewer_set_paused(preview_viewer_, !preview_active_);
            ui::gcode_viewer_set_load_callback(preview_viewer_, on_preview_loaded, this);
            ui::gcode_viewer_load_file(preview_viewer_, path.c_str());
        },
        [this](ui::GcodePreviewFetcher::Unavailable why) {
            preview_loading_ = false;
            preview_failed_ = true;
            lv_subject_set_int(&preview_render_subject_, 0);
            spdlog::debug("[U1HomePreview] Model unavailable ({})", static_cast<int>(why));
        });
}

void U1HomeOverview::on_preview_loaded(lv_obj_t* viewer, void* user_data, bool success) {
    auto* self = static_cast<U1HomeOverview*>(user_data);
    if (!self || viewer != self->preview_viewer_.get() || !self->preview_matches_current()) {
        if (self)
            self->schedule_preview();
        return;
    }
    self->preview_loading_ = false;
    self->preview_failed_ = !success;
    if (success) {
        ui::gcode_viewer_set_print_progress(viewer, -1);
        ui::gcode_viewer_apply_ams_tool_colors(viewer);
        spdlog::info("[U1HomePreview] Model ready for '{}'", self->preview_file_);
    }
    ui::gcode_viewer_set_paused(viewer, !self->preview_active_);
    lv_subject_set_int(&self->preview_render_subject_, success ? 2 : 0);
}

void U1HomeOverview::update_light() {
    auto& lights = led::LedController::instance();
    const auto targets = home_light_button_targets();
    bool all_off = !targets.empty();
    for (const auto& id : targets) {
        const auto power = lights.device_state(id).power;
        if (power == led::PowerState::On) {
            copy_text(&light_text_subject_, light_text_buf_, sizeof(light_text_buf_), "开");
            return;
        }
        all_off = all_off && power == led::PowerState::Off;
    }
    copy_text(&light_text_subject_, light_text_buf_, sizeof(light_text_buf_), all_off ? "关" : "未知");
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
