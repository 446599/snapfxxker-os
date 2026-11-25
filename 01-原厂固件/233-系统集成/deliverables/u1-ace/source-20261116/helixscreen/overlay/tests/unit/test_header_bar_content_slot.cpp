// Copyright (C) 2025-2026 356C LLC
// SPDX-License-Identifier: GPL-3.0-or-later

// Header injectors keep their children inside header_content, and the live
// header layout accommodates that slot alongside Back and page actions.

#include "../test_fixtures.h"
#include "core/update_queue.h"
#include "helix-xml/src/xml/lv_xml.h"
#include "ui_component_header_bar.h"

#include <lvgl.h>

#include "../catch_amalgamated.hpp"

using helix::ui::UpdateQueue;

namespace {

struct HeaderFixture : XMLTestFixture {
    lv_subject_t action_hidden{};
    static inline int action_clicks = 0;

    HeaderFixture() {
        ui_component_header_bar_init();
        action_clicks = 0;
        lv_subject_init_int(&action_hidden, 0);
        lv_xml_register_subject(nullptr, "test_header_action_hidden", &action_hidden);
        lv_xml_register_event_cb(nullptr, "test_header_action_clicked",
                                 [](lv_event_t*) { ++action_clicks; });
    }

    ~HeaderFixture() {
        lv_obj_clean(test_screen());
        lv_xml_unregister_subject(nullptr, "test_header_action_hidden");
        lv_subject_deinit(&action_hidden);
    }

    static void layout(lv_obj_t* header) {
        UpdateQueue::instance().drain();
        lv_obj_update_layout(header);
    }

    static lv_area_t bounds(lv_obj_t* object) {
        lv_area_t area{};
        REQUIRE(object != nullptr);
        lv_obj_get_coords(object, &area);
        return area;
    }

    static void check_left_back(lv_obj_t* header) {
        const auto frame = bounds(header);
        const auto title = bounds(lv_obj_find_by_name(header, "header_title_area"));
        const auto back = bounds(lv_obj_find_by_name(header, "back_button"));
        REQUIRE(back.x2 < title.x1);
        REQUIRE(back.x1 >= frame.x1);
        REQUIRE(back.x2 <= frame.x2);
        REQUIRE(lv_area_get_width(&back) >= 44);
        REQUIRE(lv_area_get_height(&back) >= 44);
        REQUIRE(title.x1 >= frame.x1);
    }
};

} // namespace

TEST_CASE_METHOD(HeaderFixture, "header_bar content slot exists and stays empty when unfilled",
                 "[header_bar][xml]") {
    REQUIRE(register_component("header_bar"));

    const char* attrs[] = {"title", "Tune", nullptr};
    lv_obj_t* hdr = create_component("header_bar", attrs);
    REQUIRE(hdr != nullptr);
    UpdateQueue::instance().drain();

    lv_obj_t* slot = lv_obj_find_by_name(hdr, "header_content");
    REQUIRE(slot != nullptr);
    // Opt-out guarantee: a header that injects nothing carries no children,
    // is visible, takes no layout width (content of nothing, no padding) and
    // does not steal taps from the back button's huge touch target.
    REQUIRE(lv_obj_get_child_count(slot) == 0);
    REQUIRE_FALSE(lv_obj_has_flag(slot, LV_OBJ_FLAG_HIDDEN));
    REQUIRE_FALSE(lv_obj_has_flag(slot, LV_OBJ_FLAG_CLICKABLE));
    lv_obj_update_layout(slot);
    REQUIRE(lv_obj_get_width(slot) == 0);

    check_left_back(hdr);
}

