// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include "ui_coalesced_timer.h"
#include "ui_observer_guard.h"
#include "ui_widget_ref.h"

#include "gcode_preview_fetcher.h"
#include "subject_managed_panel.h"

#include <array>
#include <cstddef>
#include <string>

struct _lv_obj_t;
using lv_obj_t = _lv_obj_t;

namespace helix {

class PrinterState;
class IMoonrakerAPI;

/// Formats and publishes the home overview's display strings (light, speed,
/// remaining time, four nozzle readings, and the active tool index). The
/// visible layout lives in u1_home_overview.xml.
class U1HomeOverview {
  public:
    static constexpr int kNozzleCount = 4;

    explicit U1HomeOverview(PrinterState& printer_state, IMoonrakerAPI* api = nullptr);
    ~U1HomeOverview();

    U1HomeOverview(const U1HomeOverview&) = delete;
    U1HomeOverview& operator=(const U1HomeOverview&) = delete;

    void setup(lv_obj_t* root);
    /// Keep parsed geometry across visits, but never render behind another panel.
    void set_preview_active(bool active);

  private:
    void init_subjects();
    void observe();
    void update_light();
    void update_speed();
    void update_fan();
    void update_remaining();
    void update_nozzles();
    void update_active_tool();
    void schedule_preview();
    void update_preview();
    void clear_preview();
    bool preview_matches_current() const;
    static void on_preview_loaded(lv_obj_t* viewer, void* user_data, bool success);

    PrinterState& printer_state_;
    IMoonrakerAPI* api_ = nullptr;
    ui::WidgetRef root_;
    ui::WidgetRef preview_viewer_;
    ui::GcodePreviewFetcher preview_fetcher_{"U1HomePreview"};
    ui::CoalescedTimer preview_update_timer_;
    std::string preview_file_;
    std::string preview_source_;
    bool preview_active_ = false;
    bool preview_loading_ = false;
    bool preview_failed_ = false;
    SubjectManager subjects_;
    bool subjects_initialized_ = false;

    lv_subject_t light_text_subject_{};
    char light_text_buf_[12]{};
    lv_subject_t fan_text_subject_{};
    char fan_text_buf_[12]{};
    lv_subject_t speed_text_subject_{};
    char speed_text_buf_[12]{};
    lv_subject_t remaining_text_subject_{};
    char remaining_text_buf_[48]{};
    lv_subject_t active_tool_subject_{};
    lv_subject_t preview_render_subject_{}; ///< 0 placeholder/thumbnail, 1 loading, 2 model.
    std::array<lv_subject_t, kNozzleCount> nozzle_temp_subjects_{};
    std::array<std::array<char, 16>, kNozzleCount> nozzle_temp_bufs_{};

    ObserverGuard light_config_observer_;
    ObserverGuard light_state_observer_;
    ObserverGuard speed_observer_;
    ObserverGuard fan_observer_;
    ObserverGuard remaining_observer_;
    ObserverGuard active_tool_observer_;
    ObserverGuard tools_observer_;
    std::array<ObserverGuard, kNozzleCount> nozzle_temp_observers_;
    std::array<ObserverGuard, 6> preview_observers_;
};

} // namespace helix
