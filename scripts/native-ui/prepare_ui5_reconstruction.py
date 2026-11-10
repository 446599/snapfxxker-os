#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare the explicitly identified UI.5 reconstruction on the saved UI.4 source."""
import argparse
import json
from pathlib import Path
import subprocess

import prepare_source

REPO = Path(__file__).resolve().parents[2]
SERIES_PATH = REPO / "bundles/native-ui/reconstruction/series.json"


def load_series():
    series = json.loads(SERIES_PATH.read_text())
    baseline = series["baseline_manifest"]
    if prepare_source.digest(REPO / baseline["path"]) != baseline["sha256"]:
        raise ValueError("UI.4 baseline manifest has changed; review the reconstruction base")
    for patch in series["patches"]:
        if prepare_source.digest(REPO / patch["path"]) != patch["sha256"]:
            raise ValueError("Reconstruction patch checksum mismatch: " + patch["path"])
    return series


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path, help="Empty output directory for reconstructed source")
    mode.add_argument("--check", action="store_true", help="Verify the saved base and patch checksums")
    parser.add_argument("--no-submodules", action="store_true",
                        help="Prepare source only; omitted submodules make the tree non-compilable")
    args = parser.parse_args()
    series = load_series()
    if args.check:
        print("Verified UI.4 baseline and %d reconstruction patches" % len(series["patches"]))
        return
    output = args.output.resolve()
    prepare_source.prepare(output, args.no_submodules)
    for record in series["patches"]:
        patch = REPO / record["path"]
        subprocess.run(["git", "-C", str(output), "apply", "--check", str(patch)], check=True)
        subprocess.run(["git", "-C", str(output), "apply", str(patch)], check=True)
    (output.parent / "ui5-reconstruction-provenance.json").write_text(
        json.dumps(series, ensure_ascii=False, indent=2) + "\n")
    print("UI.5 reconstruction source prepared: " + str(output))
    print("This is reconstructed source; original-checkpoint identity and 90% similarity are not asserted.")


if __name__ == "__main__":
    main()
