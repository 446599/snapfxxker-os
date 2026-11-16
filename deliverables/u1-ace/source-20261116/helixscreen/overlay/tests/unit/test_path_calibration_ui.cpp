// SPDX-License-Identifier: GPL-3.0-or-later
#include "../lvgl_ui_test_fixture.h"
#include "../test_helpers/scoped_runtime_config.h"
#include "../test_helpers/scoped_env.h"
#include "../catch_amalgamated.hpp"
#include "core/update_queue.h"
#include "material_workspace.h"
#include "ui_path_calibration.h"
#include "path_calibration_demo.h"
#include "ui_nav.h"

TEST_CASE_METHOD(LVGLUITestFixture,
                 "calibration UI reads its task from the actual workspace state entry",
                 "[path_calibration][material-workspace]") {
    ScopedRuntimeConfig runtime;
    get_runtime_config()->test_mode = true;
    helix::ScopedEnv printer("HELIX_MOCK_PRINTER", "snapmaker_u1");
    nlohmann::json snapshot;
    std::string error;
    helix::material_workspace::request("/state", {}, [&](auto data, auto reason) {
        snapshot = data;
        error = reason;
    });
    helix::ui::UpdateQueue::instance().drain_until_empty();
    REQUIRE(error.empty());
    REQUIRE(snapshot.contains("path_calibration"));
    REQUIRE(snapshot["path_calibration"].is_object());
    CHECK(snapshot["path_calibration"]["available"] == true);

    auto& calibration = helix::ui::get_path_calibration();
    calibration.deinit_subjects();
    calibration.init_subjects();
    REQUIRE(lv_xml_get_subject(nullptr, "path_calibration_button_0") != nullptr);
    calibration.refresh();
    helix::ui::UpdateQueue::instance().drain_until_empty();
    auto* available = lv_xml_get_subject(nullptr, "path_calibration_available");
    REQUIRE(available != nullptr);
    CHECK(lv_subject_get_int(available) == 1);
    calibration.act("prepare");
    helix::ui::UpdateQueue::instance().drain_until_empty();
    auto* active = lv_xml_get_subject(nullptr, "path_calibration_active");
    REQUIRE(active != nullptr);
    CHECK(lv_subject_get_int(active) == 1);
    helix::material_workspace::request("/state", {}, [&](auto data, auto reason) {
        snapshot = data;
        error = reason;
    });
    helix::ui::UpdateQueue::instance().drain_until_empty();
    REQUIRE(error.empty());
    for (const auto& lane : snapshot["path_calibration"]["lanes"]) CHECK(lane["included"] == true);
    calibration.act("cancel");
    helix::ui::UpdateQueue::instance().drain_until_empty();
    CHECK(lv_subject_get_int(active) == 0);
}

namespace helix::ui {
struct PathCalibrationTestAccess {
    static void publish(PathCalibration& calibration, const nlohmann::json& snapshot) {
        calibration.head_hint_ = -1;
        calibration.fresh_ = true;
        calibration.publish(snapshot);
    }
};
} // namespace helix::ui

TEST_CASE_METHOD(LVGLUITestFixture,
                 "head-mode calibration requires an explicit ambiguous target",
                 "[path_calibration][material-workspace]") {
    ScopedRuntimeConfig runtime;
    get_runtime_config()->test_mode = true;
    helix::ScopedEnv printer("HELIX_MOCK_PRINTER", "snapmaker_u1");
    helix::ScopedEnv mode("HELIX_MOCK_MULTIACE_MODE", "head");
    auto& calibration = helix::ui::get_path_calibration();
    calibration.open(0);
    helix::ui::UpdateQueue::instance().drain_until_empty();
    auto snapshot = helix::material_workspace::path_calibration_demo_state();
    snapshot.update({{"mode", "head"}, {"available", true}, {"active", false}, {"busy", false},
        {"ace", 0}, {"capable_aces", {0}}, {"heads_by_ace", {{"0", {0, 2}}}}});
    helix::ui::PathCalibrationTestAccess::publish(calibration, snapshot);
    process_lvgl(20);
    auto* can_start = lv_xml_get_subject(nullptr, "path_calibration_can_start");
    REQUIRE(can_start != nullptr);
    CHECK(lv_subject_get_int(can_start) == 0);
    auto* head = lv_obj_find_by_name(test_screen(), "path_calibration_head_2");
    REQUIRE(head != nullptr);
    lv_obj_send_event(head, LV_EVENT_CLICKED, nullptr);
    process_lvgl(20);
    CHECK(lv_subject_get_int(can_start) == 1);
    auto* detail = lv_xml_get_subject(nullptr, "path_calibration_route_detail");
    REQUIRE(detail != nullptr);
    CHECK(std::string(lv_subject_get_string(detail)).find("喷头 3") != std::string::npos);
    calibration.act("prepare");
    helix::ui::UpdateQueue::instance().drain_until_empty();
    const auto prepared = helix::material_workspace::path_calibration_demo_state();
    for (const auto& lane : prepared["lanes"]) {
        CHECK(lane["included"] == true);
        CHECK(lane["head"] == 2);
    }
    calibration.act("cancel");
    helix::ui::UpdateQueue::instance().drain_until_empty();
    helix::nav::go_back();
    process_lvgl(20);
}
