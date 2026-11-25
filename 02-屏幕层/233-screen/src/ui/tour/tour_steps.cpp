// Copyright (C) 2025-2026 356C LLC
// SPDX-License-Identifier: GPL-3.0-or-later

#include "tour_steps.h"

#include "ams_state.h"

#include <algorithm>
#include <utility>

namespace helix::tour {

bool hardware_has_ams() {
    return AmsState::instance().is_available();
}

// i18n: the literals below are translation keys (the English UI text),
// resolved indirectly via lv_tr(step.title_key.c_str()) in tour_overlay.cpp.
// They are live keys even though no lv_tr("...") call wraps them here —
// `translation_sync obsolete` finds them through its reference scan, not the
// extractor patterns. Keys must be their own English text — English registers
// no translation pack (translation_loader.cpp), so a semantic key like
// "Welcome to HelixScreen" renders RAW in the English UI. Renaming one here
// means renaming it in translations/*.yml too.
std::vector<TourStep> build_tour_steps(bool has_ams, int seen_version) {
    std::vector<TourStep> steps;
    steps.reserve(6);

    // The U1 has a fixed five-section navigation and a four-head home overview.
    // Spotlight stable navigation targets rather than optional home widgets, so
    // the guide remains correct with ACE units and with four external spools.
    steps.push_back({"",
                     "Welcome to U1",
                     "Home, Control, Filament, Settings and More are always on the left. This short guide shows where each task belongs.",
                     TooltipAnchor::Center,
                     {}});
    steps.push_back({"nav_btn_home",
                     "Your print and all four heads",
                     "See print progress and time left together with H1-H4 temperatures and filament colors. Open the task card for print details.",
                     TooltipAnchor::PreferRight,
                     {}});
    steps.push_back({"nav_btn_controls",
                     "Control the printer",
                     "Temperature, movement, fans and lights each have a clear control page. Printing locks actions that cannot run together.",
                     TooltipAnchor::PreferRight,
                     {}});
    steps.push_back({"nav_btn_filament",
                     "Choose a head, then its filament",
                     has_ams
                         ? "H1-H4 show material and color. Open a head for its connected feeders or external spool, then load or unload."
                         : "H1-H4 show four external spools. Set material and color before manual loading or unloading. Remaining amount is not estimated.",
                     TooltipAnchor::PreferRight,
                     {}});
    steps.push_back({"nav_btn_settings",
                     "Settings and Advanced Settings",
                     "Network, display and hardware are grouped here. Advanced Settings keeps all eight firmware groups and reviews old and new values before applying and reading them back.",
                     TooltipAnchor::PreferRight,
                     {}});
    steps.push_back({"nav_btn_advanced",
                     "More tools and records",
                     "Find print history, messages and the available print queue under Tools. Calibration and System have their own tabs. Replay this guide from Help and About.",
                     TooltipAnchor::PreferRight,
                     {}});
    for (auto& step : steps) {
        step.since_version = 3;
    }

    const bool upgrading = seen_version > 0;
    steps.erase(std::remove_if(steps.begin(), steps.end(),
                               [&](const TourStep& s) {
                                   return upgrading ? s.since_version <= seen_version
                                                    : s.upgrade_only;
                               }),
                steps.end());
    return steps;
}

int auto_start_seen_version(bool completed, int last_seen_version) {
    return completed ? last_seen_version : 0;
}

} // namespace helix::tour
