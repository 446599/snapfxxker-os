// Copyright (C) 2025-2026 356C LLC
// SPDX-License-Identifier: GPL-3.0-or-later

#include "ui_notification_history.h"

#include <spdlog/spdlog.h>

#include <algorithm>
#include <cstring>
#include <set>

NotificationHistory& NotificationHistory::instance() {
    static NotificationHistory instance;
    return instance;
}

void NotificationHistory::append_locked(const NotificationHistoryEntry& source) {
    auto entry = source;
    entry.id = next_id_++;
    entry.dismissed = false;
    if (entries_.empty()) entries_.reserve(MAX_ENTRIES);
    if (entries_.size() < MAX_ENTRIES) {
        entries_.push_back(entry);
        head_index_ = entries_.size() % MAX_ENTRIES;
    } else {
        buffer_full_ = true;
        // Prefer historical entries over an unresolved condition when the ring fills.
        size_t target = head_index_;
        for (size_t n = 0; n < MAX_ENTRIES; ++n) {
            const size_t i = (head_index_ + n) % MAX_ENTRIES;
            if (!entries_[i].active) { target = i; break; }
        }
        entries_[target] = entry;
        head_index_ = (target + 1) % MAX_ENTRIES;
    }
}

void NotificationHistory::add(const NotificationHistoryEntry& entry) {
    std::lock_guard<std::mutex> lock(mutex_);
    append_locked(entry);
    ++version_;
}

bool NotificationHistory::sync_active(const std::string& prefix,
        const std::vector<NotificationHistoryEntry>& current, uint64_t now_ms) {
    if (prefix.empty()) return false;
    std::lock_guard<std::mutex> lock(mutex_);
    std::set<std::string> keys;
    for (const auto& entry : current) {
        if (entry.active && std::string(entry.key).rfind(prefix, 0) == 0)
            keys.emplace(entry.key);
    }
    bool changed = false;
    for (auto& entry : entries_) {
        if (entry.active && std::string(entry.key).rfind(prefix, 0) == 0 &&
                !keys.count(entry.key)) {
            entry.active = false;
            entry.resolved = true;
            entry.action[0] = entry.action_label[0] = '\0';
            entry.severity = ToastSeverity::SUCCESS;
            entry.was_read = false;
            entry.timestamp_ms = now_ms;
            changed = true;
        }
    }
    for (const auto& item : current) {
        if (!keys.count(item.key)) continue;
        const auto found = std::find_if(entries_.begin(), entries_.end(),
            [&item](const auto& entry) { return std::strcmp(entry.key, item.key) == 0; });
        if (found != entries_.end() && found->active &&
            found->severity == item.severity && std::strcmp(found->title, item.title) == 0 &&
            std::strcmp(found->message, item.message) == 0 &&
            std::strcmp(found->action, item.action) == 0 &&
            std::strcmp(found->action_label, item.action_label) == 0) continue;
        auto entry = item;
        entry.resolved = false;
        entry.was_read = false;
        entry.timestamp_ms = now_ms;
        if (found == entries_.end()) append_locked(entry);
        else {
            entry.id = found->active ? found->id : next_id_++;
            entry.dismissed = found->active && found->dismissed;
            if (entry.dismissed) entry.was_read = true;
            *found = entry;
        }
        changed = true;
    }
    if (changed) ++version_;
    return changed;
}

std::vector<NotificationHistoryEntry> NotificationHistory::get_all() const {
    std::lock_guard<std::mutex> lock(mutex_);

    if (entries_.empty()) {
        return {};
    }

    std::vector<NotificationHistoryEntry> result;
    result.reserve(entries_.size());

    if (!buffer_full_) {
        // Buffer not full - entries are in order, just reverse
        result.assign(entries_.rbegin(), entries_.rend());
    } else {
        // Buffer is full - reconstruct newest-first order
        // head_index_ points to oldest, so start from head_index_-1 (newest)
        size_t idx = (head_index_ == 0) ? MAX_ENTRIES - 1 : head_index_ - 1;
        for (size_t i = 0; i < entries_.size(); i++) {
            result.push_back(entries_[idx]);
            idx = (idx == 0) ? MAX_ENTRIES - 1 : idx - 1;
        }
    }

    result.erase(std::remove_if(result.begin(), result.end(),
        [](const auto& entry) { return entry.dismissed; }), result.end());
    std::stable_partition(result.begin(), result.end(), [](const auto& e) { return e.active; });
    return result;
}

std::vector<NotificationHistoryEntry> NotificationHistory::get_filtered(int severity) const {
    // Don't hold lock while calling get_all() - it has its own lock
    auto all_entries = get_all();

    if (severity < 0) {
        return all_entries;
    }

    std::vector<NotificationHistoryEntry> result;
    std::copy_if(all_entries.begin(), all_entries.end(), std::back_inserter(result),
                 [severity](const NotificationHistoryEntry& e) {
                     return static_cast<int>(e.severity) == severity;
                 });

    return result;
}

size_t NotificationHistory::get_unread_count() const {
    std::lock_guard<std::mutex> lock(mutex_);

    return static_cast<size_t>(
        std::count_if(entries_.begin(), entries_.end(),
                      [](const NotificationHistoryEntry& e) { return !e.was_read && !e.dismissed; }));
}

