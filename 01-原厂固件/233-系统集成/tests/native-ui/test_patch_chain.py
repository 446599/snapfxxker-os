# SPDX-License-Identifier: GPL-3.0-or-later
"""Reject binary deltas generated from an unrecorded intermediate build."""
import json
from pathlib import Path
import re
import shlex
import unittest

REPO = Path(__file__).resolve().parents[2]


class NativeUiPatchChainTests(unittest.TestCase):
    def test_binary_preimages_follow_the_recorded_patch_order(self):
        manifest = json.loads((REPO / "bundles/native-ui/manifest.json").read_text())
        previous_blobs = {}
        compared = 0
        for patch in manifest["source"]["patches"]:
            text = (REPO / patch["path"]).read_text()
            for section in re.split(r"(?=^diff --git )", text, flags=re.MULTILINE):
                if "\nGIT binary patch\n" not in section:
                    continue
                header = shlex.split(section.splitlines()[0])
                source, target = header[2][2:], header[3][2:]
                index = re.search(r"^index ([0-9a-f]{40})\.\.([0-9a-f]{40})(?: |$)",
                                  section, re.MULTILINE)
                self.assertIsNotNone(index, "Binary patch needs full blob IDs: " + target)
                before, after = index.groups()
                if source in previous_blobs:
                    compared += 1
                    with self.subTest(patch=patch["path"], path=target):
                        self.assertEqual(before, previous_blobs[source],
                                         "Binary preimage does not match preceding patch output")
                previous_blobs[target] = after
        self.assertGreater(compared, 0, "No successive binary deltas were checked")


if __name__ == "__main__":
    unittest.main()
