#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
repo="$(cd "$(dirname "$0")/../.." && pwd)"
[[ $# == 1 ]] || { echo 'Usage: scripts/native-ui/build.sh EMPTY_OUTPUT_DIRECTORY' >&2; exit 2; }
mkdir -p "$1"
work="$(cd "$1" && pwd)"
[[ -z "$(ls -A "$work")" ]] || { echo 'Use an empty output directory' >&2; exit 2; }
case "$work" in *' '*) echo 'The upstream cross Makefiles require a path without spaces' >&2; exit 2 ;; esac
python3 "$repo/scripts/native-ui/prepare_source.py" --output "$work/source/helixscreen"
python3 "$repo/scripts/native-ui/prepare_toolchain.py" --output "$work/cross-root" --cache "${U1_DEB_CACHE:-$repo/tmp/native-ui-debs}"
mkdir -p "$work/font-tools"
cp "$repo/bundles/native-ui/font-tools/"*.json "$work/font-tools/"
npm ci --prefix "$work/font-tools" --ignore-scripts --no-audit --no-fund
export U1_UI_SOURCE="$work/source/helixscreen"
export U1_CROSS_SYSROOT="$work/cross-root"
export U1_RECORDED_DEPS="$repo/bundles/native-ui/cross-deps.mk"
export PATH="$work/toolchain/bin:$PATH"
export LD_LIBRARY_PATH="$work/cross-root/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PKG_CONFIG_LIBDIR="$work/cross-root/usr/lib/aarch64-linux-gnu/pkgconfig"
export PKG_CONFIG_SYSROOT_DIR="$work/cross-root"
cd "$U1_UI_SOURCE"
./scripts/helix-claim take build:helixscreen 233-native-ui --note 'Recorded U1 ARM64 build'
trap './scripts/helix-claim release build:helixscreen' EXIT
make apply-patches HELIX_PATCHES_FROM_CLEAN=1 SKIP_OPTIONAL_DEPS=1
make lint-xml check-xml-schema SKIP_OPTIONAL_DEPS=1
LV_FONT_CONV="$work/font-tools/node_modules/.bin/lv_font_conv" make regen-runtime-cjk-fonts
python3 "$repo/scripts/native-ui/build_inputs.py" --source "$U1_UI_SOURCE" \
    --write-build-start "$work/build-start.json"
"$repo/scripts/native-ui/make-recorded.sh" MAKE="$repo/scripts/native-ui/make-recorded.sh" \
    PLATFORM_TARGET=snapmaker-u1 SKIP_OPTIONAL_DEPS=1 DBGLEVEL=0 FAST_LINK=0 \
    ENABLE_REMOTE_CONTROL=yes ENABLE_DIAGNOSTIC_UPLOADS=no ENABLE_MOCKS=yes
python3 "$repo/scripts/native-ui/package_runtime.py" --source "$U1_UI_SOURCE" \
    --binaries "$U1_UI_SOURCE/build/snapmaker-u1/bin" \
    --build-start "$work/build-start.json" \
    --ca-bundle "${U1_CA_BUNDLE:-/etc/ssl/certs/ca-certificates.crt}" \
    --output "$work/helixscreen-233-native-ui.tar.gz"
