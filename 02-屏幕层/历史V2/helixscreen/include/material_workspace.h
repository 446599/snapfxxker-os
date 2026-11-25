// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <functional>
#include <optional>
#include <string>

#include "hv/json.hpp"

namespace helix::material_workspace {
bool supports_local_upgrade();
using Reply = std::function<void(nlohmann::json, std::string)>;
// Callbacks run on the UI thread. The companion is local to the machine screen.
std::string request_url(const std::string& path, bool post = false);
void request(const std::string& path, const nlohmann::json& body, Reply reply, bool post = false);
std::optional<std::string> build_command(const std::string& action, const nlohmann::json& args);
void command(const std::string& action, const nlohmann::json& args, Reply reply);
} // namespace helix::material_workspace
