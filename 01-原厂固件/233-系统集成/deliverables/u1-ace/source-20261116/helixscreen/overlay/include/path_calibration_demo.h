// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "hv/json.hpp"
#include <string>
namespace helix::material_workspace {
nlohmann::json path_calibration_demo_state();
std::string path_calibration_demo_action(const nlohmann::json& arguments);
} // namespace helix::material_workspace
