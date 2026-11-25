#!/bin/sh
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/dual-spool.XXXXXX")
trap 'rm -rf "$tmpdir"' EXIT HUP INT TERM
arm-none-eabi-as -mthumb -mcpu=cortex-m4 -mfpu=fpv4-sp-d16 "$here/reset_context_hook.s" -o "$tmpdir/reset.o"
arm-none-eabi-ld -Ttext 0x0801e000 --defsym resume_fdfc=0x0800fe1e "$tmpdir/reset.o" -o "$tmpdir/reset.elf" 2>"$tmpdir/link.log"
arm-none-eabi-objcopy -O binary "$tmpdir/reset.elf" "$tmpdir/reset.bin"
PYTHONPATH="${PYTHONPATH:-/tmp/ace95-unicorn}" python3 "$here/test_new_identity_reset.py"
if PYTHONPATH="${PYTHONPATH:-/tmp/ace95-unicorn}" python3 "$here/test_new_identity_reset.py" --expect-fresh >"$tmpdir/expected-failure.log" 2>&1; then
  echo 'FAIL: original 97 unexpectedly met fresh-context assertion' >&2; exit 1
fi
printf '%s\n' 'PASS: original 97 fails the fresh-context assertion (expected regression)'
DUAL_SPOOL_STUB="$tmpdir/reset.bin" PYTHONPATH="${PYTHONPATH:-/tmp/ace95-unicorn}" python3 "$here/test_candidate_thumb.py"
PYTHONPATH="${PYTHONPATH:-/tmp/ace95-unicorn}" python3 "$here/test_event_interleaving.py"
PYTHONPATH="${PYTHONPATH:-/tmp/ace95-unicorn}" python3 "$here/test_native_assignment.py"
PYTHONPATH="${PYTHONPATH:-/tmp/ace95-unicorn}" python3 "$here/test_two_jobs_thumb.py"
