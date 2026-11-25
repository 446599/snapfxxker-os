#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
test_bin=$(mktemp /tmp/u1-filament-model-tests.XXXXXX)
trap 'rm -f "$test_bin"' EXIT
"${CXX:-clang++}" -std=c++17 -O0 -Iinclude -Ilib/libhv/include -Ilib/spdlog/include \
    tests/standalone/u1_filament_model.cpp src/printer/u1_filament_model.cpp -o "$test_bin"
"$test_bin"
