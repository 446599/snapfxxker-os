// SPDX-License-Identifier: GPL-3.0-or-later
#include "../lvgl_ui_test_fixture.h"
#include "../test_helpers/backend_harness.h"
#include "../catch_amalgamated.hpp"
#include "ams_backend_multiace.h"
#include "ams_head_presence.h"
#include "ams_state.h"
#include "app_globals.h"
#include "printer_state.h"
#include "ui_head_inventory.h"
#include "ui_nav.h"
#include "ui_nav_manager.h"
#include "../test_helpers/scoped_runtime_config.h"
#include "../test_helpers/scoped_env.h"
#include "../test_helpers/scoped_moonraker_api.h"
#include "../test_helpers/tool_switcher_rig.h"
#include "path_calibration_demo.h"
#include "klippy_state.h"
#include "tool_state.h"
#include "printer_discovery.h"
#include <fstream>

namespace {
class InventoryFixture : public LVGLUITestFixture {
  protected:
    InventoryFixture() {
        auto& tools = helix::ToolState::instance();
        tools.deinit_subjects();
        tools.init_subjects(true);
        helix::PrinterDiscovery hardware;
        hardware.parse_objects(nlohmann::json::array({"toolchanger", "tool T0", "tool T1", "tool T2", "tool T3",
            "extruder", "extruder1", "extruder2", "extruder3"}));
        tools.init_tools(hardware);
        tools.update_from_status({{"toolchanger", {{"tool_number", 0}}}});
        auto* base_panel = lv_obj_create(test_screen());
        lv_obj_t* panels[kUiPanelCount]{};
        panels[static_cast<int>(helix::PanelId::Home)] = base_panel;
        NavigationManager::instance().set_panels(panels);
        auto& ams = helix::AmsState::instance();
        ams.deinit_subjects();
        ams.init_subjects(true);
        lv_subject_set_int(get_printer_state().network_state().get_klippy_state_subject(),
            static_cast<int>(helix::KlippyState::READY));
        lv_subject_set_int(get_printer_state().network_state().get_nav_buttons_enabled_subject(), 1);
    }
    ~InventoryFixture() override {
        helix::AmsState::instance().deinit_subjects();
        helix::ToolState::instance().deinit_subjects();
    }
    void settle() {
        for (int pass = 0; pass < 5; ++pass) {
            helix::ui::UpdateQueue::instance().drain_until_empty();
            process_lvgl(100);
        }
        helix::ui::UpdateQueue::instance().drain_until_empty();
    }
};
} // namespace

