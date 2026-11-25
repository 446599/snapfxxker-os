// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <string>

struct _lv_obj_t;

namespace helix::ui {
/// Add an original U1 illustration to a fault dialog, if the code matches.
void add_u1_fault_illustration(_lv_obj_t* dialog, const std::string& code);
}
