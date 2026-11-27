// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include "ui_observer_guard.h"

#include "core/static_panel_registry.h"
#include "overlay_base.h"
#include "subject_managed_panel.h"

#include <array>
#include <string>

namespace helix {
class IMoonrakerAPI;
class PrinterState;
} // namespace helix

namespace helix::ui {

/// The fixed-head printer's small landscape print page. Other displays keep
/// the full tune overlay and its flow, pressure-advance and Z-offset controls.
bool uses_compact_print_controls(const PrinterState& state, lv_obj_t* screen);

class CompactPrintTuneOverlay : public OverlayBase {
    HELIX_KIND_TAG(CompactPrintTuneOverlay)

  public:
    ~CompactPrintTuneOverlay() override;
    void show(lv_obj_t* parent, IMoonrakerAPI* api, PrinterState& state);
    const char* get_name() const override {
        return "Compact Print Tune";
    }
    const char* xml_component() const override {
        return "print_tune_compact_panel";
    }
    void init_subjects() override;
    void register_callbacks() override;
    void on_activate() override;
    void on_deactivating(DeactivateReason reason) override;

    /// XML payload: +/-1 speed, +/-2 model fan, +/-3 nozzle, +/-4 bed.
    void adjust(int control);

  private:
    enum Field { Speed, Fan, Nozzle, Bed, Count };
    struct Readout {
        lv_subject_t value{}, minimum{}, maximum{}, ready{}, text{};
        char buffer[24] = "--";
    };
    void refresh();
    void rebind_fan();
    void detach_observers();
    std::string active_nozzle() const;
    std::string active_model_fan() const;

    IMoonrakerAPI* api_ = nullptr;
    PrinterState* state_ = nullptr;
    SubjectManager subjects_;
    std::array<Readout, Count> readouts_{};
    lv_subject_t nozzle_label_{};
    char nozzle_label_buf_[128] = "";
    std::array<ObserverGuard, 8> observers_;
    ObserverGuard fan_observer_;
    ObserverGuard language_observer_;
    std::string observed_fan_;
};

inline CompactPrintTuneOverlay& get_compact_print_tune_overlay() {
    return helix::lazy_global<CompactPrintTuneOverlay>("CompactPrintTuneOverlay");
}

} // namespace helix::ui
