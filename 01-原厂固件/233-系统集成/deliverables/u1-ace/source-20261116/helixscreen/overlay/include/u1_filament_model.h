// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include "ams_types.h"

#include <array>
#include <cstdint>
#include <optional>
#include <vector>

namespace helix::u1_filament {

struct AceGroup {
    int index;             // Address published by the device.
    int backend_unit;      // Position used by the backend's unit API.
};

struct CardRoute {
    int backend_unit;
    int ace;               // -1 for external filament.
    int bay;
    int head;
    int global_slot;
};

struct InventoryView {
    std::vector<AceGroup> groups;
    std::vector<CardRoute> cards;
    int selected_ace = -1;
    bool by_ace = false;
};

InventoryView inventory_view(const AmsSystemInfo& info, bool by_ace, int selected_ace,
                             int head, const std::array<bool, 4>& external_heads);

// A falling head sensor does not imply an empty ACE inlet.
bool can_exit_ace(bool connected, bool bay_present, bool idle, bool task_fresh,
                  bool task_busy, bool stop_fault, bool held, bool recovery_allowed);
int ace_unit_for_address(const AmsSystemInfo& info, int ace);

struct EditDraft {
    explicit EditDraft(const SlotInfo& slot);
    SlotInfo original;
    std::string material;
    uint32_t color;
    void finish_color(uint32_t chosen, bool accept);

    // Validate the immutable slot/spool identity, then apply just the two fields
    // to current metadata so background weight updates survive.
    std::optional<SlotInfo> update(const SlotInfo& current) const;
};

} // namespace helix::u1_filament
