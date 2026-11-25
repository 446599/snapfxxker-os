// SPDX-License-Identifier: GPL-3.0-or-later
#include "../catch_amalgamated.hpp"
#include "../lvgl_test_fixture.h"
#include "../lvgl_ui_test_fixture.h"
#include "hv/json.hpp"
#include "printer_fan_state.h"
#include "core/observer_factory.h"
#include "ui_fan_dial.h"
#include "ui_jog_pad.h"

#include <string>
#include <vector>

TEST_CASE_METHOD(LVGLTestFixture, "Fixed XY rings keep 1 and 10 mm for every Z mode",
                 "[native_controls][motion]") {
    const helix::JogPadZoneOptions options{true, true, true};
    for (const auto mode : {helix::JogMode::Fine, helix::JogMode::Coarse, helix::JogMode::Turbo}) {
        const auto steps = helix::jog_pad_ring_distances(mode, options);
        CHECK(steps.inner == 1.0f);
        CHECK(steps.outer == 10.0f);
        CHECK(std::string(steps.inner_label) == "1");
        CHECK(std::string(steps.outer_label) == "10");
    }
    CHECK(helix::jog_pad_zone_for_offset(0, 0, 100, options).home);
    CHECK(helix::jog_pad_zone_for_offset(0, -40, 100, options).inner);
    CHECK_FALSE(helix::jog_pad_zone_for_offset(0, -80, 100, options).inner);
}

TEST_CASE_METHOD(LVGLTestFixture, "A fan's first zero report is distinct from missing telemetry",
                 "[native_controls][fan]") {
    helix::PrinterFanState fans;
    fans.init_subjects(false);
    fans.init_fans({"heater_fan hotend"});
    REQUIRE(fans.get_fans().size() == 1);
    CHECK_FALSE(fans.get_fans().front().speed_known);
    fans.update_from_status(nlohmann::json{{"heater_fan hotend", {{"rpm", 0}}}});
    CHECK_FALSE(fans.get_fans().front().speed_known);
    SubjectLifetime lifetime;
    auto* speed = fans.get_fan_speed_subject("heater_fan hotend", lifetime);
    int notifications = 0;
    auto observer = helix::ui::observe<int>(
        speed, &notifications, [](int* count, int) { ++*count; }, lifetime,
        helix::ui::Dispatch::Immediate);
    const int before = notifications;
    fans.update_from_status(nlohmann::json{{"heater_fan hotend", {{"speed", 0.0}}}});
    CHECK(notifications > before);
    CHECK(fans.get_fans().front().speed_known);
    CHECK(fans.get_fans().front().speed_percent == 0);
    fans.init_fans({"heater_fan hotend"});
    CHECK(fans.get_fans().front().speed_known);
    observer.reset();
    fans.deinit_subjects();
}

TEST_CASE_METHOD(LVGLUITestFixture, "Fan step buttons address their card and clamp the percentage",
                 "[native_controls][fan][ui]") {
    FanDial known(test_screen(), "Fan A", "fan_generic A", 95, false, true);
    FanDial unknown(test_screen(), "Fan B", "fan_generic B", -1, false, true);
    std::vector<std::pair<std::string, int>> commands;
    auto capture = [&](const std::string& id, int value) { commands.emplace_back(id, value); };
    known.set_on_speed_changed(capture);
    unknown.set_on_speed_changed(capture);
    auto* up = lv_obj_find_by_name(known.get_root(), "fan_step_up");
    REQUIRE(up != nullptr);
    lv_obj_send_event(up, LV_EVENT_CLICKED, nullptr);
    REQUIRE(commands.size() == 1);
    CHECK(commands.front().first == "fan_generic A");
    CHECK(commands.front().second == 100);
    auto* unknown_up = lv_obj_find_by_name(unknown.get_root(), "fan_step_up");
    REQUIRE(unknown_up != nullptr);
    lv_obj_send_event(unknown_up, LV_EVENT_CLICKED, nullptr);
    CHECK(commands.size() == 1);
    auto* down = lv_obj_find_by_name(known.get_root(), "fan_step_down");
    REQUIRE(down != nullptr);
    known.restore_speed(5);
    lv_obj_send_event(down, LV_EVENT_CLICKED, nullptr);
    REQUIRE(commands.size() == 2);
    CHECK(commands.back().second == 0);
}
