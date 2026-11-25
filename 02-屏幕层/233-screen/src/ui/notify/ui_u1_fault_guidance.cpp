// SPDX-License-Identifier: GPL-3.0-or-later
#include "ui_u1_fault_guidance.h"

#include "core/data_root_resolver.h"
#include "lvgl.h"
#include "snapmaker_exceptions.h"

namespace helix::ui {

void add_u1_fault_illustration(lv_obj_t* dialog, const std::string& code) {
    const auto parsed = snapmaker::decode_exception_code(code);
    if (!dialog || !parsed)
        return;
    const auto file = snapmaker::guidance_image(*parsed);
    if (file.empty())
        return;
    lv_obj_t* container = lv_obj_find_by_name(dialog, "content_container");
    if (!container)
        return;
    lv_obj_t* message = lv_obj_find_by_name(container, "dialog_message");
    const int32_t screen_width = lv_display_get_horizontal_resolution(lv_obj_get_display(dialog));
    const int32_t screen_height = lv_display_get_vertical_resolution(lv_obj_get_display(dialog));
    // Keep the footer reachable on the U1's 480 x 320 panel. The content
    // stays scrollable when a translated message needs more vertical space.
    const bool compact = screen_width <= 500 && message &&
                         !lv_obj_find_by_name(container, "fault_path");
    lv_obj_set_flex_flow(container, compact ? LV_FLEX_FLOW_ROW : LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_gap(container, 8, 0);
    if (compact) {
        lv_obj_set_width(message, LV_PCT(58));
        lv_obj_set_style_max_height(container, screen_height - 125, 0);
    }
    lv_obj_t* picture = lv_image_create(container);
    lv_obj_set_name(picture, "u1_fault_illustration");
    lv_obj_set_size(picture, LV_PCT(compact ? 39 : 100), 120);
    lv_image_set_inner_align(picture, LV_IMAGE_ALIGN_CONTAIN);
    const auto uri = helix::asset_component_uri(std::string(file));
    lv_image_set_src(picture, uri.c_str());
}
} // namespace helix::ui
