#!/bin/sh
# Run on a workstation: no actual mount, no printer access.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
. "$ROOT/overlays/mods/multiace/70-app-multiace/root/usr/local/share/multiace/common.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
MULTIACE_APP_ROOT="$TMP/provider"
LOG="$TMP/mounts"
: > "$LOG"
multiace_bind_file() { printf '%s %s\n' "$1" "$2" >> "$LOG"; }
multiace_unmount_file() { printf '%s\n' "$1" >> "$LOG"; }
while read -r source target; do
    mkdir -p "$MULTIACE_APP_ROOT/klipper/$(dirname "$source")"
    touch "$MULTIACE_APP_ROOT/klipper/$source"
done <<MAP
$(multiace_1902_module_map)
MAP
multiace_mount_1902_modules
[ "$(wc -l < "$LOG")" -eq 24 ]
[ "$(awk '{print $2}' "$LOG" | sort -u | wc -l)" -eq 24 ]
echo 'PASS all 24 distinct mapped module paths'
for name in filament_feed filament_switch_sensor extruder; do
    grep -E "/${name}_ace\.py /home/lava/klipper/klippy/(extras|kinematics)/${name}\.py$" "$LOG" >/dev/null
done
echo 'PASS three stock-name aliases'
: > "$LOG"
rm "$MULTIACE_APP_ROOT/klipper/extras/ace_drivers.py"
if multiace_mount_1902_modules; then exit 1; fi
[ ! -s "$LOG" ]
echo 'PASS incomplete package refused before any bind'
touch "$MULTIACE_APP_ROOT/klipper/extras/ace_drivers.py"
: > "$LOG"
multiace_unmount_1902_modules
[ "$(wc -l < "$LOG")" -eq 25 ]
[ "$(sort -u "$LOG" | wc -l)" -eq 25 ]
echo 'PASS rollback unmounts all new module paths'
: > "$LOG"
multiace_bind_file() { printf '%s\n' "$2" >> "$LOG"; return 1; }
if multiace_mount_1902_modules; then exit 1; fi
[ "$(wc -l < "$LOG")" -eq 1 ]
echo 'PASS bind error propagates immediately'
touch "$MULTIACE_APP_ROOT/klipper/extras/bg_concurrency_probe.py"
: > "$LOG"
multiace_bind_file() { printf '%s %s\n' "$1" "$2" >> "$LOG"; }
multiace_mount_1902_modules
[ "$(wc -l < "$LOG")" -eq 25 ]
grep 'bg_concurrency_probe.py' "$LOG" >/dev/null
echo 'PASS optional diagnostic is mapped when present'
