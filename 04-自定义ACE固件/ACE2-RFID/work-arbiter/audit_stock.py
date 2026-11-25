#!/usr/bin/env python3
"""Read-only, exact-image evidence checks. Never writes an image or reserves RAM."""
import argparse
import hashlib
import json
from pathlib import Path

BASE = 0x08008000
SHA256 = "a468e366226e7c483d3dd96b444223e89cbf0d1b10573d42b6263843ffd6be3e"
# Independently transcribed from the known stock bytes and Thumb disassembly.
# These assert narrow observations, NOT complete call coverage or hook safety.
SITES = (
    (0x0800E244, "2de9f04f", "E244 prologue; resume E248"),
    (0x0800E7A8, "2de9f047", "CMD68 prologue; resume E7AC"),
    (0x0800E910, "2de9f043", "CMD13 prologue; resume E914"),
    (0x0800E74C, "10b582b0", "CMD14 prologue; resume E750"),
    (0x0800E70C, "80b582b0", "E70C prologue; resume E710"),
    (0x0800E6FC, "fff704bd", "E244 tail-branches to E108; return is not before E108"),
    (0x08013300, "42f29460c2f20000", "Allocator loads heap base 20002694"),
    (0x08013328, "4ff4b042", "Allocator initializes heap span 5800"),
    (0x08013348, "c0e90072", "Allocator writes free-list header at aligned heap base"),
    (0x0801338C, "42f2946cc2f2000c", "Allocation range check loads 20002694"),
    (0x08013AFC, "42f29464", "Free range check loads heap base low half 2694"),
    (0x0800E736, "4ff0ff32", "E70C sets queue timeout ffffffff"),
    (0x0800E744, "07f074ff", "E70C calls queue send 08016630"),
    (0x0800E796, "4ff0ff32", "CMD14 sets queue timeout ffffffff"),
    (0x0800E79A, "07f049ff", "CMD14 calls same queue send 08016630"),
    (0x0800E26C, "08f04afb", "E244 consumes queue via 08016904"),
    (0x0800EC82, "fff7dffa", "EA14 loop invokes E244 for reader 0"),
    (0x0800EC88, "fff7dcfa", "EA14 loop invokes E244 for reader 1"),
    (0x0800EC56, "f9f744fe", "EA14 clears cached record BEFORE E244 gate"),
    (0x0800EC6A, "a5f80893", "EA14 stores cached sentinel BEFORE E244 gate"),
    (0x0800EA38, "0861", "EA14 startup writes reader GPIO outside E244"),
    (0x0800EA56, "4861", "EA14 startup clears reader GPIO outside E244"),
    (0x0800ABEC, "03f08efd", "Feed path calls E70C"),
    (0x0800ACFA, "03f007fd", "Another feed path calls E70C"),
)

def check_sites(data):
    for address, expected, description in SITES:
        start = address - BASE
        expected = bytes.fromhex(expected)
        if data[start:start + len(expected)] != expected:
            raise ValueError(f"Evidence mismatch at {address:#010x}: {description}")

def audit(data):
    if len(data) != 71592 or hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError("Unknown base image; refusing address-specific evidence")
    check_sites(data)
    return {
        "status": "OFFLINE_ONLY_DO_NOT_FLASH",
        "stock_sha256": SHA256,
        "stock_size": len(data),
        "evidence_sites_checked": len(SITES),
        "heap": {"base": "0x20002694", "aligned_header": "0x20002698",
                 "span": "0x5800", "safe_for_state": False},
        "production_integration": False,
        "hardware_tests_run": False,
        "blockers": [
            "No reserved/initialized RF state or unique boot/session epoch source",
            "CMD14/E70C full-queue wait while owning RF can block E244 consumer",
            "E70C drops work on contention in experimental adapter",
            "EA14 has cache writes and startup GPIO outside E244 gate",
            "Background reader*2 metadata is not verified active physical slot",
            "Legacy raw words carry no transaction/session end or generation",
            "Unproved SPI/ISR/drain/cleanup/antenna restore and watchdog bounds",
            "No authenticated preload generation / verified STOP integration",
            "No stack/heap headroom or hardware fault/recovery validation",
        ],
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.image.read_bytes()), indent=2))
