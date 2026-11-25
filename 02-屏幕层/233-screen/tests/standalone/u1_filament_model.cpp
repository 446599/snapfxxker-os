// SPDX-License-Identifier: GPL-3.0-or-later
#include "u1_filament_model.h"
#include "multiace_status.h"

#include <cassert>
#include <iostream>

using namespace helix;
using namespace helix::u1_filament;

namespace {
AmsSystemInfo four_heads() {
    AmsSystemInfo info;
    auto& unit = info.units.emplace_back();
    unit.slot_count = 4;
    for (int head = 0; head < 4; ++head) {
        auto& slot = unit.slots.emplace_back();
        slot.global_index = slot.slot_index = slot.mapped_tool = head;
    }
    return info;
}

void add_ace(AmsSystemInfo& info, int ace, int head, bool connected = true) {
    auto& unit = info.units.emplace_back();
    unit.first_slot_global_index = multiace::bay_global(ace, 0);
    unit.slot_count = 4;
    unit.connected = connected;
    unit.topology = head >= 0 ? PathTopology::HUB : PathTopology::PARALLEL;
    for (int bay = 0; bay < 4; ++bay) {
        auto& slot = unit.slots.emplace_back();
        slot.slot_index = bay;
        slot.global_index = multiace::bay_global(ace, bay);
        slot.mapped_tool = head;
    }
}

void test_inventory() {
    const std::array<bool, 4> all_external{true, true, true, true};
    auto info = four_heads();
    auto view = inventory_view(info, true, 0, 2, all_external);
    assert(!view.by_ace && view.groups.empty() && view.cards.size() == 4);
    for (size_t i = 0; i < view.cards.size(); ++i) {
        assert(view.cards[i].ace == -1 && view.cards[i].head == static_cast<int>(i));
    }

    constexpr std::array<int, 4> addresses{2, 0, 3, 1};
    constexpr std::array<int, 4> heads{3, 0, 1, 2};
    auto external = all_external;
    for (size_t i = 0; i < 4; ++i) {
        add_ace(info, addresses[i], heads[i]);
        external[static_cast<size_t>(heads[i])] = false;
        view = inventory_view(info, false, -1, heads[i], external);
        assert(view.groups.size() == i + 1 && view.cards.size() == 4);
        for (size_t bay = 0; bay < 4; ++bay) {
            assert(view.cards[bay].ace == addresses[i] && view.cards[bay].head == heads[i]);
            assert(view.cards[bay].bay == static_cast<int>(bay));
        }
        assert(ace_unit_for_address(info, addresses[i]) == static_cast<int>(i) + 1);
        view = inventory_view(info, true, addresses[i], -1, external);
        assert(view.by_ace && view.cards.size() == 4 &&
               view.cards.front().backend_unit == static_cast<int>(i) + 1);
        if (i < 3) {
            view = inventory_view(info, false, -1, heads[i + 1], external);
            assert(view.cards.size() == 1 && view.cards.front().ace == -1);
        }
    }
    std::swap(info.units[1], info.units[3]);
    assert(ace_unit_for_address(info, 2) == 3);
    info.units[3].absent = true;
    assert(ace_unit_for_address(info, 2) == -1);

    info = four_heads();
    add_ace(info, 2, 3, false);
    external = {true, true, true, false};
    view = inventory_view(info, false, -1, 3, external);
    assert(view.groups.size() == 1 && view.cards.size() == 4 && view.cards.front().ace == 2);
    assert(!info.units[static_cast<size_t>(view.cards.front().backend_unit)].connected);
    info.units.pop_back();
    assert(inventory_view(info, false, -1, 3, external).cards.empty());
    add_ace(info, 3, -1, false);
    for (auto& slot : info.units.back().slots) slot.mapped_tool = slot.slot_index;
    assert(inventory_view(info, true, 3, 3, {}).groups.empty());

    info = four_heads();
    add_ace(info, 3, -1);
    add_ace(info, 1, -1);
    for (size_t unit = 1; unit < info.units.size(); ++unit)
        for (auto& slot : info.units[unit].slots)
            slot.mapped_tool = slot.slot_index;
    view = inventory_view(info, false, -1, 2, {});
    assert(view.cards.size() == 2 && view.cards[0].bay == 2 && view.cards[1].bay == 2);
    assert(view.cards[0].ace == 1 && view.cards[1].ace == 3);
    info.units[1].slots[2].mapped_tool = -1;
    view = inventory_view(info, false, -1, 2, {});
    assert(view.cards.size() == 1 && view.cards.front().ace == 1);
}

void test_exit() {
    // Only ACE inlet, slot task and machine state matter here. There is no
    // confirmed source or nozzle presence in the contract for an inlet exit.
    assert(can_exit_ace(true, true, true, true, false, false, false, false));
    assert(!can_exit_ace(false, true, true, true, false, false, false, false));
    assert(!can_exit_ace(true, false, true, true, false, false, false, false));
    assert(!can_exit_ace(true, true, false, true, false, false, false, false));
    assert(!can_exit_ace(true, true, true, false, false, false, false, false));
    assert(!can_exit_ace(true, true, true, true, true, false, false, false));
    assert(!can_exit_ace(true, true, true, true, false, true, false, false));
    assert(!can_exit_ace(true, true, true, true, false, false, true, false));
    assert(can_exit_ace(true, true, true, true, false, false, true, true));
}

void test_edit() {
    SlotInfo current;
    current.global_index = 11;
    current.slot_index = 3;
    current.mapped_tool = 3;
    current.material = "Custom PLA 86";
    current.color_rgb = 0x112233;
    current.color_name = "Old";
    current.brand = "Test Brand";
    current.spoolman_id = 6;
    current.remaining_weight_g = 700;

    EditDraft draft(current);
    draft.finish_color(0x556677, false);
    assert(draft.update(current)->color_rgb == current.color_rgb);
    assert(draft.update(current)->material == "Custom PLA 86");
    draft.material = "PETG";
    draft.finish_color(0x445566, true);
    draft.finish_color(0xAABBCC, false);
    current.remaining_weight_g = 635;
    const auto edit = draft.update(current);
    assert(edit && edit->material == "PETG" && edit->color_rgb == 0x445566);
    assert(edit->color_name.empty() && edit->brand == "Test Brand" &&
           edit->remaining_weight_g == 635 && edit->spoolman_id == 6);
    assert(current.material == "Custom PLA 86" && current.color_rgb == 0x112233);
    SlotInfo changed = current;
    changed.global_index = 15;
    assert(!draft.update(changed));
    changed = current;
    changed.spoolman_id = 19;
    assert(!draft.update(changed));
    changed = current;
    changed.mapped_tool = 0;
    assert(!draft.update(changed));
}
} // namespace

int main() {
    test_inventory();
    test_exit();
    test_edit();
    std::cout << "PASS: 0–4 ACEs, head/multi modes, sparse maps, offline routes, "
                 "slot exit guards, edit identity and cancel/save drafts\n";
}
