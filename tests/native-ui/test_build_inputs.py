# SPDX-License-Identifier: GPL-3.0-or-later
"""Reject stale native binaries and inventories after source or recipe changes."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("build_inputs", REPO / "scripts/native-ui/build_inputs.py")
build_inputs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_inputs)


class RuntimeProvenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.source = self.root / "source"
        self.source.mkdir()
        self.native = self.source / "native.cpp"
        self.native.write_text("new UI source\n")
        self.binaries = self.root / "bin"
        self.binaries.mkdir()
        self.binary = self.binaries / "helix-screen"
        self.binary.write_bytes(b"test build output")
        for name in build_inputs.RECIPE_PATHS:
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("fixed recipe\n")
        (self.repo / "bundles/native-ui/reconstruction/series.json").write_text(json.dumps({"patches": []}))
        self.patch = self.repo / "bundles/native-ui/change.patch"
        self.patch.write_text("diff --git a/native.cpp b/native.cpp\n--- a/native.cpp\n+++ b/native.cpp\n")
        notices = self.repo / "bundles/native-ui/notices.json"
        notices.write_text("{}\n")
        service = self.repo / "service/device_service.py"
        service.parent.mkdir()
        service.write_text("# companion service\n")
        self.manifest_path = self.repo / "bundles/native-ui/manifest.json"
        self.manifest = {"source": {"commit": "fixed-upstream", "font_sources": {},
            "patches": [{"path": str(self.patch.relative_to(self.repo)),
                         "sha256": hashlib.sha256(self.patch.read_bytes()).hexdigest()}],
            "third_party_manifest": {"path": str(notices.relative_to(self.repo)),
                                     "sha256": hashlib.sha256(notices.read_bytes()).hexdigest()}},
            "device_service": {"path": "service", "sha256": hashlib.sha256(service.read_bytes()).hexdigest()}}
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.marker = self.root / "build-start.json"
        self.record = build_inputs.record_start(self.source, self.marker, self.repo)
        self.fresh_timestamp = self.record["started_at_ns"] + 1_000_000_000
        os.utime(self.binary, ns=(self.fresh_timestamp, self.fresh_timestamp))

    def verify(self):
        return build_inputs.verify_build_start(self.source, self.binaries, self.marker, self.repo)

    def test_fresh_build_matches_unchanged_inputs(self):
        build = self.verify()
        self.assertEqual(build_inputs.verify_runtime_inputs({"build": build}, self.repo), build)

    def test_legacy_runtime_without_build_inventory_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing current source/build provenance"):
            build_inputs.verify_runtime_inputs({"sha256": "old-runtime"}, self.repo)

    def test_binary_from_before_build_start_is_rejected(self):
        old = self.record["started_at_ns"] - 1_000_000_000
        os.utime(self.binary, ns=(old, old))
        with self.assertRaisesRegex(ValueError, "old binary"):
            self.verify()

    def test_source_edited_during_build_is_rejected(self):
        self.native.write_text("source edited while compiling\n")
        with self.assertRaisesRegex(ValueError, "source changed"):
            self.verify()

    def test_new_patch_pin_invalidates_previously_built_runtime(self):
        runtime = {"build": self.verify()}
        self.patch.write_text(self.patch.read_text() + "+another native change\n")
        self.manifest["source"]["patches"][0]["sha256"] = hashlib.sha256(self.patch.read_bytes()).hexdigest()
        self.manifest_path.write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(ValueError, "missing current source/build provenance"):
            build_inputs.verify_runtime_inputs(runtime, self.repo)

    def test_reconstruction_patch_is_included_and_tampering_is_rejected(self):
        patch = self.repo / "bundles/native-ui/reconstruction/new.patch"
        patch.write_text("diff --git a/native.cpp b/native.cpp\n--- a/native.cpp\n+++ b/native.cpp\n")
        series = self.repo / "bundles/native-ui/reconstruction/series.json"
        series.write_text(json.dumps({"patches": [{"path": str(patch.relative_to(self.repo)),
            "sha256": hashlib.sha256(patch.read_bytes()).hexdigest()}]}))
        record = build_inputs.record_start(self.source, self.marker, self.repo)
        self.assertIn(str(patch.relative_to(self.repo)), record["inputs"]["recipe_files"])
        patch.write_text(patch.read_text() + "+tampered\n")
        with self.assertRaisesRegex(ValueError, "Reconstruction patch checksum mismatch"):
            build_inputs.current_inputs(self.repo)

    def test_changed_build_recipe_invalidates_runtime(self):
        runtime = {"build": self.verify()}
        (self.repo / "scripts/native-ui/build.sh").write_text("changed compiler flags\n")
        with self.assertRaisesRegex(ValueError, "missing current source/build provenance"):
            build_inputs.verify_runtime_inputs(runtime, self.repo)

    def test_binary_font_patch_is_included_in_source_snapshot(self):
        font = self.source / "font face.bin"
        font.write_bytes(b"new font")
        self.patch.write_text(self.patch.read_text() +
            'diff --git "a/font face.bin" "b/font face.bin"\nGIT binary patch\nliteral 8\n')
        self.manifest["source"]["patches"][0]["sha256"] = hashlib.sha256(self.patch.read_bytes()).hexdigest()
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.record = build_inputs.record_start(self.source, self.marker, self.repo)
        font.write_bytes(b"font edited during compilation")
        with self.assertRaisesRegex(ValueError, "source changed"):
            self.verify()

    def test_deleted_file_does_not_need_to_exist_in_snapshot(self):
        self.patch.write_text(self.patch.read_text() +
            "diff --git a/deleted.bin b/deleted.bin\ndeleted file mode 100644\nGIT binary patch\n")
        self.manifest["source"]["patches"][0]["sha256"] = hashlib.sha256(self.patch.read_bytes()).hexdigest()
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.assertTrue(build_inputs.source_snapshot(self.source, self.repo))


if __name__ == "__main__":
    unittest.main()
