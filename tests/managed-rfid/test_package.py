import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
VERSION = '1.27b-233.20261111-single.3'
NAME = 'multiace-managed-' + VERSION + '.tar.gz'

def digest(data):
    return hashlib.sha256(data).hexdigest()

class ManagedPackageTests(unittest.TestCase):
    def test_overlay_archives_and_manifest_match(self):
        archives = [ROOT/'overlays'/profile/'root/usr/local/share/233-firmware'/NAME
                    for profile in ('233-dev.2','233-dev.2-perms.1')]
        blob = archives[0].read_bytes()
        self.assertEqual(blob, archives[1].read_bytes())
        manifest = json.loads((ROOT/'bundles/233-dev.2/manifest.json').read_text())
        self.assertEqual(manifest['version'],VERSION)
        self.assertEqual(manifest['archive_sha256'],digest(blob))
        with tarfile.open(archives[0], 'r:gz') as archive:
            files = {m.name.split('/',1)[1]:archive.extractfile(m).read() for m in archive if m.isfile()}
        self.assertEqual(set(files),set(manifest['files']))
        for name,data in files.items():
            self.assertEqual(manifest['files'][name],{'sha256':digest(data),'bytes':len(data)})
        for name in ('klipper/extras/ace.py','klipper/extras/ace_rc522.py',
                     'klipper/extras/filament_feed_ace.py','klipper/extras/ace_slot_actions.py'):
            compile(files[name],name,'exec')
        for profile in ('233-dev.2','233-dev.2-perms.1'):
            definition=(ROOT/'overlays'/profile/'root/usr/local/share/extended-pkg/multiace').read_text()
            self.assertIn('PKG_VERSION="'+VERSION+'"',definition)
            self.assertIn('PKG_SHA256="'+digest(blob)+'"',definition)
            self.assertIn(NAME,definition)
    def test_rebuild_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)/'package.tar.gz'
            manifest=Path(directory)/'manifest.json'
            subprocess.run([sys.executable,str(ROOT/'bundles/233-dev.2/managed-rfid/build.py'),
                            '--output',str(out),'--manifest',str(manifest)],check=True,stdout=subprocess.DEVNULL)
            expected=ROOT/'overlays/233-dev.2/root/usr/local/share/233-firmware'/NAME
            self.assertEqual(out.read_bytes(),expected.read_bytes())
            self.assertEqual(json.loads(manifest.read_text()),
                             json.loads((ROOT/'bundles/233-dev.2/manifest.json').read_text()))
