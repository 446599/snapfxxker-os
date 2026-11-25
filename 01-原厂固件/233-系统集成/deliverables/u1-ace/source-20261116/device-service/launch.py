#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read optional persistent credentials as data, then run the loopback service."""
import json
import os
from pathlib import Path
import runpy

credentials = Path("/oem/printer_data/config/extended/u1-device-service.json")
if credentials.is_file():
    values = json.loads(credentials.read_text())
    if not isinstance(values, dict):
        raise ValueError("u1-device-service.json must contain a JSON object")
    for key, variable in {"api_key": "U1_PAXX_API_KEY", "token": "U1_PAXX_TOKEN",
                          "api_url": "U1_PAXX_API_URL"}.items():
        if key in values:
            if not isinstance(values[key], str):
                raise ValueError("Invalid type for a device-service credential field")
            os.environ[variable] = values[key]

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
runpy.run_path(str(Path(__file__).with_name("device_service.py")), run_name="__main__")
