// Copyright (C) 2025-2026 356C LLC
// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

namespace helix {

/**
 * @brief Show the U1 startup card
 *
 * Displays the shared native U1 card with fade-in animation.
 * Blocks for the splash duration (2 seconds by default).
 * Must be called after LVGL and theme initialization.
 *
 * @param screen_width Display width in pixels (for card sizing)
 * @param screen_height Display height in pixels (for card sizing)
 */
void show_splash_screen(int screen_width, int screen_height);

} // namespace helix
