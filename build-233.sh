#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd)"
version=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$root/233-firmware.json")
base="${FW233_BASE:-$root/firmware/U1_233_2.1.0-233-dev.1-rsyncfix.1_upgrade.bin}"
out="${FW233_OUTPUT:-$root/firmware/U1_233_${version}_upgrade.bin}"
image="${FW233_IMAGE:-codex-233-overlay-tools:20261109}"
[[ $# -le 1 && ( $# -eq 0 || ${1:-} == --plan ) ]] || { echo "Usage: build-233.sh [--plan]" >&2; exit 2; }
if [[ ${1:-} == --plan ]]; then
  printf '233 %s\nbase: %s\noutput: %s\ncontainer: %s\n' "$version" "$base" "$out" "$image"
  printf '%s\n' 'overlay 1: overlays/233-dev.2-perms.1' 'overlay 2: overlays/233-native-ui'
  printf '%s\n' 'Requires a rebuilt ARM64 native runtime pinned to the current source/build inputs.'
  exit 0
fi
[[ $# == 0 ]] || { echo 'Usage: build-233.sh [--plan]' >&2; exit 2; }
# Source-only commits deliberately contain no old runtime. A matching CI artifact
# must be rebuilt and pinned before this entry point can open the base image.
python3 "$root/scripts/native-ui/validate.py"
[[ -f "$base" ]] || { echo "Missing base: $base" >&2; exit 1; }
expected=6000de25703921113fb8665a82234bdc7ab25bd3030bc70dd1a307a18d90c02f
actual=$(shasum -a 256 "$base" | awk '{print $1}')
[[ "$actual" == "$expected" ]] || { echo 'Incorrect rsyncfix base SHA-256' >&2; exit 1; }
mkdir -p "$(dirname "$out")"
base="$(cd "$(dirname "$base")" && pwd)/$(basename "$base")"
out="$(cd "$(dirname "$out")" && pwd)/$(basename "$out")"
docker run --rm -e CI=1 -e "FIRMWARE_VERSION_OVERRIDE=$version" -v "$root:/src:ro" -v "$base:/input/base.bin:ro" -v "$(dirname "$out"):/output" -e "OUTFILE=$(basename "$out")" "$image" bash -lc '
set -euo pipefail
mkdir -p /tmp/work
cp -a /src/scripts /src/tools /tmp/work/
mkdir -p /tmp/work/overlays/233-dev.2-perms.1 /tmp/work/overlays/233-native-ui
tar -C /src/overlays/233-dev.2-perms.1 --exclude="__pycache__" --exclude="*.pyc" --exclude="*.pyo" -cf - . | tar -C /tmp/work/overlays/233-dev.2-perms.1 -xf -
tar -C /src/overlays/233-native-ui --exclude="__pycache__" --exclude="*.pyc" --exclude="*.pyo" -cf - . | tar -C /tmp/work/overlays/233-native-ui -xf -
cd /tmp/work
make -C tools
scripts/create_firmware.sh /input/base.bin /tmp/build233 "/output/$OUTFILE" /tmp/work/overlays/233-dev.2-perms.1 /tmp/work/overlays/233-native-ui
'
