// SPDX-License-Identifier: GPL-3.0-or-later
#include "material_operation.h"
#include "ams_types.h"
#include "hv/json.hpp"
#include "../helix_test_fixture.h"
#include "catch_amalgamated.hpp"
using namespace helix::material_workspace;
TEST_CASE_METHOD(HelixTestFixture, "Progress only stops the displayed operation and boot", "[material-operation]") {
    nlohmann::json state = {{"slot_action", {{"kind", "insert"}, {"state", "running"},
        {"ace", 1}, {"slot", 2}, {"seq", 8}, {"epoch", "0123456789abcdef"},
        {"progress_percent", 50}, {"progress_estimated", true}}}, {"tag_op", {{"busy", false}}}};
    const auto initial = material_operations(state);
    REQUIRE(initial.size() == 1);
    CHECK(initial[0].active); CHECK(initial[0].percent == 50); CHECK(initial[0].estimated);
    CHECK(initial[0].stop_request() == nlohmann::json{{"kind","insert"},{"ace",1},{"slot",2},{"seq",8},{"epoch","0123456789abcdef"}});
    SECTION("A reboot cannot reuse a displayed stop target") {
        state["slot_action"]["epoch"] = "1123456789abcdef";
        CHECK_FALSE(initial[0].same_task(material_operations(state)[0]));
    }
    SECTION("A following task cannot reuse a displayed stop target") {
        state["slot_action"]["seq"] = 9;
        CHECK_FALSE(initial[0].same_task(material_operations(state)[0]));
    }
    SECTION("Estimate stays below complete until device terminal") {
        state["slot_action"]["progress_percent"] = 100;
        CHECK(material_operations(state)[0].percent < 100);
        state["slot_action"]["state"] = "complete";
        CHECK_FALSE(material_operations(state)[0].active);
        CHECK(material_operations(state)[0].percent == 100);
    }
    SECTION("RFID has a task target without invented percentages") {
        state["tag_op"] = {{"kind","read"},{"busy",true},{"ace",2},{"slot",0},{"seq",5}};
        const auto ops = material_operations(state);
        REQUIRE(ops.size() == 2); CHECK(ops[1].kind == "rfid"); CHECK(ops[1].percent == -1);
    }
    SECTION("Missing and invalid identity is not stoppable") {
        state["slot_action"].erase("epoch"); CHECK(material_operations(state).empty());
        state["slot_action"]["epoch"] = "0123456789abcdef";
        state["slot_action"]["slot"] = 4; CHECK(material_operations(state).empty());
    }
}
