// Exercise the actual native command serializer without linking the display.
#include "multiace_status.h"
#include "screen_batch_progress.h"

#include <cassert>
#include <iostream>
#include <string>

int main() {
    using namespace helix::multiace;
    for (int head = 0; head < kHeadCount; ++head) {
        const std::string h = "HEAD=" + std::to_string(head);
        assert(unload_gcode(head) == "ACE_UNLOAD_HEAD " + h);
        for (int ace = 0; ace < 4; ++ace) {
            for (int slot = 0; slot < kBaysPerUnit; ++slot) {
                const auto address = bay_of(bay_global(ace, slot));
                assert(address && address->first == ace && address->second == slot);
                const std::string args = h + " ACE=" + std::to_string(ace)
                                           + " SLOT=" + std::to_string(slot);
                assert(load_gcode(head, address) == "ACE_LOAD_HEAD " + args);
                assert(unload_gcode(head, address) == "ACE_UNLOAD_HEAD " + args);
            }
        }
    }
    using helix::u1_filament::recover_material_batch;
    using nlohmann::json;
    const auto tasks = json::array({
        {{"batch", "older"}, {"state", "queued"}},
        {{"batch", "current"}, {"state", "head_running"}},
        {{"batch", "current"}, {"state", "complete"}},
        {{"batch", "last"}, {"state", "failed"}},
        {{"batch", "bad"}, {"state", 123}},
    });
    assert(recover_material_batch(tasks) == "current");
    assert(recover_material_batch(json::array({{{"batch", "park"}, {"state", "awaiting_parking"}}}))
           == "park");
    assert(recover_material_batch(json::array({
        {{"batch", "done"}, {"state", "complete"}},
        {{"batch", "stopped"}, {"state", "cancelled"}},
        {{"batch", "error"}, {"state", "recovery"}},
    })).empty());
    assert(recover_material_batch(nullptr).empty());
    std::cout << "64 explicit route pairs, 4 head-only unloads and batch recovery passed\n";
}
