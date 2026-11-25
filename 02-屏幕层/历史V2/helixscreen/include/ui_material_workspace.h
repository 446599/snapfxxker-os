// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "ui_observer_guard.h"
#include "ui_timer_guard.h"

#include "helix/xml/indexed_subject_pool.h"
#include "overlay_base.h"

#include "hv/json.hpp"

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
    void open(int head, int unit, bool library = false);

  private:
    void page(int value);
    void act(const std::string& action);
    void refresh();
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
    int head_ = 0, unit_ = 0, slot_ = -1;
    std::string spool_id_, firmware_token_;
    nlohmann::json spools_ = nlohmann::json::array(), files_ = nlohmann::json::array();
    std::string selected_file_;
    LvglTimerGuard poll_;
    ObserverGuard revision_observer_;
    std::string pending_action_;
    std::optional<int> confirmed_source_;
    nlohmann::json pending_args_;
    lv_subject_t page_{}, busy_{}, list_count_{}, file_count_{}, confirmed_{}, progress_{},
        ready_{}, auto_sensor_{}, settings_ready_{};
    lv_subject_t title_{}, status_{}, detail_{}, form_material_{}, form_color_{}, form_weight_{},
        form_vendor_{}, file_info_{}, version_{};
    char title_buf_[128]{}, status_buf_[512]{}, detail_buf_[512]{}, form_material_buf_[64]{},
        form_color_buf_[32]{}, form_weight_buf_[32]{}, form_vendor_buf_[64]{},
        file_info_buf_[512]{}, version_buf_[64]{};
    helix::xml::IndexedSubjectPool auto_values_{"workspace_auto",
                                                helix::xml::IndexedSubjectPool::Type::String, 32};
    helix::xml::IndexedSubjectPool settings_labels_{
        "workspace_setting", helix::xml::IndexedSubjectPool::Type::String, 96};
    helix::xml::IndexedSubjectPool labels_{"workspace_spool",
                                           helix::xml::IndexedSubjectPool::Type::String, 160};
    helix::xml::IndexedSubjectPool file_labels_{"workspace_file",
                                                helix::xml::IndexedSubjectPool::Type::String, 160};
};
MaterialWorkspace& get_material_workspace();
} // namespace helix::ui
