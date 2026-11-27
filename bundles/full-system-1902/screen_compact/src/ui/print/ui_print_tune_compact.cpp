// SPDX-License-Identifier: GPL-3.0-or-later
#include "ui_print_tune_compact.h"

#include "ui_callback_helpers.h"
#include "ui_error_reporting.h"
#include "ui_temperature_utils.h"

#include "app_globals.h"
#include "core/observer_factory.h"
#include "i_moonraker_api.h"
#include "observe_language.h"
#include "printer_state.h"
#include "temperature_controller.h"
#include "tool_state.h"
#include "tune_controller.h"

#include <spdlog/fmt/fmt.h>

#include <algorithm>
#include <cmath>
#include <cstdio>

namespace helix::ui {

bool uses_compact_print_controls(const PrinterState& state, lv_obj_t* screen) {
    if (!screen)
        return false;
    lv_display_t* display = lv_obj_get_display(screen);
    // VENDOR_OK: only this printer's approved 480x320 print layout uses this menu.
    return display && state.get_discovery().has_snapmaker() &&
           lv_display_get_horizontal_resolution(display) == 480 &&
           lv_display_get_vertical_resolution(display) == 320;
}

CompactPrintTuneOverlay::~CompactPrintTuneOverlay() {
    detach_observers();
    deinit_subjects_base(subjects_);
}

void CompactPrintTuneOverlay::show(lv_obj_t* parent, IMoonrakerAPI* api, PrinterState& state) {
    api_ = api;
    state_ = &state;
    OverlayBase::show(parent);
}

void CompactPrintTuneOverlay::init_subjects() {
    static constexpr const char* names[] = {"speed", "fan", "nozzle", "bed"};
    for (int i = 0; i < Count; ++i) {
        auto& r = readouts_[i];
        const std::string prefix = std::string("compact_tune_") + names[i];
        UI_MANAGED_SUBJECT_INT(r.value, 0, prefix.c_str(), subjects_);
        UI_MANAGED_SUBJECT_INT(r.minimum, 0, (prefix + "_min").c_str(), subjects_);
        UI_MANAGED_SUBJECT_INT(r.maximum, 0, (prefix + "_max").c_str(), subjects_);
        UI_MANAGED_SUBJECT_INT(r.ready, 0, (prefix + "_ready").c_str(), subjects_);
        UI_MANAGED_SUBJECT_STRING(r.text, r.buffer, "--", (prefix + "_text").c_str(), subjects_);
    }
    UI_MANAGED_SUBJECT_STRING(nozzle_label_, nozzle_label_buf_, "", "compact_tune_nozzle_label",
                              subjects_);
}

void CompactPrintTuneOverlay::register_callbacks() {
    register_xml_callbacks({
        {"on_compact_print_tune_adjust",
         [](lv_event_t* e) {
             if (auto control = event_user_int(e))
                 get_compact_print_tune_overlay().adjust(*control);
         }},
    });
}

void CompactPrintTuneOverlay::on_activate() {
    OverlayBase::on_activate();
    if (!state_)
        return;
    detach_observers();
    // Discovery and TemperatureController already cache the limits. Even opening
    // this overlay must not start a second configfile-query loop.
    const auto lifetime = state_->get_subjects_lifetime();
    auto refresh_cb = [](CompactPrintTuneOverlay* self, int) { self->refresh(); };
    observers_[0] =
        observe<int>(state_->motion_state().get_speed_factor_subject(), this, refresh_cb, lifetime);
    observers_[1] = observe<int>(state_->temperature_state().get_active_extruder_target_subject(),
                                 this, refresh_cb, lifetime);
    observers_[2] = observe<int>(state_->temperature_state().get_bed_target_subject(), this,
                                 refresh_cb, lifetime);
    auto rebind_cb = [](CompactPrintTuneOverlay* self, int) {
        self->rebind_fan();
        self->refresh();
    };
    auto& tools = ToolState::instance();
    observers_[3] = observe<int>(tools.get_active_tool_subject(), this, rebind_cb,
                                 tools.get_subjects_lifetime());
    observers_[4] = observe<int>(tools.get_tools_version_subject(), this, rebind_cb,
                                 tools.get_subjects_lifetime());
    observers_[5] =
        observe<int>(state_->fan_state().get_fans_version_subject(), this, rebind_cb, lifetime);
    observers_[6] = observe<int>(state_->network_state().get_nav_buttons_enabled_subject(), this,
                                 refresh_cb, lifetime);
    // The active temperature mirror explicitly notifies on a tool switch even
    // when both heads have the same reading/target. It also covers the moment
    // when ToolState and the temperature state finish switching in different order.
    observers_[7] =
        observe<int>(state_->temperature_state().get_active_extruder_temp_subject(), this,
                     rebind_cb, state_->temperature_state().get_subjects_lifetime());
    language_observer_ =
        observe_language_change(this, [](CompactPrintTuneOverlay* self) { self->refresh(); });
    rebind_fan();
    refresh();
}

void CompactPrintTuneOverlay::on_deactivating(DeactivateReason) {
    detach_observers();
}

void CompactPrintTuneOverlay::detach_observers() {
    for (auto& observer : observers_)
        observer.reset();
    fan_observer_.reset();
    language_observer_.reset();
    observed_fan_.clear();
}

std::string CompactPrintTuneOverlay::active_nozzle() const {
    const auto* tool = ToolState::instance().active_tool();
    if (!state_ || !tool || !tool->extruder_name ||
        *tool->extruder_name != state_->temperature_state().active_extruder_name())
        return {};
    return *tool->extruder_name;
}

std::string CompactPrintTuneOverlay::active_model_fan() const {
    const auto* tool = ToolState::instance().active_tool();
    // No primary-fan fallback: on a tool changer it can resolve an auxiliary fan.
    return !active_nozzle().empty() && tool && tool->fan_name ? *tool->fan_name : "";
}

void CompactPrintTuneOverlay::rebind_fan() {
    const auto name = active_model_fan();
    if (name == observed_fan_ && fan_observer_)
        return;
    fan_observer_.reset();
    observed_fan_ = name;
    if (!state_ || name.empty())
        return;
    SubjectLifetime lifetime;
    if (auto* subject = state_->fan_state().get_fan_speed_subject(name, lifetime)) {
        fan_observer_ = observe<int>(
            subject, this, [](CompactPrintTuneOverlay* self, int) { self->refresh(); }, lifetime);
    }
}

void CompactPrintTuneOverlay::refresh() {
    if (!subjects_initialized_ || !state_)
        return;
    std::array<int, Count> values{}, minima{}, maxima{};
    std::array<bool, Count> ready{};
    values[Speed] = lv_subject_get_int(state_->motion_state().get_speed_factor_subject());
    minima[Speed] = tune::kSpeedMinPct;
    maxima[Speed] = tune::kSpeedMaxPct;
    ready[Speed] = true;

    const std::string nozzle = active_nozzle();
    const std::string model_fan = active_model_fan();
    for (const auto& fan : state_->fan_state().get_fans()) {
        if (fan.object_name == model_fan && fan.is_controllable && fan.speed_known) {
            values[Fan] = fan.speed_percent;
            ready[Fan] = true;
            break;
        }
    }
    if (api_) {
        const auto& limits = api_->get_safety_limits();
        minima[Fan] = std::max(0, static_cast<int>(std::ceil(limits.min_fan_speed_percent)));
        maxima[Fan] = std::min(100, static_cast<int>(std::floor(limits.max_fan_speed_percent)));
        for (const auto field : {Nozzle, Bed}) {
            const std::string heater = field == Nozzle ? nozzle : "heater_bed";
            if (heater.empty())
                continue;
            auto* target = field == Nozzle
                               ? state_->temperature_state().get_extruder_target_subject(heater)
                               : state_->temperature_state().get_bed_target_subject();
            // Use the temperature controller's conservative keypad ceiling until
            // discovery supplies a per-heater limit. Never fall back to 400C for a bed.
            auto* controller = get_temperature_controller();
            if (!controller || !target)
                continue;
            const auto type = field == Nozzle ? HeaterType::Nozzle : HeaterType::Bed;
            const float fallback = controller->keypad_range(type).max;
            const float ceiling = controller->keypad_max_for(heater, fallback);
            if (ceiling <= 0)
                continue;
            minima[field] =
                std::max(0, static_cast<int>(std::ceil(limits.min_temperature_celsius)));
            maxima[field] = static_cast<int>(std::floor(ceiling));
            values[field] = temperature::deci_to_degrees(lv_subject_get_int(target));
            ready[field] = maxima[field] >= minima[field];
        }
    }
    const auto* tool = ToolState::instance().active_tool();
    const auto label = nozzle.empty() || !tool
                           ? std::string(lv_tr("Current nozzle target"))
                           : fmt::format(lv_tr("Nozzle {} target temperature"), tool->index + 1);
    lv_subject_copy_string(&nozzle_label_, label.c_str());
    for (int i = 0; i < Count; ++i) {
        auto& r = readouts_[i];
        lv_subject_set_int(&r.value, values[i]);
        lv_subject_set_int(&r.minimum, minima[i]);
        lv_subject_set_int(&r.maximum, maxima[i]);
        lv_subject_set_int(&r.ready, api_ && ready[i] ? 1 : 0);
        if (ready[i])
            std::snprintf(r.buffer, sizeof(r.buffer), i < Nozzle ? "%d%%" : "%d°C", values[i]);
        else
            std::snprintf(r.buffer, sizeof(r.buffer), "--");
        lv_subject_copy_string(&r.text, r.buffer);
    }
}

void CompactPrintTuneOverlay::adjust(int control) {
    if (!visible_ || !api_ || !state_ || control == 0 || control < -Count || control > Count ||
        lv_subject_get_int(state_->network_state().get_nav_buttons_enabled_subject()) != 1)
        return;
    // Re-resolve hardware at the tap, including changes since the last rendered frame.
    refresh();
    const int field = std::abs(control) - 1;
    auto& r = readouts_[field];
    if (!lv_subject_get_int(&r.ready))
        return;
    const int current = lv_subject_get_int(&r.value);
    const int minimum = lv_subject_get_int(&r.minimum);
    const int maximum = lv_subject_get_int(&r.maximum);
    if (minimum > maximum)
        return;
    const int target = std::clamp(current + (control > 0 ? 5 : -5), minimum, maximum);
    if (target == current)
        return;
    switch (field) {
    case Speed:
        tune::set_speed_percent(api_, target);
        break;
    case Fan: {
        const auto fan = active_model_fan();
        if (!fan.empty())
            api_->set_fan_speed(fan, target, nullptr, [](const MoonrakerError& error) {
                notify_error_tr(TR_NOOP("Failed to set fan speed: {}"), error);
            });
        break;
    }
    case Nozzle:
    case Bed:
        if (auto* controller = get_temperature_controller()) {
            const auto heater = field == Nozzle ? active_nozzle() : "heater_bed";
            if (!heater.empty())
                controller->set_target(heater, target);
        }
        break;
    }
    // Readouts follow the printer's report, including commands from another client.
}

} // namespace helix::ui
