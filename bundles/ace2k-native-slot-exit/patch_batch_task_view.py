"""Complete batch task UI on top of the routed and multiselect source."""

def replace(s,a,b):
    if s.count(a)!=1:
        raise ValueError("task view anchor mismatch: "+a[:100])
    return s.replace(a,b,1)

def extend(h,c):
    h=replace(h,'    void start_batch();','''    void start_batch();
    void recover_batch();
    void open_batch_progress();
    void poll_batch();
    void stop_batch_task();
    void next_batch_task();
    void confirm_batch_parking();''')
    h=replace(h,'    std::string batch_ticket_, batch_action_;','''    std::string batch_ticket_, batch_action_, active_batch_;
    bool batch_parking_=false, batch_progress_=false, batch_polling_=false;
    bool batch_awaiting_=false, batch_finished_=false, batch_recovering_=false;
    std::vector<std::string> batch_task_ids_;
    std::vector<bool> batch_task_stoppable_;
    size_t batch_task_index_=0;
    uint64_t batch_view_generation_=0;
    lv_obj_t* batch_next_=nullptr;
    lv_obj_t* batch_current_=nullptr;
    ui::LvglTimerGuard batch_timer_;''')
    c=replace(c,'#include "u1_filament_overview.h"','#include "u1_filament_overview.h"\n#include "core/update_queue.h"\n#include "screen_batch_progress.h"')
    c=replace(c,'    batch_lifetime_.reset();','    batch_lifetime_.reset();\n    batch_timer_.reset();')
    c=replace(c,'    batch_action_.clear();\n    show_page(Page::Hub);','''    batch_action_.clear();
    batch_progress_=false;
    batch_parking_=false;
    ++batch_view_generation_;
    batch_timer_.reset();
    show_page(Page::Hub);
    layout_hub();
    recover_batch();''')
    c=replace(c,'    dim_ = make_box(hub_);','''    batch_current_ = make_soft_button(hub_, "当前任务", false, [](lv_event_t* e) {
        if (auto* self = static_cast<U1FilamentOverview*>(lv_event_get_user_data(e)))
            self->open_batch_progress();
    }, this);
    lv_obj_set_name(batch_current_, "u1_filament_current_task");
    apply_font(lv_obj_get_child(batch_current_, 0), "font_small");
    dim_ = make_box(hub_);''')
    c=replace(c,'''    lv_obj_set_style_text_color(empty_label_, kMuted(), LV_PART_MAIN);
    for (auto& card : cards_) {''','''    lv_obj_set_style_text_color(empty_label_, kMuted(), LV_PART_MAIN);
    outline_obj(batch_current_, kElevated(), kBorder(), kCardRadius, 1);
    lv_obj_set_style_text_color(lv_obj_get_child(batch_current_, 0), kText(), LV_PART_MAIN);
    for (auto& card : cards_) {''')
    c=replace(c,'''    const int action_count = external_only_ ? 2 : columns;
    for (int i = 0; i < action_count; ++i) {
        place_row_cell(actions_[static_cast<size_t>(i)], row_x, action_y, row_w, kActionH,
                       i, action_count, kCardGap);
    }''','''    const int slot_actions = external_only_ ? 2 : columns;
    const bool has_task = !active_batch_.empty();
    const int action_count = slot_actions + static_cast<int>(has_task);
    for (int i = 0; i < slot_actions; ++i) {
        place_row_cell(actions_[static_cast<size_t>(i)], row_x, action_y, row_w, kActionH,
                       i, action_count, kCardGap);
    }
    lv_obj_set_hidden(batch_current_, !has_task);
    if (has_task)
        place_row_cell(batch_current_, row_x, action_y, row_w, kActionH,
                       slot_actions, action_count, kCardGap);''')
    c=replace(c,'''        network.get_nav_buttons_enabled_subject(), this,
        [](U1FilamentOverview* self, int) { self->refresh(); },''','''        network.get_nav_buttons_enabled_subject(), this,
        [](U1FilamentOverview* self, int online) {
            self->refresh();
            if (online == 1) self->recover_batch();
        },''')
    c=replace(c,'    const bool can_eject = can_act && !external && card->inlet_has_filament;',
        '''    const bool can_eject = card && card->connected && backend && !external &&
        card->inlet_has_filament && !lv_subject_get_int(
        get_printer_state().print_state().get_job_holds_machine_subject());''')
    c=replace(c,'    if (!batch_action_.empty()) { start_batch(); return; }','''    if (batch_progress_) {
        if (batch_awaiting_) confirm_batch_parking();
        else if (batch_finished_) { active_batch_.clear(); show_hub(); refresh(); }
        else poll_batch();
        return;
    }
    if (!batch_action_.empty()) { start_batch(); return; }''')
    c=replace(c,'void U1FilamentOverview::preview_batch(const std::string& action) {','''void U1FilamentOverview::preview_batch(const std::string& action) {
    if (batch_pending_) return;
    if (!active_batch_.empty() && !batch_finished_) { open_batch_progress(); return; }''')
    c=replace(c,'    std::string names;\n    for (const auto& entry','    for (const auto& entry')
    c=replace(c,r'        names += fmt::format("ACE {} · 槽 {}\n", b.ace + 1, b.slot + 1);'+'\n','')
    c=replace(c,'[this,alive,action,names](auto data, std::string error)','[this,alive,action](auto data, std::string error)')
    c=replace(c,'            batch_action_ = action;\n            layout_confirm_page();',r'''            batch_action_ = action;
            batch_parking_ = data.value("needs_parking_confirmation",false);
            batch_progress_ = false;
            std::string names;
            for (const auto& row : data.value("slots",nlohmann::json::array())) {
                const auto state=row.value("state",std::string{});
                names += fmt::format("ACE {} · 槽 {}：{}\n",row.value("ace",-1)+1,row.value("slot",-1)+1,
                    state=="inspect_parked" ? "需检查停泊" : state=="head_unload" ? "先从喷头退料" :
                    state=="head_load" ? "进料" : row.value("detail",std::string("不可执行")));
            }
            layout_confirm_page();''')
    c=replace(c,r'            lv_label_set_text(confirm_body_, (names + "\n无冲突任务并行，公共料路任务按需排队。").c_str());',r'''            const std::string prompt = batch_parking_ ?
                "\n请实际检查标记需检查的耗材：尖端必须位于各自独立支管，未进入公共料管。检查无误才确认。" :
                "\n无冲突任务并行，公共料路任务按需排队。";
            lv_label_set_text(confirm_body_,(names+prompt).c_str());''')
    c=replace(c,'            lv_label_set_text(lv_obj_get_child(confirm_ok_,0),"确认操作");\n            show_page(Page::Confirm);','''            lv_label_set_text(lv_obj_get_child(confirm_cancel_,0),"取消");
            lv_obj_set_disabled(confirm_cancel_,false);
            lv_label_set_text(lv_obj_get_child(confirm_ok_,0),batch_parking_ ? "已检查，确认" : "确认操作");
            lv_obj_set_disabled(confirm_ok_,false);
            show_page(Page::Confirm);''')
    c=replace(c,'    if (batch_pending_ || batch_ticket_.empty() || !batch_selection_current()) return;',
        '    if (batch_pending_ || batch_ticket_.empty() || (!batch_selection_.empty() && !batch_selection_current())) return;')
    c=replace(c,'''    material_workspace::request("/material/batch/start", {{"ticket",ticket}},
        [this,alive](auto data,std::string error) {''','''    const auto generation = batch_view_generation_;
    material_workspace::request("/material/batch/start", {{"ticket",ticket},{"confirm_parked",batch_parking_}},
        [this,alive,generation](auto data,std::string error) {''')
    c=replace(c,'                show_hub(); refresh();\n                show_feedback("批量任务已受理，完成以各槽状态为准",FeedbackKind::Info);','''                active_batch_=data["batch"].template get<std::string>();
                batch_finished_=false;
                batch_task_index_=0;
                if (generation == batch_view_generation_ && page_ == Page::Confirm)
                    open_batch_progress();
                else if (page_ == Page::Hub) {
                    layout_hub();
                    refresh();
                }''')
    c=replace(c,'''    ui::get_material_workspace().open_action(slot->head, slot->backend_unit - 1, "eject", {},
                                              slot->local_slot, {}, slot->ace);''','''    if (!active_batch_.empty() && !batch_finished_) { open_batch_progress(); return; }
    auto* client=get_moonraker_client();
    auto* backend=AmsState::instance().get_backend();
    if (!client || !backend) return;
    const auto info=backend->get_system_info();
    const auto* unit=info.unit_for_slot(slot->global_index);
    if (!unit || unit->serial_number.empty()) { show_feedback("无法确认 ACE 身份"); return; }
    batch_selection_.clear();
    batch_backend_=backend;
    batch_connection_=client->connection_generation();
    batch_selection_.toggle({slot->ace,slot->local_slot,slot->global_index,unit->serial_number});
    preview_batch("eject");''')
    c=replace(c,'''    confirm_cancel_ = make_soft_button(confirm_page_, "取消", false, [](lv_event_t* e) {
        if (auto* self = static_cast<U1FilamentOverview*>(lv_event_get_user_data(e)))
            self->show_hub();
    }, this);''','''    confirm_cancel_ = make_soft_button(confirm_page_, "取消", false, [](lv_event_t* e) {
        if (auto* self = static_cast<U1FilamentOverview*>(lv_event_get_user_data(e))) {
            if (self->batch_progress_) self->stop_batch_task(); else self->show_hub();
        }
    }, this);
    batch_next_ = make_soft_button(confirm_page_,"下一槽",false,[](lv_event_t* e) {
        if (auto* self=static_cast<U1FilamentOverview*>(lv_event_get_user_data(e))) self->next_batch_task();
    },this);
    lv_obj_set_hidden(batch_next_,true);''')
    c=replace(c,'''    place_row_cell(confirm_cancel_, body.x, body.actions_y, body.width, kActionH, 0, 2, kCardGap);
    place_row_cell(confirm_ok_, body.x, body.actions_y, body.width, kActionH, 1, 2, kCardGap);''','''    const int columns=batch_progress_ ? 3 : 2;
    lv_obj_set_hidden(batch_next_,!batch_progress_);
    place_row_cell(confirm_cancel_,body.x,body.actions_y,body.width,kActionH,0,columns,kCardGap);
    if (batch_progress_) place_row_cell(batch_next_,body.x,body.actions_y,body.width,kActionH,1,columns,kCardGap);
    place_row_cell(confirm_ok_,body.x,body.actions_y,body.width,kActionH,columns-1,columns,kCardGap);''')
    c=replace(c,'void U1FilamentOverview::open_confirm(ConfirmKind kind) {','''void U1FilamentOverview::open_confirm(ConfirmKind kind) {
    batch_progress_=false;
    batch_action_.clear();
    batch_ticket_.clear();
    lv_label_set_text(lv_obj_get_child(confirm_cancel_,0),"取消");
    lv_obj_set_disabled(confirm_cancel_,false);
    lv_obj_set_disabled(confirm_ok_,false);''')
    c=replace(c,'void U1FilamentOverview::on_action_load() {',r'''void U1FilamentOverview::recover_batch() {
    auto* client = get_moonraker_client();
    if (!client || client->get_connection_state() != ConnectionState::CONNECTED ||
        page_ != Page::Hub || batch_recovering_ || batch_pending_ || !active_batch_.empty())
        return;

    batch_recovering_ = true;
    const auto connection = client->connection_generation();
    const std::weak_ptr<int> alive = batch_lifetime_;
    auto finish = [this, alive, client, connection](std::string batch) {
        ui::queue_update("U1FilamentOverview::recover_batch",
            [this, alive, client, connection, batch = std::move(batch)] {
            if (alive.expired()) return;
            batch_recovering_ = false;
            if (client != get_moonraker_client() || connection != client->connection_generation() ||
                batch_pending_ || !active_batch_.empty() || batch.empty())
                return;
            active_batch_ = batch;
            batch_finished_ = false;
            batch_task_index_ = 0;
            if (page_ == Page::Hub) layout_hub();
        });
    };
    const auto id = client->send_jsonrpc("printer.objects.query",
        {{"objects", {{"ace", {"material_batch_tasks"}}}}},
        [finish](const nlohmann::json& response) {
            static const nlohmann::json::json_pointer tasks("/result/status/ace/material_batch_tasks");
            finish(response.contains(tasks) ? u1_filament::recover_material_batch(response[tasks]) : "");
        }, [finish](const MoonrakerError&) { finish(""); }, 4000, true);
    if (!id) batch_recovering_ = false;
}

void U1FilamentOverview::open_batch_progress() {
    if (active_batch_.empty()) return;
    batch_progress_=true;
    batch_action_.clear();
    batch_ticket_.clear();
    batch_awaiting_=false;
    ++batch_view_generation_;
    layout_confirm_page();
    lv_label_set_text(confirm_title_,"批量任务");
    lv_label_set_text(confirm_target_,"正在读取槽位状态");
    lv_obj_set_hidden(confirm_extra_,true);
    lv_label_set_text(confirm_body_,"等待设备返回实际任务状态…");
    lv_obj_set_disabled(confirm_cancel_,true);
    lv_obj_set_disabled(batch_next_,true);
    lv_obj_set_disabled(confirm_ok_,true);
    show_page(Page::Confirm);
    batch_timer_.reset(lv_timer_create([](lv_timer_t* timer) {
        auto* self=static_cast<U1FilamentOverview*>(lv_timer_get_user_data(timer));
        if (self && self->batch_progress_ && self->page_==Page::Confirm) self->poll_batch();
    },1000,this));
    poll_batch();
}

void U1FilamentOverview::poll_batch() {
    if (!batch_progress_ || batch_pending_ || batch_polling_ || active_batch_.empty()) return;
    batch_polling_=true;
    const auto generation=batch_view_generation_;
    const std::weak_ptr<int> alive=batch_lifetime_;
    material_workspace::request("/material/batch/status",{{"batch",active_batch_}},
        [this,alive,generation](auto data,std::string error) {
            if (alive.expired()) return;
            batch_polling_=false;
            if (generation!=batch_view_generation_ || !batch_progress_) return;
            if (!error.empty() || !data.is_object() || !data.contains("tasks") || !data["tasks"].is_array()) {
                lv_label_set_text(confirm_target_,"任务状态未确认");
                lv_obj_set_disabled(confirm_cancel_,true);
                lv_obj_set_disabled(batch_next_,true);
                lv_obj_set_disabled(confirm_ok_,true);
                return;
            }
            batch_task_ids_.clear();
            batch_task_stoppable_.clear();
            batch_awaiting_=false;
            batch_finished_=!data["tasks"].empty();
            for (const auto& t:data["tasks"]) {
                const auto state=t.value("state",std::string{});
                const bool terminal=u1_filament::material_batch_slot_finished(state);
                batch_finished_ &= terminal;
                batch_awaiting_ |= state=="awaiting_parking";
                batch_task_ids_.push_back(t.value("id",std::string{}));
                batch_task_stoppable_.push_back(!terminal && state!="stopping");
            }
            if (batch_task_index_>=batch_task_ids_.size()) batch_task_index_=0;
            std::string body;
            size_t n=0;
            for (const auto& t:data["tasks"]) {
                const auto state=t.value("state",std::string{});
                const char* label=state=="complete" ? "完成" : state=="cancelled" ? "已取消" :
                    state=="rejected" ? "未执行" : state=="failed" ? "失败" : state=="recovery" ? "需恢复" :
                    state=="awaiting_parking" ? "请检查停泊" : state=="head_running" ? "喷头操作中" :
                    state=="native" ? "退出中" : state=="stopping" ? "停止确认中" :
                    state=="sampling" ? "检查中" : "排队";
                body += fmt::format("{} ACE {} · 槽 {}：{}\n",n++==batch_task_index_ ? ">" : " ",
                    t.value("ace",-1)+1,t.value("slot",-1)+1,label);
                const auto detail=t.value("detail",std::string{});
                if (!detail.empty()) body+=detail+"\n";
            }
            lv_label_set_text(confirm_target_,batch_awaiting_ ? "需检查退回位置" : batch_finished_ ? "任务结果" : "各槽独立执行");
            lv_label_set_text(confirm_body_,body.empty() ? "无任务记录，未确认完成" : body.c_str());
            lv_label_set_text(lv_obj_get_child(confirm_cancel_,0),"停止选中槽");
            lv_label_set_text(lv_obj_get_child(confirm_ok_,0),batch_awaiting_ ? "检查停泊" : batch_finished_ ? "完成" : "刷新");
            lv_obj_set_disabled(confirm_cancel_,batch_task_ids_.empty() || !batch_task_stoppable_[batch_task_index_]);
            lv_obj_set_disabled(batch_next_,batch_task_ids_.size()<2);
            lv_obj_set_disabled(confirm_ok_,batch_task_ids_.empty());
        },true);
}

void U1FilamentOverview::next_batch_task() {
    if (batch_task_ids_.empty()) return;
    batch_task_index_=(batch_task_index_+1)%batch_task_ids_.size();
    lv_obj_set_disabled(confirm_cancel_,true);
    poll_batch();
}

void U1FilamentOverview::stop_batch_task() {
    if (batch_pending_ || batch_task_index_>=batch_task_ids_.size() ||
        !batch_task_stoppable_[batch_task_index_]) return;
    batch_pending_=true;
    const std::weak_ptr<int> alive=batch_lifetime_;
    material_workspace::request("/material/batch/stop",{{"task",batch_task_ids_[batch_task_index_]}},
        [this,alive](auto data,std::string error) {
            if (alive.expired()) return;
            batch_pending_=false;
            if (!error.empty()) show_feedback(error);
            else if (!data.value("accepted",false)) show_feedback("停止请求未受理，请查看任务状态");
            else if (data.value("state",std::string{})=="after_head")
                show_feedback("喷头操作将在安全边界停止",FeedbackKind::Info);
            poll_batch();
        },true);
}

void U1FilamentOverview::confirm_batch_parking() {
    if (batch_pending_ || active_batch_.empty()) return;
    batch_pending_=true;
    const auto generation=batch_view_generation_;
    const std::weak_ptr<int> alive=batch_lifetime_;
    material_workspace::request("/material/batch/parking_preview",{{"batch",active_batch_}},
        [this,alive,generation](auto data,std::string error) {
            if (alive.expired()) return;
            batch_pending_=false;
            if (generation!=batch_view_generation_ || !batch_progress_) return;
            if (!error.empty() || !data.is_object() || data.value("ticket",std::string{}).empty()) {
                show_feedback(error.empty() ? "没有可确认的槽位，请检查设备状态" : error); return;
            }
            batch_ticket_=data.value("ticket",std::string{});
            batch_action_="eject";
            batch_parking_=true;
            batch_selection_.clear();
            batch_progress_=false;
            batch_timer_.reset();
            ++batch_view_generation_;
            layout_confirm_page();
            std::string body;
            for (const auto& row:data.value("slots",nlohmann::json::array()))
                body += fmt::format("ACE {} · 槽 {}：{}\n",row.value("ace",-1)+1,row.value("slot",-1)+1,
                    row.value("state",std::string{})=="inspect_parked" ? "需实际检查" : row.value("detail",std::string("不可执行")));
            body+="\n请检查上述耗材尖端已退回各自独立支管，完全离开公共料管。检查无误才按确认。";
            lv_label_set_text(confirm_title_,"确认停泊位置");
            lv_label_set_text(confirm_target_,"喷头无料不等于已离开公共管");
            lv_label_set_text(confirm_body_,body.c_str());
            lv_label_set_text(lv_obj_get_child(confirm_cancel_,0),"取消");
            lv_obj_set_disabled(confirm_cancel_,false);
            lv_label_set_text(lv_obj_get_child(confirm_ok_,0),"已检查，确认");
            lv_obj_set_disabled(confirm_ok_,false);
        },true);
}

void U1FilamentOverview::on_action_load() {''')
    return h,c
