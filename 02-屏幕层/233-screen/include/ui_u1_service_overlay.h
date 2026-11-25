// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include "core/static_panel_registry.h"
#include "overlay_base.h"
#include "ui_timer_guard.h"
#include "hv/json.hpp"
#include <set>
#include <string>
#include <vector>

namespace helix {

class U1ServiceOverlay : public OverlayBase {
  public:
    const char* get_name() const override { return "U1 Maintenance and Export"; }
    const char* xml_component() const override { return "u1_service_overlay"; }
    void register_callbacks() override;
    void on_activate() override;

  protected:
    void before_show() override;
    void on_deactivating(DeactivateReason) override;

  private:
    void action(const std::string& name);
    void export_view(bool open);
    void read_service();
    void display_job(const nlohmann::json& data);
    void update_actions();
    void list_files(int offset);
    void select_kind(int index);
    void send_export(const std::string& action, const nlohmann::json& body);
    void system_page(bool show);
    void system_request(const std::string& path, const nlohmann::json& body, bool post);
    void query_heads();
    void confirm_lubrication();
    lv_obj_t* widget(const char* name) const;
    void label(const char* name, const std::string& text) const;
    void enable(const char* name, bool enabled) const;
    static void check_file(lv_event_t* e);

    struct File { std::string id, name; };
    std::vector<File> files_;
    std::set<std::string> checked_;
    std::vector<int> previous_;
    std::string job_id_, system_version_, firmware_token_, firmware_hash_;
    bool showing_system_ = false, system_pending_ = false, firmware_started_ = false;
    unsigned system_generation_ = 0;
    int kind_ = 0, offset_ = 0, next_ = -1;
    unsigned list_sequence_ = 0, status_sequence_ = 0;
    bool exporting_ = false, mounted_ = false, active_job_ = false;
    bool reading_ = false, posting_ = false, listing_ = false, listed_ = false;
    ui::LvglTimerGuard timer_;
};

inline U1ServiceOverlay& get_u1_service_overlay() {
    return lazy_global<U1ServiceOverlay>("U1ServiceOverlay");
}

} // namespace helix
