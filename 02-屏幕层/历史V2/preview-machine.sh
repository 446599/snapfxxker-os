#!/bin/sh
set -eu

NATIVE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
UI_ROOT="$NATIVE_DIR/helixscreen"
ACE_COUNT=${1:-4}
case "$ACE_COUNT" in
    1|2|3|4) ;;
    *) printf '%s\n' 'ACE 数量需为 1 至 4' >&2; exit 2 ;;
esac

PREVIEW_DIR="$NATIVE_DIR/.preview"
mkdir -p "$PREVIEW_DIR/config" "$PREVIEW_DIR/cache"
if [ ! -f "$PREVIEW_DIR/config/settings-test.json" ]; then
    python3 - "$UI_ROOT/config/settings.json.template" "$PREVIEW_DIR/config/settings-test.json" <<'PY'
import json
import sys
from pathlib import Path

settings = json.loads(Path(sys.argv[1]).read_text())
settings['language'] = 'zh'
settings['dark_mode'] = False
settings['display']['theme'] = 'u1-warm'
settings['display']['animations_enabled'] = False
Path(sys.argv[2]).write_text(json.dumps(settings, ensure_ascii=False, indent=2))
PY
fi

cd "$UI_ROOT"
export HELIX_CONFIG_DIR="$PREVIEW_DIR/config"
export HELIX_CACHE_DIR="$PREVIEW_DIR/cache"
export HELIX_MOCK_PRINTER=snapmaker_u1
export HELIX_MOCK_AMS=multiace
export HELIX_MOCK_MULTIACE_MODE=multi
export HELIX_MOCK_MULTIACE_UNITS="$ACE_COUNT"
export SDL_RENDER_DRIVER=software
exec ./build/bin/helix-screen --test --skip-splash -vv -s 480x320 \
    --remote-socket /tmp/helix-u1-native-preview.sock
