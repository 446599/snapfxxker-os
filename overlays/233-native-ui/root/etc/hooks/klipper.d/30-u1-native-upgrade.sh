#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# S60 sources this hook before Klipper reads its existing extended/*.cfg includes.
case "${1:-}" in
    start|restart)
        if [ -e /run/u1-native-upgrade/request.json ]; then
            echo 'Klipper start blocked: native U1 upgrade owns the device.' >&2
            exit 1
        fi
        upgrade_cfg_dir=/oem/printer_data/config/extended/klipper
        mkdir -p "$upgrade_cfg_dir" || exit 1
        upgrade_cfg="$upgrade_cfg_dir/u1_native_upgrade.cfg"
        if [ ! -f "$upgrade_cfg" ] || ! grep -q '^\[u1_native_upgrade\]$' "$upgrade_cfg"; then
            printf '# Managed by the 233 native UI upgrade service.\n[u1_native_upgrade]\n' > "$upgrade_cfg.tmp" || exit 1
            chmod 644 "$upgrade_cfg.tmp" || exit 1
            mv "$upgrade_cfg.tmp" "$upgrade_cfg" || exit 1
        fi
        unset upgrade_cfg upgrade_cfg_dir
        ;;
esac
