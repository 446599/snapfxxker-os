import hashlib
import re
import tarfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[3]
VERSION = "1.27b-233.20261122-flow.15.116"
OVERLAYS = (
    REPOSITORY / "overlays/233-dev.2/root",
    REPOSITORY / "overlays/mods/multiace/70-app-multiace/root",
)


class FirmwarePackageTests(unittest.TestCase):
    def test_both_installers_load_the_same_local_payload(self):
        pins = []
        for root in OVERLAYS:
            descriptor = (root / "usr/local/share/extended-pkg/multiace").read_text()
            fields = dict(re.findall(r'^(PKG_[A-Z0-9_]+)="([^"]+)"$', descriptor, re.M))
            self.assertEqual(fields["PKG_VERSION"], VERSION)
            archive = f"/usr/local/share/233-firmware/multiace-managed-{VERSION}.tar.gz"
            self.assertEqual(fields["PKG_URL"], "file://" + archive)
            checksum = hashlib.sha256((root / archive.lstrip("/")).read_bytes()).hexdigest()
            self.assertEqual(checksum, fields["PKG_SHA256"])
            pins.append(checksum)
        self.assertEqual(pins[0], pins[1])

    def test_offline_runtime_has_both_published_images_and_the_upstream_flasher(self):
        prefix = f"multiace-{VERSION}/"
        archive = OVERLAYS[0] / f"usr/local/share/233-firmware/multiace-managed-{VERSION}.tar.gz"
        hashes = {
            "web/backend/firmware/ACE2-1.2.04O-insert-policy.bin":
                "818200b199c1d121bd7f1919e5ca507a443b650fe0debe3ddaf6d77e3b5d3251",
            "web/backend/firmware/ace2k-0.16.0-233b.bin":
                "6f4a0e38c481fb0fc057031bb05bdb07900d4f9f64a2007d12c80c7ff59d6d18",
            "web/backend/vendor/ace2k_flash.pyz":
                "290a47cc677b9000eaf6d7edc5bcefb8947a0659ca71b5367865560dfc91547b",
            "web/backend/vendor/ace2k-flasher-LICENSE":
                "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986",
        }
        with tarfile.open(archive) as package:
            self.assertEqual(package.extractfile(prefix + "VERSION").read().decode().strip(), VERSION)
            for name, digest in hashes.items():
                self.assertEqual(hashlib.sha256(package.extractfile(prefix + name).read()).hexdigest(), digest)
            self.assertTrue(package.getmember(prefix + "web/backend/ace2k_update.py").isfile())
            self.assertTrue(package.getmember(prefix + "web/backend/vendor/ace2k-flasher-NOTICE").isfile())


if __name__ == "__main__":
    unittest.main()
