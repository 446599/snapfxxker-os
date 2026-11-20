# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the bundled dependency check with the installer's restrictive umask."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
DEFINITIONS = tuple(
    REPO / "overlays" / overlay / "root/usr/local/share/extended-pkg/multiace"
    for overlay in ("233-dev.2", "233-dev.2-perms.1"))
REQUIRED_FILES = (
    "klipper/extras/ace.py", "klipper/extras/ace_gen1_tunnel.py",
    "klipper/extras/ace_protocol.py", "klipper/extras/filament_feed_ace.py",
    "klipper/kinematics/extruder_ace.py", "config/extended/ace.cfg",
    "config/extended/multiace/ace_vars.cfg", "VERSION", "web/backend/main.py",
    "web/backend/requirements.txt", "web/frontend/index.html",
    "tools/post_process_virtual_toolheads.py",
)
DEPENDENCIES = ("fastapi", "uvicorn", "httpx", "pydantic", "websockets", "multipart", "serial")


class ManagedInstallPermissionsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.stage = Path(temporary.name)
        for name in REQUIRED_FILES:
            target = self.stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("fixture\n")
        self.vendor = self.stage / "web/backend/vendor"
        for name in DEPENDENCIES:
            package = self.vendor / name
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("READY = True\n")

    def install(self, definition):
        environment = dict(os.environ)
        environment.pop("PYTHONDONTWRITEBYTECODE", None)
        environment.pop("PYTHONPYCACHEPREFIX", None)
        return subprocess.run(
            ["bash", "-c", 'umask 077; source "$1"; pkg_post_install "$2"',
             "test", str(definition), str(self.stage)],
            capture_output=True, text=True, env=environment)

    def test_dependency_check_does_not_create_private_bytecode_directories(self):
        for definition in DEFINITIONS:
            with self.subTest(definition=definition):
                result = self.install(definition)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(list(self.stage.rglob("__pycache__")))
                marker = self.vendor / ".paxx-dependencies-ready"
                self.assertEqual(marker.stat().st_mode & 0o777, 0o755)
                if "233-dev.2-perms.1" in definition.parts:
                    self.assertTrue(all(
                        path.stat().st_mode & 0o777 == 0o755
                        for path in self.stage.rglob("*") if path.is_dir()))

    def test_failed_dependency_check_still_refuses_to_mark_package_ready(self):
        (self.vendor / "serial/__init__.py").write_text(
            "raise ImportError('fixture dependency unavailable')\n")
        for definition in DEFINITIONS:
            with self.subTest(definition=definition):
                result = self.install(definition)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("fixture dependency unavailable", result.stderr)
                self.assertFalse((self.vendor / ".paxx-dependencies-ready").exists())


if __name__ == "__main__":
    unittest.main()
