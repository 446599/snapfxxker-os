// Copyright (C) 2025-2026 356C LLC
// SPDX-License-Identifier: GPL-3.0-or-later

#include "ui_modal.h"
#include "ui_nav_manager.h"
#include "ui_panel_print_select.h"
#include "ui_wizard.h"

#include "../helix_test_fixture.h"
#include "../lvgl_ui_test_fixture.h"
#include "app_globals.h"
#include "config.h"
#include "first_run_tour.h"
#include "printer_image_manager.h"
#include "tour_overlay.h"
#include "tour_steps.h"

#include <algorithm>
#include <array>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "../catch_amalgamated.hpp"

using helix::Config;
using helix::tour::build_tour_steps;
using helix::tour::FirstRunTour;
using helix::tour::TourStep;

namespace {
void reset_tour_settings() {
    auto* cfg = Config::get_instance();
    cfg->set<bool>("/tour/completed", false);
    cfg->set<int>("/tour/last_seen_version", 0);
    // is_wizard_required() checks per-printer first, then falls back to root-level.
    // In the singleton-without-init test environment, active_printer_id_ is empty,
    // so the root-level key is the one that's read.
    cfg->set<bool>("/wizard_completed", true);
    // Edit mode gates the tour, and the subject is global, so a case that leaves
    // it set would decide every case that runs after it.
    lv_subject_set_int(&get_home_edit_mode_subject(), 0);
    FirstRunTour::instance().set_printer_ready(true);
    // In-memory singleton state is left clean by the public API used in these
    // tests: start()/skip() always pair, and advance() past the last step calls
    // finish() which resets running_. No explicit reset needed.
}
} // namespace

TEST_CASE("FirstRunTour gate: blocks when tour already completed", "[tour]") {
    reset_tour_settings();
    Config::get_instance()->set<bool>("/tour/completed", true);
    Config::get_instance()->set<int>("/tour/last_seen_version", helix::tour::kTourVersion);
    REQUIRE(FirstRunTour::should_auto_start() == false);
}

TEST_CASE("FirstRunTour gate: blocks when wizard not complete", "[tour]") {
    reset_tour_settings();
    Config::get_instance()->set<bool>("/wizard_completed", false);
    REQUIRE(FirstRunTour::should_auto_start() == false);
}

TEST_CASE("FirstRunTour gate: allows auto-start when fresh and wizards done", "[tour]") {
    reset_tour_settings();
    REQUIRE(FirstRunTour::should_auto_start() == true);
}

TEST_CASE("FirstRunTour gate: re-triggers when last_seen_version is behind TOUR_VERSION",
          "[tour]") {
    reset_tour_settings();
    Config::get_instance()->set<bool>("/tour/completed", true);
    Config::get_instance()->set<int>("/tour/last_seen_version", 0);
    REQUIRE(FirstRunTour::should_auto_start() == true);
}

// The edit-mode subject is owned by app_globals_init_subjects(), which the UI
// fixture runs; without it the subject has no value to set.
TEST_CASE_METHOD(LVGLUITestFixture, "FirstRunTour gate: blocks while home grid edit mode is active",
                 "[tour]") {
    reset_tour_settings();
    // Pin that the gate is otherwise open, so a gate stuck at false cannot pass.
    REQUIRE(FirstRunTour::should_auto_start() == true);

    lv_subject_set_int(&get_home_edit_mode_subject(), 1);
    REQUIRE(FirstRunTour::should_auto_start() == false);

    lv_subject_set_int(&get_home_edit_mode_subject(), 0);
    REQUIRE(FirstRunTour::should_auto_start() == true);
}

TEST_CASE_METHOD(LVGLUITestFixture, "FirstRunTour: wizard completion re-evaluates the gate",
                 "[tour][wizard]") {
    reset_tour_settings();
    Config::get_instance()->set<bool>("/wizard_completed", false);

    // Home is already the active panel while the wizard owns the screen, so the
    // deferred set_active(PanelId::Home) returns without activating anything.
    // on_activate() is the tour's other entry point and does not run here.
    REQUIRE(NavigationManager::instance().get_active() == helix::PanelId::Home);
    REQUIRE(FirstRunTour::should_auto_start() == false);

    auto& tour = FirstRunTour::instance();
    REQUIRE(tour.is_running() == false);

    ui_wizard_complete();
    process_lvgl(200); // past the 100ms nav timer, and drains the async start

    REQUIRE(tour.is_running() == true);
    tour.skip();
}

