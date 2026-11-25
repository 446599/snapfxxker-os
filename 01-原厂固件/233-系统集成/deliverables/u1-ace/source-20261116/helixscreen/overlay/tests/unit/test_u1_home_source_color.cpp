// SPDX-License-Identifier: GPL-3.0-or-later

#include "../lvgl_ui_test_fixture.h"
#include "../test_helpers/backend_user_edit.h"
#include "../test_helpers/tool_switcher_rig.h"
#include "../catch_amalgamated.hpp"
#include "app_globals.h"
#include "printer_state.h"

using namespace helix;

TEST_CASE_METHOD(LVGLUITestFixture,
                 "head color follows the reported upstream spool",
                 "[tool_switcher][band][u1_home]") {
    ToolChangerRig rig(4, 8);
    lv_subject_set_int(get_printer_state().network_state().get_nav_buttons_enabled_subject(), 1);
    SECTION("topology supplied backend slots") {
        ToolTopology topology;
        topology.tool_count = 4;
        topology.active_tool = 0;
        topology.tool_to_slot = {0, 1, 2, 3};
        ToolState::instance().set_ams_topology(topology);
        REQUIRE(ToolState::instance().tools().at(0).backend_slot >= 0);
    }
    SECTION("fixed U1 heads discovered separately from their feeders") {
        rig.backend->report_head_inventory = true;
        auto& tools = ToolState::instance();
        tools.clear_ams_topology();
        PrinterDiscovery hardware;
        hardware.parse_objects(nlohmann::json::array(
            {"toolchanger", "heater_bed", "gcode_move", "tool T0", "tool T1", "tool T2",
             "tool T3", "extruder", "extruder1", "extruder2", "extruder3"}));
        tools.init_tools(hardware);
        tools.update_from_status({{"toolchanger", {{"tool_number", 0}}}});
        REQUIRE(tools.tools().at(0).backend_slot == -1);
    }
    rig.backend->spool_sources = {4, -1, -1, -1};
    auto own = rig.backend->get_slot_info(0);
    own.color_rgb = 0x0000FF;
    own.material = "PETG";
    REQUIRE(helix::test::apply_edit(*rig.backend, 0, own).success());
    auto source = rig.backend->get_slot_info(4);
    source.color_rgb = 0xFF0000;
    source.material = "PLA";
    REQUIRE(helix::test::apply_edit(*rig.backend, 4, source).success());
    process_lvgl(30);

    PanelWidgetHarness<ToolSwitcherWidget> widget(test_screen(), state());
    resize_content(widget, 392, 56);
    process_lvgl(30);
    auto* color = lv_xml_get_subject(nullptr, "tool_switcher_seg_0_color");
    auto* material = lv_xml_get_subject(nullptr, "tool_switcher_seg_0_material");
    REQUIRE(color != nullptr);
    REQUIRE(material != nullptr);
    CHECK((lv_color_to_u32(lv_subject_get_color(color)) & 0xFFFFFF) == 0xFF0000);
    CHECK(std::string(lv_subject_get_string(material)) == "PLA");

    rig.backend->spool_sources[0] = -1;
    resize_content(widget, 393, 56);
    process_lvgl(30);
    if (rig.backend->report_head_inventory) {
        auto* has_color = lv_xml_get_subject(nullptr, "tool_switcher_seg_0_has_color");
        REQUIRE(has_color != nullptr);
        CHECK(lv_subject_get_int(has_color) == 0);
        CHECK(std::string(lv_subject_get_string(material)).empty());
    } else {
        CHECK((lv_color_to_u32(lv_subject_get_color(color)) & 0xFFFFFF) == 0x0000FF);
        CHECK(std::string(lv_subject_get_string(material)) == "PETG");
    }
}

TEST_CASE_METHOD(LVGLUITestFixture,
                 "head physical presence remains independent of printable load state",
                 "[tool_switcher][u1_home][head_presence]") {
    ToolChangerRig rig(4, 8);
    rig.backend->report_head_inventory = true;
    rig.backend->loaded_override = false;
    rig.backend->spool_sources = {-1, -1, -1, -1};
    rig.backend->physical_presence[0] = true;
    lv_subject_set_int(get_printer_state().network_state().get_nav_buttons_enabled_subject(), 1);
    PanelWidgetHarness<ToolSwitcherWidget> widget(test_screen(), state());
    resize_content(widget, 392, 56);
    process_lvgl(30);
    auto* badge = lv_xml_get_subject(nullptr, "tool_switcher_seg_0_badge");
    REQUIRE(badge != nullptr);
    CHECK_FALSE(rig.backend->slot_is_actively_loaded(0));
    CHECK(std::string(lv_subject_get_string(badge)).find("到料") != std::string::npos);
    rig.backend->physical_presence[0] = std::nullopt;
    resize_content(widget, 393, 56);
    process_lvgl(30);
    CHECK(std::string(lv_subject_get_string(badge)).find("待核") != std::string::npos);
    rig.backend->physical_presence[0] = false;
    resize_content(widget, 394, 56);
    process_lvgl(30);
    CHECK(std::string(lv_subject_get_string(badge)).find("空") != std::string::npos);
}
