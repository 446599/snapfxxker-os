// SPDX-License-Identifier: GPL-3.0-or-later

#include "../lvgl_ui_test_fixture.h"
#include "../test_helpers/print_control_buttons_test_access.h"
#include "../test_helpers/print_state_test_drivers.h"
#include "app_globals.h"
#include "print_control_buttons.h"
#include "printer_state.h"
#include "src/ui/panel_widgets/print_status_widget.h"
#include "ui_button.h"

#include <string>

#include "../catch_amalgamated.hpp"

using namespace helix;

TEST_CASE_METHOD(LVGLUITestFixture,
                 "home task actions follow the shared pause acknowledgement",
                 "[print_status][print_control][u1_home]") {
    auto& controls = helix::ui::PrintControlButtons::instance();
    controls.init_subjects();
    {
        PrintStatusWidget widget;
        widget.set_config({{"layout_style", "detailed"}});
        auto* card = static_cast<lv_obj_t*>(
            lv_xml_create(test_screen(), "panel_widget_print_status", nullptr));
        REQUIRE(card != nullptr);
        widget.attach(card, test_screen());
        widget.on_size_changed(10, 4, 400, 150);
        helix::test::set_wire_state(get_printer_state(), PrintJobState::PRINTING);
        process_lvgl(30);

        auto* primary = lv_obj_find_by_name(card, "home_print_primary");
        auto* stop = lv_obj_find_by_name(card, "home_print_stop");
        auto* progress = lv_obj_find_by_name(card, "detailed_progress_bar");
        REQUIRE(primary != nullptr);
        REQUIRE(stop != nullptr);
        REQUIRE(progress != nullptr);
        CHECK(std::string(lv_label_get_text(ui_button_get_label(primary))) == lv_tr("Pause"));

        helix::ui::PrintControlButtonsTestAccess::set_pending(helix::ui::PendingAction::Pausing);
        process_lvgl(10);
        CHECK(lv_obj_has_state(primary, LV_STATE_DISABLED));
        CHECK(std::string(lv_label_get_text(ui_button_get_label(primary))) == lv_tr("Pausing..."));

        helix::test::set_wire_state(get_printer_state(), PrintJobState::PAUSED);
        process_lvgl(30);
        CHECK(std::string(lv_label_get_text(ui_button_get_label(primary))) == lv_tr("Resume"));
        CHECK(helix::ui::PrintControlButtonsTestAccess::pending() ==
              helix::ui::PendingAction::None);
        CHECK_FALSE(lv_obj_has_flag(stop, LV_OBJ_FLAG_HIDDEN));
        widget.detach();
    }
    PrintStatusWidget::destroy_formatter_for_test();
}
