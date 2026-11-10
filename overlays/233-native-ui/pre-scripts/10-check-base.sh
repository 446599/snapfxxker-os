#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
set -eu
rootfs=$1
for required in etc/init.d/S49extended-config etc/init.d/S90lmd \
    usr/local/bin/extended-config.py usr/local/bin/extended-pkg \
    etc/nginx/fluidd.d/firmware-config.conf usr/local/bin/firmware-config.py; do
    [ -f "$rootfs/$required" ] || { echo "233 UI requires $required in the base" >&2; exit 1; }
done
grep -Fq '/etc/hooks/lmd.d/*.sh' "$rootfs/etc/init.d/S90lmd" || {
    echo '233 UI requires the existing PAXX lmd hooks' >&2; exit 1;
}
[ "$(cat "$rootfs/etc/FULLVERSION")" = '2.1.0-233-dev.2-perms.1' ] || {
    echo 'Apply the verified 233-dev.2-perms.1 overlay before the native UI overlay' >&2; exit 1;
}
