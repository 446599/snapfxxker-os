// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "overlay_base.h"
#include "ui_timer_guard.h"
#include "helix/xml/indexed_subject_pool.h"
#include "hv/json.hpp"

namespace helix::ui {
class PathCalibration : public OverlayBase {
  public:
    HELIX_KIND_TAG(PathCalibration)
    ~PathCalibration() override;
    const char* get_name() const override { return "ACE path calibration"; }
    const char* xml_component() const override { return "path_calibration"; }
    bool is_destination() const override { return true; }
    void init_subjects() override;
    void deinit_subjects();
    void register_callbacks() override;
    lv_obj_t* create(lv_obj_t* parent) override;
    void open(int ace = -1);
    void refresh();
    void act(const std::string& operation);

  private:
    friend struct PathCalibrationTestAccess;
    void publish(const nlohmann::json& snapshot);
    std::string next_id();
    SubjectManager subjects_;
    LvglTimerGuard poll_;
    nlohmann::json state_ = nlohmann::json::object();
    bool reading_ = false, sending_ = false, fresh_ = false;
    int ace_ = 0, slot_ = 0, head_hint_ = -1;
    unsigned int sequence_ = 0, generation_ = 0;
    std::string client_id_;
    lv_subject_t active_{}, available_{}, busy_{}, stage_{}, waiting_{}, selected_{}, step_{},
        forward_ready_{}, back_ready_{}, confirm_ready_{},
        can_save_{}, can_start_{}, can_continue_{}, source_confirm_ready_{},
        head_choices_visible_{}, selected_head_{};
    lv_subject_t title_{}, message_{}, position_{}, banner_{}, route_detail_{};
    char title_buf_[96]{}, message_buf_[512]{}, position_buf_[128]{}, banner_buf_[128]{}, route_detail_buf_[160]{};
    helix::xml::IndexedSubjectPool head_choices_{"path_calibration_head_choice",
        helix::xml::IndexedSubjectPool::Type::Int};
    helix::xml::IndexedSubjectPool buttons_{"path_calibration_button",
        helix::xml::IndexedSubjectPool::Type::String, 64};
};
PathCalibration& get_path_calibration();
} // namespace helix::ui
