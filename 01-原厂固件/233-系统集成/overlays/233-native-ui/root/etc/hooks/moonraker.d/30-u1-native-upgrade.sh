#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# S61 sources this before its action. Keep external restart commands from
# reopening the control API while the native upgrader owns the device.
case "${1:-}" in
    start|restart)
        if [ -e /run/u1-native-upgrade/request.json ]; then
            echo 'Moonraker start blocked: native U1 upgrade owns the device.' >&2
            exit 1
        fi
        ;;
esac
