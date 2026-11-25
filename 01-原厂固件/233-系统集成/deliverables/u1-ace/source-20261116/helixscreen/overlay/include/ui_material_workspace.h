// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "ui_observer_guard.h"
#include "ui_timer_guard.h"

#include "helix/xml/indexed_subject_pool.h"
#include "overlay_base.h"

#include "hv/json.hpp"
#include "ams_types.h"
#include "material_workspace.h"

#include <functional>
#include <optional>
#include <array>

namespace helix::ui {
class MaterialWorkspace : public OverlayBase {
  public:
    HELIX_KIND_TAG(MaterialWorkspace)
    ~MaterialWorkspace() override;
    const char* get_name() const override {
        return "Material workspace";
    }
    const char* xml_component() const override {
        return "material_workspace";
    }
    bool is_destination() const override {
        return true;
    }
    void init_subjects() override;
    void register_callbacks() override;
    lv_obj_t* create(lv_obj_t* parent) override;
    void on_activate() override;
    void on_deactivating(DeactivateReason reason) override;
    void open_firmware();
    void open(int head, int unit, bool library = false,
              std::function<void(int)> on_return = {}, int local_slot = -1);
    void open_action(int head, int unit, const std::string& action,
                     std::function<void(int)> on_return = {}, int local_slot = -1,
                     const std::string& expected_recovery_ticket = {},
                     int expected_ace = -1);
    void open_nozzle(int head, std::function<void(int)> on_return = {});
    void open_temperature();

  private:
    void page(int value);
    void act(const std::string& action);
    void finish_recovery_notice(const std::string& message);
    void refresh();
    void poll_slot_task();
    void stop_slot_task();
    void refresh_nozzle();
    void refresh_temperatures();
    void browse_head(int delta);
    void stage_nozzle_target(int delta);
    void apply_nozzle_target();
    void edit_nozzle_material();
    void activate_head();
    void edit_external();
    void save_external();
    void return_from_editor();
    std::string load_refusal();
    void move_filament(int millimeters);
    void finish_motion();
    void load_library();
    void load_files();
    void load_settings();
    void show_progress();
    void choose_spool(int index);
    void choose_file(int index);
    void firmware_status();
    void run(const std::string& action, const nlohmann::json& args);
    std::string input(const char* name) const;
    SubjectManager subjects_;
    bool visible_ = false;
    bool firmware_entry_ = false;
    bool auto_dry_entry_ = false;
    bool recovery_notice_ = false, recovery_request_pending_ = false, recovery_done_ = false;
    int recovery_seq_ = 0;
    bool reading_auto_dry_ = false;
    bool motion_rpc_pending_ = false;
    bool publishes_motion_ = false;
    unsigned session_ = 0;
    int edit_origin_ = 0;
    int manual_origin_ = 0;
    int library_origin_ = -1;
    std::optional<SlotInfo> original_slot_;
    std::function<void(int)> on_return_;
    std::string observed_extruder_;
    std::array<std::string, 5> overview_heaters_{};
    std::array<ObserverGuard, 5> overview_current_observers_, overview_target_observers_;
    bool binding_temperatures_ = false;
    bool nozzle_draft_dirty_ = false;
    int nozzle_draft_c_ = 0;
    std::string nozzle_draft_extruder_;
    int head_ = 0, unit_ = 0, slot_ = -1, local_slot_ = 0, ace_index_ = -1;
    std::string spool_id_, firmware_token_;
    nlohmann::json spools_ = nlohmann::json::array(), files_ = nlohmann::json::array();
    std::string selected_file_;
    LvglTimerGuard poll_;
    LvglTimerGuard slot_poll_;
    material_workspace::SlotTaskState slot_task_;
    bool slot_poll_pending_ = false, slot_stop_pending_ = false;
    uint32_t slot_state_at_ = 0;
    LvglTimerGuard motion_timer_;
    ObserverGuard revision_observer_;
    ObserverGuard connection_observer_, job_observer_, tool_observer_;
    ObserverGuard temperature_observer_, target_observer_;
    std::string pending_action_;
    std::string deferred_slot_action_;
    std::string expected_recovery_ticket_;
    int expected_ace_ = -1;
    std::optional<int> confirmed_source_;
    std::optional<int> confirmed_target_;
    nlohmann::json pending_args_;
    lv_subject_t page_{}, busy_{}, list_count_{}, file_count_{}, confirmed_{}, progress_{},
        eject_ready_{}, source_ready_{}, task_stoppable_{}, recovery_check_ready_{}, recovery_clear_ready_{},
        recovery_needed_{}, confirm_ready_{}, task_message_{},
        ready_{}, route_mapped_{}, auto_sensor_{}, auto_enabled_{}, settings_ready_{}, external_{},
        edit_external_{}, editable_{}, manual_ready_{}, nozzle_color_{}, nozzle_head_{},
        nozzle_heat_ready_{}, nozzle_target_dirty_{}, nozzle_activation_ready_{},
        nozzle_active_{}, nozzle_editable_{};
    lv_subject_t confirm_text_{}, title_{}, status_{}, detail_{}, form_material_{}, form_color_{}, form_weight_{},
        form_vendor_{}, file_info_{}, version_{}, nozzle_title_{}, nozzle_temperature_{},
        nozzle_material_{}, nozzle_source_{}, manual_hint_{}, nozzle_target_{};
    char confirm_text_buf_[64]{}, title_buf_[128]{}, status_buf_[512]{}, detail_buf_[512]{}, form_material_buf_[64]{},
        task_message_buf_[512]{},
        form_color_buf_[32]{}, form_weight_buf_[32]{}, form_vendor_buf_[64]{},
        file_info_buf_[512]{}, version_buf_[64]{}, nozzle_title_buf_[32]{},
        nozzle_temperature_buf_[64]{}, nozzle_material_buf_[96]{}, nozzle_source_buf_[96]{},
        manual_hint_buf_[192]{}, nozzle_target_buf_[32]{};
    helix::xml::IndexedSubjectPool auto_values_{"workspace_auto",
                                                helix::xml::IndexedSubjectPool::Type::String, 32};
    helix::xml::IndexedSubjectPool overview_current_{
        "workspace_heater", helix::xml::IndexedSubjectPool::Type::String, 32, "_current"};
    helix::xml::IndexedSubjectPool overview_target_{
        "workspace_heater", helix::xml::IndexedSubjectPool::Type::String, 32, "_target"};
    helix::xml::IndexedSubjectPool overview_colors_{
        "workspace_heater", helix::xml::IndexedSubjectPool::Type::Color, 0, "_color"};
    helix::xml::IndexedSubjectPool settings_labels_{
        "workspace_setting", helix::xml::IndexedSubjectPool::Type::String, 96};
    helix::xml::IndexedSubjectPool settings_enabled_{
        "workspace_setting_enabled", helix::xml::IndexedSubjectPool::Type::Int};
    helix::xml::IndexedSubjectPool labels_{"workspace_spool",
                                           helix::xml::IndexedSubjectPool::Type::String, 160};
    helix::xml::IndexedSubjectPool file_labels_{"workspace_file",
                                                helix::xml::IndexedSubjectPool::Type::String, 160};
};
MaterialWorkspace& get_material_workspace();
} // namespace helix::ui
