// SPDX-License-Identifier: GPL-3.0-or-later
#include "material_workspace.h"

#include "catch_amalgamated.hpp"

#include <utility>

using helix::material_workspace::build_command;
TEST_CASE("Nozzle unload allows a held position only with matching live filament", "[material-workspace]") {
    using helix::material_workspace::can_unload_ace_nozzle;
    helix::material_workspace::SlotTaskState task;
    task.valid = true;
    task.busy = false;
    task.held = true;
    CHECK(can_unload_ace_nozzle(task, 5, 5, true));
    CHECK_FALSE(can_unload_ace_nozzle(task, 5, 4, true));
    CHECK_FALSE(can_unload_ace_nozzle(task, 5, std::nullopt, true));
    CHECK_FALSE(can_unload_ace_nozzle(task, 5, 5, false));
    CHECK_FALSE(can_unload_ace_nozzle(task, 5, 5, std::nullopt));
    task.busy = true;
    CHECK_FALSE(can_unload_ace_nozzle(task, 5, 5, true));
    task.busy = false;
    task.stop_fault = true;
    CHECK_FALSE(can_unload_ace_nozzle(task, 5, 5, true));
    task.stop_fault = false;
    task.valid = false;
    CHECK_FALSE(can_unload_ace_nozzle(task, 5, 5, true));
}

TEST_CASE("Workspace commands keep the selected zero-based ACE and bay", "[material-workspace]") {
    const auto command = build_command("read_tag", {{"ACE", 2}, {"SLOT", 3}});
    REQUIRE(command);
    CHECK(*command == "ACE_SLOT_RFID ACE=2 SLOT=3");
    CHECK(build_command("eject", {{"ACE", 1}, {"SLOT", 0}}) == "ACE_SLOT_EXIT ACE=1 SLOT=0");
}

TEST_CASE("Slot recovery stays available independently of nozzle unload", "[material-workspace]") {
    using helix::material_workspace::slot_task_state;
    nlohmann::json data = {
        {"aces", {{{"idx", 0}, {"connected", true},
                   {"slots", {{{"idx", 0}, {"position_unconfirmed", true},
                                {"eject_recovery_allowed", true}}}}}}},
        {"tag_op", {{"busy", false}}}, {"slot_action", {{"state", "idle"}}},
        {"swap_in_progress", false}, {"unload_all_active", false}};
    SECTION("Exact held route retains eject recovery") {
        const auto task = slot_task_state(data, 0, 0);
        CHECK(task.valid);
        CHECK(task.held);
        CHECK(task.recovery);
        CHECK_FALSE(task.busy);
        CHECK_FALSE(task.stop_target);
    }
    SECTION("Other slot, missing flags and disconnected device never authorize motion") {
        CHECK_FALSE(slot_task_state(data, 0, 1).valid);
        data["aces"][0]["slots"][0].erase("position_unconfirmed");
        CHECK_FALSE(slot_task_state(data, 0, 0).valid);
        data["aces"][0]["connected"] = false;
        CHECK_FALSE(slot_task_state(data, 0, 0).valid);
    }
    SECTION("RFID stop targets the current task generation") {
        data["tag_op"] = {{"busy", true}, {"kind", "read"}, {"ace", 0}, {"slot", 0}, {"seq", 7}};
        CHECK_FALSE(slot_task_state(data, 0, 0).stop_target);
        data["slot_action"]["epoch"] = "0123456789abcdef";
        const auto task = slot_task_state(data, 0, 0);
        CHECK(task.busy);
        REQUIRE(task.stop_target);
        CHECK(task.stop_target->stop_request() == nlohmann::json{
            {"kind", "rfid"}, {"ace", 0}, {"slot", 0}, {"seq", 7}, {"epoch", "0123456789abcdef"}});
        CHECK(task.kind == "rfid");
        CHECK(task.seq == 7);
        data["tag_op"]["slot"] = 1;
        CHECK_FALSE(slot_task_state(data, 0, 0).stop_target);
    }
    SECTION("Preparing nozzle unload can stop the subsequent ACE eject") {
        data["slot_action"] = {{"state", "preparing"}, {"kind", "eject"},
                               {"ace", 0}, {"slot", 0}, {"seq", 9}, {"message", "退出喷头"}};
        data["slot_action"]["epoch"] = "invalid";
        CHECK_FALSE(slot_task_state(data, 0, 0).stop_target);
        data["slot_action"]["epoch"] = "0123456789abcdef";
        CHECK(slot_task_state(data, 0, 0).stop_target);
        data["slot_action"]["state"] = "cancelled";
        CHECK_FALSE(slot_task_state(data, 0, 0).busy);
        CHECK_FALSE(slot_task_state(data, 0, 0).stop_target);
    }
}

