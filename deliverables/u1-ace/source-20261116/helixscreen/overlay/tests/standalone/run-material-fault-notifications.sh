#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
test_bin=$(mktemp /tmp/material-fault-tests.XXXXXX)
trap 'rm -f "$test_bin"' EXIT
"${CXX:-clang++}" -std=c++17 -O0 -DLV_CONF_INCLUDE_SIMPLE -Iinclude -I. -Ilib/lvgl -Ilib/libhv/include -Ilib/spdlog/include tests/standalone/material_fault_notifications.cpp src/printer/material_fault_notice.cpp src/ui/notify/ui_notification_history.cpp -o "$test_bin"
"$test_bin"
