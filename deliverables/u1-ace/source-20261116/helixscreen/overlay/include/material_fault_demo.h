// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "json_fwd.h"
#include <string>
namespace helix::material_workspace {
// Called exclusively inside request()'s explicit mock-printer branch.
nlohmann::json operation_demo_request(const std::string& path, const nlohmann::json& body);
void fault_demo_state(nlohmann::json& state);
nlohmann::json fault_demo_recover(const nlohmann::json& body);
} // namespace helix::material_workspace
