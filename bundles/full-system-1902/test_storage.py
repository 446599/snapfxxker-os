import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('storage', Path(__file__).with_name('runtime')/'multiace-relocate-storage.py')
storage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage)

class StorageMigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.old = self.root/'oem/apps/multiace'
        self.new = self.root/'userdata/233-apps/multiace'
        self.old.mkdir(parents=True)
        self.name = 'multiace-1.90.2-233.20261126-paxx.1'
        self.src = self.old/self.name
        self.src.mkdir()
        (self.src/'VERSION').write_text(self.name[len('multiace-'):]+'\n')
        for rel in ('klipper/extras/ace.py','web/backend/main.py','web/backend/vendor/example.py'):
            p = self.src/rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(rel)
        (self.src/'example-link').symlink_to('web/backend/vendor/example.py')
        (self.old/'latest').symlink_to(self.name)
        self.unrelated = self.old/'calibration'
        self.unrelated.mkdir()
        (self.unrelated/'data').write_text('do not move')
        self.state = self.root/'oem/printer_data/ace_vars.cfg'
        self.state.parent.mkdir()
        self.state.write_text('persistent state')
    def migrate(self):
        return storage.migrate(self.old,self.new,verify_mounts=False,sync=lambda:None)
    def test_verified_move_and_idempotence(self):
        before = storage.fingerprint(self.src)
        self.assertEqual(self.migrate(),[self.name])
        self.assertEqual(storage.fingerprint(self.new/self.name),before)
        self.assertTrue(self.src.is_symlink())
        self.assertEqual(self.src.resolve(),self.new/self.name)
        self.assertEqual((self.old/'latest').resolve(),self.new/self.name)
        self.assertEqual((self.new/'latest').resolve(),self.new/self.name)
        self.assertEqual(self.migrate(),[])
        self.assertEqual((self.unrelated/'data').read_text(),'do not move')
        self.assertEqual(self.state.read_text(),'persistent state')
    def test_conflict_keeps_original(self):
        import shutil
        self.new.mkdir(parents=True)
        shutil.copytree(self.src,self.new/self.name,symlinks=True)
        (self.new/self.name/'web/backend/main.py').write_text('different')
        with self.assertRaises(RuntimeError):
            self.migrate()
        self.assertFalse(self.src.is_symlink())
        self.assertEqual((self.src/'web/backend/main.py').read_text(),'web/backend/main.py')
    def test_resume_partial_cleanup(self):
        import shutil
        self.new.mkdir(parents=True)
        shutil.copytree(self.src,self.new/self.name,symlinks=True)
        tomb = self.old/(storage.TOMB+self.name)
        self.src.rename(tomb)
        (tomb/'VERSION').unlink()
        self.assertEqual(self.migrate(),[self.name])
        self.assertTrue(self.src.is_symlink())
        self.assertFalse(tomb.exists())
    def test_preserve_new_latest(self):
        self.new.mkdir(parents=True)
        (self.new/'latest').symlink_to('multiace-newer')
        self.migrate()
        self.assertEqual(os.readlink(self.new/'latest'),'multiace-newer')
    def test_unknown_version_directory_not_deleted(self):
        unknown = self.old/'multiace-custom'
        unknown.mkdir()
        (unknown/'VERSION').write_text('different-version')
        self.assertEqual(self.migrate(),[self.name])
        self.assertTrue(unknown.is_dir())
        self.assertEqual((unknown/'VERSION').read_text(),'different-version')

    def test_50_legacy_links_and_6_real_providers(self):
        import shutil
        runtime = self.root/'userdata/multiace-provider-runtime'
        runtime.mkdir(parents=True)
        alias_target = runtime/'legacy-release'
        alias_target.mkdir()
        (alias_target/'sentinel').write_text('legacy contents must stay')
        links = {}
        for i in range(50):
            link = self.old/('multiace-0-legacy-%02d' % i)
            # Include existing, dangling and looping links. None should be
            # traversed, rewritten or deleted, regardless of sort order.
            target = alias_target if i < 48 else (runtime/'missing' if i == 48 else link)
            link.symlink_to(target)
            links[link] = os.readlink(link)
        providers = {self.name: storage.fingerprint(self.src)}
        for i in range(5):
            name = 'multiace-1.27b-field-%d' % i
            src = self.old/name
            shutil.copytree(self.src,src,symlinks=True)
            (src/'VERSION').write_text(name[len('multiace-'):]+'\n')
            providers[name] = storage.fingerprint(src)
        self.assertEqual(set(self.migrate()),set(providers))
        for name, before in providers.items():
            self.assertEqual(storage.fingerprint(self.new/name),before)
            self.assertTrue((self.old/name).is_symlink())
        for link, target in links.items():
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.readlink(link),target)
        self.assertEqual((alias_target/'sentinel').read_text(),'legacy contents must stay')
        self.assertEqual((self.new/'latest').resolve(),self.new/self.name)
        self.assertEqual(self.migrate(),[])

    def test_latest_points_to_legacy_link(self):
        runtime = self.root/'userdata/multiace-provider-runtime'
        runtime.mkdir(parents=True)
        target = runtime/'standalone'
        target.mkdir()
        alias = self.old/'multiace-legacy-standalone'
        alias.symlink_to(target)
        (self.old/'latest').unlink()
        (self.old/'latest').symlink_to(alias.name)
        self.assertEqual(self.migrate(),[self.name])
        self.assertEqual((self.old/'latest').resolve(),target)
        # Do not promote an unverified external alias to new managed latest.
        self.assertFalse(os.path.lexists(self.new/'latest'))

    def test_incomplete_historical_directory_does_not_block(self):
        incomplete = self.old/'multiace-0-incomplete'
        incomplete.mkdir()
        (incomplete/'partial').write_text('retain')
        self.assertEqual(self.migrate(),[self.name])
        self.assertEqual((incomplete/'partial').read_text(),'retain')


if __name__ == '__main__':
    unittest.main()
