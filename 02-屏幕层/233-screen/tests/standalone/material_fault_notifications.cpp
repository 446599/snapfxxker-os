// Copyright (C) 2025-2026 356C LLC
// SPDX-License-Identifier: GPL-3.0-or-later

#include "material_fault_notice.h"
#include "ui_notification_history.h"
#include "hv/json.hpp"
#include <cassert>
#include <cstring>
#include <iostream>
extern "C" uint32_t lv_tick_get(void) { return 1000; }
using nlohmann::json;
using helix::material_workspace::fault_notices;
using helix::material_workspace::kUnknownStopFaultKey;
using helix::material_workspace::valid_recovery_ticket;
const std::string kSlot = "material_fault:0:3";
const std::string kOtherSlot = "material_fault:1:0";
const std::string kTicket = "0123456789abcdef0123456789abcdef";
json fixture() {
 return {{"slot_action",{{"state","idle"}}}, {"error_recovery",{{"active",false},{"stop_fault",nullptr},{"slots",json::array({{{"ace",0},{"slot",3},{"needs_clear",true},{"can_check",true},{"can_clear",true},{"ticket",kTicket},{"detail","old failed load"}}})}}}};
}

void test_recovery_evidence() {
    auto state = fixture();
    auto& slots = state["error_recovery"]["slots"];
    slots[0]["needs_clear"] = false;
    slots.push_back({{"ace", 1}, {"slot", 0}, {"needs_clear", false},
        {"can_check", true}, {"can_clear", true}, {"ticket", kTicket}});
    auto& task = state["slot_action"];
    task = {{"ace", 0}, {"slot", 3}, {"state", "error"}, {"kind", "insert"}, {"seq", 7}};
    const auto failure = fault_notices(state);
    assert(failure && failure->faults.size() == 1 && failure->faults.front().key == kSlot);
    assert(failure->recovery_tickets.at(kSlot) == kTicket);
    const int seen_sequence = failure->task_sequence;
    assert(seen_sequence == 7);

    task = {{"ace", 1}, {"slot", 0}, {"state", "running"}, {"kind", "recover"}, {"seq", 8}};
    auto snapshot = fault_notices(state);
    assert(snapshot && snapshot->faults.empty() && !snapshot->resolved_after(kSlot, seen_sequence));
    task["state"] = "complete";
    snapshot = fault_notices(state);
    assert(snapshot && snapshot->completed_recovery_key == kOtherSlot);
    assert(!snapshot->resolved_after(kSlot, seen_sequence));
    assert(snapshot->resolved_after(kUnknownStopFaultKey, seen_sequence));

    task = {{"ace", 0}, {"slot", 3}, {"state", "complete"}, {"kind", "recover"}, {"seq", 7}};
    snapshot = fault_notices(state);
    assert(snapshot && snapshot->completed_recovery_key == kSlot);
    assert(!snapshot->resolved_after(kSlot, seen_sequence));
    // The provider may restart and begin counting tasks from one again.
    task["seq"] = 1;
    assert(fault_notices(state)->resolved_after(kSlot, seen_sequence));
    task["seq"] = 9;
    assert(fault_notices(state)->resolved_after(kSlot, seen_sequence));
    assert(!fault_notices(state)->resolved_after(kOtherSlot, seen_sequence));
    task["seq"] = "9";
    assert(!fault_notices(state)->resolved_after(kSlot, seen_sequence));
    task["seq"] = 9;
    state["error_recovery"]["stop_fault"] = {{"ace_idx", 1}, {"slot", 0}};
    assert(!fault_notices(state)->resolved_after(kUnknownStopFaultKey, seen_sequence));
    state["error_recovery"]["stop_fault"] = nullptr;
    slots[0]["needs_clear"] = true;
    assert(!fault_notices(state)->resolved_after(kSlot, seen_sequence));
    slots[0]["needs_clear"] = false;
    state["error_recovery"]["active"] = true;
    assert(!fault_notices(state)->resolved_after(kSlot, seen_sequence));
    state["error_recovery"]["active"] = false;
    slots[0]["can_check"] = slots[0]["can_clear"] = false;
    snapshot = fault_notices(state);
    assert(snapshot && !snapshot->resolved_after(kSlot, seen_sequence));
    assert(snapshot->recovery_tickets.count(kSlot) == 0);
    slots[0]["can_check"] = true;
    slots[0]["ticket"] = std::string(32, 'f');
    snapshot = fault_notices(state);
    assert(snapshot && snapshot->recovery_tickets.at(kSlot) != failure->recovery_tickets.at(kSlot));
    assert(valid_recovery_ticket(snapshot->recovery_tickets.at(kSlot)));
    slots[0]["ticket"] = std::string(32, 'x');
    snapshot = fault_notices(state);
    assert(snapshot && snapshot->recovery_tickets.count(kSlot) == 0);
    assert(!snapshot->resolved_after(kSlot, seen_sequence));
}
void test_current_clear_status_retires_a_stale_notice() {
    auto state = fixture();
    auto& row = state["error_recovery"]["slots"][0];
    row["needs_clear"] = false;
    row["needs_source_confirmation"] = false;
    row["blocked_by_stop"] = false;
    state["aces"] = json::array({{{"idx", 0}, {"connected", true},
        {"slots", json::array({{{"idx", 3}, {"position_unconfirmed", false}}})}}});
    state["slot_action"] = {{"state", "complete"}, {"kind", "insert"}, {"ace", 1}, {"slot", 0}, {"seq", 11}};
    assert(fault_notices(state)->resolved_after(kSlot, 10));
    assert(!fault_notices(state)->resolved_after(kOtherSlot, 10));
    row["blocked_by_stop"] = true;
    assert(!fault_notices(state)->resolved_after(kSlot, 10));
    row["blocked_by_stop"] = false;
    state["aces"][0]["slots"][0]["position_unconfirmed"] = true;
    assert(!fault_notices(state)->resolved_after(kSlot, 10));
}
int main() {
 test_recovery_evidence();
 test_current_clear_status_retires_a_stale_notice();
 auto s=fixture();auto r=fault_notices(s);assert(r&&r->faults.size()==1&&r->faults[0].ace==0&&r->faults[0].slot==3&&r->recovery_tickets.count(kSlot));
 s["slot_action"]={{"state","error"},{"kind","recover"},{"ace",0},{"slot",3},{"message","inlet present"}};
 assert(fault_notices(s)->faults.size()==1);
 s["error_recovery"]["stop_fault"]={{"ace_idx",0},{"slot",3},{"reason","stop"}};assert(fault_notices(s)->faults.size()==1);
 s["error_recovery"]["stop_fault"]={{"ace_idx",-1},{"slot",-1}};r=fault_notices(s);assert(r->faults.size()==2);assert(!r->recovery_tickets.count(kUnknownStopFaultKey));
 s=fixture();s["error_recovery"]["slots"][0]["needs_clear"]=false;s["slot_action"]={{"state","complete"},{"kind","recover"},{"ace",0},{"slot",3}};assert(fault_notices(s)->faults.empty());
 s["slot_action"]["state"]="error";assert(fault_notices(s)->faults.size()==1);
 s["error_recovery"]["active"]=true;assert(fault_notices(s)->recovering&&fault_notices(s)->recovery_tickets.empty());
 s=fixture();s["error_recovery"]["slots"].push_back(s["error_recovery"]["slots"][0]);assert(!fault_notices(s));assert(!fault_notices(json::object()));
 s=fixture();s["slot_action"].erase("state");assert(!fault_notices(s));
 s=fixture();s["error_recovery"].erase("stop_fault");assert(!fault_notices(s));
 auto& h=NotificationHistory::instance();h.clear();NotificationHistoryEntry e{};e.active=true;e.severity=ToastSeverity::ERROR;std::strcpy(e.key,"material_fault:0:3");std::strcpy(e.action,e.key);std::strcpy(e.action_label,"clear");std::strcpy(e.title,"slot 4 fault");std::strcpy(e.message,"withdraw filament");
 assert(h.sync_active("material_fault:",{e},1000));auto v=h.version();h.mark_all_read();assert(h.get_unread_count()==0);assert(!h.sync_active("material_fault:",{e},2000)&&h.version()==v&&h.get_unread_count()==0);
 h.clear();assert(h.count()==1&&h.get_all()[0].active);
 for(int i=0;i<110;++i){NotificationHistoryEntry normal{};normal.timestamp_ms=3000+i;h.add(normal);}assert(h.count()==100&&h.get_all()[0].active&&std::string(h.get_all()[0].key)==e.key);
 assert(h.sync_active("material_fault:",{},4000));auto all=h.get_all();auto it=std::find_if(all.begin(),all.end(),[&](auto& x){return std::string(x.key)==e.key;});assert(it!=all.end()&&it->resolved&&!it->active&&it->action[0]==0&&it->severity==ToastSeverity::SUCCESS);
 assert(h.sync_active("material_fault:",{e},5000));assert(h.get_all()[0].active&&!h.get_all()[0].resolved&&!h.get_all()[0].was_read);
 assert(!h.sync_active("",{},6000));h.sync_active("material_fault:",{},6000);h.clear();assert(h.count()==0);
 std::cout<<"PASS: scoped newer recovery, ticket changes, offline and malformed evidence, fault dedup, STOP + failed load, active recovery, history actions and retention, unread state, ring overflow, recurrence\n";
}
