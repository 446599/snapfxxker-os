// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include "helix/xml/indexed_subject_pool.h"
#include "overlay_base.h"
#include "hv/json.hpp"

#include <string>
#include <vector>

namespace helix::ui {

// Native presentation of the settings advertised by the local firmware provider.
// The provider owns vendor IDs, capability filtering and application semantics.
class FirmwareSettings : public OverlayBase {
  public:
    HELIX_KIND_TAG(FirmwareSettings)
    ~FirmwareSettings() override;
    const char* get_name() const override { return "Firmware Settings"; }
    const char* xml_component() const override { return "firmware_settings"; }
    bool is_destination() const override { return true; }
    void init_subjects() override;
    void register_callbacks() override;
    lv_obj_t* create(lv_obj_t* parent) override;
    void open();
    void deinit_subjects();

  private:
    void refresh(bool after_apply = false);
    void rebuild_groups();
    void choose_group(int index);
    void choose_setting(int index);
    void choose_option(int index);
    void review();
    void apply();
    void back();
    void show_output();
    void set_page(int page, const std::string& title);
    std::string input(const std::string& name) const;
    std::string current_label(const nlohmann::json& setting) const;

    SubjectManager subjects_;
    nlohmann::json groups_ = nlohmann::json::array();
    nlohmann::json settings_ = nlohmann::json::array();
    nlohmann::json options_ = nlohmann::json::array();
    nlohmann::json inputs_ = nlohmann::json::array();
    nlohmann::json pending_ = nlohmann::json::object();
    std::vector<int> visible_settings_;
    int group_index_ = -1;
    int setting_index_ = -1;
    bool applying_ = false;
    std::string output_;

    lv_subject_t page_{}, busy_{}, ready_{}, group_count_{}, setting_count_{}, option_count_{},
        input_count_{}, selected_option_{}, dirty_{}, has_output_{};
    lv_subject_t title_{}, status_{}, description_{}, current_{}, review_{}, output_text_{};
    char title_buf_[160]{}, status_buf_[768]{}, description_buf_[8192]{}, current_buf_[256]{},
        review_buf_[16384]{}, output_buf_[32768]{};

    helix::xml::IndexedSubjectPool group_labels_{
        "firmware_group_label", helix::xml::IndexedSubjectPool::Type::String, 128};
    helix::xml::IndexedSubjectPool setting_labels_{
        "firmware_setting_label", helix::xml::IndexedSubjectPool::Type::String, 160};
    helix::xml::IndexedSubjectPool setting_values_{
        "firmware_setting_value", helix::xml::IndexedSubjectPool::Type::String, 160};
    helix::xml::IndexedSubjectPool option_labels_{
        "firmware_option_label", helix::xml::IndexedSubjectPool::Type::String, 192};
    helix::xml::IndexedSubjectPool input_labels_{
        "firmware_input_label", helix::xml::IndexedSubjectPool::Type::String, 256};
    helix::xml::IndexedSubjectPool input_values_{
        "firmware_input_value", helix::xml::IndexedSubjectPool::Type::String, 1024};
};

FirmwareSettings& get_firmware_settings();
} // namespace helix::ui
