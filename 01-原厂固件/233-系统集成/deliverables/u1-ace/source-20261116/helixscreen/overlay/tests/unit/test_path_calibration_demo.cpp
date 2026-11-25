// SPDX-License-Identifier: GPL-3.0-or-later
#include "path_calibration_demo.h"
#include "../catch_amalgamated.hpp"

using helix::material_workspace::path_calibration_demo_action;
using helix::material_workspace::path_calibration_demo_state;

TEST_CASE("path calibration preview follows two-stage task and action identity", "[path_calibration]") {
    int sequence = 0;
    auto run = [&](const char* operation, int slot = 0, int step = 10) {
        auto state = path_calibration_demo_state();
        return path_calibration_demo_action({{"OP", operation}, {"TASK_ID", "preview_task"},
            {"ACTION_ID", "preview_action_" + std::to_string(++sequence)},
            {"EXPECTED_VERSION", state["version"]}, {"ACE", 1},
            {"SLOT", std::string(operation) == "prepare" ? -1 : slot}, {"STEP", step}});
    };
    REQUIRE(run("prepare").empty());
    CHECK(path_calibration_demo_state()["ace"] == 1);
    CHECK(path_calibration_demo_state()["stage"] == "measuring");
    REQUIRE(run("insert").empty());
    REQUIRE(run("insert").empty());
    REQUIRE(run("skip").empty());
    CHECK(path_calibration_demo_state()["stage"] == "measuring");
    REQUIRE(run("insert").empty());
    CHECK(path_calibration_demo_state()["stage"] == "marking");
    CHECK_FALSE(path_calibration_demo_state()["position_trusted"].get<bool>());
    REQUIRE(run("select", 0).empty());
    const double original = path_calibration_demo_state()["position_mm"];
    REQUIRE(run("jog_back", 0, 50).empty());
    CHECK(path_calibration_demo_state()["position_mm"].get<double>() == original - 50);
    REQUIRE(run("save").empty());
    CHECK(path_calibration_demo_state()["lanes"][0]["park_mm"].get<double>() == original - 50);
    CHECK(path_calibration_demo_state()["lanes"][2]["status"] == "skipped");

    const auto snapshot = path_calibration_demo_state();
    const nlohmann::json command = {{"OP", "jog_back"}, {"TASK_ID", "preview_task"},
        {"ACTION_ID", "repeat_motion"}, {"EXPECTED_VERSION", snapshot["version"]}, {"STEP", 10}};
    REQUIRE(path_calibration_demo_action(command).empty());
    const double stopped = path_calibration_demo_state()["position_mm"];
    REQUIRE(path_calibration_demo_action(command).empty());
    CHECK(path_calibration_demo_state()["position_mm"].get<double>() == stopped);
    auto conflict = command;
    conflict["STEP"] = 100;
    CHECK_FALSE(path_calibration_demo_action(conflict).empty());

    CHECK(path_calibration_demo_action({{"OP", "stop"}, {"TASK_ID", "preview_task"},
        {"ACTION_ID", "late_version_stop"}, {"EXPECTED_VERSION", -1}}).empty());
    CHECK_FALSE(path_calibration_demo_action({{"OP", "cancel"}, {"TASK_ID", "old_task"},
        {"ACTION_ID", "late_task_cancel"}, {"EXPECTED_VERSION", -1}}).empty());
    REQUIRE(run("cancel").empty());
    CHECK_FALSE(path_calibration_demo_state()["active"].get<bool>());
    CHECK(path_calibration_demo_state()["lanes"][0]["status"] == "complete");
    const auto saved_park = path_calibration_demo_state()["lanes"][0]["park_mm"];
    const auto before_single = path_calibration_demo_state();
    REQUIRE(path_calibration_demo_action({{"OP", "prepare"}, {"TASK_ID", "single_task"},
        {"ACTION_ID", "single_prepare"}, {"EXPECTED_VERSION", before_single["version"]},
        {"ACE", 1}, {"SLOT", 2}, {"HEAD", -1}}).empty());
    CHECK(path_calibration_demo_state()["slot"] == 2);
    CHECK(path_calibration_demo_state()["lanes"][0]["included"] == false);
    CHECK(path_calibration_demo_state()["lanes"][2]["included"] == true);
    CHECK(path_calibration_demo_state()["lanes"][0]["park_mm"] == saved_park);
    REQUIRE(path_calibration_demo_action({{"OP", "cancel"}, {"TASK_ID", "single_task"},
        {"ACTION_ID", "single_cancel"}, {"EXPECTED_VERSION", -1}}).empty());
}
