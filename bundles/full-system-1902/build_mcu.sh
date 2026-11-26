#!/usr/bin/env bash
# Build exact upstream v0.16.0 + the two checked-in 233b patches + RFID fix.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
OUT=$(realpath -m "${1:?usage: build_mcu.sh output-directory}")
mkdir -p "$OUT"
WORK=$(mktemp -d "${RUNNER_TEMP:-/tmp}/ace2k-233c.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
SOURCE_PIN=4edbd50feb36e47d072b5cd4e516b9792b8b4ea9
UPSTREAM_PIN=27c564d210170f01aa1a57cd928a60c520247aff
KLIPPER_PIN=2d7717e3b62ea2fe3401b27f54f8681f80451c69
git -C "$REPO" fetch --quiet --depth=1 origin "$SOURCE_PIN"
mkdir "$WORK/233b"
git -C "$REPO" archive "$SOURCE_PIN:04-自定义ACE固件/ACE2-233-Parallel" | tar -x -C "$WORK/233b"
(cd "$WORK/233b" && printf '%s  %s\n' '6f4a0e38c481fb0fc057031bb05bdb07900d4f9f64a2007d12c80c7ff59d6d18' ace2k-0.16.0-233b.bin | sha256sum -c -)
git clone --quiet --depth=1 --branch v0.16.0 https://github.com/tobecwb/ace2k.git "$WORK/ace2k"
cd "$WORK/ace2k"
test "$(git rev-parse HEAD)" = "$UPSTREAM_PIN"
git submodule update --init --depth=1 firmware/klipper
test "$(git -C firmware/klipper rev-parse HEAD)" = "$KLIPPER_PIN"
for p in parallel-lanes.patch dryer-220v-rise-margin.patch; do
    git apply --check "$WORK/233b/$p"
    git apply "$WORK/233b/$p"
done
python3 "$REPO/bundles/ace2k-rfid-neighbor/apply.py" "$PWD"
git diff --check
git diff --stat
make -C firmware -j2 test-c | tee "$WORK/c-tests.log"
# Run the release's MCU build and the mandatory hardware pin/command flags.
make -C firmware flag-check ACE2K_VERSION=0.16.0-233c
cp firmware/build/ace2k-0.16.0-233c.{bin,dict} "$OUT/"
cp "$WORK/233b"/ace2k-0.16.0-233b.{bin,dict} "$OUT/"
{
    printf 'Version: 0.16.0-233c (hardware test pending)\nUpstream: %s\nKlipper: %s\n233b source pin: %s\n' "$UPSTREAM_PIN" "$KLIPPER_PIN" "$SOURCE_PIN"
    printf 'Integration commit: %s\n' "$(git -C "$REPO" rev-parse HEAD)"
    arm-none-eabi-gcc --version | head -1
    sha256sum "$WORK/233b"/parallel-lanes.patch "$WORK/233b"/dryer-220v-rise-margin.patch "$REPO/bundles/ace2k-rfid-neighbor/changes.json"
    grep 'tests,.*assertions,.*failures' "$WORK/c-tests.log" || true
} > "$OUT/BUILD_INFO.txt"
python3 - "$REPO/bundles/full-system-1902" "$OUT" <<'PY'
import sys, json
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from mcu_bundle import inspect_bundle
print(json.dumps(inspect_bundle(Path(sys.argv[2])), indent=2))
PY
(cd "$OUT" && sha256sum ace2k-*.bin ace2k-*.dict BUILD_INFO.txt)
