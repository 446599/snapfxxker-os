# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise source validation across the multiACE submodule conversion."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts/native-ui"))
spec = importlib.util.spec_from_file_location(
    "native_validate_provider_layout", REPO / "scripts/native-ui/validate.py")
validate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validate)


class ProviderLayoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.git("init", "-q")
        self.manifest = {"firmware_version": "test"}
        self.provider = {
            "version": "test", "path": "managed.tar.gz",
            "sha256": hashlib.sha256(b"pinned payload").hexdigest(),
        }
        (self.root / "managed.tar.gz").write_bytes(b"pinned payload")
        (self.root / "233-firmware.json").write_text(json.dumps({
            "version": "test",
            "base_firmware": {"sha256":
                "6000de25703921113fb8665a82234bdc7ab25bd3030bc70dd1a307a18d90c02f"},
            "managed_provider_pin": self.provider,
        }))
        (self.root / "build-233.sh").write_text(
            "#!/bin/sh\nprintf '%s\\n' overlays/233-dev.2-perms.1 overlays/233-native-ui\n")
        (self.root / "scripts").mkdir()
        (self.root / "scripts/create_firmware.sh").write_text("#!/bin/sh\n")

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True, capture_output=True, text=True)

    def commit_layout(self, *, vendored=True, screen_sha=None):
        if vendored:
            provider = self.root / "providers/multiACE"
            provider.mkdir(parents=True)
            (provider / "README.md").write_text("vendored provider\n")
        self.git("add", ".")
        if not vendored:
            self.git("update-index", "--add", "--cacheinfo",
                     "160000,a1a7b91b1fd297c1486a13ea22c61e01499a196d,providers/multiACE")
        self.git("update-index", "--add", "--cacheinfo",
                 "160000," + (screen_sha or
                     "4a81b50d9b6ef7a2c253b45dd00fe6f732c9d203") + ",deps/screen-apps")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                 "commit", "-qm", "fixture")

    def validate_source(self):
        with mock.patch.object(validate, "REPO", self.root), \
             mock.patch.object(validate, "PROVIDER", self.provider), \
             mock.patch.object(validate, "current_inputs"):
            validate.validate_source(self.manifest, partial_tree=False)

    def test_vendored_provider_passes_without_changing_managed_package(self):
        self.commit_layout()
        self.validate_source()

    def test_provider_cannot_silently_return_to_old_submodule(self):
        self.commit_layout(vendored=False)
        with self.assertRaisesRegex(ValueError, "Vendored multiACE"):
            self.validate_source()

    def test_screen_apps_still_requires_its_pinned_commit(self):
        self.commit_layout(screen_sha="1" * 40)
        with self.assertRaisesRegex(ValueError, "submodule pin changed: deps/screen-apps"):
            self.validate_source()


if __name__ == "__main__":
    unittest.main()