TEST_CASE("Modern slot recovery confirms the original source before a stop-only check",
          "[material-workspace]") {
    using helix::material_workspace::next_slot_recovery_action;
    using helix::material_workspace::slot_task_state;
    nlohmann::json state = {
        {"aces", {{{"idx", 2}, {"connected", true}, {"gate_status", {0, 1}},
                   {"slots", {{{"index", 1}, {"position_unconfirmed", false},
                                {"eject_recovery_allowed", false}}}}}}},
        {"tag_op", {{"busy", false}}}, {"slot_action", {{"state", "idle"}}},
        {"swap_in_progress", false}, {"unload_all_active", false}};
    state["error_recovery"] = {{"active", false}, {"stop_fault", {{"ace_idx", 2}, {"slot", 1}}},
        {"slots", {{{"ace", 2}, {"slot", 1}, {"source_head", 1},
                    {"needs_clear", true}, {"can_check", true}, {"can_clear", true}, {"can_auto", false},
                    {"needs_source_confirmation", true}, {"can_confirm_source", true},
                    {"ticket", std::string(32, 'a')}}}}};
    auto& lane = state["error_recovery"]["slots"][0];
    CHECK(slot_task_state(state, 2, 1).stop_fault);
    lane["blocked_by_stop"] = false;
    CHECK_FALSE(slot_task_state(state, 2, 1).stop_fault);
    lane["blocked_by_stop"] = true;
    CHECK(slot_task_state(state, 2, 1).stop_fault);
    CHECK(next_slot_recovery_action(slot_task_state(state, 2, 1)) == "recover_source");
    CHECK(next_slot_recovery_action(slot_task_state(state, 1, 1)).empty());
    state["aces"][0]["gate_status"][1] = 0;
    CHECK(next_slot_recovery_action(slot_task_state(state, 2, 1)).empty());
    state["aces"][0]["gate_status"][1] = 1;
    lane["needs_source_confirmation"] = false;
    CHECK(next_slot_recovery_action(slot_task_state(state, 2, 1)).empty());
    lane["can_auto"] = true;
    CHECK(next_slot_recovery_action(slot_task_state(state, 2, 1)) == "recover_auto");
    lane["ticket"] = std::string(32, 'x');
    CHECK(next_slot_recovery_action(slot_task_state(state, 2, 1)).empty());
}

TEST_CASE("Workspace metadata quotes spaces and permits clearing a color", "[material-workspace]") {
    const auto command =
        build_command("spool_set", {{"ID", "42"}, {"MATERIAL", "Silk PLA"}, {"COLOR", ""}});
    REQUIRE(command);
    CHECK(*command == "ACE_SPOOL_SET COLOR= ID=42 MATERIAL=\"Silk PLA\"");
}
TEST_CASE("Workspace rejects command injection and unknown operations", "[material-workspace]") {
    CHECK_FALSE(build_command("reboot", {}));
    CHECK_FALSE(build_command("spool_set", {{"VENDOR", "PLA\u2028M112"}}));
    CHECK_FALSE(build_command("spool_set", {{"VENDOR", std::string("\xC0\xAF", 2)}}));
    CHECK_FALSE(build_command("read_tag", {{"ACE", "0\nM112"}}));
    CHECK_FALSE(build_command("read_tag", {{"ACE;M112", 0}}));
    CHECK_FALSE(build_command("spool_set", {{"MATERIAL", "PLA\"\nG28"}}));
}
TEST_CASE("Workspace uses the same continuation and purge setters as the web",
          "[material-workspace]") {
    CHECK(build_command("quad_replenish", {{"ENABLE", 1}}).value() ==
          "ACE_SET_QUAD_REPLENISH ENABLE=1");
    CHECK(build_command("purge_matrix", {{"MATRIX", 0}}).value() == "ACE_SET_PURGE MATRIX=0");
}

TEST_CASE("Workspace preserves existing UTF-8 brand metadata", "[material-workspace]") {
    const auto command =
        build_command("spool_set", {{"ID", "42"}, {"VENDOR", "拓竹"}, {"WEIGHT", 600}});
    REQUIRE(command);
    CHECK(*command == "ACE_SPOOL_SET ID=42 VENDOR=拓竹 WEIGHT=600");
}

TEST_CASE("Head inventory distinguishes direct spools from missing or offline ACE state",
          "[material-workspace]") {
    using namespace helix;
    AmsSystemInfo info;
    CHECK_FALSE(material_workspace::supports_head_inventory(info));
    CHECK_FALSE(material_workspace::uses_external_spools(info));
    CHECK(material_workspace::head_slot(info, 0) == nullptr);

    AmsUnit heads;
    heads.topology = PathTopology::PARALLEL;
    heads.connected = true;
    for (int head = 0; head < 4; ++head) {
        SlotInfo slot;
        slot.mapped_tool = head;
        slot.global_index = head;
        slot.material = "PLA";
        heads.slots.push_back(slot);
    }
    info.units.push_back(heads);

    SECTION("The real four-head parallel unit works without a multiACE mode") {
        CHECK(material_workspace::uses_external_spools(info));
        CHECK(material_workspace::supports_head_inventory(info));
        REQUIRE(material_workspace::head_slot(info, 2));
        CHECK(material_workspace::head_slot(info, 2)->global_index == 2);
        CHECK(material_workspace::head_slot(info, -1) == nullptr);
        CHECK(material_workspace::head_slot(info, 4) == nullptr);
    }
    SECTION("Configured but offline ACE bays stay ACE bays") {
        info.head_focused_inventory = true;
        AmsUnit ace;
        ace.connected = false;
        info.units.push_back(ace);
        CHECK_FALSE(material_workspace::uses_external_spools(info));
        CHECK(material_workspace::supports_head_inventory(info));
    }
    SECTION("A mode flag with no upstream units still has direct spools") {
        info.head_focused_inventory = true;
        CHECK(material_workspace::uses_external_spools(info));
    }
    SECTION("Incomplete or shared-nozzle topology is never invented as four external spools") {
        info.units.front().slots.pop_back();
        CHECK_FALSE(material_workspace::uses_external_spools(info));
        CHECK_FALSE(material_workspace::supports_head_inventory(info));
        info.units.front() = heads;
        info.units.front().topology = PathTopology::LINEAR;
        CHECK_FALSE(material_workspace::uses_external_spools(info));
    }
    SECTION("Physical head mapping does not depend on slot vector order") {
        std::swap(info.units.front().slots[0], info.units.front().slots[3]);
        CHECK(material_workspace::uses_external_spools(info));
        REQUIRE(material_workspace::head_slot(info, 3));
        CHECK(material_workspace::head_slot(info, 3)->global_index == 3);
    }
}
