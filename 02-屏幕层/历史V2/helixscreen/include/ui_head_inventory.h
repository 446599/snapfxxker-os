// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include "ui_observer_guard.h"

#include "helix/xml/indexed_subject_pool.h"
#include "overlay_base.h"

namespace helix::ui {

class HeadInventory : public OverlayBase {
  public:
    HELIX_KIND_TAG(HeadInventory)
    ~HeadInventory() override;
    const char* get_name() const override {
        return "Head inventory";
    }
    const char* xml_component() const override {
        return "head_inventory";
    }
    bool is_destination() const override {
        return true;
    }
    void init_subjects() override;
    void register_callbacks() override;
    lv_obj_t* create(lv_obj_t* parent) override;
    void on_activate() override;
    void on_deactivating(DeactivateReason reason) override;

  private:
    SubjectManager subjects_;
    void refresh();
    void select_head(int head);
    void select_unit(int unit);
    void draw_routes(lv_event_t* event);
    int head_ = 0;
    int selected_unit_ = -1;
    ObserverGuard revision_observer_;
    ObserverGuard connection_observer_;
    lv_subject_t selected_head_{}, selected_unit_subject_{}, unit_count_{}, hold_{}, online_{};
    lv_subject_t source_{}, selection_{}, head_title_{}, notice_{};
    lv_subject_t toolhead_image_{};
    char toolhead_image_buf_[256]{};
    char source_buf_[128]{}, selection_buf_[160]{}, head_title_buf_[32]{}, notice_buf_[128]{};
    helix::xml::IndexedSubjectPool head_names_{"inventory_head",
                                               helix::xml::IndexedSubjectPool::Type::String};
    helix::xml::IndexedSubjectPool names_{
        "inventory_unit", helix::xml::IndexedSubjectPool::Type::String, 64, "_name"};
    helix::xml::IndexedSubjectPool env_{"inventory_unit",
                                        helix::xml::IndexedSubjectPool::Type::String, 64, "_env"};
    helix::xml::IndexedSubjectPool materials_{
        "inventory_unit", helix::xml::IndexedSubjectPool::Type::String, 64, "_material"};
    helix::xml::IndexedSubjectPool weights_{
        "inventory_unit", helix::xml::IndexedSubjectPool::Type::String, 64, "_weight"};
    helix::xml::IndexedSubjectPool percentages_{
        "inventory_unit", helix::xml::IndexedSubjectPool::Type::String, 32, "_percent"};
    helix::xml::IndexedSubjectPool colors_{
        "inventory_unit", helix::xml::IndexedSubjectPool::Type::Color, 0, "_color"};
    helix::xml::IndexedSubjectPool fills_{"inventory_unit",
                                          helix::xml::IndexedSubjectPool::Type::Int, 0, "_fill"};
    helix::xml::IndexedSubjectPool current_{
        "inventory_unit", helix::xml::IndexedSubjectPool::Type::Int, 0, "_current"};
};

HeadInventory& get_head_inventory();
} // namespace helix::ui
