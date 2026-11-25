// SPDX-License-Identifier: GPL-3.0-or-later
#include "u1_filament_model.h"
#include "multiace_status.h"

#include <algorithm>

namespace helix::u1_filament {

InventoryView inventory_view(const AmsSystemInfo& info, bool by_ace, int selected_ace,
                             int head, const std::array<bool, 4>& external_heads) {
    InventoryView view;
    for (int index = 1; index < static_cast<int>(info.units.size()); ++index) {
        const auto& unit = info.units[static_cast<size_t>(index)];
        const auto address = multiace::bay_of(unit.first_slot_global_index);
        // A multi-mode frame can reserve addresses even for boxes it has
        // never detected. The head-mode hub mapping is an explicit assignment.
        const bool known = unit.connected || !unit.firmware_version.empty() ||
            (unit.topology == PathTopology::HUB &&
             std::any_of(unit.slots.begin(), unit.slots.end(),
                 [](const SlotInfo& slot) { return slot.mapped_tool >= 0; }));
        if (address && address->first < 4 && address->second == 0 && !unit.absent && known) {
            view.groups.push_back({address->first, index});
        }
    }
    view.by_ace = by_ace && !view.groups.empty();
    std::sort(view.groups.begin(), view.groups.end(),
        [](const AceGroup& lhs, const AceGroup& rhs) { return lhs.index < rhs.index; });
    if (view.by_ace) {
        const auto selected = std::find_if(view.groups.begin(), view.groups.end(),
            [selected_ace](const AceGroup& group) { return group.index == selected_ace; });
        view.selected_ace = selected != view.groups.end() ? selected->index : view.groups.front().index;
    }
    for (const auto& group : view.groups) {
        if (view.by_ace && group.index != view.selected_ace) continue;
        const auto& unit = info.units[static_cast<size_t>(group.backend_unit)];
        for (const auto& slot : unit.slots) {
            const auto address = multiace::bay_of(slot.global_index);
            if (!address || address->first != group.index || address->second != slot.slot_index)
                continue;
            if (view.by_ace || slot.mapped_tool == head)
                view.cards.push_back({group.backend_unit, group.index, slot.slot_index,
                    slot.mapped_tool, slot.global_index});
        }
    }
    if (!view.by_ace && !info.units.empty()) {
        const bool all_external = std::all_of(external_heads.begin(), external_heads.end(),
            [](bool external) { return external; });
        for (const auto& slot : info.units.front().slots) {
            const int tool = slot.mapped_tool;
            if (tool >= 0 && tool < 4 && external_heads[static_cast<size_t>(tool)] &&
                (all_external || head == tool) && slot.global_index >= 0 &&
                !multiace::bay_of(slot.global_index))
                view.cards.push_back({0, -1, -1, tool, slot.global_index});
        }
    }
    return view;
}

bool can_exit_ace(bool connected, bool bay_present, bool idle, bool task_fresh,
                  bool task_busy, bool stop_fault, bool held, bool recovery_allowed) {
    return connected && bay_present && idle && task_fresh &&
        !task_busy && !stop_fault && (!held || recovery_allowed);
}

int ace_unit_for_address(const AmsSystemInfo& info, int ace) {
    if (ace < 0 || ace >= 4) return -1;
    const int first_slot = multiace::bay_global(ace, 0);
    for (int unit = 1; unit < static_cast<int>(info.units.size()); ++unit) {
        const auto& box = info.units[static_cast<size_t>(unit)];
        if (!box.absent && box.first_slot_global_index == first_slot) return unit;
    }
    return -1;
}

EditDraft::EditDraft(const SlotInfo& slot)
    : original(slot), material(slot.material), color(slot.color_rgb) {}

void EditDraft::finish_color(uint32_t chosen, bool accept) {
    if (accept) color = chosen;
}

std::optional<SlotInfo> EditDraft::update(const SlotInfo& current) const {
    if (current.global_index != original.global_index ||
        current.slot_index != original.slot_index || current.mapped_tool != original.mapped_tool ||
        current.is_present() != original.is_present() ||
        current.extruder_name != original.extruder_name || current.material != original.material ||
        current.color_rgb != original.color_rgb || current.brand != original.brand ||
        current.spoolman_id != original.spoolman_id ||
        current.spoolman_filament_id != original.spoolman_filament_id ||
        current.catalog_id != original.catalog_id || current.product_name != original.product_name)
        return std::nullopt;
    auto updated = current;
    updated.material = material;
    updated.color_rgb = color;
    if (color != current.color_rgb) {
        updated.color_name.clear();
        updated.multi_color_hexes.clear();
    }
    return updated;
}
} // namespace helix::u1_filament