TEST_CASE_METHOD(LVGLUITestFixture, "FirstRunTour: a modal shown before the deferred start wins",
                 "[tour]") {
    reset_tour_settings();
    auto& tour = FirstRunTour::instance();
    REQUIRE_FALSE(tour.is_running());
    REQUIRE(FirstRunTour::should_auto_start());

    // The gate passes, then a modal comes up before the queued start runs.
    tour.maybe_start();
    lv_obj_t* backdrop = lv_obj_create(lv_screen_active());
    lv_obj_t* dialog = lv_obj_create(backdrop);
    ModalStack::instance().push(backdrop, dialog, "plr_offer_stand_in");
    CHECK_FALSE(FirstRunTour::should_auto_start());
    process_lvgl(50);
    CHECK_FALSE(tour.is_running());

    ModalStack::instance().remove(backdrop, true);
    lv_obj_delete(backdrop);
    CHECK(FirstRunTour::should_auto_start());
    tour.maybe_start();
    process_lvgl(50);
    CHECK(tour.is_running());
    tour.skip();
}

TEST_CASE("FirstRunTour mark_completed writes both flags", "[tour]") {
    reset_tour_settings();
    FirstRunTour::mark_completed();
    auto* cfg = Config::get_instance();
    REQUIRE(cfg->get<bool>("/tour/completed", false) == true);
    REQUIRE(cfg->get<int>("/tour/last_seen_version", 0) == helix::tour::kTourVersion);
}

namespace {
std::vector<std::string> titles(const std::vector<TourStep>& steps) {
    std::vector<std::string> out;
    for (const auto& s : steps)
        out.push_back(s.title_key);
    return out;
}
const TourStep& step_titled(const std::vector<TourStep>& steps, const std::string& title) {
    auto it = std::find_if(steps.begin(), steps.end(),
                           [&](const TourStep& s) { return s.title_key == title; });
    REQUIRE(it != steps.end());
    return *it;
}
} // namespace

TEST_CASE("Tour steps: both filament configurations retain every navigation entry", "[tour]") {
    const auto with_ams = build_tour_steps(true);
    const auto without = build_tour_steps(false);
    REQUIRE(with_ams.size() == 6);
    REQUIRE(without.size() == with_ams.size());
    const auto& connected = step_titled(with_ams, "Choose a head, then its filament");
    const auto& external = step_titled(without, "Choose a head, then its filament");
    CHECK(connected.target_name == "nav_btn_filament");
    CHECK(external.target_name == connected.target_name);
    CHECK(external.body_key.find("Remaining amount is not estimated") != std::string::npos);
    CHECK(connected.body_key.find("external spool") != std::string::npos);
}

TEST_CASE("Tour steps: the home guide works without optional widgets", "[tour]") {
    for (bool has_ams : {false, true}) {
        const auto steps = build_tour_steps(has_ams);
        const auto& step = step_titled(steps, "Your print and all four heads");
        CHECK(step.target_name == "nav_btn_home");
        CHECK(step.body_key.find("H1-H4") != std::string::npos);
        CHECK_FALSE(step.shows_print_status_preview);
    }
}

TEST_CASE("Tour steps: the U1 guide follows the visible five-section navigation", "[tour]") {
    const auto steps = build_tour_steps(true);
    CHECK(steps.front().title_key == "Welcome to U1");
    CHECK(steps.front().target_name.empty());
    const std::array<const char*, 5> nav = {
        "nav_btn_home", "nav_btn_controls", "nav_btn_filament", "nav_btn_settings", "nav_btn_advanced"};
    for (size_t i = 0; i < nav.size(); ++i) {
        CHECK(steps[i + 1].target_name == nav[i]);
        CHECK(steps[i + 1].panel == helix::tour::TourPanel::Home);
        CHECK(steps[i + 1].anchor_hint == helix::tour::TooltipAnchor::PreferRight);
    }
}

TEST_CASE("Tour steps: new guide does not require a fabricated print or optional feeder page", "[tour]") {
    for (const auto& step : build_tour_steps(true)) {
        CHECK_FALSE(step.shows_print_status_preview);
        CHECK(step.panel == helix::tour::TourPanel::Home);
        CHECK(step.fallback_target_name.empty());
    }
}

TEST_CASE("Tour steps: an earlier-version upgrader receives the redesigned navigation", "[tour]") {
    for (int seen : {1, 2}) {
        CHECK(titles(build_tour_steps(true, seen)) == titles(build_tour_steps(true)));
        CHECK(titles(build_tour_steps(false, seen)) == titles(build_tour_steps(false)));
    }
}

TEST_CASE("Tour steps: nothing is new to someone on the current version", "[tour]") {
    CHECK(build_tour_steps(true, helix::tour::kTourVersion).empty());
}

