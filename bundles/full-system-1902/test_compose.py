"""Synthetic fixtures only; does not validate an actual U1 image."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import compose as c

# The mount list is loaded from the candidate branch when this is run in CI.
REPO = Path(__file__).resolve().parents[2]
CANDIDATE_COMMON = REPO / 'overlays/mods/multiace/70-app-multiace/root/usr/local/share/multiace/common.sh'
if not CANDIDATE_COMMON.is_file():
    # The local synthetic test environment has the exact mapping fixture.
    CANDIDATE_COMMON = Path('/mnt/data/ace2k_boot_test/1902-mount-functions.sh')


def write(root, rel, content):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def tar(root, path):
    with tarfile.open(path, 'w:gz') as t:
        t.add(root, arcname='provider')
    return path


class CompositionTest(unittest.TestCase):
    def test_cfg_values_scope(self):
        old = '[ace]\nlanguage: zh\nenable_web: true\nweb_dir: /old\n[other]\nenable_web: true\n'
        new = c.cfg_values(old, c.WEB_SETTINGS)
        self.assertIn('language: zh\n', new)
        self.assertIn('enable_web: false\n', new)
        self.assertIn('[other]\nenable_web: true\n', new)
        self.assertEqual(c.cfg_values(new, c.WEB_SETTINGS), new)

    def test_package_composition(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            root = p / 'rootfs'; root.mkdir()
            work = p / 'work'; work.mkdir()
            old = p / 'old'; old.mkdir()
            field = p / 'field'; field.mkdir()
            write(root, 'etc/passwd', 'root:x:0:0:root:/root:/bin/sh\n')
            write(old, 'web/backend/vendor/fastapi/__init__.py', '# old offline dependency\n')
            write(old, 'web/backend/vendor/ace2k_flash.pyz', 'old tool must not override new')
            write(old, 'web/backend/vendor/.paxx-dependencies-ready', 'stale marker')
            write(old, 'web/backend/firmware/ace2k.bin', 'old image bytes')
            write(old, 'web/backend/main.py', 'old backend')
            oldpkg = root / f'usr/local/share/233-firmware/multiace-managed-{c.BASE_PROVIDER}.tar.gz'
            oldpkg.parent.mkdir(parents=True)
            tar(old, oldpkg)
            oldhash = c.sha(oldpkg)
            write(root, 'usr/local/share/extended-pkg/multiace', f'PKG_VERSION="{c.BASE_PROVIDER}"\nPKG_URL="file:///{oldpkg.name}"\nPKG_SHA256="{oldhash}"\npkg_post_install() {{\n    for required in \\\n        klipper/extras/ace.py; do\n        test -f "$required"\n    done\n}}\n')
            write(root, 'etc/hooks/klipper.d/10-multiace.sh', 'case "$1" in\n start)\n        if multiace_enabled; then\n            multiace_activate\n        fi\n        ;;\nesac\n')
            write(root, 'etc/init.d/S98multiace-web', 'start() {\n    if ! multiace_seed_config; then\n        echo "$NAME cannot prepare its persistent configuration" >&2\n        return 1\n    fi\n}\n')
            common = CANDIDATE_COMMON.read_text()
            if '    if [ "$config_seeded" = "1" ]; then\n' not in common:
                common += '\nseed() {\n    if [ "$config_seeded" = "1" ]; then\n        :\n    fi\n    if [ ! -e "$MULTIACE_STATE_DIR/ace_vars.cfg" ]; then\n        :\n    fi\n}\n'
            common_path = write(p, 'common.sh', common)
            write(root, 'usr/local/share/multiace/common.sh', common)
            mapping = common.split("multiace_1902_module_map() {\n    cat <<'MAP'\n",1)[1].split('\nMAP',1)[0]
            for row in mapping.splitlines():
                write(field, 'klipper/' + row.split()[0], '# field module\n')
            engine = write(field, 'klipper/extras/ace.py', '# SharedAce2Discovery\n# official or explicitly_configured\n')
            expected_engine = c.sha(engine)
            for rel in ('config/extended/multiace/ace_vars.cfg','tools/merge_ace_cfg.py','tools/post_process_virtual_toolheads.py','web/backend/requirements.txt','web/frontend/index.html','web/backend/ace2k_flasher.py'):
                write(field, rel, '# field file\n')
            write(field, 'VERSION', c.FIELD_VERSION+'\n')
            write(field, 'config/extended/ace.cfg', '[ace]\nenable_ace2k: true\nlanguage: zh\n')
            write(field, 'web/backend/vendor/ace2k_flash.pyz', 'new tool')
            write(field, 'web/backend/main.py', 'new backend')
            fieldpkg = tar(field, p/'field.tar.gz')
            with patch.object(c, 'BASE_PROVIDER_SHA', oldhash), patch.object(c, 'FIELD_SHA', c.sha(fieldpkg)), contextlib.redirect_stdout(io.StringIO()):
                new = c.compose(root, fieldpkg, common_path, work)
            manifest = json.loads((work/'composition.json').read_text())
            self.assertEqual(manifest['engine_sha256'], expected_engine)
            self.assertEqual(c.sha(root/manifest['provider_archive']), manifest['provider_sha256'])
            self.assertFalse(oldpkg.exists())
            self.assertEqual((new/'web/backend/main.py').read_text(), 'new backend')
            self.assertEqual((new/'web/backend/vendor/ace2k_flash.pyz').read_text(), 'new tool')
            self.assertTrue((new/'web/backend/vendor/fastapi/__init__.py').is_file())
            self.assertFalse((new/'web/backend/vendor/.paxx-dependencies-ready').exists())
            self.assertTrue((new/'web/backend/firmware/ace2k.bin').is_file())
            self.assertIn('v2_extra_usb_ids: 1a86:55d5', (new/'config/extended/ace.cfg').read_text())
            self.assertIn('enable_web: false', (new/'config/extended/ace.cfg').read_text())
            cfg = write(p,'existing.cfg','[ace]\nlanguage: zh\nweb_dir: /old\n[other]\nkey: retained\n')
            subprocess.run(['python3',str(new/'tools/set_managed_web.py'),str(cfg)],check=True)
            self.assertIn('language: zh',cfg.read_text())
            self.assertIn('[other]\nkey: retained',cfg.read_text())
            self.assertIn(c.APP+'/web',cfg.read_text())
            for rel in ('etc/hooks/klipper.d/10-multiace.sh','etc/init.d/S98multiace-web','usr/local/share/multiace/common.sh','usr/local/share/extended-pkg/multiace'):
                subprocess.run(['bash','-n',str(root/rel)],check=True)
            self.assertIn(manifest['provider_sha256'],(root/'usr/local/share/extended-pkg/multiace').read_text())

    def test_wrong_field_hash_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            root = p/'root'; root.mkdir()
            write(root,'etc/passwd','root')
            field = write(p,'bad','not the release')
            with self.assertRaisesRegex(ValueError, 'wrong field-tested'):
                c.compose(root, field, p/'common.sh', p/'work')


if __name__ == '__main__':
    unittest.main()
