#!/bin/sh
set -eu
SERVICE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$SERVICE_DIR/device_service.py"
