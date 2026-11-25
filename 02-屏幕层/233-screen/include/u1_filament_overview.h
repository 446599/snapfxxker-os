// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include "ui_observer_guard.h"
#include "ui_timer_guard.h"
#include "ui_widget_ref.h"

#include "ams_types.h"
#include "subject_managed_panel.h"
#include "u1_filament_model.h"

#include "lvgl/lvgl.h"

#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace helix {
class AmsBackend;

// U1 耗材 tab: content-band overview that keeps the standard header + bell.
// New frontend; does not restyle the old inventory widget grid.
class U1FilamentOverview {
  public:
    U1FilamentOverview();
    ~U1FilamentOverview();

    U1FilamentOverview(const U1FilamentOverview&) = delete;
    U1FilamentOverview& operator=(const U1FilamentOverview&) = delete;

    void setup(lv_obj_t* host);
    void show_hub();

  private:
    enum class Page { Hub, Edit, Color, Dryer, Confirm };
    enum class ConfirmKind { Load, Unload };
    enum class FeedbackKind { Attention, Info, Success };

    struct SlotCard {
        int backend_unit = -1;
        int ace = -1;
        int local_slot = -1;
        int head = -1;
        int global_index = -1;
        bool present = false;
        bool connected = false;
        bool editable = false;
        bool inlet_has_filament = false;
        bool empty = false;
        bool has_color = false;
        bool has_weight = false;
        bool current = false;
        bool source_unknown = false;
        uint32_t color_rgb = 0;
        float remaining_g = -1;
        float fill = 0;
        std::string title;
        std::string type;
        std::string weight;
    };

    void observe();
    void apply_theme();
    void refresh();
    void layout_hub();
    void apply_hub_data();
    void select_card(int index);
    void select_group(int index);
    void set_view_mode(int mode);
    void show_page(Page page);
    void set_secondary_fullscreen(bool fullscreen);
    void hide_edit_menu();
    void show_edit_menu(int card);
    void on_identify();
    void open_edit();
    void build_edit_page();
    void open_color();
    void close_color(bool accept);
    void save_edit();
    void refresh_edit_summary();
    bool head_can_unload(const AmsBackend& backend, int head, bool external) const;
    const SlotCard* selected_slot() const;
    int dryer_unit(const AmsSystemInfo& info) const;
    void schedule_color_layout();
    void cancel_color_layout_timer();
    void on_action_load();
    void on_action_unload();
    void on_action_eject();
    void on_action_dry();
    void open_confirm(ConfirmKind kind);
    void confirm_action();
    void build_color_page();
    void layout_color_geometry();
    void ensure_color_canvases();
    void set_draft_color(uint32_t rgb, bool from_preset);
    void refresh_color_widgets();
    void build_dryer_page();
    void refresh_dryer();
    void apply_dryer_preset(int index);
    void select_dryer_roll(int slot);
    void start_drying(bool confirmed = false);
    void stop_drying();
    void open_auto_drying();
    void open_number_keypad(bool temperature);
    void build_confirm_page();
    void layout_confirm_page();
    void show_feedback(const std::string& text, FeedbackKind kind = FeedbackKind::Attention);
    void style_card_like(lv_obj_t* obj, bool selected, bool elevated = false);
    lv_obj_t* make_label(lv_obj_t* parent, const char* text, const char* font, lv_color_t color);
    lv_obj_t* make_soft_button(lv_obj_t* parent, const char* text, bool primary, lv_event_cb_t cb,
                               void* user);
    lv_obj_t* make_page_back(lv_obj_t* parent, lv_event_cb_t cb);
    static uint32_t hsv_to_rgb(float h, float s, float v);
    static void rgb_to_hsv(uint32_t rgb, float* h, float* s, float* v);
    void render_sv_canvas();
    void render_hue_canvas();
    void update_sv_from_pointer(lv_event_t* e);
    void update_hue_from_pointer(lv_event_t* e);
    void on_host_size(lv_event_t* e);

    ui::WidgetRef host_;
    ui::WidgetRef host_parent_;
    lv_obj_t* hub_ = nullptr;
    lv_obj_t* edit_page_ = nullptr;
    lv_obj_t* color_page_ = nullptr;
    lv_obj_t* dryer_page_ = nullptr;
    lv_obj_t* confirm_page_ = nullptr;

    lv_obj_t* mode_bar_ = nullptr;
    lv_obj_t* mode_ace_ = nullptr;
    lv_obj_t* mode_head_ = nullptr;
    std::array<lv_obj_t*, 4> group_pills_{};
    lv_obj_t* cards_row_ = nullptr;
    lv_obj_t* external_title_ = nullptr;
    struct CardWidgets {
        lv_obj_t* root = nullptr;
        lv_obj_t* title = nullptr;
        lv_obj_t* pencil = nullptr;
        lv_obj_t* well = nullptr;
        lv_obj_t* fill = nullptr;
        lv_obj_t* type = nullptr;
    };
    std::array<CardWidgets, 4> cards_{};
    std::array<lv_obj_t*, 4> actions_{};
    lv_obj_t* dim_ = nullptr;
    lv_obj_t* edit_menu_ = nullptr;
    lv_obj_t* edit_identify_ = nullptr;
    lv_obj_t* edit_edit_ = nullptr;
    lv_obj_t* empty_label_ = nullptr;
    lv_obj_t* material_dropdown_ = nullptr;
    lv_subject_t edit_visible_{}, edit_target_{}, edit_summary_{};
    lv_subject_t edit_quick_color_{}, edit_quick_colors_[8]{};
    lv_subject_t external_supply_visible_{};
    char edit_target_buffer_[96]{}, edit_summary_buffer_[32]{};
    SubjectManager edit_subjects_;

