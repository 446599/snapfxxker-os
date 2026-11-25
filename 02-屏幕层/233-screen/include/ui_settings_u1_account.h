// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include "core/static_panel_registry.h"
#include "hv/json.hpp"
#include "overlay_base.h"
#include "ui_timer_guard.h"

namespace helix {
class IMoonrakerClient;
}

namespace helix::settings {

class U1AccountOverlay : public OverlayBase {
  public:
    HELIX_KIND_TAG(U1AccountOverlay)
    const char* get_name() const override { return "U1 Account"; }
    const char* xml_component() const override { return "u1_account_overlay"; }
    bool destroy_on_close() const override { return true; }
    lv_obj_t* create(lv_obj_t* parent) override;
    void register_callbacks() override;
    void on_activate() override;
    void on_deactivating(DeactivateReason reason) override;
    void action(std::string name);

  private:
    lv_obj_t* widget(const char* name) const;
    void status(const std::string& text);
    void clear_codes();
    void render();
    void poll();
    void request(const std::string& method, const nlohmann::json& params = nlohmann::json::object());
    void received(const std::string& method, const nlohmann::json& result);
    void failure();
    bool current(uint64_t generation) const;
    std::string username_, userid_, region_, device_name_, pin_, access_, message_;
    int mode_ = -1;
    int clients_ = -1;
    bool busy_ = false;
    bool device_checked_ = false;
    uint64_t generation_ = 0;
    uint32_t pin_time_ = 0;
    ui::LvglTimerGuard timer_;
    IMoonrakerClient* client_ = nullptr;
};

inline U1AccountOverlay& get_u1_account_overlay() {
    return lazy_global<U1AccountOverlay>("U1AccountOverlay");
}
}
