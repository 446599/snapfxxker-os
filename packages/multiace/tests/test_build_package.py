# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise package contents, pinned dependencies and reproducible output."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock


PACKAGE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("multiace_package_build", PACKAGE / "build.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class BuildPackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = self.root / "runtime"
        self.snapshot = self.root / "snapshot"
        for name, data in {
            "VERSION": b"1.27b-test\n", "LICENSE": b"GPL fixture\n",
            "licenses/Filament-approximation.txt": b"MIT fixture\n",
            "klipper/extras/ace.py": b"NEW_ACE = True\n",
            "web/backend/main.py": b"NEW_BACKEND = True\n",
            "web/frontend/index.html": b"<html>new UI</html>\n",
            "web/frontend/vendor/vue.global.prod.js": b"frontend dependency\n",
        }.items():
            self.write(self.runtime / name, data)
        dependencies = {
            "web/backend/vendor/.paxx-dependencies-ready": b"dependencies ready\n",
            "web/backend/vendor/example/__init__.py": b"DEPENDENCY = True\n",
            "web/backend/vendor/example/example.aarch64.so": b"binary fixture\n",
            "web/backend/vendor/example/LICENSE": b"dependency license\n",
        }
        records = {}
        for name, data in dependencies.items():
            path = self.snapshot / "runtime" / name
            self.write(path, data)
            records[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        (self.snapshot / "runtime/web/backend/vendor/.paxx-dependencies-ready").chmod(0o755)
        # These old application files must not be pulled in with dependencies.
        self.write(self.snapshot / "runtime/klipper/extras/ace.py", b"OLD_ACE = True\n")
        self.write(self.snapshot / "runtime/web/backend/main.py", b"OLD_BACKEND = True\n")
        manifest = json.dumps({"files": records}, sort_keys=True).encode()
        self.write(self.snapshot / "manifest.json", manifest)
        for patch in (
            mock.patch.object(builder, "REPO", self.root),
            mock.patch.object(builder, "RUNTIME", self.runtime),
            mock.patch.object(builder, "SNAPSHOT", self.snapshot),
            mock.patch.object(builder, "DEPENDENCY_MANIFEST_SHA256", hashlib.sha256(manifest).hexdigest()),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    @staticmethod
    def write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read_archive(self, path):
        with tarfile.open(path) as archive:
            members = archive.getmembers()
            self.assertTrue(all(member.name.startswith("multiace-1.27b-test/") for member in members))
            self.assertTrue(all(member.isfile() and member.uid == member.gid == member.mtime == 0
                                for member in members))
            return {member.name.split("/", 1)[1]: archive.extractfile(member).read()
                    for member in members}

    def test_code_only_is_reproducible_and_explicit_about_missing_dependencies(self):
        self.write(self.runtime / "klipper/extras/__pycache__/ace.pyc", b"cache")
        first, second = self.root / "one.tar.gz", self.root / "two.tar.gz"
        metadata, digest = builder.build(first, code_only=True)
        os.utime(self.runtime / "klipper/extras/ace.py", (1234567890, 1234567890))
        builder.build(second, code_only=True)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(digest, hashlib.sha256(first.read_bytes()).hexdigest())
        self.assertEqual(first.with_name(first.name + ".sha256").read_text(), digest + "  one.tar.gz\n")
        files = self.read_archive(first)
        self.assertEqual(json.loads(files["PACKAGE.json"]), metadata)
        self.assertEqual(metadata["package_kind"], "code-only-update")
        self.assertFalse(metadata["includes_dependencies"])
        self.assertTrue(metadata["requires_existing_dependencies"])
        self.assertFalse(metadata["hardware_tested"])
        self.assertFalse(any(name.startswith("web/backend/vendor/") or "__pycache__" in name for name in files))
        self.assertIn("web/frontend/vendor/vue.global.prod.js", files)
        self.assertIn("LICENSE", files)
        self.assertIn("licenses/Filament-approximation.txt", files)
        for name, record in metadata["files"].items():
            self.assertEqual(record["sha256"], hashlib.sha256(files[name]).hexdigest())

    def test_full_package_only_imports_dependencies_from_snapshot(self):
        output = self.root / "full.tar.gz"
        metadata, _ = builder.build(output)
        files = self.read_archive(output)
        self.assertEqual(files["klipper/extras/ace.py"], b"NEW_ACE = True\n")
        self.assertEqual(files["web/backend/main.py"], b"NEW_BACKEND = True\n")
        self.assertIn("web/backend/vendor/example/example.aarch64.so", files)
        self.assertIn("web/backend/vendor/example/LICENSE", files)
        self.assertEqual(metadata["package_kind"], "complete-managed-application")
        self.assertTrue(metadata["includes_dependencies"])
        self.assertFalse(metadata["requires_existing_dependencies"])
        self.assertFalse(metadata["hardware_tested"])
        self.assertEqual(metadata["files"]["web/backend/vendor/.paxx-dependencies-ready"]["mode"], "0o755")

    def test_missing_binary_cannot_produce_a_complete_package(self):
        (self.snapshot / "runtime/web/backend/vendor/example/example.aarch64.so").unlink()
        output = self.root / "incomplete.tar.gz"
        with self.assertRaisesRegex(FileNotFoundError, "Missing 1 pinned dependency files.*--code-only"):
            builder.build(output)
        self.assertFalse(output.exists())
        builder.build(output, code_only=True)

    def test_changed_dependency_is_rejected_before_creating_archive(self):
        self.write(self.snapshot / "runtime/web/backend/vendor/example/__init__.py", b"changed dependency")
        output = self.root / "changed.tar.gz"
        with self.assertRaisesRegex(ValueError, "dependency file SHA256/size mismatch"):
            builder.build(output)
        self.assertFalse(output.exists())

    def test_changed_dependency_manifest_is_rejected(self):
        self.write(self.snapshot / "manifest.json", b'{"files": {}}')
        with self.assertRaisesRegex(ValueError, "dependency manifest SHA256 mismatch"):
            builder.build(self.root / "changed-manifest.tar.gz")

    def test_output_cannot_be_reincluded_as_runtime_input(self):
        with self.assertRaisesRegex(ValueError, "outside runtime"):
            builder.build(self.runtime / "old-output.tar.gz", code_only=True)

    def test_shared_firmware_patch_is_archived_as_a_regular_file(self):
        patch = self.root / "firmware/ace/patches/ace2_open_60_patch.json"
        self.write(patch, b'{"source": "single shared firmware patch"}\n')
        link = self.runtime / "web/backend/ace2_open_60_patch.json"
        link.symlink_to(os.path.relpath(patch, link.parent))
        output = self.root / "linked-source.tar.gz"
        builder.build(output, code_only=True)
        files = self.read_archive(output)
        self.assertEqual(files["web/backend/ace2_open_60_patch.json"], patch.read_bytes())


if __name__ == "__main__":
    unittest.main()
