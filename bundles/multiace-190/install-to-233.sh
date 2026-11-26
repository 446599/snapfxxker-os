#!/bin/sh
# Temporary 1.90.2 runtime installer for the local 233 test device.
# Does not change the production overlay pin or the MCU firmware.
# Web service activation is separate; the extracted provider has the matching web tree.
set -eu
PKG=${1:?usage: install-to-233.sh <package.tar.gz> [--check]}
MODE=${2:-install}
[ "$MODE" = install ] || [ "$MODE" = --check ] || { echo 'unknown mode' >&2; exit 2; }
PKG=$(readlink -f "$PKG")
KLIPPY=${MULTIACE_KLIPPY_DIR:-/home/lava/klipper/klippy}
DATA=${MULTIACE_PRINTER_DATA:-/home/lava/printer_data}
STAGING=${MULTIACE_STAGE_DIR:-/userdata/multiace-provider-runtime}
BACKUPS=${MULTIACE_BACKUP_DIR:-/userdata}
SERVICE=${MULTIACE_KLIPPER_SERVICE:-/etc/init.d/S60klipper}
API=${MOONRAKER_URL:-http://127.0.0.1:7125}

# Verify the archive and derive every module mapping from the package itself.
# No extraction or modification in --check mode.
if [ -f "$PKG.sha256" ]; then
    EXPECTED=$(awk 'NR==1 {print $1}' "$PKG.sha256")
    ACTUAL=$(sha256sum "$PKG" | awk '{print $1}')
    [ "$EXPECTED" = "$ACTUAL" ] || { echo 'package checksum mismatch' >&2; exit 1; }
fi
LAYOUT=$(python3 - "$PKG" <<'PY'
import sys, tarfile
from pathlib import PurePosixPath
with tarfile.open(sys.argv[1], 'r:gz') as t:
    members = t.getmembers()
    roots = set()
    files = set()
    for m in members:
        p = PurePosixPath(m.name)
        if p.is_absolute() or '..' in p.parts or not p.parts or not (m.isfile() or m.isdir()):
            raise SystemExit('unsupported archive entry: ' + m.name)
        roots.add(p.parts[0])
        if m.isfile():
            if m.name in files:
                raise SystemExit('duplicate archive entry: ' + m.name)
            files.add(m.name)
    if len(roots) != 1:
        raise SystemExit('expected one provider root')
    root = next(iter(roots))
    for name in ('VERSION', 'klipper/extras/ace.py', 'klipper/extras/ace_protocol_ace2k.py',
                 'klipper/extras/filament_feed_ace.py', 'klipper/extras/filament_switch_sensor_ace.py',
                 'klipper/kinematics/extruder_ace.py', 'config/extended/ace.cfg',
                 'web/backend/main.py', 'web/frontend/index.html'):
        if root + '/' + name not in files:
            raise SystemExit('missing ' + name)
    version = t.extractfile(root + '/VERSION').read().decode().strip()
    if not (version == '1.90.2' or version.startswith('1.90.2-')):
        raise SystemExit('wrong provider version: ' + version)
    print(root)
    for name in sorted(files):
        rel = PurePosixPath(name).relative_to(root)
        if len(rel.parts) == 3 and rel.parts[0] == 'klipper' and rel.parts[1] in ('extras', 'kinematics') and rel.suffix == '.py':
            dest = str(rel.relative_to('klipper'))
            print(str(rel) + ' ' + dest)
            if rel.name in ('filament_feed_ace.py', 'filament_switch_sensor_ace.py', 'extruder_ace.py'):
                print(str(rel) + ' ' + dest.replace('_ace.py', '.py'))
PY
)
NAME=$(printf '%s\n' "$LAYOUT" | head -n 1)
MAPPINGS=$(printf '%s\n' "$LAYOUT" | tail -n +2)
echo "provider: $NAME"
printf '%s\n' "$MAPPINGS"
if [ "$MODE" = --check ]; then
    echo 'check only: archive structure and runtime mappings verified; nothing written'
    exit 0
fi

# Never install during a print (paused counts as active).
STATE=$(curl -fsS --max-time 5 "$API/printer/objects/query?print_stats=state" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["status"]["print_stats"]["state"])')
case "$STATE" in standby|complete|cancelled|error) ;; *) echo "printer is $STATE; install requires idle" >&2; exit 1;; esac
# PAXX bind-mounted modules are managed by its activation hook, not this script.
# Refuse to write through such a mount or a symlink into a different provider.
while read -r source dest; do
    target="$KLIPPY/$dest"
    [ ! -L "$target" ] || { echo "managed symlink: $target" >&2; exit 1; }
    if awk -v p="$target" '$2 == p {found=1} END {exit !found}' /proc/mounts; then
        echo "managed bind mount: $target; use the active PAXX provider instead" >&2; exit 1
    fi
