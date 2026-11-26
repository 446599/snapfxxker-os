// Exercise the actual native command serializer without linking the display.
#include "multiace_status.h"

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
    std::cout << "64 explicit load/unload route pairs and 4 head-only unloads passed\n";
}