    lv_obj_t* color_back_ = nullptr;
    lv_obj_t* color_title_ = nullptr;
    lv_obj_t* color_chip_ = nullptr;
    lv_obj_t* color_slot_ = nullptr;
    std::array<lv_obj_t*, 24> preset_swatches_{};
    lv_obj_t* sv_image_ = nullptr;
    lv_obj_t* sv_cursor_ = nullptr;
    lv_obj_t* hue_image_ = nullptr;
    lv_obj_t* hue_cursor_ = nullptr;
    lv_obj_t* color_cancel_ = nullptr;
    lv_obj_t* color_save_ = nullptr;
    lv_draw_buf_t* sv_buf_ = nullptr;
    lv_draw_buf_t* hue_buf_ = nullptr;
    int sv_w_ = 0;
    int sv_h_ = 0;
    int hue_w_ = 0;
    int hue_h_ = 0;

    lv_obj_t* dryer_content_ = nullptr;
    lv_subject_t dryer_title_state_{}, dryer_temperature_{}, dryer_humidity_{}, dryer_status_state_{};
    lv_subject_t dryer_temp_value_{}, dryer_hours_value_{};
    lv_subject_t dryer_cycle_text_{}, dryer_roll_offer_{}, dryer_roll_selected_{}, dryer_confirm_{};
    std::array<lv_subject_t, 4> dryer_roll_allowed_{};
    char dryer_cycle_buffer_[256]{};
    int dryer_roll_slot_ = -1;
    bool dryer_confirming_ = false;
    lv_subject_t dryer_ready_{}, dryer_active_{}, dryer_can_start_{}, dryer_pending_state_{}, dryer_preset_state_{};
    std::array<lv_subject_t, 5> dryer_preset_text_{};
    char dryer_title_buffer_[64]{}, dryer_temperature_buffer_[48]{}, dryer_humidity_buffer_[48]{};
    char dryer_status_buffer_[96]{}, dryer_temp_buffer_[24]{}, dryer_hours_buffer_[24]{};
    char dryer_preset_buffers_[5][48]{};
    SubjectManager dryer_subjects_;
    ui::LvglTimerGuard dryer_refresh_timer_;
    int dryer_pending_action_ = 0, dryer_request_revision_ = 0;
    uint32_t dryer_request_at_ = 0;

    lv_obj_t* confirm_back_ = nullptr;
    lv_obj_t* confirm_title_ = nullptr;
    lv_obj_t* confirm_target_ = nullptr;
    lv_obj_t* confirm_extra_ = nullptr;
    lv_obj_t* confirm_body_ = nullptr;
    lv_obj_t* confirm_cancel_ = nullptr;
    lv_obj_t* confirm_ok_ = nullptr;

    Page page_ = Page::Hub;
    ConfirmKind confirm_kind_ = ConfirmKind::Load;
    std::optional<int> confirm_source_;
    int confirm_head_ = -1, confirm_global_ = -1;
    bool confirm_external_ = false;
    int view_mode_ = 0;
    bool showing_ace_ = false;
    bool external_only_ = false;
    int selected_ace_ = -1;
    int selected_head_ = 0;
    int selected_global_ = -1;
    int selected_card_ = -1;
    int menu_global_ = -1;
    int dryer_ace_ = -1;
    int dryer_preset_ = 0;
    int dryer_temp_c_ = 45;
    int dryer_hours_ = 4;
    uint32_t draft_color_ = 0xD68459;
    float draft_h_ = 0.07f;
    float draft_s_ = 0.58f;
    float draft_v_ = 0.84f;
    bool laid_out_ = false;
    int last_w_ = 0;
    int last_h_ = 0;
    lv_timer_t* color_layout_timer_ = nullptr;

    std::vector<SlotCard> slots_;
    std::vector<u1_filament::AceGroup> ace_groups_;
    std::array<bool, 4> external_heads_{};
    std::optional<u1_filament::EditDraft> edit_draft_;
    std::vector<std::string> edit_materials_;
    AmsBackend* edit_backend_ = nullptr;
    uint64_t edit_connection_ = 0;
    std::string edit_serial_, edit_label_;
    bool edit_invalidated_ = false;
    ObserverGuard revision_observer_;
    ObserverGuard connection_observer_;
    ObserverGuard job_observer_;
    ObserverGuard theme_observer_;
};

} // namespace helix
