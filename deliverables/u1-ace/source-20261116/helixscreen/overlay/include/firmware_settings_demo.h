// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include "json_fwd.h"

namespace helix {

// In-memory settings provider for the explicit mock-printer path only.
nlohmann::json firmware_settings_demo(const nlohmann::json& body, bool post);

} // namespace helix