TEST_CASE("auto_start_seen_version: only a finished tour counts as seen", "[tour]") {
    CHECK(helix::tour::auto_start_seen_version(false, 1) == 0);
    CHECK(helix::tour::auto_start_seen_version(true, 0) == 0);
    CHECK(helix::tour::auto_start_seen_version(true, 1) == 1);
}

TEST_CASE("FirstRunTour gate: waits until the printer has been discovered", "[tour]") {
    reset_tour_settings();
    FirstRunTour::instance().set_printer_ready(false);
    CHECK_FALSE(FirstRunTour::should_auto_start());
    FirstRunTour::instance().set_printer_ready(true);
    CHECK(FirstRunTour::should_auto_start());
}

TEST_CASE("FirstRunTour gate: a 1.0 upgrader is offered the tour", "[tour]") {
    reset_tour_settings();
    Config::get_instance()->set<bool>("/tour/completed", true);
    Config::get_instance()->set<int>("/tour/last_seen_version", 1);
    REQUIRE(helix::tour::kTourVersion == 3);
    CHECK(FirstRunTour::should_auto_start());
}

TEST_CASE_METHOD(LVGLUITestFixture, "FirstRunTour: stable navigation highlights keep Home active", "[tour]") {
    reset_tour_settings();
    auto& nav = NavigationManager::instance();
    std::array<lv_obj_t*, kUiPanelCount> panels{};
    for (auto& panel : panels)
        panel = lv_obj_create(lv_screen_active());
    nav.set_panels(panels.data());
    nav.set_active(helix::PanelId::Home);

    auto& tour = FirstRunTour::instance();
    tour.start();
    const auto count = build_tour_steps(helix::tour::hardware_has_ams()).size();
    for (size_t i = 0; i + 1 < count; ++i) {
        REQUIRE(tour.is_running());
        CHECK(nav.get_active() == helix::PanelId::Home);
        tour.advance();
    }
    REQUIRE(tour.is_running());
    tour.skip();
    CHECK(nav.get_active() == helix::PanelId::Home);
    CHECK_FALSE(tour.is_running());
}

TEST_CASE_METHOD(HelixTestFixture, "FirstRunTour: start() sets is_running", "[tour]") {
    reset_tour_settings();
    auto& t = FirstRunTour::instance();
    REQUIRE(!t.is_running());
    t.start();
    REQUIRE(t.is_running());
    t.skip();
    REQUIRE(!t.is_running());
}

TEST_CASE_METHOD(HelixTestFixture, "FirstRunTour: skip writes tour.completed", "[tour]") {
    reset_tour_settings();
    auto& t = FirstRunTour::instance();
    t.start();
    t.skip();
    REQUIRE(Config::get_instance()->get<bool>("/tour/completed", false) == true);
}

TEST_CASE_METHOD(HelixTestFixture, "FirstRunTour: advance past last step finishes", "[tour]") {
    reset_tour_settings();
    auto& t = FirstRunTour::instance();
    t.start();
    const auto count = build_tour_steps(helix::tour::hardware_has_ams()).size();
    for (std::size_t i = 0; i < count; ++i)
        t.advance();
    REQUIRE(!t.is_running());
    REQUIRE(Config::get_instance()->get<bool>("/tour/completed", false) == true);
}

namespace {
helix::tour::TourStep plain_step(const char* target) {
    helix::tour::TourStep step;
    step.target_name = target;
    step.title_key = "Title";
    step.body_key = "Body";
    return step;
}

lv_obj_t* named_box(const char* name, int x, int width) {
    lv_obj_t* box = lv_obj_create(lv_screen_active());
    lv_obj_set_name(box, name);
    lv_obj_set_pos(box, x, 100);
    lv_obj_set_size(box, width, 80);
    lv_obj_update_layout(box);
    return box;
}

// TourOverlay builds its root as dim, highlight, tooltip, in that order.
lv_obj_t* highlight_of(helix::tour::TourOverlay& overlay) {
    return lv_obj_get_child(overlay.root(), 1);
}
lv_obj_t* tooltip_of(helix::tour::TourOverlay& overlay) {
    return lv_obj_get_child(overlay.root(), 2);
}
} // namespace

TEST_CASE_METHOD(LVGLUITestFixture, "TourOverlay: a missing target falls back to the second name",
                 "[tour]") {
    named_box("fallback_target", 40, 200);
    auto with_fallback = plain_step("absent_target");
    with_fallback.fallback_target_name = "fallback_target";
    helix::tour::TourOverlay overlay({with_fallback, plain_step("absent_target")}, {}, {});

    overlay.show_step(0);
    CHECK_FALSE(lv_obj_has_flag(highlight_of(overlay), LV_OBJ_FLAG_HIDDEN));
    overlay.show_step(1);
    CHECK(lv_obj_has_flag(highlight_of(overlay), LV_OBJ_FLAG_HIDDEN));
}

