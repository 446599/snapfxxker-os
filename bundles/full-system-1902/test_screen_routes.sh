#!/usr/bin/env bash
# Host checks for the patched, pinned 233-screen source tree.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
cd "${1:?expected path to the patched 233-screen tree}"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# libhv generates its host configuration in-tree. Use its own public header
# list in a disposable copy, so the ARM build keeps a clean source tree.
cp -a lib/libhv "$work/libhv"
(
    cd "$work/libhv"
    ./configure --without-openssl > "$work/hv-configure.log"
    make -s --no-print-directory \
        --eval='u1-headers: ; @mkdir -p "$(U1_HEADERS)/hv" && cp $(LIBHV_HEADERS) "$(U1_HEADERS)/hv/"' \
        U1_HEADERS="$work/include" u1-headers
)

flags=(-std=c++17 -DLV_CONF_INCLUDE_SIMPLE -DHELIX_HAS_SNAPMAKER=1 -DHELIX_ENABLE_MOCKS
       -Wno-deprecated-declarations -include include/lvgl_pch.h
       -I. -Iinclude -Itests -Isrc/generated -isystem lib -isystem lib/glm
       -isystem lib/lvgl -isystem lib/lvgl/src -isystem lib/spdlog/include
       -isystem "$work/include")
cxx=${CXX:-c++}
"$cxx" "${flags[@]}" -fsyntax-only src/printer/ams_backend_multiace.cpp \
    src/ui/filament/u1_filament_overview.cpp src/ui/print/ui_print_tune_compact.cpp \
    tests/unit/test_ams_backend_multiace.cpp \
    tests/unit/test_multiace_status.cpp
"$cxx" "${flags[@]}" -ffunction-sections -fdata-sections -Wl,--gc-sections \
    src/printer/multiace_status.cpp "$here/test_screen_routes.cpp" -o "$work/screen-routes"
"$work/screen-routes"
