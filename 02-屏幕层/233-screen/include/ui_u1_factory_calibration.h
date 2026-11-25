// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include "ui_timer_guard.h"

#include "core/static_panel_registry.h"
#include "overlay_base.h"

#include <optional>

#include "hv/json.hpp"

namespace helix {
class IMoonrakerClient;
}
namespace helix::ui {
class U1FactoryCalibration : public OverlayBase {
  public:
    HELIX_KIND_TAG(U1FactoryCalibration)
    const char* get_name() const override {
        return "U1 Factory Calibration";
    }
    const char* xml_component() const override {
        return "u1_factory_calibration";
    }
    bool destroy_on_close() const override {
        return true;
    }
    lv_obj_t* create(lv_obj_t* parent) override;
    void open(const std::string& kind);
    void register_callbacks() override;
    void on_activate() override;
    void on_deactivating(DeactivateReason reason) override;
    void choose(const std::string& key);

  private:
    void query(const std::string& action = "");
    bool live(uint64_t epoch) const;
    bool allowed(const std::string& action) const;
    void run(const std::string& action);
    void render();
    void guide(int step);
    void reset_position();
    std::string script(const std::string& action) const;
    lv_obj_t* widget(const char* name) const;
    IMoonrakerClient* client_ = nullptr;
    LvglTimerGuard timer_;
    std::string kind_, notice_, homed_, noise_;
    int machine_ = -1, head_ = 0, step_ = 0;
    uint64_t epoch_ = 0;
    bool eligible_ = false, parked_ = false, read_busy_ = false, motion_ = false;
    bool entered_ = false, awaiting_move_position_ = false;
    bool awaiting_origin_ = false, watch_save_ = false, save_warning_ = false;
    std::string queued_action_;
    std::string image_;
    bool reference_ = false, positioned_ = false, verified_ = false;
    std::optional<double> xpos_, candidate_, starting_x_;
};
inline U1FactoryCalibration& get_u1_factory_calibration() {
    return lazy_global<U1FactoryCalibration>("U1FactoryCalibration");
}
} // namespace helix::ui
