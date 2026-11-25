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

TEST_CASE_METHOD(HelixTestFixture,"Concurrent tasks survive panel navigation and stale reports","[material-operation]") {
    nlohmann::json data={{"slot_action",{{"kind","insert"},{"ace",0},{"slot",1},{"seq",4},
        {"epoch","0123456789abcdef"},{"state","running"}}},{"tag_op",{{"busy",false}}},
        {"dryers",{{{"ace",1},{"phase","venting"},{"message","排湿口打开"},{"cycle_id",std::string(32,'a')}}}}};
    MaterialTaskList list;
    list.update(material_operations(data),50);
    REQUIRE(list.size()==2);
    auto dry=list.current()->op;
    CHECK(dry.stop_request()==nlohmann::json{{"kind","dry"},{"ace",1},{"cycle_id",std::string(32,'a')}});
    list.next();
    REQUIRE(list.current()->op.kind=="insert");
    list.current()->failure="停止请求被拒绝";
    list.update(material_operations(data),250);
    CHECK(list.current()->failure=="停止请求被拒绝");
    list.current()->awaiting_stop=true; list.current()->stop_at=250;
    list.update(material_operations(data),5251);
    CHECK_FALSE(list.current()->awaiting_stop);
    CHECK_FALSE(list.current()->failure.empty());
    data["dryers"][0]["cycle_id"]=std::string(32,'b');
    list.update(material_operations(data),5351);
    CHECK(list.find(dry)->missing);
    list.next();
    CHECK(list.current()->op.id==std::string(32,'b'));
    CHECK_FALSE(list.current()->op.same_task(dry));
    data["slot_action"]["state"]="complete";
    list.update(material_operations(data),5400);
    list.update(material_operations(data),9010);
    CHECK(list.size()==2);
    data["path_calibration"]={{"task_id","cal-a"},{"version",5},{"stage","heating"},{"active",true}};
    list.update(material_operations(data),9210);
    REQUIRE(list.size()==3);
    list.next(); auto op=list.current()->op;
    REQUIRE(op.kind=="calibration"); CHECK(op.stop_request()["task_id"]=="cal-a");
    CHECK(op.stop_request()["expected_version"]==5);
    data["feeds"]={{{"ace",0},{"slot",2},{"seq",4},{"epoch","0123456789abcdef"},{"state","feeding"},{"active",true}}};
    list.update(material_operations(data),9400); list.next();
    REQUIRE(list.current()->op.kind=="feed");
    CHECK(list.current()->op.stop_request().is_null());
}

TEST_CASE_METHOD(HelixTestFixture,"Unload all requires an exact session and waits for the current head","[material-operation]") {
    nlohmann::json data={{"unload_all_active",true}};
    REQUIRE(material_operations(data).at(0).stop_request().is_null());
    data["unload_all"]={{"epoch","0123456789abcdef"},{"seq",2},{"state","running"}};
    auto task=material_operations(data).at(0);
    REQUIRE(task.stop_request()==nlohmann::json{{"kind","unload_all"},{"epoch","0123456789abcdef"},{"seq",2}});
    data["unload_all"]["seq"]=3;
    REQUIRE_FALSE(material_operations(data).at(0).same_task(task));
    data["unload_all"]["state"]="cancelling";
    REQUIRE(material_operations(data).at(0).stop_request().is_null());
    data["unload_all"]["state"]="cancelled";
    data["unload_all_active"]=false;
    REQUIRE_FALSE(material_operations(data).at(0).active);
}
