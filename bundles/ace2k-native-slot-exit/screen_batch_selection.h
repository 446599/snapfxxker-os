// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <map>
#include <set>
#include <string>
#include <utility>

namespace helix::u1_filament {
struct BatchBay {
    int ace, slot, global;
    std::string serial;
};
class BatchSelection {
    std::map<std::pair<int,int>, BatchBay> bays_;
  public:
    bool toggle(BatchBay bay) {
        if (bay.ace < 0 || bay.ace >= 4 || bay.slot < 0 || bay.slot >= 4 ||
            bay.global < 0 || bay.serial.empty()) return false;
        const auto key = std::make_pair(bay.ace,bay.slot);
        auto old = bays_.find(key);
        if (old != bays_.end()) { bays_.erase(old); return true; }
        bays_.emplace(key,std::move(bay)); return true;
    }
    bool contains(int ace,int slot) const { return bays_.count({ace,slot}) != 0; }
    const auto& entries() const { return bays_; }
    bool empty() const { return bays_.empty(); }
    void clear() { bays_.clear(); }
    bool distinct_heads() const {
        std::set<int> heads;
        for (const auto& e : bays_) if (!heads.insert(e.second.slot).second) return false;
        return true;
    }
};
}
