#!/bin/sh
# Install the multiACE 1.90.2 managed package onto a 233 / U1 printer.
#
# Why a script: the archive ships klipper/extras/filament_feed_ace.py, but
# klipper loads the module named by [filament_feed ...] -> filament_feed.py.
# The 1.27b runtime had only the renamed file; installing the archive as-is
# leaves the stock 102 KB filament_feed.py in place, and the feed state
# machine then fails at load_prepare with 'no_filament' and a short retract
# on unload.
#
# Usage: install-to-233.sh <package.tar.gz> [--check]
set -eu

PKG=${1:?usage: install-to-233.sh <package.tar.gz> [--check]}
CHECK=${2:-}
EXTRAS=/home/lava/klipper/klippy/extras
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP=/userdata/multiace-install-backup-$STAMP

tar xzf "$PKG" -C /userdata/multiace-provider-runtime/
NAME=$(tar tzf "$PKG" | head -1 | cut -d/ -f1)
SRC=/userdata/multiace-provider-runtime/$NAME/klipper/extras

[ -f "$SRC/ace.py" ] || { echo "no ace.py in $SRC" >&2; exit 1; }

echo "package : $NAME"
echo "source  : $SRC ($(ls "$SRC"/*.py | wc -l | tr -d ' ') modules)"
if [ "$CHECK" = "--check" ]; then
    echo "check only, nothing written"
    exit 0
fi

mkdir -p "$BACKUP"
cp -a "$EXTRAS"/ace*.py "$EXTRAS"/filament_feed*.py "$BACKUP"/ 2>/dev/null || true
echo "backup  : $BACKUP"

cp -a "$SRC"/*.py "$EXTRAS"/
# The module klipper actually loads.
cp -a "$SRC/filament_feed_ace.py" "$EXTRAS/filament_feed.py"
rm -f "$EXTRAS"/__pycache__/ace*.pyc "$EXTRAS"/__pycache__/filament_feed*.pyc

echo "installed; restarting klipper"
/etc/init.d/S60klipper restart >/dev/null 2>&1
i=0
while [ $i -lt 40 ]; do
    state=$(curl -s --max-time 3 http://127.0.0.1:7125/printer/info 2>/dev/null \
            | python3 -c 'import json,sys;print(json.load(sys.stdin)["result"]["state"])' 2>/dev/null || true)
    [ "$state" = ready ] && break
    i=$((i + 1)); sleep 2
done
echo "klipper : ${state:-unknown}"
curl -s --max-time 6 'http://127.0.0.1:7125/printer/objects/query?ace' 2>/dev/null \
    | python3 -c 'import json,sys
d=json.load(sys.stdin)["result"]["status"]["ace"]
print("ACE     : %d/%d connected" % (sum(1 for a in d["aces"] if a.get("connected")), len(d["aces"])))' \
    2>/dev/null || true
echo "rollback: cp -a $BACKUP/*.py $EXTRAS/ && /etc/init.d/S60klipper restart"