done <<MAP
$MAPPINGS
MAP
# Keep room for persistent save_variables writes on the inode-limited U1 root.
python3 - "$KLIPPY" "$MAPPINGS" <<'PYI'
import os, sys
from pathlib import Path
root = Path(sys.argv[1])
missing = sum(not (root / row.split()[1]).exists() for row in sys.argv[2].splitlines())
free = os.statvfs(root).f_favail
if free < missing + 16:
    raise SystemExit('insufficient free inodes for modules and persistent state: %d' % free)
PYI
mkdir -p "$STAGING" "$BACKUPS"
STAGE=$(mktemp -d "$STAGING/stage.XXXXXX")
BACKUP=$(mktemp -d "$BACKUPS/multiace-install-backup.XXXXXX")
tar xzf "$PKG" -C "$STAGE"
SRC="$STAGE/$NAME"
# Back up all overwritten paths, including the original stock aliases; record new files.
: > "$BACKUP/new-files"
printf '%s\n' "$MAPPINGS" > "$BACKUP/mappings"
while read -r source dest; do
    if [ -f "$KLIPPY/$dest" ]; then
        mkdir -p "$BACKUP/$(dirname "$dest")"
        cp -p "$KLIPPY/$dest" "$BACKUP/$dest"
    else
        printf '%s\n' "$dest" >> "$BACKUP/new-files"
    fi
done < "$BACKUP/mappings"
if [ -f "$DATA/config/extended/ace.cfg" ]; then
    cp -p "$DATA/config/extended/ace.cfg" "$BACKUP/ace.cfg"
fi
if [ -d "$DATA/config/extended/multiace" ]; then
    cp -a "$DATA/config/extended/multiace" "$BACKUP/state"
fi
echo "backup: $BACKUP"
echo "staged provider (including matching web): $SRC"
restore_modules() {
    while read -r source dest; do
        if [ -f "$BACKUP/$dest" ]; then
            cp -p "$BACKUP/$dest" "$KLIPPY/$dest" || return 1
        else
            rm -f "$KLIPPY/$dest" || return 1
        fi
        rm -f "$KLIPPY/$(dirname "$dest")/__pycache__/$(basename "$dest" .py)".*.pyc
    done < "$BACKUP/mappings"
}
install_modules() {
    while read -r source dest; do
        cp -p "$SRC/$source" "$KLIPPY/$dest" || return 1
        cmp -s "$SRC/$source" "$KLIPPY/$dest" || return 1
        rm -f "$KLIPPY/$(dirname "$dest")/__pycache__/$(basename "$dest" .py)".*.pyc || return 1
    done < "$BACKUP/mappings"
}
verify_modules() {
    while read -r source dest; do
        cmp -s "$SRC/$source" "$KLIPPY/$dest" || return 1
    done < "$BACKUP/mappings"
}
wait_ready() {
    i=0
    while [ "$i" -lt 30 ]; do
        state=$(curl -fsS --max-time 3 "$API/printer/info" 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin)["result"]["state"])' 2>/dev/null || true)
        [ "$state" = ready ] && return 0
        i=$((i+1)); sleep 2
    done
    return 1
}
"$SERVICE" stop
if ! install_modules || ! "$SERVICE" start || ! wait_ready; then
    echo 'activation failed; restoring previous module files' >&2
    "$SERVICE" stop || true
    restore_modules
    "$SERVICE" start
    echo 'configuration/state backup retained; review logs before restoring state' >&2
    exit 1
fi
if ! verify_modules; then
    echo 'activation hook changed the loaded module paths; inspect the PAXX provider, do not mark deployment successful' >&2
    exit 1
fi
# The manual installer does not claim the PAXX package pin, web service, or the
# four ACE connections are verified just because Klipper reaches ready.
echo 'Klipper ready; installed module files match the archive.'
echo 'Web: staged only; point the existing service at this provider after checking its dependencies and config.'
echo "Verify active ACE status and web version before marking migration complete. Backup: $BACKUP"
