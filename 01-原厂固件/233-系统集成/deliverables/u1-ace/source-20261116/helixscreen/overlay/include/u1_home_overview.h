// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include "subject_managed_panel.h"
#include "ui_observer_guard.h"

#include <array>
#include <cstddef>

struct _lv_obj_t;
using lv_obj_t = _lv_obj_t;

namespace helix {

class PrinterState;

/// Formats and publishes the home overview's display strings (bed, speed,
/// remaining time, four nozzle readings, and the active tool index). The
/// visible layout lives in u1_home_overview.xml.
class U1HomeOverview {
  public:
    static constexpr int kNozzleCount = 4;

    explicit U1HomeOverview(PrinterState& printer_state);
    ~U1HomeOverview();

    U1HomeOverview(const U1HomeOverview&) = delete;
    U1HomeOverview& operator=(const U1HomeOverview&) = delete;

    void setup(lv_obj_t* root);

  private:
    void init_subjects();
    void observe();
    void update_bed();
    void update_speed();
    void update_fan();
    void update_remaining();
    void update_nozzles();
    void update_active_tool();

    PrinterState& printer_state_;
    lv_obj_t* root_ = nullptr;
    SubjectManager subjects_;
    bool subjects_initialized_ = false;

    lv_subject_t bed_text_subject_{};
    char bed_text_buf_[16]{};
    lv_subject_t fan_text_subject_{};
    char fan_text_buf_[12]{};
    lv_subject_t speed_text_subject_{};
    char speed_text_buf_[12]{};
    lv_subject_t remaining_text_subject_{};
    char remaining_text_buf_[48]{};
    lv_subject_t active_tool_subject_{};
    std::array<lv_subject_t, kNozzleCount> nozzle_temp_subjects_{};
    std::array<std::array<char, 16>, kNozzleCount> nozzle_temp_bufs_{};

    ObserverGuard bed_observer_;
    ObserverGuard speed_observer_;
    ObserverGuard fan_observer_;
    ObserverGuard remaining_observer_;
    ObserverGuard active_tool_observer_;
    ObserverGuard tools_observer_;
    std::array<ObserverGuard, kNozzleCount> nozzle_temp_observers_;
};

} // namespace helix
