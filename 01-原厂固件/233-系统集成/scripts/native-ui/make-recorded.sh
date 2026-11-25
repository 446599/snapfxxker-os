#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Apply the recorded libnl provider only to the top-level project Makefile.
set -eu
extra=1
for argument in "$@"; do
    case "$argument" in -C|-C*|-f|-f*|--directory*|--file*) extra=0 ;; esac
done
if [ "$PWD" = "$U1_UI_SOURCE" ] && [ "$extra" = 1 ]; then
    exec make -f Makefile -f "$U1_RECORDED_DEPS" "$@"
fi
exec make "$@"
