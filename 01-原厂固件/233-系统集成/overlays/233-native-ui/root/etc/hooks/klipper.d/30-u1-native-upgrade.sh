#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# S60 sources this hook before Klipper reads its existing extended/*.cfg includes.
# The shared config contains an optional include only. The module's section lives
# in this system slot, so old slots can parse the same /oem configuration.
case "${1:-}" in
    start|restart)
        if [ -e /run/u1-native-upgrade/request.json ]; then
            echo 'Klipper start blocked: native U1 upgrade owns the device.' >&2
            exit 1
        fi
        /usr/bin/python3 /usr/local/lib/u1-device-service/slot_config.py || exit 1
        ;;
esac