ToastSeverity NotificationHistory::get_highest_unread_severity() const {
    std::lock_guard<std::mutex> lock(mutex_);

    // Severity priority: ERROR > WARNING > SUCCESS > INFO
    ToastSeverity highest = ToastSeverity::INFO;

    for (const auto& entry : entries_) {
        if (!entry.was_read && !entry.dismissed) {
            if (entry.severity == ToastSeverity::ERROR) {
                return ToastSeverity::ERROR; // Can't get higher than this
            }
            if (entry.severity == ToastSeverity::WARNING && highest != ToastSeverity::ERROR) {
                highest = ToastSeverity::WARNING;
            }
        }
    }

    return highest;
}

void NotificationHistory::mark_all_read() {
    std::lock_guard<std::mutex> lock(mutex_);

    for (auto& entry : entries_) {
        entry.was_read = true;
    }

    spdlog::debug("[Notification History] Marked all {} notifications as read", entries_.size());
}

void NotificationHistory::clear() {
    std::lock_guard<std::mutex> lock(mutex_);

    entries_.erase(std::remove_if(entries_.begin(), entries_.end(),
        [](const auto& entry) { return !entry.active; }), entries_.end());
    head_index_ = entries_.size() % MAX_ENTRIES;
    buffer_full_ = entries_.size() == MAX_ENTRIES;
    ++version_;

    spdlog::debug("[Notification History] Cleared notification history");
}

bool NotificationHistory::dismiss(uint64_t id) {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto found = std::find_if(entries_.begin(), entries_.end(),
        [id](const auto& entry) { return entry.id == id; });
    if (found == entries_.end() || found->dismissed) return false;
    found->dismissed = true;
    found->was_read = true;
    ++version_;
    return true;
}

size_t NotificationHistory::count() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return std::count_if(entries_.begin(), entries_.end(),
        [](const auto& entry) { return !entry.dismissed; });
}

uint64_t NotificationHistory::version() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return version_;
}

void NotificationHistory::seed_test_data() {
    // Add test notifications with varied severities and timestamps
    // Timestamps are offset from current tick to simulate "time ago" display
    uint64_t now = lv_tick_get();

    // Error from 2 hours ago
    NotificationHistoryEntry error_entry = {};
    error_entry.timestamp_ms = now - (2 * 60 * 60 * 1000); // 2 hours ago
    error_entry.severity = ToastSeverity::ERROR;
    strncpy(error_entry.title, "Thermal Runaway", sizeof(error_entry.title) - 1);
    strncpy(error_entry.message, "Hotend temperature exceeded safety threshold. Heater disabled.",
            sizeof(error_entry.message) - 1);
    error_entry.was_modal = true;
    error_entry.was_read = false;
    add(error_entry);

    // Warning from 45 minutes ago
    NotificationHistoryEntry warning_entry = {};
    warning_entry.timestamp_ms = now - (45 * 60 * 1000); // 45 min ago
    warning_entry.severity = ToastSeverity::WARNING;
    strncpy(warning_entry.title, "Filament Low", sizeof(warning_entry.title) - 1);
    strncpy(warning_entry.message, "AMS slot 1 has less than 10m of filament remaining.",
            sizeof(warning_entry.message) - 1);
    warning_entry.was_modal = false;
    warning_entry.was_read = false;
    add(warning_entry);

    // Untitled entries are what plain toasts record, the most common kind
    // Success from 20 minutes ago
    NotificationHistoryEntry success_entry = {};
    success_entry.timestamp_ms = now - (20 * 60 * 1000); // 20 min ago
    success_entry.severity = ToastSeverity::SUCCESS;
    strncpy(success_entry.message, "benchy_v2.gcode finished successfully in 1h 23m.",
            sizeof(success_entry.message) - 1);
    success_entry.was_modal = false;
    success_entry.was_read = false;
    add(success_entry);

    // Info from 5 minutes ago
    NotificationHistoryEntry info_entry = {};
    info_entry.timestamp_ms = now - (5 * 60 * 1000); // 5 min ago
    info_entry.severity = ToastSeverity::INFO;
    strncpy(info_entry.title, "Firmware Update", sizeof(info_entry.title) - 1);
    strncpy(info_entry.message, "Klipper v0.12.1 is available. Current: v0.12.0",
            sizeof(info_entry.message) - 1);
    info_entry.was_modal = false;
    info_entry.was_read = false;
    add(info_entry);

    // Another warning from just now
    NotificationHistoryEntry warning2_entry = {};
    warning2_entry.timestamp_ms = now - (30 * 1000); // 30 sec ago
    warning2_entry.severity = ToastSeverity::WARNING;
    strncpy(warning2_entry.message,
            "Bed mesh is outdated. Re-calibrate before the next print to avoid first-layer "
            "adhesion problems.",
            sizeof(warning2_entry.message) - 1);
    warning2_entry.was_modal = false;
    warning2_entry.was_read = false;
    add(warning2_entry);

    spdlog::debug("[Notification History] Seeded {} test notifications", 5);
}
