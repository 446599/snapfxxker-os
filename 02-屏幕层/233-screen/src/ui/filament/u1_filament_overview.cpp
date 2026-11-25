// SPDX-License-Identifier: GPL-3.0-or-later
#include "u1_filament_overview.h"

#include "ams_dryer.h"
#include "ams_drying_types.h"
#include "ams_state.h"
#include "core/observer_factory.h"
#include "material_workspace.h"
#include "filament_database.h"
#include "i_moonraker_client.h"
#include "multiace_status.h"
#include "ui_material_workspace.h"
#include "app_globals.h"
#include "printer_state.h"
#include "theme_manager.h"
#include "ui_component_keypad.h"
#include "ui_icon_codepoints.h"
#include "ui_notification.h"
#include "ui_timer_guard.h"
#include "ui_utils.h"

#include "lv_draw_buf_guard.h"
#include "lvgl/lvgl.h"

#include <spdlog/fmt/fmt.h>
#include <spdlog/spdlog.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <utility>

namespace helix {
namespace {

bool head_has_filament(const AmsBackend& backend, int head) {
    if (head < 0 || head >= 4) return false;
    const auto presence = material_workspace::head_physical_presence(&backend, head);
    return presence ? *presence : backend.slot_has_filament_at_toolhead(head);
}

inline lv_color_t kScreen() { return theme_manager_get_color("screen_bg"); }
inline lv_color_t kCard() { return theme_manager_get_color("card_bg"); }
inline lv_color_t kElevated() { return theme_manager_get_color("elevated_bg"); }
inline lv_color_t kBorder() { return theme_manager_get_color("border"); }
inline lv_color_t kText() { return theme_manager_get_color("text"); }
inline lv_color_t kMuted() { return theme_manager_get_color("text_muted"); }
inline lv_color_t kPrimary() { return theme_manager_get_color("primary"); }
inline lv_color_t kPrimarySoft() { return lv_color_mix(kPrimary(), kCard(), 64); }
inline lv_color_t kOnPrimary() {
    return theme_manager_get_contrast_adjusted_text(kText(), kPrimary());
}
inline lv_color_t kLoadedCard() {
    return lv_color_mix(theme_manager_get_color("danger"), kCard(), 48);
}


// U1 (and other FBDEV targets) allocate RGB565 draw buffers (UI_NATIVE_BPP==2).
// LVGL 9's lv_color_t is 3-byte RGB (typically 4-byte aligned). Writing lv_color_t
// into a native buffer overflows each row, corrupts the heap, and aborts in
// realloc on the next set_size (live SIGABRT, crash_1.txt, 2026-11-14).
void write_native_pixel(lv_draw_buf_t* buf, int x, int y, uint32_t rgb) {
    if (!buf || !buf->data)
        return;
    const uint32_t stride = buf->header.stride;
    uint8_t* dest = buf->data + static_cast<size_t>(y) * stride +
                    static_cast<size_t>(x) * static_cast<size_t>(UI_NATIVE_BPP);
#if UI_NATIVE_BPP == 2
    *reinterpret_cast<uint16_t*>(dest) = lv_color_to_u16(lv_color_hex(rgb));
#else
    auto* px = reinterpret_cast<lv_color32_t*>(dest);
    px->red = static_cast<uint8_t>((rgb >> 16) & 0xFF);
    px->green = static_cast<uint8_t>((rgb >> 8) & 0xFF);
    px->blue = static_cast<uint8_t>(rgb & 0xFF);
    px->alpha = 255;
#endif
}

constexpr int kToolbarH = 44;
constexpr int kActionH = 44;
constexpr int kCardGap = 8;
constexpr int kMinMargin = 8;
constexpr int kWellInset = 6;
constexpr int kCardBorder = 2;
constexpr int kCardRadius = 8;
constexpr int kPencilSize = 24;
constexpr int kWorkAreaMaxW = 680;
constexpr int kHubMaxH = 304;
constexpr float kDefaultSpoolG = 1000.0f;

int work_area_width(int width) {
    return std::min(width - 2 * kMinMargin, kWorkAreaMaxW);
}

struct PageContent {
    int x;
    int width;
    int y;
    int height;
    int actions_y;
};

PageContent page_content(int width, int height, int max_body_h) {
    const int row_w = work_area_width(width);
    const int available_h = std::max(1, height - kToolbarH - kActionH - 3 * kCardGap);
    const int body_h = std::min(available_h, max_body_h);
    const int body_y = kToolbarH + kCardGap + (available_h - body_h) / 2;
    return {(width - row_w) / 2, row_w, body_y, body_h, body_y + body_h + kCardGap};
}

// The measured U1 surfaces share the 8px page/gap and 44px touch/header
// dimensions with the XML surfaces. Divide the edges first so a row uses
// all its width, including the pixels left over from integer division.
int place_row_cell(lv_obj_t* obj, int x, int y, int width, int height,
                   int index, int count, int gap) {
    const int available = width - (count - 1) * gap;
    const int start = (index * available + count / 2) / count;
    const int end = ((index + 1) * available + count / 2) / count;
    lv_obj_set_pos(obj, x + start + index * gap, y);
    lv_obj_set_size(obj, end - start, height);
    return end - start;
}

void place_page_heading(lv_obj_t* back, lv_obj_t* title, int width, int side_width) {
    constexpr int back_width = 64;
    const int margin = kMinMargin;
    lv_obj_set_pos(back, margin, 0);
    lv_obj_set_size(back, back_width, kToolbarH);
    lv_obj_set_width(title, width - 2 * margin - side_width - back_width - 2 * kCardGap);
    lv_label_set_long_mode(title, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_align(title, LV_TEXT_ALIGN_LEFT, LV_PART_MAIN);
    lv_obj_align(title, LV_ALIGN_TOP_LEFT, margin + back_width + kCardGap,
                 (kToolbarH - lv_obj_get_style_text_font(title, LV_PART_MAIN)->line_height) / 2);
}

const uint32_t kPresets[24] = {
    0xF5F5F5, 0xF3E5D0, 0xF5C400, 0xD4A017, 0xF0751A, 0xD68459,
    0xD32F2F, 0x8B1E1E, 0xEC5C8C, 0xC2185B, 0x7B3FA1, 0x5E35B1,
    0x1A237E, 0x1565C0, 0x42A5F5, 0x0097A7, 0x2E7D32, 0x8BC34A,
    0x6B7C3A, 0x6D4C41, 0xC8B08A, 0x9E9E9E, 0x515151, 0x1C1C1C,
};

const std::array kQuickColors = {kPresets[0], kPresets[23], kPresets[21], kPresets[6],
                                 kPresets[4], kPresets[2], kPresets[16], kPresets[13]};

struct DryPreset {
    const char* name;
    int temp_c;
    int hours;
};

const DryPreset kDryerPresets[] = {
    {"PLA", 45, 4}, {"PETG", 65, 4}, {"ABS", 80, 4}, {"TPU", 50, 5}, {"PA", 70, 8},
};

void apply_font(lv_obj_t* obj, const char* token) {
    if (const lv_font_t* font = theme_manager_get_font(token)) {
        lv_obj_set_style_text_font(obj, font, LV_PART_MAIN);
    }
}

void clear_scroll(lv_obj_t* obj) {
    lv_obj_remove_flag(obj, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scrollbar_mode(obj, LV_SCROLLBAR_MODE_OFF);
}

void fill_obj(lv_obj_t* obj, lv_color_t bg, int radius) {
    lv_obj_set_style_bg_color(obj, bg, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(obj, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(obj, radius, LV_PART_MAIN);
    lv_obj_set_style_border_width(obj, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(obj, 0, LV_PART_MAIN);
    clear_scroll(obj);
}

void outline_obj(lv_obj_t* obj, lv_color_t bg, lv_color_t border, int radius, int width) {
    fill_obj(obj, bg, radius);
    lv_obj_set_style_border_width(obj, width, LV_PART_MAIN);
    lv_obj_set_style_border_color(obj, border, LV_PART_MAIN);
    lv_obj_set_style_border_opa(obj, LV_OPA_COVER, LV_PART_MAIN);
}

void soft_shadow(lv_obj_t* obj, int opa = 28) {
    lv_obj_set_style_shadow_color(obj, kText(), LV_PART_MAIN);
    lv_obj_set_style_shadow_opa(obj, opa, LV_PART_MAIN);
    lv_obj_set_style_shadow_width(obj, 8, LV_PART_MAIN);
    lv_obj_set_style_shadow_offset_y(obj, 2, LV_PART_MAIN);
    lv_obj_set_style_shadow_offset_x(obj, 0, LV_PART_MAIN);
}

lv_obj_t* make_box(lv_obj_t* parent) {
    lv_obj_t* obj = lv_obj_create(parent);
    lv_obj_remove_style_all(obj);
    clear_scroll(obj);
    lv_obj_remove_flag(obj, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_layout(obj, LV_LAYOUT_NONE);
    lv_obj_add_flag(obj, LV_OBJ_FLAG_IGNORE_LAYOUT);
    return obj;
}

} // namespace

U1FilamentOverview::U1FilamentOverview() = default;

U1FilamentOverview::~U1FilamentOverview() {
    set_secondary_fullscreen(false);
    if (host_ && !host_parent_) lv_obj_delete(host_);
    cancel_color_layout_timer();
    dryer_refresh_timer_.reset();
    revision_observer_.reset();
    connection_observer_.reset();
    job_observer_.reset();
    theme_observer_.reset();
    helix::safe_draw_buf_destroy(sv_buf_, "u1_sv");
    helix::safe_draw_buf_destroy(hue_buf_, "u1_hue");
}

void U1FilamentOverview::setup(lv_obj_t* host) {
    host_ = host;
    host_parent_ = host ? lv_obj_get_parent(host) : nullptr;
    if (!host_) {
        return;
    }
    lv_obj_set_style_bg_color(host_, kScreen(), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(host_, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_pad_all(host_, 0, LV_PART_MAIN);
    lv_obj_set_style_border_width(host_, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(host_, 0, LV_PART_MAIN);
    clear_scroll(host_);
    lv_obj_remove_flag(host_, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_layout(host_, 0);
    lv_obj_add_flag(host_, LV_OBJ_FLAG_IGNORE_LAYOUT);
    auto make_page = [&]() {
        lv_obj_t* page = make_box(host_);
        lv_obj_set_layout(page, 0);
        lv_obj_add_flag(page, LV_OBJ_FLAG_IGNORE_LAYOUT);
        lv_obj_set_pos(page, 0, 0);
        lv_obj_set_size(page, lv_pct(100), lv_pct(100));
        fill_obj(page, kScreen(), 0);
        return page;
    };
    hub_ = make_page();
    color_page_ = make_page();
    lv_obj_add_flag(color_page_, LV_OBJ_FLAG_HIDDEN);
    dryer_page_ = make_page();
    lv_obj_add_flag(dryer_page_, LV_OBJ_FLAG_HIDDEN);
    confirm_page_ = make_page();
    lv_obj_add_flag(confirm_page_, LV_OBJ_FLAG_HIDDEN);

    mode_bar_ = make_box(hub_);
    mode_ace_ = make_soft_button(mode_bar_, "按 ACE", true, [](lv_event_t* e) {
        if (auto* self = static_cast<U1FilamentOverview*>(lv_event_get_user_data(e)))
            self->set_view_mode(0);
    }, this);
    mode_head_ = make_soft_button(mode_bar_, "按喷头", false, [](lv_event_t* e) {
        if (auto* self = static_cast<U1FilamentOverview*>(lv_event_get_user_data(e)))
            self->set_view_mode(1);
    }, this);
    for (int i = 0; i < 4; ++i) {
        group_pills_[static_cast<size_t>(i)] = make_soft_button(hub_, "", false, [](lv_event_t* e) {
            if (auto* self = static_cast<U1FilamentOverview*>(lv_event_get_user_data(e))) {
                const int idx = static_cast<int>(reinterpret_cast<intptr_t>(
                    lv_obj_get_user_data(static_cast<lv_obj_t*>(lv_event_get_current_target(e)))));
                self->select_group(idx);
            }
        }, this);
        lv_obj_set_user_data(group_pills_[static_cast<size_t>(i)],
                             reinterpret_cast<void*>(static_cast<intptr_t>(i)));
    }

    cards_row_ = make_box(hub_);
    lv_obj_set_name(cards_row_, "u1_filament_cards");
    empty_label_ = make_label(cards_row_, "", "font_body", kMuted());
    lv_obj_center(empty_label_);
    for (int i = 0; i < 4; ++i) {
        auto& card = cards_[static_cast<size_t>(i)];
        card.root = make_box(cards_row_);
        lv_obj_set_name(card.root, "u1_filament_card");
        lv_obj_add_flag(card.root, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_user_data(card.root, reinterpret_cast<void*>(static_cast<intptr_t>(i)));
        lv_obj_add_event_cb(card.root, [](lv_event_t* e) {
            if (auto* self = static_cast<U1FilamentOverview*>(lv_event_get_user_data(e))) {
                const int idx = static_cast<int>(reinterpret_cast<intptr_t>(
                    lv_obj_get_user_data(static_cast<lv_obj_t*>(lv_event_get_current_target(e)))));
                self->select_card(idx);
            }
        }, LV_EVENT_CLICKED, this);
        outline_obj(card.root, kCard(), kBorder(), kCardRadius, kCardBorder);

        card.title = make_label(card.root, "", "font_small", kMuted());
        lv_label_set_long_mode(card.title, LV_LABEL_LONG_DOT);
        card.pencil = make_box(card.root);
        lv_obj_set_name(card.pencil, "u1_filament_card_pencil");
        outline_obj(card.pencil, kElevated(), kBorder(), 6, 1);
        // Expand the hit area within this card. Its body remains a separate
        // selection target and the visible pencil stays clear of the title.
        lv_obj_set_ext_click_area(card.pencil, 3);
        lv_obj_add_flag(card.pencil, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_user_data(card.pencil, reinterpret_cast<void*>(static_cast<intptr_t>(i)));
        lv_obj_add_event_cb(card.pencil, [](lv_event_t* e) {
            lv_event_stop_bubbling(e);
            if (auto* self = static_cast<U1FilamentOverview*>(lv_event_get_user_data(e))) {
                const int idx = static_cast<int>(reinterpret_cast<intptr_t>(
                    lv_obj_get_user_data(static_cast<lv_obj_t*>(lv_event_get_current_target(e)))));
                self->show_edit_menu(idx);
            }
        }, LV_EVENT_CLICKED, this);
        lv_obj_t* icon = lv_label_create(card.pencil);
        const char* cp = helix::ui::icon::lookup_codepoint("pencil");
        lv_label_set_text(icon, cp ? cp : "✎");
        apply_font(icon, "icon_font_xs");
        lv_obj_set_style_text_color(icon, kMuted(), LV_PART_MAIN);
        lv_obj_center(icon);
        lv_obj_remove_flag(icon, LV_OBJ_FLAG_CLICKABLE);

        card.well = make_box(card.root);
        outline_obj(card.well, kElevated(), kBorder(), 4, 1);
        lv_obj_set_style_clip_corner(card.well, true, LV_PART_MAIN);
        card.fill = make_box(card.well);
        fill_obj(card.fill, kPrimary(), 0);
        lv_obj_add_flag(card.fill, LV_OBJ_FLAG_HIDDEN);
        card.type = make_label(card.root, "", "font_small", kText());
        lv_label_set_long_mode(card.type, LV_LABEL_LONG_DOT);
    }

    const char* action_names[] = {"进料", "从喷头退出", "退出 ACE", "烘干"};
    for (int i = 0; i < 4; ++i) {
        actions_[static_cast<size_t>(i)] =
            make_soft_button(hub_, action_names[i], i == 0, [](lv_event_t* e) {
                if (auto* self = static_cast<U1FilamentOverview*>(lv_event_get_user_data(e))) {
                    const int idx = static_cast<int>(reinterpret_cast<intptr_t>(
                        lv_obj_get_user_data(static_cast<lv_obj_t*>(lv_event_get_current_target(e)))));
                    if (idx == 0) self->on_action_load();
                    else if (idx == 1) self->on_action_unload();
                    else if (idx == 2) self->on_action_eject();
                    else if (idx == 3) self->on_action_dry();
                }
            }, this);
        if (i == 3) lv_obj_set_name(actions_[i], "u1_filament_dry");
        lv_obj_set_user_data(actions_[static_cast<size_t>(i)],
                             reinterpret_cast<void*>(static_cast<intptr_t>(i)));
    }

    dim_ = make_box(hub_);
    lv_obj_set_style_bg_color(dim_, lv_color_black(), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(dim_, 70, LV_PART_MAIN);
    lv_obj_add_flag(dim_, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(dim_, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_event_cb(dim_, [](lv_event_t* e) {
        if (auto* self = static_cast<U1FilamentOverview*>(lv_event_get_user_data(e)))
            self->hide_edit_menu();
    }, LV_EVENT_CLICKED, this);

    edit_menu_ = make_box(hub_);
    outline_obj(edit_menu_, kCard(), kBorder(), 10, 1);
    soft_shadow(edit_menu_, 40);
    lv_obj_add_flag(edit_menu_, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(edit_menu_, LV_OBJ_FLAG_CLICKABLE);
    edit_identify_ = make_soft_button(edit_menu_, "识别", false, [](lv_event_t* e) {
        if (auto* self = static_cast<U1FilamentOverview*>(lv_event_get_user_data(e)))
            self->on_identify();
    }, this);
    edit_edit_ = make_soft_button(edit_menu_, "编辑", false, [](lv_event_t* e) {
        if (auto* self = static_cast<U1FilamentOverview*>(lv_event_get_user_data(e)))
            self->open_edit();
    }, this);
    lv_obj_set_name(edit_edit_, "u1_filament_edit_slot");

    UI_MANAGED_SUBJECT_INT(external_supply_visible_, 0, "u1_external_supply_visible",
                           edit_subjects_);
    external_title_ = static_cast<lv_obj_t*>(lv_xml_create(hub_, "u1_external_supply_title", nullptr));
    build_edit_page();
    build_color_page();
    build_dryer_page();
    build_confirm_page();
    spdlog::info("[U1Filament] pages built");

    // Do not call lv_obj_update_layout(host_) here. Also suspend style
    // refresh so set_size during the first absolute layout cannot walk
    // back into the stock flex panel.
    lv_obj_enable_style_refresh(false);
    layout_hub();
    lv_obj_enable_style_refresh(true);
    spdlog::info("[U1Filament] hub laid out");
    refresh();
    spdlog::info("[U1Filament] first refresh done");
    show_hub();

    lv_obj_add_event_cb(host_, [](lv_event_t* e) {
        if (auto* self = static_cast<U1FilamentOverview*>(lv_event_get_user_data(e)))
            self->on_host_size(e);
    }, LV_EVENT_SIZE_CHANGED, this);

    observe();
    spdlog::info("[U1Filament] observers attached");
}

void U1FilamentOverview::observe() {
    auto& state = AmsState::instance();
    const auto lifetime = state.get_subjects_lifetime();
    revision_observer_ = helix::ui::observe<int>(
        state.get_ams_data_revision_subject(), this,
        [](U1FilamentOverview* self, int) { self->refresh(); }, lifetime);
    auto& network = get_printer_state().network_state();
    connection_observer_ = helix::ui::observe<int>(
        network.get_nav_buttons_enabled_subject(), this,
        [](U1FilamentOverview* self, int) { self->refresh(); },
        get_printer_state().get_subjects_lifetime());
    job_observer_ = helix::ui::observe<int>(
        get_printer_state().print_state().get_job_holds_machine_subject(), this,
        [](U1FilamentOverview* self, int) { self->refresh(); },
        get_printer_state().get_subjects_lifetime());
    theme_observer_ = helix::ui::observe<int>(
        theme_manager_get_changed_subject(), this,
        [](U1FilamentOverview* self, int) { self->apply_theme(); },
        subject_never_freed());
}

void U1FilamentOverview::apply_theme() {
    if (!host_) return;
    // The global palette walker runs first. Restore state-dependent and material
    // colors afterwards, including swatches that happen to equal a palette color.
    lv_obj_set_style_bg_color(host_, kScreen(), LV_PART_MAIN);
    for (auto* page : {hub_, color_page_, dryer_page_, confirm_page_})
        lv_obj_set_style_bg_color(page, kScreen(), LV_PART_MAIN);
    apply_hub_data();
    lv_obj_set_style_text_color(empty_label_, kMuted(), LV_PART_MAIN);
    for (auto& card : cards_) {
        lv_obj_set_style_text_color(card.title, kMuted(), LV_PART_MAIN);
        lv_obj_set_style_text_color(card.type, kText(), LV_PART_MAIN);
        outline_obj(card.pencil, kElevated(), kBorder(), 6, 1);
        lv_obj_set_style_text_color(lv_obj_get_child(card.pencil, 0), kMuted(), LV_PART_MAIN);
        lv_obj_set_style_bg_color(card.well, kElevated(), LV_PART_MAIN);
    }
    lv_obj_set_style_bg_color(dim_, lv_color_black(), LV_PART_MAIN);
    lv_obj_set_style_border_color(edit_edit_, kBorder(), LV_PART_MAIN);
    lv_obj_set_style_text_color(lv_obj_get_child(edit_edit_, 0), kPrimary(), LV_PART_MAIN);
    lv_obj_set_style_text_color(lv_obj_get_child(edit_identify_, 0), kText(), LV_PART_MAIN);
    refresh_color_widgets();
    lv_obj_set_style_border_color(sv_cursor_, kText(), LV_PART_MAIN);
    lv_obj_set_style_bg_color(hue_cursor_, kText(), LV_PART_MAIN);
    for (auto* button : {color_save_, confirm_ok_}) {
        lv_obj_set_style_bg_color(button, kPrimary(), LV_PART_MAIN);
        lv_obj_set_style_border_color(button, kPrimary(), LV_PART_MAIN);
        lv_obj_set_style_text_color(lv_obj_get_child(button, 0), kOnPrimary(), LV_PART_MAIN);
    }
    lv_obj_set_style_text_color(confirm_extra_, kMuted(), LV_PART_MAIN);
}

void U1FilamentOverview::on_host_size(lv_event_t*) {
    if (!host_)
        return;
    const int w = lv_obj_get_width(host_);
    const int h = lv_obj_get_height(host_);
    if (w <= 1 || h <= 1)
        return;
    if (w == last_w_ && h == last_h_)
        return;
    last_w_ = w;
    last_h_ = h;
    layout_hub();
    apply_hub_data();
    if (page_ == Page::Color) {
        layout_color_geometry();
        schedule_color_layout();
    } else if (page_ == Page::Confirm)
        layout_confirm_page();
    if (page_ == Page::Dryer)
        refresh_dryer();
}

void U1FilamentOverview::set_secondary_fullscreen(bool fullscreen) {
    if (!host_ || !host_parent_) return;
    lv_obj_t* parent = fullscreen ? lv_obj_get_screen(host_) : host_parent_.get();
    if (lv_obj_get_parent(host_) == parent) return;
    // These nested material pages share one host. Restore its original owner
    // on Back so the primary tab resumes normal layout and destruction.
    lv_obj_set_parent(host_, parent);
    lv_obj_set_pos(host_, 0, 0);
    lv_obj_set_size(host_, lv_pct(100), lv_pct(100));
    lv_obj_move_foreground(host_);
    lv_obj_update_layout(host_);
    // Reparenting changes the clip used by the hidden page's invalidation.
    // Repaint the old full-screen header/footer as well as the new tab body.
    lv_obj_invalidate(lv_obj_get_screen(host_));
}

void U1FilamentOverview::show_hub() {
    edit_draft_.reset();
    show_page(Page::Hub);
}

void U1FilamentOverview::show_page(Page page) {
    hide_edit_menu();
    page_ = page;
    set_secondary_fullscreen(page != Page::Hub);
    if (host_) {
        lv_obj_remove_flag(host_, LV_OBJ_FLAG_HIDDEN);
    }
    if (edit_page_) lv_subject_set_int(&edit_visible_, page == Page::Edit);
    if (hub_) {
        if (page == Page::Hub) lv_obj_remove_flag(hub_, LV_OBJ_FLAG_HIDDEN);
        else lv_obj_add_flag(hub_, LV_OBJ_FLAG_HIDDEN);
    }
    if (color_page_) {
        if (page == Page::Color) lv_obj_remove_flag(color_page_, LV_OBJ_FLAG_HIDDEN);
        else lv_obj_add_flag(color_page_, LV_OBJ_FLAG_HIDDEN);
    }
    if (dryer_page_) {
        if (page == Page::Dryer) lv_obj_remove_flag(dryer_page_, LV_OBJ_FLAG_HIDDEN);
        else lv_obj_add_flag(dryer_page_, LV_OBJ_FLAG_HIDDEN);
    }
    if (confirm_page_) {
        if (page == Page::Confirm) lv_obj_remove_flag(confirm_page_, LV_OBJ_FLAG_HIDDEN);
        else lv_obj_add_flag(confirm_page_, LV_OBJ_FLAG_HIDDEN);
    }
}

void U1FilamentOverview::set_view_mode(int mode) {
    view_mode_ = mode == 1 ? 1 : 0;
    refresh();
}

void U1FilamentOverview::select_group(int index) {
    if (showing_ace_) {
        if (index < 0 || index >= static_cast<int>(ace_groups_.size())) return;
        selected_ace_ = ace_groups_[static_cast<size_t>(index)].index;
    } else {
        selected_head_ = index;
    }
    refresh();
}

const U1FilamentOverview::SlotCard* U1FilamentOverview::selected_slot() const {
    return selected_card_ >= 0 && selected_card_ < static_cast<int>(slots_.size())
        ? &slots_[static_cast<size_t>(selected_card_)] : nullptr;
}

void U1FilamentOverview::select_card(int index) {
    if (index < 0 || index >= static_cast<int>(slots_.size()))
        return;
    selected_card_ = index;
    const auto& slot = slots_[static_cast<size_t>(index)];
    selected_global_ = slot.global_index;
    if (slot.ace >= 0) selected_ace_ = slot.ace;
    if (slot.head >= 0) selected_head_ = slot.head;
    apply_hub_data();
}

void U1FilamentOverview::layout_hub() {
    if (!host_ || !hub_)
        return;
    const int width = lv_obj_get_width(host_);
    const int height = lv_obj_get_height(host_);
    if (width <= 1 || height <= 1)
        return;
    constexpr int columns = 4;
    const int row_w = work_area_width(width);
    const int row_x = (width - row_w) / 2;
    const int group_count = external_only_ ? 0 :
        showing_ace_ ? std::min(columns, static_cast<int>(ace_groups_.size())) : columns;
    const int card_count = std::min(columns, static_cast<int>(slots_.size()));
    const int band_h = std::min(height, kHubMaxH);
    const int band_y = (height - band_h) / 2;
    const int toolbar_y = band_y + kCardGap;
    const int grid_y = toolbar_y + kToolbarH + kCardGap;
    const int action_y = band_y + band_h - kCardGap - kActionH;
    const int card_h = std::max(1, action_y - kCardGap - grid_y);

    lv_obj_set_hidden(mode_bar_, external_only_);
    if (external_title_) {
        lv_obj_set_pos(external_title_, row_x, toolbar_y);
        lv_obj_set_size(external_title_, row_w, kToolbarH);
    }
    const int mode_w = 160;
    const int mode_x = group_count > 0 ? row_x : (width - mode_w) / 2;
    lv_obj_set_pos(mode_bar_, mode_x, toolbar_y);
    lv_obj_set_size(mode_bar_, mode_w, kToolbarH);
    outline_obj(mode_bar_, kElevated(), kBorder(), 10, 1);
    place_row_cell(mode_ace_, 0, 0, mode_w - 2, kToolbarH - 2, 0, 2, 0);
    place_row_cell(mode_head_, 0, 0, mode_w - 2, kToolbarH - 2, 1, 2, 0);
    const int group_x = row_x + mode_w + kCardGap;
    const int group_w = row_w - mode_w - kCardGap;
    for (int i = 0; i < group_count; ++i) {
        place_row_cell(group_pills_[static_cast<size_t>(i)], group_x, toolbar_y,
                       group_w, kToolbarH, i, group_count, kCardGap);
    }

    lv_obj_set_pos(cards_row_, row_x, grid_y);
    lv_obj_set_size(cards_row_, row_w, card_h);
    lv_obj_set_width(empty_label_, row_w);
    lv_obj_set_style_text_align(empty_label_, LV_TEXT_ALIGN_CENTER, LV_PART_MAIN);
    const int full_cell_w = (row_w - (columns - 1) * kCardGap) / columns;
    const int card_row_w = card_count == columns ? row_w :
        card_count * full_cell_w + std::max(0, card_count - 1) * kCardGap;
    for (int i = 0; i < card_count; ++i) {
        auto& card = cards_[static_cast<size_t>(i)];
        const int card_w = place_row_cell(card.root, (row_w - card_row_w) / 2, 0,
                                          card_row_w, card_h, i, card_count, kCardGap);
        const int title_h = lv_obj_get_style_text_font(card.title, LV_PART_MAIN)->line_height;
        const int type_h = lv_obj_get_style_text_font(card.type, LV_PART_MAIN)->line_height;
        const int inner_w = card_w - 2 * kCardBorder;
        const int row_pad = 4;
        // The header uses equal compact insets; reserve the full route label
        // alongside the independent edit target, rather than eliding its bay.
        const int pencil_x = inner_w - row_pad - kPencilSize;
        const int title_row_h = std::max(title_h, kPencilSize);
        lv_obj_set_pos(card.title, row_pad, row_pad + (title_row_h - title_h) / 2);
        lv_obj_set_size(card.title, std::max(1, pencil_x - 2 * row_pad), title_h);
        lv_obj_set_pos(card.pencil, pencil_x, row_pad + (title_row_h - kPencilSize) / 2);
        lv_obj_set_size(card.pencil, kPencilSize, kPencilSize);

        const int well_w = std::max(1, inner_w - 2 * kWellInset);
        const int well_y = row_pad + title_row_h + row_pad;
        // Reserve a fixed caption below the swatch; the color keeps all of
        // the remaining body height, independent of its material or weight.
        const int type_y = card_h - 2 * kCardBorder - kMinMargin - type_h;
        const int well_h = std::max(1, type_y - row_pad - well_y);
        lv_obj_set_pos(card.well, kWellInset, well_y);
        lv_obj_set_size(card.well, well_w, well_h);
        lv_obj_set_pos(card.type, kWellInset, type_y);
        lv_obj_set_size(card.type, well_w, type_h);
        lv_obj_set_style_text_align(card.type, LV_TEXT_ALIGN_CENTER, LV_PART_MAIN);


    }

    const int action_count = external_only_ ? 2 : columns;
    for (int i = 0; i < action_count; ++i) {
        place_row_cell(actions_[static_cast<size_t>(i)], row_x, action_y, row_w, kActionH,
                       i, action_count, kCardGap);
    }

    lv_obj_set_pos(dim_, 0, 0);
    lv_obj_set_size(dim_, width, height);
    lv_obj_move_foreground(dim_);
    lv_obj_move_foreground(edit_menu_);
    laid_out_ = true;
}

void U1FilamentOverview::style_card_like(lv_obj_t* obj, bool selected, bool elevated) {
    outline_obj(obj, selected ? kCard() : (elevated ? kElevated() : kCard()),
                selected ? kPrimary() : kBorder(), 8, selected ? 2 : 1);
}

lv_obj_t* U1FilamentOverview::make_label(lv_obj_t* parent, const char* text, const char* font,
                                         lv_color_t color) {
    lv_obj_t* label = lv_label_create(parent);
    lv_label_set_text(label, text ? text : "");
    apply_font(label, font);
    lv_obj_set_style_text_color(label, color, LV_PART_MAIN);
    lv_obj_remove_flag(label, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_layout(label, LV_LAYOUT_NONE);
    lv_obj_add_flag(label, LV_OBJ_FLAG_IGNORE_LAYOUT);
    return label;
}

lv_obj_t* U1FilamentOverview::make_soft_button(lv_obj_t* parent, const char* text, bool primary,
                                               lv_event_cb_t cb, void* user) {
    lv_obj_t* btn = make_box(parent);
    if (primary)
        outline_obj(btn, kPrimary(), kPrimary(), kCardRadius, 1);
    else
        outline_obj(btn, kElevated(), kBorder(), kCardRadius, 1);
    soft_shadow(btn, primary ? 24 : 14);
    lv_obj_add_flag(btn, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_t* label = make_label(btn, text, "font_body",
                                 primary ? kOnPrimary() : kText());
    lv_obj_center(label);
    lv_obj_set_style_text_align(label, LV_TEXT_ALIGN_CENTER, LV_PART_MAIN);
    if (cb)
        lv_obj_add_event_cb(btn, cb, LV_EVENT_CLICKED, user);
    return btn;
}

lv_obj_t* U1FilamentOverview::make_page_back(lv_obj_t* parent, lv_event_cb_t cb) {
    lv_obj_t* button = make_soft_button(parent, "返回", false, cb, this);
    apply_font(lv_obj_get_child(button, 0), "font_small");
    lv_obj_set_style_bg_opa(button, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_border_width(button, 0, LV_PART_MAIN);
    lv_obj_set_style_shadow_opa(button, 0, LV_PART_MAIN);
    lv_obj_align(lv_obj_get_child(button, 0), LV_ALIGN_CENTER, 8, 0);
    if (const char* glyph = ui::icon::lookup_codepoint("chevron_left")) {
        auto* icon = make_label(button, glyph, "icon_font_xs", kText());
        lv_obj_align(icon, LV_ALIGN_LEFT_MID, 0, 0);
    }
    return button;
}

void U1FilamentOverview::show_feedback(const std::string& text, FeedbackKind kind) {
    if (text.empty())
        return;
    // The shared notification system retains feedback in the header bell's
    // history. Refusals stay readable until acknowledged; accepted commands
    // are informational because completion still comes from device status.
    switch (kind) {
    case FeedbackKind::Attention:
        ui_notification_error("耗材提示", text.c_str(), true);
        break;
    case FeedbackKind::Info:
        ui_notification_info("耗材", text.c_str());
        break;
    case FeedbackKind::Success:
        ui_notification_success("耗材", text.c_str());
        break;
    }
}

void U1FilamentOverview::refresh() {
    auto* backend = AmsState::instance().get_backend();
    const auto info = backend ? backend->get_system_info() : AmsSystemInfo{};
    const bool online =
        lv_subject_get_int(get_printer_state().network_state().get_nav_buttons_enabled_subject()) == 1;
    const bool ready = backend && material_workspace::supports_head_inventory(info);
    external_heads_.fill(false);
    if (ready) {
        const bool external_only = material_workspace::uses_external_spools(info) &&
            !info.head_focused_inventory;
        for (int head = 0; head < 4; ++head) {
            const auto* slot = material_workspace::head_slot(info, head);
            external_heads_[static_cast<size_t>(head)] = slot &&
                (external_only || backend->head_source_slot(head, info) == slot->global_index);
        }
    }
    const auto view = u1_filament::inventory_view(info, view_mode_ == 0, selected_ace_,
                                                  selected_head_, external_heads_);
    const bool external_only = std::all_of(external_heads_.begin(), external_heads_.end(),
        [](bool head_external) { return head_external; }) && view.groups.empty();
    const size_t previous_card_count = slots_.size();
    const bool toolbar_changed = external_only_ != external_only || showing_ace_ != view.by_ace ||
        ace_groups_.size() != view.groups.size();
    external_only_ = external_only;
    lv_subject_set_int(&external_supply_visible_, external_only_);
    ace_groups_ = view.groups;
    showing_ace_ = view.by_ace;
    if (showing_ace_)
        selected_ace_ = view.selected_ace;

    const bool job = lv_subject_get_int(
        get_printer_state().print_state().get_job_holds_machine_subject()) != 0;
    auto editable = [&](const AmsUnit& unit, const SlotInfo& slot) {
        return online && unit.connected && !unit.absent && !job &&
            !ams_action_is_busy(info.action) && backend->lane_records_ready() && slot.global_index >= 0;
    };
    if (edit_draft_) {
        const auto* unit = info.unit_for_slot(edit_draft_->original.global_index);
        const auto* slot = info.get_slot_global(edit_draft_->original.global_index);
        if (!unit || !slot || !editable(*unit, *slot) ||
            slot->is_present() != edit_draft_->original.is_present() || (slot->global_index < 4 &&
            !external_heads_[static_cast<size_t>(slot->global_index)]))
            edit_invalidated_ = true;
    }

    slots_.clear();
    for (const auto& route : view.cards) {
        const auto& unit = info.units[static_cast<size_t>(route.backend_unit)];
        const auto* slot = info.get_slot_global(route.global_slot);
        if (!slot) continue;
        SlotCard card;
        card.backend_unit = route.backend_unit;
        card.ace = route.ace;
        card.local_slot = route.bay;
        card.head = route.head;
        card.global_index = route.global_slot;
        card.present = true;
        card.connected = online && unit.connected;
        card.editable = editable(unit, *slot);
        card.inlet_has_filament = route.ace >= 0 && slot->is_present();
        card.empty = slot->status == SlotStatus::EMPTY;
        if (route.ace < 0) {
            card.title = fmt::format("{}号外置", card.head + 1);
        } else if (showing_ace_) {
            card.title = card.head >= 0 ? fmt::format("槽{}→头{}", route.bay + 1, card.head + 1)
                                      : fmt::format("{}槽·未接入", route.bay + 1);
        } else {
            card.title = fmt::format("ACE{}·{}槽", route.ace + 1, route.bay + 1);
        }
        if (!card.connected)
            card.type = "离线";
        else if (card.empty)
            card.type = "无耗材";
        else
            card.type = slot->material.empty() ? "未识别" : slot->material;
        if (card.connected && slot->remaining_weight_g >= 0 && !card.empty) {
            card.has_weight = true;
            card.remaining_g = slot->remaining_weight_g;
            const float total = slot->total_weight_g > 0 ? slot->total_weight_g : kDefaultSpoolG;
            card.fill = std::clamp(slot->remaining_weight_g / total, 0.0f, 1.0f);
            card.weight = fmt::format("约 {:.0f} g", card.remaining_g);
        }
        if (card.connected && !card.empty && slot->color_rgb != kAmsDefaultSlotColor) {
            card.has_color = true;
            card.color_rgb = slot->color_rgb;
        }
        if (card.connected && card.head >= 0) {
            const auto source = material_workspace::head_physical_source(backend, card.head);
            const auto presence = material_workspace::head_physical_presence(backend, card.head);
            card.current = route.ace < 0 ? backend->slot_has_filament_at_toolhead(card.head)
                                        : presence == true && source == slot->global_index;
            card.source_unknown = route.ace >= 0 && presence == true && !source &&
                backend->upstream_slot(card.head) == slot->global_index;
        }
        slots_.push_back(std::move(card));
    }
    lv_label_set_text(empty_label_, !online ? "打印机离线" : ready ? "当前喷头没有可用料槽 · 检查 ACE 连接"
                         : "正在读取耗材配置");
    if (slots_.empty()) lv_obj_remove_flag(empty_label_, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(empty_label_, LV_OBJ_FLAG_HIDDEN);

    const auto selected = std::find_if(slots_.begin(), slots_.end(), [&](const SlotCard& slot) {
        return slot.global_index == selected_global_ &&
            (showing_ace_ || slot.head == selected_head_);
    });
    const auto head_match = std::find_if(slots_.begin(), slots_.end(), [&](const SlotCard& slot) {
        return slot.head == selected_head_;
    });
    const auto fallback = !showing_ace_ && head_match != slots_.end() ? head_match : slots_.begin();
    selected_card_ = slots_.empty() ? -1 : static_cast<int>(
        std::distance(slots_.begin(), selected != slots_.end() ? selected : fallback));
    if (const auto* slot = selected_slot()) {
        if (menu_global_ >= 0 && menu_global_ != slot->global_index)
            hide_edit_menu();
        selected_global_ = slot->global_index;
    } else {
        hide_edit_menu();
    }

    if (!laid_out_ || toolbar_changed || previous_card_count != slots_.size())
        layout_hub();
    apply_hub_data();
    if (page_ == Page::Dryer) refresh_dryer();
}

void U1FilamentOverview::apply_hub_data() {
    const bool by_ace = showing_ace_;
    auto paint_mode = [&](lv_obj_t* btn, bool on) {
        outline_obj(btn, on ? kCard() : kElevated(), on ? kPrimarySoft() : kElevated(), 8, 1);
        if (lv_obj_t* label = lv_obj_get_child(btn, 0)) {
            lv_obj_set_style_text_color(label, on ? kPrimary() : kMuted(), LV_PART_MAIN);
            apply_font(label, "font_body");
        }
    };
    paint_mode(mode_ace_, by_ace);
    paint_mode(mode_head_, !by_ace);
    if (ace_groups_.empty()) lv_obj_add_state(mode_ace_, LV_STATE_DISABLED);
    else lv_obj_remove_state(mode_ace_, LV_STATE_DISABLED);

    const auto* backend = AmsState::instance().get_backend();
    const auto info = backend ? backend->get_system_info() : AmsSystemInfo{};
    const auto* card = selected_slot();
    const bool can_act = card && card->connected && backend &&
        !ams_action_holds_system(info.action) && !lv_subject_get_int(
            get_printer_state().print_state().get_job_holds_machine_subject());
    const bool external = card && card->ace < 0;
    using Eligibility = AmsBackend::FilamentOpEligibility;
    const auto load_eligibility = can_act && external
        ? backend->slot_op_eligibility(card->global_index, true) : Eligibility::NotSupported;
    const bool loaded = card && backend && head_has_filament(*backend, card->head);
    const bool can_load = can_act && !loaded && card->head >= 0 && !info.path_held(card->head) &&
        (external ? load_eligibility == Eligibility::Eligible : card->inlet_has_filament);
    lv_label_set_text(lv_obj_get_child(actions_.front(), 0),
                      loaded ? "已进料" :
                      load_eligibility == Eligibility::Empty ? "请先插入耗材" : "进料");
    // A connected ACE route still exposes the head action when its sensor is
    // empty so that the operator can get the explanation and the slot action.
    const auto lifecycle = get_printer_state().print_state().get_print_lifecycle();
    const bool can_confirm_source = card && card->connected && backend && !external &&
        card->inlet_has_filament && card->head >= 0 && card->head < 4 &&
        (!job_holds_machine(lifecycle) || lifecycle == PrintState::Paused) &&
        material_workspace::head_physical_presence(backend, card->head) == true &&
        !material_workspace::head_physical_source(backend, card->head);
    const bool head_action = can_confirm_source || (can_act && card->head >= 0 &&
        (external ? head_can_unload(*backend, card->head, true)
                  : card->inlet_has_filament || head_can_unload(*backend, card->head, false)));
    const bool can_eject = can_act && !external && card->inlet_has_filament;
    const bool can_dry = card && card->connected && backend && !external &&
        helix::dryer_info(*backend, card->backend_unit).supported;
    const std::array<bool, 4> available{can_load, head_action, can_eject, can_dry};
    for (size_t i = 0; i < actions_.size(); ++i) {
        auto* action = actions_[i];
        lv_obj_set_hidden(action, external_only_ && i >= 2);
        const bool enabled = available[i];
        const bool primary = enabled && i == 0;
        lv_obj_set_disabled(action, !enabled);
        apply_font(lv_obj_get_child(action, 0), primary ? "font_body" : "font_small");
        outline_obj(action, primary ? kPrimary() : kElevated(),
                    primary ? kPrimary() : kBorder(), kCardRadius, 1);
        soft_shadow(action, !enabled ? 0 : primary ? 24 : 14);
        lv_obj_set_style_text_color(lv_obj_get_child(action, 0),
                                    primary ? kOnPrimary() : enabled ? kText() : kMuted(),
                                    LV_PART_MAIN);
    }
    const bool unknown_head_source = !by_ace &&
        !external_heads_[static_cast<size_t>(selected_head_)] &&
        material_workspace::head_physical_presence(backend, selected_head_) == true &&
        !material_workspace::head_physical_source(backend, selected_head_);

    for (int i = 0; i < 4; ++i) {
        lv_obj_t* pill = group_pills_[static_cast<size_t>(i)];
        if (external_only_ || (by_ace && i >= static_cast<int>(ace_groups_.size()))) {
            lv_obj_add_flag(pill, LV_OBJ_FLAG_HIDDEN);
            continue;
        }
        lv_obj_remove_flag(pill, LV_OBJ_FLAG_HIDDEN);
        const int group = by_ace ? ace_groups_[static_cast<size_t>(i)].index : i;
        const bool on = group == (by_ace ? selected_ace_ : selected_head_);
        const std::string label = by_ace ? fmt::format("ACE {}", group + 1)
            : fmt::format("喷头 {}{}", i + 1, on && unknown_head_source ? " ?" : "");
        if (lv_obj_t* text = lv_obj_get_child(pill, 0)) {
            lv_label_set_text(text, label.c_str());
            apply_font(text, "font_small");
            lv_obj_set_style_text_color(text, on ? kText() : kMuted(), LV_PART_MAIN);
            lv_obj_center(text);
        }
        outline_obj(pill, on ? kCard() : kElevated(), on ? kPrimary() : kBorder(), 8, 2);
        soft_shadow(pill, on ? 16 : 8);
    }

    for (int i = 0; i < 4; ++i) {
        auto& w = cards_[static_cast<size_t>(i)];
        if (i >= static_cast<int>(slots_.size())) {
            lv_obj_add_flag(w.root, LV_OBJ_FLAG_HIDDEN);
            continue;
        }
        const auto& slot = slots_[static_cast<size_t>(i)];
        lv_obj_remove_flag(w.root, LV_OBJ_FLAG_HIDDEN);
        const bool selected = (i == selected_card_);
        // Keep border geometry fixed so the pencil and text never shift when selected.
        outline_obj(w.root, slot.current ? kLoadedCard() : kCard(), selected ? kPrimary() : kBorder(),
                    kCardRadius, kCardBorder);
        lv_label_set_text(w.title, slot.title.c_str());
        if (slot.editable) lv_obj_remove_flag(w.pencil, LV_OBJ_FLAG_HIDDEN);
        else lv_obj_add_flag(w.pencil, LV_OBJ_FLAG_HIDDEN);
        // Keep the source warning when needed; ordinary captions contain only
        // the material/presence state, with current feed marked by the background.
        lv_label_set_text(w.type, slot.source_unknown ? "来源待确认" : slot.type.c_str());

        const int well_h = lv_obj_get_style_height(w.well, LV_PART_MAIN) - 2;
        const int well_w = lv_obj_get_style_width(w.well, LV_PART_MAIN) - 2;
        const auto color = slot.has_color ? lv_color_hex(slot.color_rgb) : kMuted();
        // With no estimate the whole color area is a swatch. A known zero weight
        // leaves the track empty, with the material color visible on its edge.
        const int fill_w = slot.has_weight ? static_cast<int>(std::round(well_w * slot.fill))
                                           : slot.has_color ? well_w : 0;
        lv_obj_set_style_border_color(w.well, slot.has_color && fill_w == 0 ? color : kBorder(),
                                      LV_PART_MAIN);
        lv_obj_set_hidden(w.fill, fill_w <= 0 || well_h <= 0);
        if (fill_w > 0 && well_h > 0) {
            lv_obj_set_size(w.fill, fill_w, well_h);
            lv_obj_set_style_bg_color(w.fill, color, LV_PART_MAIN);
        }
    }
}

void U1FilamentOverview::hide_edit_menu() {
    if (dim_)
        lv_obj_add_flag(dim_, LV_OBJ_FLAG_HIDDEN);
    if (edit_menu_)
        lv_obj_add_flag(edit_menu_, LV_OBJ_FLAG_HIDDEN);
    menu_global_ = -1;
}

void U1FilamentOverview::show_edit_menu(int card) {
    if (card < 0 || card >= static_cast<int>(slots_.size()))
        return;
    select_card(card);
    const auto* slot = selected_slot();
    if (!slot || !slot->editable) return;
    menu_global_ = slot->global_index;
    const bool identify = slot->ace >= 0;
    if (identify) lv_obj_remove_flag(edit_identify_, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(edit_identify_, LV_OBJ_FLAG_HIDDEN);
    // The assigned offsets anchor the popover without flushing layout in the click handler.
    const int menu_w = 116;
    const int menu_h = (identify ? 2 : 1) * kActionH + 2;
    const int W = lv_obj_get_width(host_);
    const int H = lv_obj_get_height(host_);
    const auto& widgets = cards_[static_cast<size_t>(card)];
    const int pencil_x = lv_obj_get_style_x(cards_row_, LV_PART_MAIN) +
                         lv_obj_get_style_x(widgets.root, LV_PART_MAIN) +
                         kCardBorder + lv_obj_get_style_x(widgets.pencil, LV_PART_MAIN);
    const int pencil_y = lv_obj_get_style_y(cards_row_, LV_PART_MAIN) + kCardBorder +
                         lv_obj_get_style_y(widgets.pencil, LV_PART_MAIN);
    const int mx = std::clamp(pencil_x + kPencilSize - menu_w, kMinMargin,
                              W - kMinMargin - menu_w);
    const int my = std::min(pencil_y + kPencilSize + 4, H - kMinMargin - menu_h);

    lv_obj_set_layout(dim_, LV_LAYOUT_NONE);
    lv_obj_add_flag(dim_, LV_OBJ_FLAG_IGNORE_LAYOUT);
    lv_obj_add_flag(dim_, LV_OBJ_FLAG_FLOATING);
    lv_obj_set_layout(edit_menu_, LV_LAYOUT_NONE);
    lv_obj_add_flag(edit_menu_, LV_OBJ_FLAG_IGNORE_LAYOUT);
    lv_obj_add_flag(edit_menu_, LV_OBJ_FLAG_FLOATING);
    lv_obj_set_pos(dim_, 0, 0);
    lv_obj_set_size(dim_, W, H);
    lv_obj_set_pos(edit_menu_, mx, my);
    lv_obj_set_size(edit_menu_, menu_w, menu_h);
    lv_obj_set_pos(edit_identify_, 0, 0);
    lv_obj_set_size(edit_identify_, menu_w - 2, kActionH);
    fill_obj(edit_identify_, kCard(), 0);
    lv_obj_set_style_shadow_opa(edit_identify_, 0, LV_PART_MAIN);
    lv_obj_set_pos(edit_edit_, 0, identify ? kActionH : 0);
    lv_obj_set_size(edit_edit_, menu_w - 2, kActionH);
    fill_obj(edit_edit_, kCard(), 0);
    lv_obj_set_style_shadow_opa(edit_edit_, 0, LV_PART_MAIN);
    lv_obj_set_style_border_side(edit_edit_, LV_BORDER_SIDE_TOP, LV_PART_MAIN);
    lv_obj_set_style_border_width(edit_edit_, 1, LV_PART_MAIN);
    lv_obj_set_style_border_color(edit_edit_, kBorder(), LV_PART_MAIN);
    if (lv_obj_t* lab = lv_obj_get_child(edit_edit_, 0)) {
        lv_obj_set_style_text_color(lab, kPrimary(), LV_PART_MAIN);
        apply_font(lab, "font_heading");
    }
    if (lv_obj_t* lab = lv_obj_get_child(edit_identify_, 0)) {
        lv_obj_set_style_text_color(lab, kText(), LV_PART_MAIN);
        apply_font(lab, "font_body");
    }
    lv_obj_remove_flag(dim_, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(edit_menu_, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_foreground(dim_);
    lv_obj_move_foreground(edit_menu_);
    spdlog::info("[U1Filament] edit menu shown at {},{}", mx, my);
}

void U1FilamentOverview::on_identify() {
    const int target = menu_global_;
    hide_edit_menu();
    const auto* slot = selected_slot();
    if (!slot || !slot->editable || slot->ace < 0 || slot->global_index != target)
        return;
    ui::get_material_workspace().open_action(slot->head, slot->backend_unit - 1,
                                              "read_tag", {}, slot->local_slot, {}, slot->ace);
}

void U1FilamentOverview::build_edit_page() {
    UI_MANAGED_SUBJECT_INT(edit_visible_, 0, "u1_material_edit_visible", edit_subjects_);
    UI_MANAGED_SUBJECT_STRING(edit_target_, edit_target_buffer_, "", "u1_material_edit_target",
                              edit_subjects_);
    UI_MANAGED_SUBJECT_STRING(edit_summary_, edit_summary_buffer_, "", "u1_material_edit_color",
                              edit_subjects_);
    UI_MANAGED_SUBJECT_INT(edit_quick_color_, -1, "u1_material_edit_quick_color", edit_subjects_);
    for (size_t i = 0; i < kQuickColors.size(); ++i) {
        const auto name = fmt::format("u1_material_edit_quick_color_{}", i);
        UI_MANAGED_SUBJECT_COLOR(edit_quick_colors_[i], lv_color_hex(kQuickColors[i]),
                                 name.c_str(), edit_subjects_);
    }
    auto editor = [](lv_event_t* event) -> U1FilamentOverview* {
        for (auto* obj = lv_event_get_target_obj(event); obj; obj = lv_obj_get_parent(obj)) {
            const auto* name = lv_obj_get_name(obj);
            if (name && std::strcmp(name, "u1_material_edit") == 0)
                return static_cast<U1FilamentOverview*>(lv_obj_get_user_data(obj));
        }
        return nullptr;
    };
    // XML callbacks resolve the owning instance through the root of this component.
    static const auto find_editor = editor;
    lv_xml_register_event_cb(nullptr, "u1_material_edit_cancel", [](lv_event_t* event) {
        if (auto* self = find_editor(event)) self->show_hub();
    });
    lv_xml_register_event_cb(nullptr, "u1_material_edit_save", [](lv_event_t* event) {
        if (auto* self = find_editor(event)) self->save_edit();
    });
    lv_xml_register_event_cb(nullptr, "u1_material_edit_open_color", [](lv_event_t* event) {
        if (auto* self = find_editor(event)) self->open_color();
    });
    lv_xml_register_event_cb(nullptr, "u1_material_edit_pick_quick_color", [](lv_event_t* event) {
        auto* self = find_editor(event);
        const auto* index = static_cast<const char*>(lv_event_get_user_data(event));
        if (!self || self->page_ != Page::Edit || !self->edit_draft_ || !index ||
            !index[0] || index[1] != '\0') return;
        const auto i = static_cast<size_t>(index[0] - '0');
        if (i >= kQuickColors.size()) return;
        self->edit_draft_->finish_color(kQuickColors[i], true);
        self->refresh_edit_summary();
    });
    edit_page_ = static_cast<lv_obj_t*>(lv_xml_create(host_, "u1_material_edit", nullptr));
    if (!edit_page_) return;
    lv_obj_set_name(edit_page_, "u1_material_edit");
    lv_obj_set_user_data(edit_page_, this);
    material_dropdown_ = lv_obj_find_by_name(edit_page_, "material_edit_dropdown");
}

void U1FilamentOverview::open_edit() {
    const int target = menu_global_;
    hide_edit_menu();
    const auto* card = selected_slot();
    auto* backend = AmsState::instance().get_backend();
    auto* client = get_moonraker_client();
    if (!edit_page_ || !material_dropdown_ || !backend || !client || !card ||
        !card->editable || card->global_index != target) {
        show_feedback("该槽当前不可编辑");
        return;
    }
    const auto info = backend->get_system_info();
    const auto* unit = info.unit_for_slot(target);
    const auto* slot = info.get_slot_global(target);
    if (!unit || !slot || !unit->connected || unit->absent) {
        show_feedback("所选料槽已变化，请重新打开编辑");
        return;
    }

    edit_draft_.emplace(*slot);
    edit_backend_ = backend;
    edit_connection_ = client->connection_generation();
    edit_serial_ = unit->serial_number;
    edit_invalidated_ = false;
    edit_label_ = card->ace < 0 ? card->title
        : fmt::format("ACE {} · {} 槽", card->ace + 1, card->local_slot + 1);
    lv_subject_copy_string(&edit_target_, edit_label_.c_str());

    // The first option carries the exact current string, including aliases and
    // material names that a tag or the user supplied outside the catalog.
    edit_materials_.clear();
    edit_materials_.push_back(slot->material);
    std::string options = slot->material.empty() ? "未设置" : slot->material;
    std::replace(options.begin(), options.end(), '\n', ' ');
    for (const auto* name : filament::get_all_material_names()) {
        if (slot->material == name) continue;
        edit_materials_.emplace_back(name);
        options += "\n";
        options += name;
    }
    lv_dropdown_set_options(material_dropdown_, options.c_str());
    lv_dropdown_set_selected(material_dropdown_, 0);
    refresh_edit_summary();
    show_page(Page::Edit);
}

void U1FilamentOverview::refresh_edit_summary() {
    if (!edit_draft_) return;
    const auto color = edit_draft_->color;
    lv_subject_copy_string(&edit_summary_, color == kAmsDefaultSlotColor
        ? "未设置" : fmt::format("#{:06X}", color & 0xffffff).c_str());
    const auto quick = std::find(kQuickColors.begin(), kQuickColors.end(), color);
    lv_subject_set_int(&edit_quick_color_, quick == kQuickColors.end()
        ? -1 : static_cast<int>(quick - kQuickColors.begin()));
}

void U1FilamentOverview::open_color() {
    if (!edit_draft_) return;
    draft_color_ = edit_draft_->color == kAmsDefaultSlotColor ? kPresets[0] : edit_draft_->color;
    rgb_to_hsv(draft_color_, &draft_h_, &draft_s_, &draft_v_);
    layout_color_geometry();
    show_page(Page::Color);
    schedule_color_layout();
}

void U1FilamentOverview::close_color(bool accept) {
    if (!edit_draft_) return;
    edit_draft_->finish_color(draft_color_, accept);
    cancel_color_layout_timer();
    refresh_edit_summary();
    show_page(Page::Edit);
}

void U1FilamentOverview::save_edit() {
    if (!edit_draft_) return;
    auto* backend = AmsState::instance().get_backend();
    auto* client = get_moonraker_client();
    refresh();
    const int target = edit_draft_->original.global_index;
    const auto info = backend ? backend->get_system_info() : AmsSystemInfo{};
    const auto* slot = info.get_slot_global(target);
    const auto* unit = info.unit_for_slot(target);
    const auto selection = lv_dropdown_get_selected(material_dropdown_);
    if (edit_invalidated_ || !backend || backend != edit_backend_ || !client ||
        client->connection_generation() != edit_connection_ || !slot || !unit ||
        unit->serial_number != edit_serial_ || selection >= edit_materials_.size()) {
        show_hub();
        show_feedback("所选料槽或连接已变化，请重新打开编辑");
        return;
    }
    edit_draft_->material = edit_materials_[selection];
    const auto updated = edit_draft_->update(*slot);
    if (!updated) {
        show_hub();
        show_feedback("耗材信息已变化，请重新打开编辑");
        return;
    }
    if (updated->material == slot->material && updated->color_rgb == slot->color_rgb) {
        show_hub();
        return;
    }
    if (slot->spoolman_id > 0 && updated->material != slot->material) {
        show_feedback("此料卷的材质由 Spoolman 管理。请在 Spoolman 修改材质，或保留原材质后单独保存颜色。");
        return;
    }
    const auto result = AmsState::instance().commit_slot_edit(target, *slot, *updated);
    show_hub();
    show_feedback(result.success() ? "耗材已保存" : result.user_msg,
                  result.success() ? FeedbackKind::Success : FeedbackKind::Attention);
    refresh();
}

bool U1FilamentOverview::head_can_unload(const AmsBackend& backend, int head, bool external) const {
    if (head < 0 || head >= 4) return false;
    if (external) return backend.slot_has_filament_at_toolhead(head);
    return material_workspace::head_physical_presence(&backend, head) == true &&
        material_workspace::head_physical_source(&backend, head).has_value();
}

void U1FilamentOverview::on_action_load() {
    hide_edit_menu();
    const auto* slot = selected_slot();
    auto* backend = AmsState::instance().get_backend();
    if (!slot || !slot->connected || slot->head < 0 || slot->head >= 4 || !backend ||
        (slot->ace >= 0 && !slot->inlet_has_filament)) {
        show_feedback("该槽当前无法进料");
        return;
    }
    if (head_has_filament(*backend, slot->head)) {
        refresh();
        return;
    }
    open_confirm(ConfirmKind::Load);
}

void U1FilamentOverview::on_action_unload() {
    hide_edit_menu();
    const auto* slot = selected_slot();
    const int head = slot ? slot->head : -1;
    auto* backend = AmsState::instance().get_backend();
    if (slot && slot->connected && backend && slot->ace >= 0 && slot->inlet_has_filament &&
        material_workspace::head_physical_presence(backend, head) == true &&
        !material_workspace::head_physical_source(backend, head)) {
        ui::get_material_workspace().open_action(head, slot->backend_unit - 1,
            "confirm_source", {}, slot->local_slot, {}, slot->ace);
        return;
    }
    if (!slot || !slot->connected || !backend ||
        !head_can_unload(*backend, head, slot->ace < 0)) {
        const auto presence = material_workspace::head_physical_presence(backend, head);
        show_feedback(slot && slot->ace >= 0 && slot->inlet_has_filament && presence != true
            ? "喷头未检测到耗材。要取下这卷料，请用「退出 ACE」。"
            : presence == true ? "无法确认喷头的耗材来源，请检查料路。"
                               : "喷头未检测到耗材");
        return;
    }
    open_confirm(ConfirmKind::Unload);
}

void U1FilamentOverview::on_action_eject() {
    hide_edit_menu();
    const auto* slot = selected_slot();
    if (!slot || !slot->connected || !slot->inlet_has_filament || slot->ace < 0) return;
    ui::get_material_workspace().open_action(slot->head, slot->backend_unit - 1, "eject", {},
                                              slot->local_slot, {}, slot->ace);
}

int U1FilamentOverview::dryer_unit(const AmsSystemInfo& info) const {
    const int unit = u1_filament::ace_unit_for_address(info, dryer_ace_);
    return unit >= 0 && info.units[static_cast<size_t>(unit)].connected ? unit : -1;
}

void U1FilamentOverview::on_action_dry() {
    hide_edit_menu();
    const auto* slot = selected_slot();
    auto* backend = AmsState::instance().get_backend();
    if (!slot || !slot->connected || slot->ace < 0 || !backend ||
        !helix::dryer_info(*backend, slot->backend_unit).supported) return;
    if (dryer_ace_ != slot->ace) dryer_pending_action_ = 0;
    dryer_ace_ = slot->ace;
    dryer_roll_slot_ = -1;
    dryer_confirming_ = false;
    dryer_preset_ = 0;
    dryer_temp_c_ = kDryerPresets[0].temp_c;
    dryer_hours_ = kDryerPresets[0].hours;
    show_page(Page::Dryer);
    refresh_dryer();
}

void U1FilamentOverview::open_confirm(ConfirmKind kind) {
    const auto* slot = selected_slot();
    if (!slot) return;
    confirm_kind_ = kind;
    confirm_head_ = slot->head;
    confirm_global_ = slot->global_index;
    confirm_external_ = slot->ace < 0;
    auto* backend = AmsState::instance().get_backend();
    confirm_source_ = material_workspace::head_physical_source(backend, confirm_head_);
    layout_confirm_page();
    const bool load = kind == ConfirmKind::Load;
    const char* title = load ? "确认进料" : "确认从喷头退出";
    std::string target;
    std::string body;
    if (load) {
        target = confirm_external_ ? fmt::format("喷头 {} · 外置料卷", confirm_head_ + 1)
            : fmt::format("ACE {} · {} 槽  →  喷头 {}", slot->ace + 1, slot->local_slot + 1,
                          confirm_head_ + 1);
        body = confirm_external_ ? "从外置入口装入耗材，请确认耗材已插入。"
                                  : "将所选耗材装入喷头，已有耗材会先退料。";
    } else {
        const auto bay = confirm_source_ ? multiace::bay_of(*confirm_source_) : std::nullopt;
        target = bay ? fmt::format("当前供料  ACE {} · {} 槽  →  喷头 {}",
                                   bay->first + 1, bay->second + 1, confirm_head_ + 1)
                     : fmt::format("喷头 {} · 外置料卷", confirm_head_ + 1);
        body = confirm_external_ ? "退出喷头中的耗材，可从外置入口取下。"
                                 : "退出喷头中的耗材，保留在 ACE 内待用。";
    }
    const std::string extra = !load ? "" : slot->has_weight
        ? fmt::format("{}  ·  {}", slot->type, slot->weight) : slot->type;
    lv_label_set_text(confirm_title_, title);
    lv_label_set_text(confirm_target_, target.c_str());
    lv_label_set_text(confirm_extra_, extra.c_str());
    lv_label_set_text(confirm_body_, body.c_str());
    lv_obj_set_hidden(confirm_extra_, extra.empty());
    lv_obj_scroll_to_y(lv_obj_get_parent(confirm_body_), 0, LV_ANIM_OFF);
    if (lv_obj_t* ok = lv_obj_get_child(confirm_ok_, 0))
        lv_label_set_text(ok, "确认操作");
    show_page(Page::Confirm);
}

void U1FilamentOverview::confirm_action() {
    refresh();
    auto* backend = AmsState::instance().get_backend();
    auto& printer = get_printer_state();
    if (!backend || lv_subject_get_int(printer.network_state().get_nav_buttons_enabled_subject()) != 1) {
        lv_label_set_text(confirm_body_, "打印机离线");
        return;
    }
    const auto info = backend->get_system_info();
    if (ams_action_holds_system(info.action) ||
        lv_subject_get_int(printer.print_state().get_job_holds_machine_subject())) {
        lv_label_set_text(confirm_body_, "请等待设备空闲后重试");
        return;
    }
    const auto* slot = info.get_slot_global(confirm_global_);
    const auto* unit = info.unit_for_slot(confirm_global_);
    if (!slot || !unit || !unit->connected || unit->absent || slot->mapped_tool != confirm_head_ ||
        (confirm_external_ && !external_heads_[static_cast<size_t>(confirm_head_)])) {
        lv_label_set_text(confirm_body_, "料槽连接已变化，请返回后重试");
        return;
    }
    const bool load = confirm_kind_ == ConfirmKind::Load;
    if (load && head_has_filament(*backend, confirm_head_)) {
        show_hub();
        return;
    }
    const auto source = material_workspace::head_physical_source(backend, confirm_head_);
    if (source != confirm_source_) {
        lv_label_set_text(confirm_body_, "喷头供料已变化，请返回后重试");
        return;
    }
    // Head unload follows the actual source shown in the dialog, not the
    // selected card. The backend checks the live head and bound source.
    if (load && info.path_held(confirm_head_)) {
        lv_label_set_text(confirm_body_, "耗材料路位置待确认，请先完成位置恢复");
        return;
    }
    if ((load && !confirm_external_ && !slot->is_present()) ||
        (!load && !head_can_unload(*backend, confirm_head_, confirm_external_))) {
        lv_label_set_text(confirm_body_, "耗材状态已变化，请返回后重试");
        return;
    }
    const auto result = load ? backend->load_filament(confirm_global_)
                             : backend->unload_filament(confirm_head_);
    show_hub();
    show_feedback(result.success() ? "命令已受理，等待实际料路确认" : result.user_msg,
                  result.success() ? FeedbackKind::Info : FeedbackKind::Attention);
}

void U1FilamentOverview::build_color_page() {
    color_back_ = make_page_back(color_page_, [](lv_event_t* e) {
        if (auto* self = static_cast<U1FilamentOverview*>(lv_event_get_user_data(e)))
            self->close_color(false);
    });
    color_title_ = make_label(color_page_, "颜色设置", "font_heading", kText());
    color_chip_ = make_box(color_page_);
    outline_obj(color_chip_, lv_color_hex(draft_color_), kBorder(), 6, 1);
    color_slot_ = make_label(color_page_, "", "font_small", kText());
    for (int i = 0; i < 24; ++i) {
        preset_swatches_[static_cast<size_t>(i)] = make_box(color_page_);
        outline_obj(preset_swatches_[static_cast<size_t>(i)], lv_color_hex(kPresets[i]),
                    lv_color_hex(kPresets[i]), 6, 1);
        lv_obj_add_flag(preset_swatches_[static_cast<size_t>(i)], LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_user_data(preset_swatches_[static_cast<size_t>(i)],
                             reinterpret_cast<void*>(static_cast<intptr_t>(i)));
        lv_obj_add_event_cb(preset_swatches_[static_cast<size_t>(i)], [](lv_event_t* e) {
            if (auto* self = static_cast<U1FilamentOverview*>(lv_event_get_user_data(e))) {
                const int idx = static_cast<int>(reinterpret_cast<intptr_t>(
                    lv_obj_get_user_data(static_cast<lv_obj_t*>(lv_event_get_current_target(e)))));
                if (idx >= 0 && idx < 24)
                    self->set_draft_color(kPresets[idx], true);
            }
        }, LV_EVENT_CLICKED, this);
    }
    auto bind_pointer = [](lv_obj_t* obj, void (*fn)(U1FilamentOverview*, lv_event_t*),
                           U1FilamentOverview* self) {
        lv_obj_add_flag(obj, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_add_event_cb(obj, [](lv_event_t* e) {
            if (auto* s = static_cast<U1FilamentOverview*>(lv_event_get_user_data(e))) {
                const lv_event_code_t code = lv_event_get_code(e);
                if (code == LV_EVENT_PRESSING || code == LV_EVENT_CLICKED) {
                    if (lv_event_get_current_target(e) == s->sv_image_)
                        s->update_sv_from_pointer(e);
                    else
                        s->update_hue_from_pointer(e);
                }
            }
        }, LV_EVENT_ALL, self);
        (void)fn;
    };
    sv_image_ = lv_image_create(color_page_);
    lv_obj_set_layout(sv_image_, LV_LAYOUT_NONE);
    lv_obj_add_flag(sv_image_, LV_OBJ_FLAG_IGNORE_LAYOUT);
    bind_pointer(sv_image_, nullptr, this);
    sv_cursor_ = make_box(color_page_);
    outline_obj(sv_cursor_, lv_color_white(), kText(), 6, 2);
    lv_obj_set_size(sv_cursor_, 12, 12);
    hue_image_ = lv_image_create(color_page_);
    lv_obj_set_layout(hue_image_, LV_LAYOUT_NONE);
    lv_obj_add_flag(hue_image_, LV_OBJ_FLAG_IGNORE_LAYOUT);
    bind_pointer(hue_image_, nullptr, this);
    hue_cursor_ = make_box(color_page_);
    fill_obj(hue_cursor_, kText(), 2);
    lv_obj_set_size(hue_cursor_, 10, 8);
    color_cancel_ = make_soft_button(color_page_, "取消", false, [](lv_event_t* e) {
        if (auto* self = static_cast<U1FilamentOverview*>(lv_event_get_user_data(e)))
            self->close_color(false);
    }, this);
    lv_obj_set_name(color_cancel_, "u1_filament_color_cancel");
    color_save_ = make_soft_button(color_page_, "确定", true, [](lv_event_t* e) {
        if (auto* self = static_cast<U1FilamentOverview*>(lv_event_get_user_data(e)))
            self->close_color(true);
    }, this);
}

void U1FilamentOverview::cancel_color_layout_timer() {
    helix::ui::lv_timer_cancel_safe(color_layout_timer_);
    color_layout_timer_ = nullptr;
}

void U1FilamentOverview::schedule_color_layout() {
    cancel_color_layout_timer();
    color_layout_timer_ = lv_timer_create(
        [](lv_timer_t* t) {
            auto* self = static_cast<U1FilamentOverview*>(lv_timer_get_user_data(t));
            if (!self)
                return;
            self->color_layout_timer_ = nullptr;
            if (self->page_ != Page::Color)
                return;
            // Prepare the image sources after the input or size event returns.
            self->ensure_color_canvases();
            self->refresh_color_widgets();
        },
        10, this);
    if (color_layout_timer_)
        lv_timer_set_repeat_count(color_layout_timer_, 1);
}

void U1FilamentOverview::layout_color_geometry() {
    // LVGL's position and size setters schedule layout through style refresh.
    const int host_w = lv_obj_get_width(host_);
    const int host_h = lv_obj_get_height(host_);
    const int width = host_w > 1 ? host_w : 480;
    const int height = host_h > 1 ? host_h : 236;
    const auto body = page_content(width, height, 192);
    constexpr int target_w = 128;
    constexpr int chip = 22;
    place_page_heading(color_back_, color_title_, width, target_w);
    const int target_right = width - kMinMargin;
    const int target_x = target_right - target_w;
    lv_obj_set_pos(color_chip_, target_right - chip, (kToolbarH - chip) / 2);
    lv_obj_set_size(color_chip_, chip, chip);
    lv_obj_set_pos(color_slot_, target_x,
                   (kToolbarH - lv_obj_get_style_text_font(color_slot_, LV_PART_MAIN)->line_height) / 2);
    lv_obj_set_width(color_slot_, target_w - chip - kCardGap);
    lv_label_set_long_mode(color_slot_, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_align(color_slot_, LV_TEXT_ALIGN_RIGHT, LV_PART_MAIN);

    const int palette_w = (body.width - kCardGap) * 5 / 12;
    const int palette_x = body.x + body.width - palette_w;
    constexpr int hue_h = 24;
    constexpr int compact_gap = 4;
    const int sv_h = std::max(1, body.height - hue_h - compact_gap);
    lv_obj_set_pos(sv_image_, palette_x, body.y);
    lv_obj_set_size(sv_image_, palette_w, sv_h);
    lv_obj_set_style_radius(sv_image_, 8, LV_PART_MAIN);
    lv_obj_set_style_clip_corner(sv_image_, true, LV_PART_MAIN);
    lv_obj_set_pos(hue_image_, palette_x, body.y + sv_h + compact_gap);
    lv_obj_set_size(hue_image_, palette_w, hue_h);
    lv_obj_set_style_radius(hue_image_, 6, LV_PART_MAIN);
    lv_obj_set_style_clip_corner(hue_image_, true, LV_PART_MAIN);

    constexpr int cols = 6;
    constexpr int rows = 4;
    const int grid_w = body.width - palette_w - kCardGap;
    const int grid_h = body.height - (rows - 1) * compact_gap;
    for (int i = 0; i < rows * cols; ++i) {
        const int r = i / cols;
        const int top = (r * grid_h + rows / 2) / rows;
        const int bottom = ((r + 1) * grid_h + rows / 2) / rows;
        place_row_cell(preset_swatches_[static_cast<size_t>(i)], body.x,
                       body.y + top + r * compact_gap, grid_w, bottom - top, i % cols,
                       cols, compact_gap);
    }
    place_row_cell(color_cancel_, body.x, body.actions_y, body.width, kActionH, 0, 2, kCardGap);
    place_row_cell(color_save_, body.x, body.actions_y, body.width, kActionH, 1, 2, kCardGap);
}

void U1FilamentOverview::ensure_color_canvases() {
    // Use the assigned pixel sizes even if the display has not laid out this frame yet.
    const int pal_w = lv_obj_get_style_width(sv_image_, LV_PART_MAIN);
    const int sv_h = lv_obj_get_style_height(sv_image_, LV_PART_MAIN);
    const int hue_w = lv_obj_get_style_width(hue_image_, LV_PART_MAIN);
    const int hue_h = lv_obj_get_style_height(hue_image_, LV_PART_MAIN);
    if (!sv_buf_ || sv_w_ != pal_w || sv_h_ != sv_h) {
        lv_image_set_src(sv_image_, nullptr);
        helix::safe_draw_buf_destroy(sv_buf_, "u1_sv");
        sv_w_ = pal_w;
        sv_h_ = sv_h;
        sv_buf_ = lv_draw_buf_create(static_cast<uint32_t>(sv_w_), static_cast<uint32_t>(sv_h_),
                                     UI_NATIVE_COLOR_FORMAT, 0);
        if (sv_buf_)
            lv_image_set_src(sv_image_, sv_buf_);
    }
    if (!hue_buf_ || hue_w_ != hue_w || hue_h_ != hue_h) {
        lv_image_set_src(hue_image_, nullptr);
        helix::safe_draw_buf_destroy(hue_buf_, "u1_hue");
        hue_w_ = hue_w;
        hue_h_ = hue_h;
        hue_buf_ = lv_draw_buf_create(static_cast<uint32_t>(hue_w_), static_cast<uint32_t>(hue_h_),
                                      UI_NATIVE_COLOR_FORMAT, 0);
        if (hue_buf_)
            lv_image_set_src(hue_image_, hue_buf_);
        render_hue_canvas();
    }
}

void U1FilamentOverview::set_draft_color(uint32_t rgb, bool) {
    draft_color_ = rgb & 0xFFFFFFu;
    rgb_to_hsv(draft_color_, &draft_h_, &draft_s_, &draft_v_);
    refresh_color_widgets();
}

void U1FilamentOverview::refresh_color_widgets() {
    if (!color_chip_)
        return;
    outline_obj(color_chip_, lv_color_hex(draft_color_), kBorder(), 6, 1);
    lv_label_set_text(color_slot_, edit_label_.c_str());
    for (int i = 0; i < 24; ++i) {
        const bool on = kPresets[i] == draft_color_;
        const uint32_t col = kPresets[i];
        const int minc = std::min({static_cast<int>((col >> 16) & 0xFF),
                                   static_cast<int>((col >> 8) & 0xFF),
                                   static_cast<int>(col & 0xFF)});
        const lv_color_t outline = on ? kPrimary() : (minc > 230 ? kBorder() : lv_color_hex(col));
        outline_obj(preset_swatches_[static_cast<size_t>(i)], lv_color_hex(col), outline, 6,
                    on ? 2 : 1);
    }
    render_sv_canvas();
    if (sv_buf_) {
        const int x = lv_obj_get_style_x(sv_image_, LV_PART_MAIN) +
                      static_cast<int>(draft_s_ * (sv_w_ - 1)) - 6;
        const int y = lv_obj_get_style_y(sv_image_, LV_PART_MAIN) +
                      static_cast<int>((1.0f - draft_v_) * (sv_h_ - 1)) - 6;
        lv_obj_set_pos(sv_cursor_, x, y);
        lv_obj_move_foreground(sv_cursor_);
    }
    if (hue_buf_) {
        const int x = lv_obj_get_style_x(hue_image_, LV_PART_MAIN) +
                      static_cast<int>(draft_h_ * (hue_w_ - 1)) - 5;
        const int y = lv_obj_get_style_y(hue_image_, LV_PART_MAIN) - 4;
        lv_obj_set_pos(hue_cursor_, x, y);
        lv_obj_move_foreground(hue_cursor_);
    }
}

uint32_t U1FilamentOverview::hsv_to_rgb(float h, float s, float v) {
    h = std::clamp(h, 0.0f, 1.0f);
    s = std::clamp(s, 0.0f, 1.0f);
    v = std::clamp(v, 0.0f, 1.0f);
    const float c = v * s;
    const float x = c * (1.0f - std::fabs(std::fmod(h * 6.0f, 2.0f) - 1.0f));
    const float m = v - c;
    float r = 0, g = 0, b = 0;
    const int region = static_cast<int>(h * 6.0f) % 6;
    switch (region) {
        case 0: r = c; g = x; break;
        case 1: r = x; g = c; break;
        case 2: g = c; b = x; break;
        case 3: g = x; b = c; break;
        case 4: r = x; b = c; break;
        default: r = c; b = x; break;
    }
    const auto ch = [](float n) {
        return static_cast<uint32_t>(std::clamp(std::round((n) * 255.0f), 0.0f, 255.0f));
    };
    return (ch(r + m) << 16) | (ch(g + m) << 8) | ch(b + m);
}

void U1FilamentOverview::rgb_to_hsv(uint32_t rgb, float* h, float* s, float* v) {
    const float r = ((rgb >> 16) & 0xFF) / 255.0f;
    const float g = ((rgb >> 8) & 0xFF) / 255.0f;
    const float b = (rgb & 0xFF) / 255.0f;
    const float maxc = std::max({r, g, b});
    const float minc = std::min({r, g, b});
    const float d = maxc - minc;
    float hh = 0;
    if (d > 1e-6f) {
        if (maxc == r) hh = std::fmod((g - b) / d, 6.0f);
        else if (maxc == g) hh = (b - r) / d + 2.0f;
        else hh = (r - g) / d + 4.0f;
        hh /= 6.0f;
        if (hh < 0) hh += 1.0f;
    }
    if (h) *h = hh;
    if (s) *s = maxc > 1e-6f ? d / maxc : 0;
    if (v) *v = maxc;
}

void U1FilamentOverview::render_sv_canvas() {
    if (!sv_buf_ || sv_w_ <= 0 || sv_h_ <= 0)
        return;
    for (int y = 0; y < sv_h_; ++y) {
        const float val = 1.0f - (sv_h_ <= 1 ? 0.0f : static_cast<float>(y) / (sv_h_ - 1));
        for (int x = 0; x < sv_w_; ++x) {
            const float sat = sv_w_ <= 1 ? 0.0f : static_cast<float>(x) / (sv_w_ - 1);
            write_native_pixel(sv_buf_, x, y, hsv_to_rgb(draft_h_, sat, val));
        }
    }
    if (sv_image_)
        lv_obj_invalidate(sv_image_);
}

void U1FilamentOverview::render_hue_canvas() {
    if (!hue_buf_ || hue_w_ <= 0 || hue_h_ <= 0)
        return;
    for (int x = 0; x < hue_w_; ++x) {
        const float h = hue_w_ <= 1 ? 0.0f : static_cast<float>(x) / (hue_w_ - 1);
        const uint32_t rgb = hsv_to_rgb(h, 1.0f, 1.0f);
        for (int y = 0; y < hue_h_; ++y)
            write_native_pixel(hue_buf_, x, y, rgb);
    }
    if (hue_image_)
        lv_obj_invalidate(hue_image_);
}

void U1FilamentOverview::update_sv_from_pointer(lv_event_t*) {
    if (!sv_image_ || sv_w_ <= 1 || sv_h_ <= 1)
        return;
    lv_indev_t* indev = lv_indev_active();
    if (!indev)
        return;
    lv_point_t p{};
    lv_indev_get_point(indev, &p);
    lv_area_t a{};
    lv_obj_get_coords(sv_image_, &a);
    draft_s_ = std::clamp(static_cast<float>(p.x - a.x1) / static_cast<float>(sv_w_ - 1), 0.0f, 1.0f);
    draft_v_ = 1.0f - std::clamp(static_cast<float>(p.y - a.y1) / static_cast<float>(sv_h_ - 1),
                                 0.0f, 1.0f);
    draft_color_ = hsv_to_rgb(draft_h_, draft_s_, draft_v_);
    refresh_color_widgets();
}

void U1FilamentOverview::update_hue_from_pointer(lv_event_t*) {
    if (!hue_image_ || hue_w_ <= 1)
        return;
    lv_indev_t* indev = lv_indev_active();
    if (!indev)
        return;
    lv_point_t p{};
    lv_indev_get_point(indev, &p);
    lv_area_t a{};
    lv_obj_get_coords(hue_image_, &a);
    draft_h_ = std::clamp(static_cast<float>(p.x - a.x1) / static_cast<float>(hue_w_ - 1), 0.0f, 1.0f);
    draft_color_ = hsv_to_rgb(draft_h_, draft_s_, draft_v_);
    render_sv_canvas();
    refresh_color_widgets();
}

void U1FilamentOverview::build_dryer_page() {
    UI_MANAGED_SUBJECT_STRING(dryer_title_state_, dryer_title_buffer_, "烘干", "u1_dryer_title", dryer_subjects_);
    UI_MANAGED_SUBJECT_STRING(dryer_temperature_, dryer_temperature_buffer_, "仓温 —", "u1_dryer_temperature", dryer_subjects_);
    UI_MANAGED_SUBJECT_STRING(dryer_humidity_, dryer_humidity_buffer_, "湿度 —", "u1_dryer_humidity", dryer_subjects_);
    UI_MANAGED_SUBJECT_STRING(dryer_status_state_, dryer_status_buffer_, "读取中", "u1_dryer_status", dryer_subjects_);
    UI_MANAGED_SUBJECT_STRING(dryer_temp_value_, dryer_temp_buffer_, "45", "u1_dryer_temp_value", dryer_subjects_);
    UI_MANAGED_SUBJECT_STRING(dryer_hours_value_, dryer_hours_buffer_, "4", "u1_dryer_hours_value", dryer_subjects_);
    UI_MANAGED_SUBJECT_STRING(dryer_cycle_text_, dryer_cycle_buffer_, "", "u1_dryer_cycle_text", dryer_subjects_);
    UI_MANAGED_SUBJECT_INT(dryer_roll_offer_, 0, "u1_dryer_roll_offer", dryer_subjects_);
    UI_MANAGED_SUBJECT_INT(dryer_roll_selected_, -1, "u1_dryer_roll_selected", dryer_subjects_);
    UI_MANAGED_SUBJECT_INT(dryer_confirm_, 0, "u1_dryer_confirming", dryer_subjects_);
    for (size_t i = 0; i < dryer_roll_allowed_.size(); ++i) {
        const auto name = fmt::format("u1_dryer_roll_allowed_{}", i);
        UI_MANAGED_SUBJECT_INT(dryer_roll_allowed_[i], 0, name.c_str(), dryer_subjects_);
    }
    UI_MANAGED_SUBJECT_INT(dryer_ready_, 0, "u1_dryer_ready", dryer_subjects_);
    UI_MANAGED_SUBJECT_INT(dryer_active_, 0, "u1_dryer_active", dryer_subjects_);
    UI_MANAGED_SUBJECT_INT(dryer_can_start_, 0, "u1_dryer_can_start", dryer_subjects_);
    UI_MANAGED_SUBJECT_INT(dryer_pending_state_, 0, "u1_dryer_pending", dryer_subjects_);
    UI_MANAGED_SUBJECT_INT(dryer_preset_state_, 0, "u1_dryer_preset", dryer_subjects_);
    for (size_t i = 0; i < dryer_preset_text_.size(); ++i) {
        const auto name = fmt::format("u1_dryer_preset_{}", i);
        UI_MANAGED_SUBJECT_STRING(dryer_preset_text_[i], dryer_preset_buffers_[i], "", name.c_str(), dryer_subjects_);
    }
    static const auto owner = [](lv_event_t* event) -> U1FilamentOverview* {
        for (auto* obj = lv_event_get_target_obj(event); obj; obj = lv_obj_get_parent(obj)) {
            const auto* name = lv_obj_get_name(obj);
            if (name && std::strcmp(name, "u1_dryer_content") == 0)
                return static_cast<U1FilamentOverview*>(lv_obj_get_user_data(obj));
        }
        return nullptr;
    };
    lv_xml_register_event_cb(nullptr, "u1_dryer_back", [](lv_event_t* event) {
        if (auto* self = owner(event)) self->show_hub();
    });
    lv_xml_register_event_cb(nullptr, "u1_dryer_temp", [](lv_event_t* event) {
        if (auto* self = owner(event)) self->open_number_keypad(true);
    });
    lv_xml_register_event_cb(nullptr, "u1_dryer_hours", [](lv_event_t* event) {
        if (auto* self = owner(event)) self->open_number_keypad(false);
    });
    lv_xml_register_event_cb(nullptr, "u1_dryer_preset", [](lv_event_t* event) {
        const auto* value = static_cast<const char*>(lv_event_get_user_data(event));
        if (auto* self = owner(event); self && value && value[0] >= '0' && value[0] <= '4' && value[1] == '\0')
            self->apply_dryer_preset(value[0] - '0');
    });
    lv_xml_register_event_cb(nullptr, "u1_dryer_start", [](lv_event_t* event) {
        if (auto* self = owner(event)) self->start_drying();
    });
    lv_xml_register_event_cb(nullptr, "u1_dryer_roll", [](lv_event_t* event) {
        const auto* slot = static_cast<const char*>(lv_event_get_user_data(event));
        if (auto* self = owner(event); self && slot && slot[0] >= '0' && slot[0] <= '3' && !slot[1])
            self->select_dryer_roll(slot[0] - '0');
    });
    lv_xml_register_event_cb(nullptr, "u1_dryer_confirm", [](lv_event_t* event) {
        if (auto* self = owner(event)) self->start_drying(true);
    });
    lv_xml_register_event_cb(nullptr, "u1_dryer_reselect", [](lv_event_t* event) {
        if (auto* self = owner(event)) {
            self->dryer_confirming_ = false;
            self->refresh_dryer();
        }
    });
    lv_xml_register_event_cb(nullptr, "u1_dryer_stop", [](lv_event_t* event) {
        if (auto* self = owner(event)) self->stop_drying();
    });
    lv_xml_register_event_cb(nullptr, "u1_dryer_auto", [](lv_event_t* event) {
        if (auto* self = owner(event)) self->open_auto_drying();
    });
    dryer_content_ = static_cast<lv_obj_t*>(lv_xml_create(dryer_page_, "u1_dryer_content", nullptr));
    if (!dryer_content_) return;
    lv_obj_set_user_data(dryer_content_, this);
    // Re-read the selected ACE's cached telemetry even when only environment
    // data changes; this never sends a command or starts its own network poll.
    dryer_refresh_timer_.reset(lv_timer_create([](lv_timer_t* timer) {
        auto* self = static_cast<U1FilamentOverview*>(lv_timer_get_user_data(timer));
        if (self && self->page_ == Page::Dryer) self->refresh_dryer();
    }, 1000, this));
}

void U1FilamentOverview::apply_dryer_preset(int index) {
    if (index < 0 || index >= 5)
        return;
    dryer_preset_ = index;
    dryer_temp_c_ = kDryerPresets[index].temp_c;
    dryer_hours_ = kDryerPresets[index].hours;
    if (auto* backend = AmsState::instance().get_backend()) {
        const int unit = dryer_unit(backend->get_system_info());
        if (unit >= 0)
            dryer_temp_c_ = static_cast<int>(std::round(helix::dryer_info(*backend, unit).clamp_temp(
                static_cast<float>(kDryerPresets[index].temp_c))));
    }
    refresh_dryer();
}

void U1FilamentOverview::refresh_dryer() {
    if (!dryer_content_) return;
    auto* backend = AmsState::instance().get_backend();
    const auto system = backend ? backend->get_system_info() : AmsSystemInfo{};
    const int unit = dryer_unit(system);
    const bool online = lv_subject_get_int(
        get_printer_state().network_state().get_nav_buttons_enabled_subject()) == 1;
    const bool connected = online && backend && unit >= 0;
    const auto info = connected ? helix::dryer_info(*backend, unit) : DryerInfo{};
    const bool ready = connected && info.supported;
    auto* phased = connected ? backend->as<multiace::ICycleDryer>() : nullptr;
    const auto cycle = phased ? phased->dry_cycle(unit) : std::nullopt;
    // Unknown and error both need a verified stop before another cycle.
    const bool occupied = info.active || (cycle && !cycle->idle());
    const bool starting_confirmed = cycle ? (cycle->phase == "warming" || cycle->phase == "drying" ||
        cycle->phase == "venting") : info.active;
    const int revision = lv_subject_get_int(AmsState::instance().get_ams_data_revision_subject());
    if (dryer_pending_action_ && (!ready ||
        (revision != dryer_request_revision_ && (dryer_pending_action_ == 1 ? starting_confirmed : !occupied)) ||
        (revision != dryer_request_revision_ && cycle &&
         (cycle->phase == "unknown" || cycle->phase == "error")))) {
        dryer_pending_action_ = 0;
    } else if (dryer_pending_action_ && lv_tick_elaps(dryer_request_at_) >= 10000) {
        dryer_pending_action_ = 0;
        show_feedback("烘干状态尚未确认，请查看设备后重试。", FeedbackKind::Attention);
    }
    const bool job = lv_subject_get_int(
        get_printer_state().print_state().get_job_holds_machine_subject()) != 0;
    const bool can_start = ready && !occupied && !dryer_pending_action_ &&
        (!job || info.allows_during_print) && !ams_action_holds_system(system.action);
    lv_subject_set_int(&dryer_ready_, ready);
    lv_subject_set_int(&dryer_active_, ready && occupied);
    const bool roll_offer = can_start && cycle && cycle->roll_supported;
    for (int i = 0; i < 4; ++i)
        lv_subject_set_int(&dryer_roll_allowed_[i], roll_offer && cycle->permits_roll(i));
    if (dryer_roll_slot_ >= 0 && (!roll_offer || !cycle->permits_roll(dryer_roll_slot_))) {
        dryer_roll_slot_ = -1;
        dryer_confirming_ = false;
    }
    lv_subject_set_int(&dryer_roll_offer_, roll_offer);
    lv_subject_set_int(&dryer_roll_selected_, dryer_roll_slot_);
    lv_subject_set_int(&dryer_confirm_, dryer_confirming_);
    std::string detail;
    if (cycle) {
        detail = cycle->message;
        if (cycle->exhaust_supported && !cycle->idle())
            detail += cycle->exhaust == "open" ? " · 风门已开" :
                      cycle->exhaust == "closed" ? " · 风门已关" : " · 风门待确认";
        if (cycle->roll_slot >= 0 || cycle->roll_count > 0)
            detail += fmt::format(" · 回卷 {} 次", cycle->roll_count);
    }
    lv_subject_copy_string(&dryer_cycle_text_, detail.c_str());
    lv_subject_set_int(&dryer_pending_state_, dryer_pending_action_);
    lv_subject_set_int(&dryer_can_start_, can_start);
    lv_subject_set_int(&dryer_preset_state_, info.active ? -1 : dryer_preset_);
    lv_subject_copy_string(&dryer_title_state_, dryer_confirming_
        ? fmt::format("ACE {} · {} 槽 · {}°C · {} h", dryer_ace_ + 1, dryer_roll_slot_ + 1,
                      dryer_temp_c_, dryer_hours_).c_str()
        : fmt::format("ACE {} 烘干", dryer_ace_ + 1).c_str());
    std::string temperature = "仓温 —", humidity = "湿度 —";
    if (connected) {
        const auto& environment = system.units[static_cast<size_t>(unit)].environment;
        if (environment && environment->temperature_available && std::isfinite(environment->temperature_c))
            temperature = fmt::format("仓温 {:.0f}°C", environment->temperature_c);
        else if (std::isfinite(info.current_temp_c) && info.current_temp_c > 0)
            temperature = fmt::format("仓温 {:.0f}°C", info.current_temp_c);
        if (environment && environment->has_humidity && std::isfinite(environment->humidity_pct))
            humidity = fmt::format("湿度 {:.0f}%", environment->humidity_pct);
    }
    lv_subject_copy_string(&dryer_temperature_, temperature.c_str());
    lv_subject_copy_string(&dryer_humidity_, humidity.c_str());
    std::string status = !connected ? "设备离线" : !ready ? "不支持烘干" :
        dryer_pending_action_ == 1 ? "正在启动…" : dryer_pending_action_ == 2 ? "正在停止…" :
        info.active ? (info.duration_min > 0 ? fmt::format("剩余 {}h{:02}m", info.remaining_min / 60,
                                                        info.remaining_min % 60) : "烘干中") :
        job && !info.allows_during_print ? "打印中" : "待机";
    if (cycle && !cycle->idle() && !dryer_pending_action_) {
        static const std::pair<const char*, const char*> phases[] = {
            {"starting", "启动中"}, {"warming", "升温中"}, {"drying", "烘干中"},
            {"venting", "排湿中"}, {"stopping", "停止中"}, {"error", "异常"}, {"unknown", "待核实"}};
        status = "待核实";
        for (const auto& [value, label] : phases)
            if (cycle->phase == value) status = label;
    }
    lv_subject_copy_string(&dryer_status_state_, status.c_str());
    lv_subject_copy_string(&dryer_temp_value_, fmt::format("{:.0f}", info.active ? info.target_temp_c :
        ready ? info.clamp_temp(static_cast<float>(dryer_temp_c_)) : dryer_temp_c_).c_str());
    lv_subject_copy_string(&dryer_hours_value_, info.active && info.duration_min > 0
        ? fmt::format("{:g}", info.duration_min / 60.0).c_str() : fmt::format("{}", dryer_hours_).c_str());
    for (size_t i = 0; i < dryer_preset_text_.size(); ++i) {
        const auto& preset = kDryerPresets[i];
        const auto temp = ready ? info.clamp_temp(static_cast<float>(preset.temp_c)) : preset.temp_c;
        lv_subject_copy_string(&dryer_preset_text_[i], fmt::format("{}\n{:.0f}°C/{}h", preset.name, temp,
                                                                  preset.hours).c_str());
    }
}

void U1FilamentOverview::open_number_keypad(bool temperature) {
    refresh_dryer();
    if (!lv_subject_get_int(&dryer_can_start_)) return;
    ui_keypad_config_t config{};
    config.initial_value = static_cast<float>(temperature ? dryer_temp_c_ : dryer_hours_);
    config.min_value = temperature ? 20.0f : 1.0f;
    config.max_value = temperature ? 90.0f : 24.0f;
    if (temperature) {
        if (auto* backend = AmsState::instance().get_backend()) {
            const int unit = backend ? dryer_unit(backend->get_system_info()) : -1;
            if (unit >= 0) {
                const auto info = helix::dryer_info(*backend, unit);
                if (info.max_temp_c > 0) config.max_value = info.max_temp_c;
                if (info.min_temp_c > 0) config.min_value = info.min_temp_c;
            }
        }
    }
    config.title_label = temperature ? "烘干温度" : "烘干时间";
    config.unit_label = temperature ? "°C" : "h";
    config.allow_decimal = false;
    config.allow_negative = false;
    config.callback = nullptr;
    config.user_data = this;
    config.on_confirm = [this, temperature](float value) {
        if (temperature)
            dryer_temp_c_ = static_cast<int>(std::round(value));
        else
            dryer_hours_ = static_cast<int>(std::round(value));
        dryer_preset_ = -1;
        refresh_dryer();
    };
    ui_keypad_show(&config);
}

void U1FilamentOverview::select_dryer_roll(int slot) {
    refresh_dryer();
    if (slot < 0 || slot > 3 || !lv_subject_get_int(&dryer_roll_allowed_[slot])) return;
    dryer_roll_slot_ = dryer_roll_slot_ == slot ? -1 : slot;
    dryer_confirming_ = false;
    refresh_dryer();
}

void U1FilamentOverview::start_drying(bool confirmed) {
    const int selected = dryer_roll_slot_;
    refresh_dryer();
    if (confirmed && (!dryer_confirming_ || selected < 0 || selected != dryer_roll_slot_)) {
        show_feedback("回卷条件已变，请重新选择槽位。", FeedbackKind::Attention);
        return;
    }
    if (selected != dryer_roll_slot_) return;
    auto* backend = AmsState::instance().get_backend();
    auto* dryer = backend ? backend->as<IDryer>() : nullptr;
    const int unit = backend ? dryer_unit(backend->get_system_info()) : -1;
    if (!dryer || unit < 0 || !lv_subject_get_int(&dryer_can_start_)) {
        show_feedback("当前设备不能开始烘干，请确认连接和运行状态。", FeedbackKind::Attention);
        return;
    }
    const auto info = helix::dryer_info(*backend, unit);
    DryingRequest request;
    request.temp_c = info.clamp_temp(static_cast<float>(dryer_temp_c_));
    request.duration_min = info.clamp_duration(dryer_hours_ * 60);
    request.unit = unit;
    if (selected >= 0 && !confirmed) {
        dryer_confirming_ = true;
        refresh_dryer();
        return;
    }
    auto* phased = backend->as<multiace::ICycleDryer>();
    if (selected >= 0 && !phased) return;
    dryer_confirming_ = false;
    dryer_request_revision_ = lv_subject_get_int(AmsState::instance().get_ams_data_revision_subject());
    const auto result = selected >= 0 ?
        phased->start_confirmed_roll(request.temp_c, request.duration_min, unit, dryer_ace_, selected) :
        dryer->start_drying_request(request);
    if (!result.success()) {
        show_feedback(result.user_msg, FeedbackKind::Attention);
        return;
    }
    dryer_roll_slot_ = -1;
    dryer_pending_action_ = 1;
    dryer_request_at_ = lv_tick_get();
    refresh_dryer();
}

void U1FilamentOverview::stop_drying() {
    refresh_dryer();
    auto* backend = AmsState::instance().get_backend();
    auto* dryer = backend ? backend->as<IDryer>() : nullptr;
    const int unit = backend ? dryer_unit(backend->get_system_info()) : -1;
    // Stopping remains available during a print or filament operation.
    if (!dryer || unit < 0 || !lv_subject_get_int(&dryer_ready_) || dryer_pending_action_ == 2)
        return;
    dryer_request_revision_ = lv_subject_get_int(AmsState::instance().get_ams_data_revision_subject());
    const auto result = dryer->stop_drying(unit);
    if (!result.success()) {
        show_feedback(result.user_msg, FeedbackKind::Attention);
        return;
    }
    dryer_pending_action_ = 2;
    dryer_request_at_ = lv_tick_get();
    refresh_dryer();
}

void U1FilamentOverview::open_auto_drying() {
    refresh_dryer();
    auto* backend = AmsState::instance().get_backend();
    const int unit = backend ? dryer_unit(backend->get_system_info()) : -1;
    if (unit < 0 || !lv_subject_get_int(&dryer_ready_) || dryer_pending_action_) return;
    // Reuse the device-backed strategy editor, including its saved thresholds,
    // validation, enable/disable actions and the expected physical ACE address.
    ui::get_material_workspace().open_action(selected_head_, unit - 1, "auto_dry", {}, -1, {}, dryer_ace_);
}

void U1FilamentOverview::build_confirm_page() {
    confirm_back_ = make_page_back(confirm_page_, [](lv_event_t* e) {
        if (auto* self = static_cast<U1FilamentOverview*>(lv_event_get_user_data(e)))
            self->show_hub();
    });
    confirm_title_ = make_label(confirm_page_, "确认操作", "font_heading", kText());
    lv_obj_t* card = make_box(confirm_page_);
    outline_obj(card, kCard(), kBorder(), kCardRadius, 1);
    lv_obj_set_name(card, "u1_confirm_card");
    confirm_target_ = make_label(card, "", "font_heading", kText());
    confirm_extra_ = make_label(card, "", "font_small", kMuted());
    confirm_body_ = make_label(card, "", "font_body", kText());
    lv_obj_set_flex_flow(card, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_all(card, 12, LV_PART_MAIN);
    lv_obj_set_style_pad_gap(card, kCardGap, LV_PART_MAIN);
    lv_obj_add_flag(card, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scroll_dir(card, LV_DIR_VER);
    lv_obj_set_scrollbar_mode(card, LV_SCROLLBAR_MODE_AUTO);
    for (auto* label : {confirm_target_, confirm_extra_, confirm_body_}) {
        lv_obj_remove_flag(label, LV_OBJ_FLAG_IGNORE_LAYOUT);
        lv_obj_set_width(label, lv_pct(100));
        lv_label_set_long_mode(label, LV_LABEL_LONG_WRAP);
    }
    confirm_cancel_ = make_soft_button(confirm_page_, "取消", false, [](lv_event_t* e) {
        if (auto* self = static_cast<U1FilamentOverview*>(lv_event_get_user_data(e)))
            self->show_hub();
    }, this);
    confirm_ok_ = make_soft_button(confirm_page_, "确认操作", true, [](lv_event_t* e) {
        if (auto* self = static_cast<U1FilamentOverview*>(lv_event_get_user_data(e)))
            self->confirm_action();
    }, this);
}

void U1FilamentOverview::layout_confirm_page() {
    if (!confirm_page_ || !host_)
        return;
    const int width = lv_obj_get_width(host_);
    const int height = lv_obj_get_height(host_);
    if (width <= 1 || height <= 1)
        return;
    place_page_heading(confirm_back_, confirm_title_, width, 64);
    const auto body = page_content(width, height, 152);
    if (lv_obj_t* card = lv_obj_find_by_name(confirm_page_, "u1_confirm_card")) {
        lv_obj_set_pos(card, body.x, body.y);
        lv_obj_set_size(card, body.width, body.height);
    }
    place_row_cell(confirm_cancel_, body.x, body.actions_y, body.width, kActionH, 0, 2, kCardGap);
    place_row_cell(confirm_ok_, body.x, body.actions_y, body.width, kActionH, 1, 2, kCardGap);
}

} // namespace helix