TEST_CASE_METHOD(HeaderFixture, "header_bar content slot re-parents injected children",
                 "[header_bar][xml]") {
    REQUIRE(register_component("header_bar"));

    // A screen filling the slot the way motion_panel.xml does: the
    // header_bar instantiation nests a <header_bar-header_content> tag and
    // the engine routes that tag's children into the named slot widget.
    REQUIRE(lv_xml_register_component_from_data(
                "slot_filler_screen",
                "<component>"
                "<view name=\"slot_filler_screen\" extends=\"lv_obj\" width=\"480\" height=\"320\">"
                "<header_bar name=\"injected_header\" title=\"Tune\" action_button_text=\"Apply\" "
                "hide_action_button=\"false\">"
                "<header_bar-header_content>"
                "<lv_label name=\"injected_coord\" text=\"X 0.00\"/>"
                "</header_bar-header_content>"
                "</header_bar>"
                "</view>"
                "</component>") == LV_RESULT_OK);

    lv_obj_t* screen = create_component("slot_filler_screen");
    REQUIRE(screen != nullptr);
    UpdateQueue::instance().drain();

    lv_obj_t* slot = lv_obj_find_by_name(screen, "header_content");
    lv_obj_t* injected = lv_obj_find_by_name(screen, "injected_coord");
    REQUIRE(slot != nullptr);
    REQUIRE(injected != nullptr);
    // The whole point of the slot: injected content renders inside it, not
    // next to the header_bar instantiation.
    REQUIRE(lv_obj_get_parent(injected) == slot);
    REQUIRE(lv_obj_get_child_count(slot) == 1);
    auto* header = lv_obj_find_by_name(screen, "injected_header");
    REQUIRE(header != nullptr);
    layout(header);
    check_left_back(header);
    const auto title = bounds(lv_obj_find_by_name(header, "header_title_area"));
    const auto readout = bounds(injected);
    const auto action = bounds(lv_obj_find_by_name(header, "action_button"));
    CHECK(title.x2 < readout.x1);
    CHECK(readout.x2 < action.x1);
}

TEST_CASE_METHOD(HeaderFixture, "header_bar keeps Back on the left as actions appear and hide",
                 "[header_bar][xml]") {
    REQUIRE(register_component("header_bar"));

    const int width = GENERATE(480, 800);
    CAPTURE(width);
    const char* attrs[] = {"title", "Tune",
                           "action_button_text", "Apply",
                           "action_button_width", "132",
                           "hide_action_button", "false",
                           "action_button_hidden_subject", "test_header_action_hidden",
                           "action_button_callback", "test_header_action_clicked",
                           nullptr};
    lv_obj_t* header = create_component("header_bar", attrs);
    REQUIRE(header != nullptr);
    lv_obj_set_width(header, width);
    auto* leading = lv_obj_find_by_name(header, "header_leading_slot");
    auto* trailing = lv_obj_find_by_name(header, "header_trailing_slot");
    auto* action = lv_obj_find_by_name(header, "action_button");
    REQUIRE(leading != nullptr);
    REQUIRE(trailing != nullptr);
    REQUIRE(action != nullptr);

    for (int hidden : {0, 1, 0}) {
        CAPTURE(hidden);
        lv_subject_set_int(&action_hidden, hidden);
        layout(header);
        check_left_back(header);
        CHECK(lv_obj_has_flag(action, LV_OBJ_FLAG_HIDDEN) == (hidden != 0));
        CHECK(lv_obj_get_width(leading) >= 44);
        if (hidden != 0) {
            const auto back = bounds(lv_obj_find_by_name(header, "back_button"));
            CHECK(lv_obj_get_width(leading) == lv_area_get_width(&back));
            continue;
        }

        const auto title = bounds(lv_obj_find_by_name(header, "header_title_area"));
        const auto target = bounds(action);
        const auto side = bounds(trailing);
        CHECK(title.x2 < target.x1);
        CHECK(side.x1 <= target.x1);
        CHECK(side.x2 >= target.x2);
        CHECK(side.y1 <= target.y1);
        CHECK(side.y2 >= target.y2);
        CHECK(lv_area_get_width(&target) >= 44);
        CHECK(lv_area_get_height(&target) >= 44);
        CHECK(lv_obj_has_flag(action, LV_OBJ_FLAG_CLICKABLE));
        const int prior_clicks = action_clicks;
        lv_obj_send_event(action, LV_EVENT_CLICKED, nullptr);
        CHECK(action_clicks == prior_clicks + 1);
    }
}
