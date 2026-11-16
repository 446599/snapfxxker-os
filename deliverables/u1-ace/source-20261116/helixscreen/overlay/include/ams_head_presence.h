// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <optional>
namespace helix {
/// Physical toolhead switch readings. Missing evidence remains unknown.
class IHeadPresence {
  public:
    [[nodiscard]] virtual std::optional<bool> head_physical_presence(int head) const = 0;
    /// Confirmed source as a backend-global slot, independent of nozzle-load routing.
    [[nodiscard]] virtual std::optional<int> head_physical_source(int) const { return std::nullopt; }
  protected:
    ~IHeadPresence() = default;
};
} // namespace helix
