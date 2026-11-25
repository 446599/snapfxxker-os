#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p build
ARM="${ARM_TOOLCHAIN:-../toolchain/usr/bin}/arm-none-eabi"
CFLAGS=(-std=c11 -Wall -Wextra -Werror -O2)
OFFLINE=(-DRF_ADAPTER_OFFLINE_TEST=1)
gcc "${CFLAGS[@]}" rf_owner.c test_rf_owner.c -o build/test_rf_owner
build/test_rf_owner
gcc "${CFLAGS[@]}" "${OFFLINE[@]}" rf_owner.c rf_stock_adapter.c test_rf_stock_adapter.c -o build/test_rf_stock_adapter
build/test_rf_stock_adapter
for test in rf_owner rf_stock_adapter; do
    sources=(rf_owner.c)
    if [[ "$test" == rf_stock_adapter ]]; then sources+=(rf_stock_adapter.c); fi
    gcc "${CFLAGS[@]}" "${OFFLINE[@]}" -g -O1 -fno-omit-frame-pointer -fno-pie -no-pie \
        -fsanitize=address,undefined "${sources[@]}" "test_${test}.c" -o "build/test_${test}_san"
    ASAN_OPTIONS=detect_leaks=0 "build/test_${test}_san"
done
# Expected denial: adapter may not compile as a normal firmware source.
if gcc "${CFLAGS[@]}" -c rf_stock_adapter.c -o build/forbidden_adapter.o >build/compile-gate.log 2>&1; then
    echo 'ERROR: adapter compile gate failed' >&2; exit 1
fi
grep -q 'Unintegrated RF adapter' build/compile-gate.log
for source in rf_owner.c rf_stock_adapter.c rf_stock_trampolines.S; do
    "$ARM-gcc" -mcpu=cortex-m3 -mthumb -Os -ffreestanding -fno-builtin -nostdlib \
        -Wall -Wextra -Werror -fstack-usage "${OFFLINE[@]}" -c "$source" -o "build/${source%.*}.o"
done
"$ARM-ld" -r build/rf_owner.o build/rf_stock_adapter.o build/rf_stock_trampolines.o -o build/rf_research.o
"$ARM-nm" -u build/rf_research.o | tee build/unresolved-symbols.txt
"$ARM-size" build/rf_research.o | tee build/arm-size.txt
"$ARM-objdump" -d build/rf_research.o >build/rf_research.dis
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_audit_stock.py
PYTHONDONTWRITEBYTECODE=1 python3 audit_stock.py ../input/ACE2_V1.1.31_20260306.bin >build/stock-audit.json
for builder in build_patch apply_patch; do
    if python3 "source/firmware/${builder}.py" --base ../input/ACE2_V1.1.31_20260306.bin \
            --out build/FORBIDDEN.bin >"build/${builder}-gate.log" 2>&1; then
        echo 'ERROR: image creation gate failed' >&2; exit 1
    fi
    grep -q 'REFUSED: work-arbiter' "build/${builder}-gate.log"
done
test ! -e build/FORBIDDEN.bin
echo 'PASS: host + sanitizer + ARM object compilation + image evidence/gates. NOT linked firmware; DO NOT FLASH.'