TEST_CASE_METHOD(InventoryFixture,
                 "head inventory exposes every matching ACE slot and exact source",
                 "[material-workspace][head-inventory]") {
    helix::test::RegisteredWired<helix::AmsBackendMultiAce> backend;
    nlohmann::json ace;
    std::ifstream("tests/fixtures/multiace/head_mode_2ace_2feeder.json") >> ace;
    ace["head_source"]["0"]["slot"] = 2;
    ace["head_physical_source"] = {{"0", {{"ace_index", 0}, {"slot", 2}}}};
    ace["head_presence"] = {{"0", true}, {"1", false}, {"2", false}, {"3", nullptr}};
    backend->fire_status({{"ace", ace}});
    helix::ui::UpdateQueue::instance().drain_until_empty();
    REQUIRE(backend->head_physical_presence(0) == true);
    CHECK_FALSE(backend->slot_is_actively_loaded(0));
    lv_subject_set_int(get_printer_state().network_state().get_nav_buttons_enabled_subject(), 1);
    helix::ui::HeadInventory inventory;
    REQUIRE(inventory.show(test_screen()));
    settle();
    REQUIRE(inventory.is_visible());
    auto* by_head = lv_obj_find_by_name(test_screen(), "inventory_view_head");
    REQUIRE(by_head != nullptr);
    lv_obj_send_event(by_head, LV_EVENT_CLICKED, nullptr);
    settle();
    auto* count = lv_xml_get_subject(nullptr, "inventory_card_count");
    REQUIRE(count != nullptr);
    CHECK(lv_subject_get_int(count) == 4);
    for (int slot = 0; slot < 4; ++slot) {
        const std::string name = "inventory_card_" + std::to_string(slot);
        REQUIRE(lv_obj_find_by_name(test_screen(), name.c_str()) != nullptr);
        auto* label = lv_xml_get_subject(nullptr, ("inventory_unit_" + std::to_string(slot) + "_name").c_str());
        REQUIRE(label != nullptr);
        CHECK(std::string(lv_subject_get_string(label)).find(std::to_string(slot + 1) + "槽") != std::string::npos);
    }
    for (const int slot : {1, 2, 3}) {
        auto* card = lv_obj_find_by_name(test_screen(), ("inventory_card_" + std::to_string(slot)).c_str());
        REQUIRE(card != nullptr);
        lv_obj_send_event(card, LV_EVENT_CLICKED, nullptr);
        settle();
        auto* detail = lv_xml_get_subject(nullptr, "workspace_detail");
        REQUIRE(detail != nullptr);
        CHECK(std::string(lv_subject_get_string(detail)).find(std::to_string(slot + 1) + " 槽") != std::string::npos);
        helix::nav::go_back();
        settle();
        auto* selection = lv_xml_get_subject(nullptr, "inventory_selection");
        REQUIRE(selection != nullptr);
        const std::string selected = lv_subject_get_string(selection);
        CHECK((selected.find("当前供料") != std::string::npos) == (slot == 2));
    }
    auto* by_ace = lv_obj_find_by_name(test_screen(), "inventory_view_ace");
    REQUIRE(by_ace != nullptr);
    lv_obj_send_event(by_ace, LV_EVENT_CLICKED, nullptr);
    settle();
    auto* first = lv_obj_find_by_name(test_screen(), "inventory_card_0");
    REQUIRE(first != nullptr);
    lv_obj_send_event(first, LV_EVENT_CLICKED, nullptr);
    settle();
    helix::nav::go_back();
    settle();
    auto* second_ace = lv_obj_find_by_name(test_screen(), "inventory_head_1");
    REQUIRE(second_ace != nullptr);
    lv_obj_send_event(second_ace, LV_EVENT_CLICKED, nullptr);
    settle();
    auto* details = lv_obj_find_by_name(test_screen(), "inventory_details");
    REQUIRE(details != nullptr);
    lv_obj_send_event(details, LV_EVENT_CLICKED, nullptr);
    settle();
    helix::nav::go_back();
    settle();
    CHECK(lv_subject_get_int(lv_xml_get_subject(nullptr, "inventory_selected_unit")) == 1);
    CHECK(lv_subject_get_int(lv_xml_get_subject(nullptr, "inventory_selected_card")) == 0);
    CHECK(lv_subject_get_int(lv_xml_get_subject(nullptr, "inventory_selected_head")) == 1);
    lv_obj_send_event(lv_obj_find_by_name(test_screen(), "inventory_head_0"), LV_EVENT_CLICKED, nullptr);
    settle();
    backend->fire_status({{"ace", {{"head_presence", {{"0", nullptr}}}}}});
    settle();
    auto* source = lv_xml_get_subject(nullptr, "inventory_source");
    REQUIRE(source != nullptr);
    CHECK(std::string(lv_subject_get_string(source)).find("到料待核") != std::string::npos);
    CHECK_FALSE(backend->head_physical_presence(0).has_value());
    backend->fire_status({{"ace", {{"head_presence", {{"0", true}}},
        {"head_physical_source", {{"0", {{"ace_index", 0}, {"slot", 2}}}}}}}});
    settle();
    REQUIRE(backend->head_physical_source(0) == 6);
    backend->on_connection_lost();
    settle();
    CHECK_FALSE(backend->head_physical_presence(0).has_value());
    CHECK_FALSE(backend->head_physical_source(0).has_value());
    backend->fire_status({{"ace", {{"api_version", 1}, {"mode", "head"}}}});
    settle();
    CHECK_FALSE(backend->head_physical_presence(0).has_value());
    helix::nav::go_back();
    settle();
}

