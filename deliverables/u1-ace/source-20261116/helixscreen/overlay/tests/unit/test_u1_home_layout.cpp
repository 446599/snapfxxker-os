// SPDX-License-Identifier: GPL-3.0-or-later
#include "default_layout_resolver.h"
#include "../catch_amalgamated.hpp"
#include <fstream>
#include <algorithm>

TEST_CASE("U1 task and four-head default fits measured 480x320 grids", "[u1_home][default_layout_resolver]") {
    nlohmann::json table, preset;
    std::ifstream("assets/config/default_layout.json") >> table;
    std::ifstream("assets/config/panel_widgets/snapmaker_u1/home.json") >> preset;
    helix::layout::CapabilitySnapshot capabilities;
    capabilities.subjects["ams_is_tool_changer"] = 1;
    for (const int rows : {4, 6}) {
        const auto layout = helix::layout::resolve_default_layout(table, &preset, "tiny", 12, rows,
            {"tiny"}, "snapmaker_u1", capabilities);
        REQUIRE(layout.ok);
        REQUIRE(layout.placed.size() == 3);
        for (const auto& cell : layout.placed) {
            CHECK(cell.col >= 0);
            CHECK(cell.row >= 0);
            CHECK(cell.col + cell.colspan <= 12);
            CHECK(cell.row + cell.rowspan <= rows);
        }
        const auto find = [&](const char* id) {
            return std::find_if(layout.placed.begin(), layout.placed.end(),
                [id](const auto& cell) { return cell.id == id; });
        };
        const auto tools = find("tool_switcher");
        const auto task = find("print_status");
        REQUIRE(tools != layout.placed.end());
        REQUIRE(task != layout.placed.end());
        CHECK(tools->colspan == 8);
        CHECK(tools->row == 0);
        CHECK(task->colspan == 12);
        CHECK(task->row == 2);
        CHECK(task->rowspan == rows - 2);
        CHECK(task->config["layout_style"] == "detailed");
    }
}
