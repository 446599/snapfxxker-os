#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 233 firmware contributors
set -euo pipefail

if [[ $# -gt 1 || (${1:-} != "" && ${1:-} != "--plan") ]]; then
    echo "Usage: $0 [--plan]" >&2
    exit 2
fi

fw233_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$fw233_root"
fw233_version="$(python3 -c 'import json; print(json.load(open("233-firmware.json"))["version"])')"
fw233_profile="$(python3 -c 'import json; print(json.load(open("233-firmware.json"))["build_profile"])')"
export GIT_VERSION="$fw233_version"
fw233_output="firmware/U1_233_${fw233_version}_upgrade.bin"
fw233_args=(make build "PROFILE=$fw233_profile" "OUTPUT_FILE=$fw233_output")

if [[ ${1:-} == "--plan" ]]; then
    printf '233固件 / %s\n' "$fw233_version"
    printf 'GIT_VERSION=%q ./dev.sh' "$GIT_VERSION"
    printf ' %q' "${fw233_args[@]}"
    printf '\n'
    exit 0
fi

exec ./dev.sh "${fw233_args[@]}"