TEST_CASE_METHOD(LVGLUITestFixture, "TourOverlay: LeftNarrowed fits the card beside its target",
                 "[tour]") {
    REQUIRE(lv_display_get_horizontal_resolution(nullptr) >= 800);
    named_box("wide_target", 340, 400);
    named_box("left_edge_target", 40, 400);
    auto narrowed = plain_step("wide_target");
    narrowed.anchor_hint = helix::tour::TooltipAnchor::LeftNarrowed;
    auto no_room = plain_step("left_edge_target");
    no_room.anchor_hint = helix::tour::TooltipAnchor::LeftNarrowed;
    auto centered = plain_step("");
    centered.anchor_hint = helix::tour::TooltipAnchor::Center;
    helix::tour::TourOverlay overlay({centered, narrowed, no_room}, {}, {});
    lv_obj_t* card = tooltip_of(overlay);

    overlay.show_step(0);
    lv_obj_update_layout(card);
    const int full_width = lv_obj_get_width(card);
    REQUIRE(full_width > 340);

    overlay.show_step(1);
    lv_obj_update_layout(card);
    CHECK(lv_obj_get_width(card) < 340);
    CHECK(lv_obj_get_x(card) + lv_obj_get_width(card) <= 340);

    overlay.show_step(2);
    lv_obj_update_layout(card);
    CHECK(lv_obj_get_width(card) == full_width);

    overlay.show_step(0);
    lv_obj_update_layout(card);
    CHECK(lv_obj_get_width(card) == full_width);
}

TEST_CASE_METHOD(LVGLUITestFixture, "TourOverlay: the preview belongs only to its own step",
                 "[tour]") {
    REQUIRE(lv_display_get_vertical_resolution(nullptr) >= 400);
    auto with_picture = plain_step("");
    with_picture.shows_print_status_preview = true;
    helix::tour::TourOverlay overlay({with_picture, plain_step("")}, {}, {});
    lv_obj_t* preview = lv_obj_find_by_name(tooltip_of(overlay), "tour_preview");
    REQUIRE(preview != nullptr);

    overlay.show_step(0);
    CHECK_FALSE(lv_obj_has_flag(preview, LV_OBJ_FLAG_HIDDEN));
    lv_obj_t* printer = lv_obj_find_by_name(preview, "tour_preview_printer");
    REQUIRE(printer != nullptr);
    const std::string expected = helix::PrinterImageManager::instance().get_displayed_image_path(
        lv_display_get_horizontal_resolution(nullptr));
    REQUIRE_FALSE(expected.empty());
    CHECK(static_cast<const char*>(lv_image_get_src(printer)) == expected);
    overlay.show_step(1);
    CHECK(lv_obj_has_flag(preview, LV_OBJ_FLAG_HIDDEN));
}

TEST_CASE_METHOD(LVGLUITestFixture, "TourOverlay: hide_card lasts until the next step", "[tour]") {
    helix::tour::TourOverlay overlay({plain_step("")}, {}, {});
    overlay.show_step(0);
    overlay.hide_card();
    CHECK(lv_obj_has_flag(tooltip_of(overlay), LV_OBJ_FLAG_HIDDEN));
    overlay.show_step(0);
    CHECK_FALSE(lv_obj_has_flag(tooltip_of(overlay), LV_OBJ_FLAG_HIDDEN));
}

TEST_CASE_METHOD(HelixTestFixture, "FirstRunTour: a running tour holds back the plugin prompt",
                 "[tour]") {
    reset_tour_settings();
    CHECK_FALSE(PrintSelectPanel::plugin_screen_state().modal_up);
    auto& t = FirstRunTour::instance();
    t.start();
    REQUIRE(t.is_running());
    CHECK(PrintSelectPanel::plugin_screen_state().modal_up);
    t.skip();
}

TEST_CASE("FirstRunTour: all navigation spotlight targets exist in the shipped navigation", "[tour]") {
    std::ifstream file("ui_xml/navigation_bar.xml");
    REQUIRE(file.good());
    std::stringstream text;
    text << file.rdbuf();
    const std::string xml = text.str();
    for (const auto& step : build_tour_steps(true)) {
        if (!step.target_name.empty()) {
            CHECK(xml.find("name=\"" + step.target_name + "\"") != std::string::npos);
        }
    }
}
