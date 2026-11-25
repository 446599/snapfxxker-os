// SPDX-License-Identifier: GPL-3.0-or-later
#include "material_workspace.h"

#include "catch_amalgamated.hpp"

using helix::material_workspace::build_command;
TEST_CASE("Workspace reads MultiACE state without the optional companion service",
          "[material-workspace]") {
    CHECK(helix::material_workspace::request_url("/state") ==
          "http://127.0.0.1:7126/api/state");
    CHECK(helix::material_workspace::request_url("/spoolman/sync", true) ==
          "http://127.0.0.1:7126/api/spoolman/sync");
}
TEST_CASE("Local firmware operations stay behind the validating device service",
          "[material-workspace]") {
    CHECK(helix::material_workspace::request_url("/firmware/files") ==
          "http://127.0.0.1:7136/firmware/files");
    CHECK(helix::material_workspace::request_url("/firmware/start", true) ==
          "http://127.0.0.1:7136/firmware/start");
}
TEST_CASE("Workspace commands keep the selected zero-based ACE and bay", "[material-workspace]") {
    const auto command = build_command("read_tag", {{"ACE", 2}, {"SLOT", 3}});
    REQUIRE(command);
    CHECK(*command == "ACE_TAG_READ ACE=2 SLOT=3");
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
