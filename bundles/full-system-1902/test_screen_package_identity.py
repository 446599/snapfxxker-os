"""The package manager decides whether to upgrade from the directory name."""
import pathlib
import shlex
import subprocess
import tempfile
import unittest

import package_screen_1908 as old
import package_screen_1910 as new


REPO = pathlib.Path(__file__).resolve().parents[2]
MANAGER = REPO / 'overlays/233-dev.2/root/usr/local/bin/extended-pkg'


class ScreenPackageIdentityTest(unittest.TestCase):
    def test_new_compiled_screen_has_distinct_install_version(self):
        self.assertNotEqual(new.SCREEN_VERSION, old.SCREEN_VERSION)
        self.assertEqual(new.SCREEN_PIN, old.SCREEN_PIN)

    def test_existing_1908_install_requires_upgrade_to_1910(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            defs, apps = root / 'defs', root / 'apps'
            defs.mkdir()
            apps.mkdir()
            existing = 'helixscreen-233-' + old.SCREEN_VERSION
            (apps / existing).mkdir()
            (apps / 'latest').symlink_to(existing)
            definition = defs / 'helixscreen'
            definition.write_text('\n'.join([
                'PKG_VERSION=' + shlex.quote(new.SCREEN_VERSION),
                'PKG_PREFIX=helixscreen-233',
                'PKG_APP_DIR=' + shlex.quote(str(apps)),
                'PKG_URL=file:///screen.tar.gz',
                'PKG_SHA256=sha256',
            ]) + '\n')
            source = MANAGER.read_text()
            needle = 'PKG_SHARE_DIR="/usr/local/share/extended-pkg"'
            self.assertEqual(source.count(needle), 1)
            script = root / 'extended-pkg'
            script.write_text(source.replace(needle, 'PKG_SHARE_DIR=' + shlex.quote(str(defs)), 1))
            run = subprocess.run(['bash', str(script), 'helixscreen', 'needs_upgrade'], capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr.decode())
            definition.write_text(definition.read_text().replace(new.SCREEN_VERSION, old.SCREEN_VERSION))
            same = subprocess.run(['bash', str(script), 'helixscreen', 'needs_upgrade'], capture_output=True)
            self.assertEqual(same.returncode, 1)


if __name__ == '__main__':
    unittest.main()
