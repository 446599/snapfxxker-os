#!/usr/bin/env python3
"""Apply the U1 same-reader RFID scheduling fix. All anchors checked before writes."""
import json
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
changes = json.loads(Path(__file__).with_name("changes.json").read_text())
pending = {}
for name, pairs in changes:
    path = root / name
    data = path.read_text()
    for old, new in pairs:
        if data.count(old) != 1:
            raise SystemExit(f"Source anchor mismatch: {name}: {old[:100]!r}")
        data = data.replace(old, new, 1)
    pending[path] = data
for path, data in pending.items():
    path.write_text(data)
    print(f"Patched {path.relative_to(root)}")
