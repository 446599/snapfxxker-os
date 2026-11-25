// Copyright (C) 2025-2026 356C LLC
// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <algorithm>
#include <lvgl.h>

// These three faces are in FONTS_CORE for both binaries. Keep the same mutable
// declarations as ui_fonts.h without pulling in its application include path.
extern lv_font_t noto_sans_16;
extern lv_font_t noto_sans_bold_24;
extern lv_font_t noto_sans_bold_28;

namespace helix {

// The two startup processes share this small native layout. They run before
// XML, translations, and the theme registry are ready, so the boot palette
// lives here rather than depending on the application's registered widgets.
inline uint32_t u1_splash_background(bool dark_mode) {
    return dark_mode ? 0x191C20 : 0xF4F5F7;
}

inline uint32_t u1_splash_text(bool dark_mode) {
    return dark_mode ? 0xF5F6F7 : 0x25282C;
}

inline lv_obj_t* create_u1_splash(lv_obj_t* screen, int width, int height, bool dark_mode) {
    lv_obj_clear_flag(screen, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_color(screen, lv_color_hex(u1_splash_background(dark_mode)), 0);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);

    auto* card = lv_obj_create(screen);
    lv_obj_set_size(card, std::max(200, std::min(width - 32, 400)),
                    std::max(132, std::min(height - 112, 176)));
    lv_obj_clear_flag(card, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_color(card, lv_color_hex(dark_mode ? 0x25282C : 0xFFFFFF), 0);
    lv_obj_set_style_bg_opa(card, LV_OPA_COVER, 0);
    lv_obj_set_style_border_color(card, lv_color_hex(dark_mode ? 0x3A3F46 : 0xE5E7EB), 0);
    lv_obj_set_style_border_width(card, 1, 0);
    lv_obj_set_style_radius(card, 12, 0);
    lv_obj_set_style_shadow_width(card, 0, 0);
    lv_obj_set_style_pad_all(card, 16, 0);
    lv_obj_set_style_pad_row(card, 8, 0);
    lv_obj_set_flex_flow(card, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(card, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER,
                         LV_FLEX_ALIGN_CENTER);
    lv_obj_center(card);

    auto* mark = lv_obj_create(card);
    lv_obj_set_size(mark, 72, 48);
    lv_obj_clear_flag(mark, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_pad_all(mark, 0, 0);
    lv_obj_set_style_border_width(mark, 0, 0);
    lv_obj_set_style_radius(mark, 12, 0);
    lv_obj_set_style_bg_color(mark, lv_color_hex(0xF5C842), 0);
    lv_obj_set_style_bg_opa(mark, LV_OPA_COVER, 0);
    lv_obj_set_style_shadow_width(mark, 0, 0);
    auto* emblem = lv_label_create(mark);
    lv_label_set_text(emblem, "U1");
    lv_obj_set_style_text_font(emblem, &noto_sans_bold_28, 0);
    lv_obj_set_style_text_color(emblem, lv_color_hex(0x25282C), 0);
    lv_obj_center(emblem);

    auto* title = lv_label_create(card);
    lv_label_set_text(title, "Snapmaker U1");
    lv_obj_set_style_text_font(title, &noto_sans_bold_24, 0);
    lv_obj_set_style_text_color(title, lv_color_hex(u1_splash_text(dark_mode)), 0);

    auto* heads = lv_label_create(card);
    lv_label_set_text(heads, "H1  /  H2  /  H3  /  H4");
    lv_obj_set_style_text_font(heads, &noto_sans_16, 0);
    lv_obj_set_style_text_color(heads, lv_color_hex(dark_mode ? 0xADB2BB : 0x6B7078), 0);
    return card;
}

} // namespace helix
