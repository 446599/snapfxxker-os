// Copyright (C) 2025-2026 356C LLC
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include "ui_toast_manager.h"

#include <cstdint>
#include <mutex>
#include <string>
#include <vector>

/**
 * @brief Single notification history entry
 */
struct NotificationHistoryEntry {
    uint64_t timestamp_ms;  ///< LVGL tick time when notification occurred
    ToastSeverity severity; ///< INFO, SUCCESS, WARNING, ERROR
    char title[64];         ///< Title (empty for toasts)
    char message[256];      ///< Notification message
    bool was_modal;         ///< true if shown as modal dialog
    bool was_read;          ///< true if user viewed in history panel
    char key[64]{};         ///< Stable key for a live condition; empty for ordinary history
    char action_label[32]{}; ///< Explicit row action label, empty uses the generic label
    bool active = false;    ///< Still reported by its source
    bool resolved = false;  ///< Source confirmed that this condition ended
    uint64_t id = 0;        ///< Unique occurrence, independent of row order
    bool dismissed = false; ///< User removed this occurrence from the list
    char action[64];        ///< Action identifier (empty = no action, e.g. "show_update_modal")
};

/**
 * @brief Notification history manager
 *
 * Maintains a circular buffer of the last N notifications for user review.
 * Thread-safe for concurrent access from UI and background threads.
 */
class NotificationHistory {
  public:
    static constexpr size_t MAX_ENTRIES = 100; ///< Circular buffer size

    /**
     * @brief Get singleton instance
     *
     * @return Reference to the singleton instance
     */
    static NotificationHistory& instance();

    /**
     * @brief Add notification to history
     *
     * @param entry Notification entry to add
     */
    void add(const NotificationHistoryEntry& entry);

    /// Reconcile one source's active conditions. Unchanged entries retain their
    /// read state; missing keys become resolved history with no action.
    bool sync_active(const std::string& prefix,
                     const std::vector<NotificationHistoryEntry>& entries, uint64_t now_ms);

    /**
     * @brief Get entries with active conditions first, then newest history
     *
     * @return Vector of history entries
     */
    std::vector<NotificationHistoryEntry> get_all() const;

    /**
     * @brief Get entries filtered by severity
     *
     * @param severity Filter by this severity (or pass -1 for all)
     * @return Vector of filtered entries
     */
    std::vector<NotificationHistoryEntry> get_filtered(int severity) const;

    /**
     * @brief Get count of unread notifications
     *
     * @return Number of unread entries
     */
    size_t get_unread_count() const;

    /**
     * @brief Get highest severity among unread notifications
     *
     * @return Highest unread severity, or INFO if no unread notifications
     */
    ToastSeverity get_highest_unread_severity() const;

    /**
     * @brief Mark all notifications as read
     */
    void mark_all_read();

    /**
     * @brief Clear past history while retaining active conditions
     */
    void clear();

    /// Dismiss one occurrence; source state is retained so unchanged active
    /// conditions do not reappear on every status update.
    bool dismiss(uint64_t id);

    /**
     * @brief Get total notification count
     *
     * @return Number of entries in history
     */
    size_t count() const;

    /**
     * @brief Get the history revision
     *
     * Increments on every add() and clear(). mark_all_read() does not bump it,
     * so a consumer that refreshes on revision change cannot loop itself
     * (refresh marks the entries read). Used by the notification panel to
     * rebuild its list when a notification arrives while it is open.
     *
     * @return Monotonic revision counter
     */
    uint64_t version() const;

    /**
     * @brief Seed test notifications for --test mode debugging
     *
     * Adds a variety of test notifications with different severities
     * for UI testing and debugging purposes.
     */
    void seed_test_data();

  private:
    NotificationHistory() = default;
    ~NotificationHistory() = default;
    NotificationHistory(const NotificationHistory&) = delete;
    NotificationHistory& operator=(const NotificationHistory&) = delete;

    void append_locked(const NotificationHistoryEntry& entry);
    mutable std::mutex mutex_;
    std::vector<NotificationHistoryEntry> entries_;
    size_t head_index_ = 0;    ///< Circular buffer write position
    bool buffer_full_ = false; ///< True when we've wrapped around
    uint64_t next_id_ = 1;
    uint64_t version_ = 0;     ///< Bumped on add()/clear(), never on mark_all_read()
};