TEST_CASE_METHOD(InventoryFixture,
                 "gapped ACE ids and raw-only presence reach the normal UI entry points",
                 "[material-workspace][head-inventory][head_presence]") {
    helix::test::RegisteredWired<helix::AmsBackendMultiAce> backend;
    ScopedMoonrakerApi api(backend->wire().api());
    backend->wire().respond_to("printer.gcode.script", nlohmann::json::object());
    nlohmann::json ace;
    std::ifstream("tests/fixtures/multiace/head_mode_4ace.json") >> ace;
    nlohmann::json units = nlohmann::json::array();
    for (const auto& unit : ace["aces"])
        if (unit["idx"] == 0 || unit["idx"] == 2) units.push_back(unit);
    ace["aces"] = units;
    ace["head_ace"] = {{"0", 0}, {"1", -1}, {"2", 2}, {"3", -1}};
    ace["head_feeder"] = {{"0", false}, {"1", true}, {"2", false}, {"3", true}};
    ace["head_source"] = {{"2", {{"ace_index", 2}, {"slot", 1}}}};
    ace["head_physical_source"] = {{"2", {{"ace_index", 2}, {"slot", 1}}}};
    ace["head_presence"] = {{"0", false}, {"1", false}, {"2", true}, {"3", false}};
    backend->fire_status({{"ace", ace}});
    settle();
    REQUIRE(backend->head_physical_source(2) == 13);
    CHECK_FALSE(backend->slot_is_actively_loaded(2));
    helix::register_tool_switcher_widget();
    helix::PanelWidgetHarness<helix::ToolSwitcherWidget> widget(test_screen(), state());
    helix::resize_content(widget, 392, 56);
    settle();
    auto* badge = lv_xml_get_subject(nullptr, "tool_switcher_seg_2_badge");
    REQUIRE(badge != nullptr);
    CHECK(std::string(lv_subject_get_string(badge)).find("A3·2") != std::string::npos);
    const int slot_version = lv_subject_get_int(helix::AmsState::instance().get_slots_version_subject());
    backend->fire_status({{"ace", {{"head_presence", {{"2", false}}}}}});
    settle();
    CHECK(lv_subject_get_int(helix::AmsState::instance().get_slots_version_subject()) == slot_version);
    CHECK(std::string(lv_subject_get_string(badge)).find("空") != std::string::npos);
    backend->fire_status({{"ace", {{"head_presence", {{"2", true}}}}}});
    settle();
    CHECK_FALSE(backend->head_physical_source(2).has_value());
    CHECK(std::string(lv_subject_get_string(badge)).find("到料") != std::string::npos);
    backend->fire_status({{"ace", {{"head_presence", {{"2", nullptr}}}}}});
    settle();
    CHECK(std::string(lv_subject_get_string(badge)).find("待核") != std::string::npos);

    helix::ui::HeadInventory inventory;
    REQUIRE(inventory.show(test_screen()));
    settle();
    REQUIRE(inventory.is_visible());
    auto* unit = lv_obj_find_by_name(test_screen(), "inventory_head_1");
    REQUIRE(unit != nullptr);
    lv_obj_send_event(unit, LV_EVENT_CLICKED, nullptr);
    settle();
    ScopedRuntimeConfig runtime;
    get_runtime_config()->test_mode = true;
    helix::ScopedEnv printer("HELIX_MOCK_PRINTER", "snapmaker_u1");
    auto* read = lv_obj_find_by_name(test_screen(), "inventory_read_tag");
    REQUIRE(read != nullptr);
    lv_obj_send_event(read, LV_EVENT_CLICKED, nullptr);
    settle();
    const auto* sent = backend->wire().fake_client().last_rpc("printer.gcode.script");
    REQUIRE(sent != nullptr);
    CHECK(sent->params["script"] == "ACE_SLOT_RFID ACE=2 SLOT=0");
    helix::nav::go_back();
    settle();
    helix::ScopedEnv mode("HELIX_MOCK_MULTIACE_MODE", "head");
    auto* calibrate = lv_obj_find_by_name(test_screen(), "inventory_calibrate");
    REQUIRE(calibrate != nullptr);
    lv_obj_send_event(calibrate, LV_EVENT_CLICKED, nullptr);
    settle();
    auto* title = lv_xml_get_subject(nullptr, "path_calibration_title");
    REQUIRE(title != nullptr);
    CHECK(std::string(lv_subject_get_string(title)).find("ACE 3") != std::string::npos);
    auto* prepare = lv_obj_find_by_name(test_screen(), "path_calibration_prepare");
    REQUIRE(prepare != nullptr);
    lv_obj_send_event(prepare, LV_EVENT_CLICKED, nullptr);
    settle();
    CHECK(helix::material_workspace::path_calibration_demo_state()["ace"] == 2);
    auto* cancel = lv_obj_find_by_name(test_screen(), "path_calibration_cancel");
    REQUIRE(cancel != nullptr);
    lv_obj_send_event(cancel, LV_EVENT_CLICKED, nullptr);
    settle();
    helix::nav::go_back();
    settle();
    helix::nav::go_back();
    settle();
}
