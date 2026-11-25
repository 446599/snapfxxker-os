import importlib.util
import pathlib
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "device_service", pathlib.Path(__file__).with_name("device_service.py")
)
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


class FirmwareTests(unittest.TestCase):
    def test_rejects_empty_and_wrong_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "u1.bin"
            path.write_bytes(b"")
            with self.assertRaises(ValueError):
                service.validate_u1(path)
            path.write_bytes(b"RKFW" + bytes(300))
            with self.assertRaises(ValueError):
                service.validate_u1(path)

    def test_checks_payload_and_bounds(self):
        import hashlib, struct

        payloads = [b"RKFW" + bytes(100), b"mcu1", b"mcu2", b"build\n"]
        header = bytearray(64)
        header[:4] = b"SNMK"
        struct.pack_into(">H", header, 4, 1)
        header[8:13] = b"2.1.0"
        struct.pack_into(">H", header, 46, 4)
        struct.pack_into(">H", header, 6, sum(header) & 65535)
        entries = []
        offset = 192
        for i, payload in enumerate(payloads):
            e = bytearray(
                struct.pack(
                    ">HHQI16s",
                    i,
                    0,
                    offset,
                    len(payload),
                    hashlib.md5(payload).digest(),
                )
            )
            struct.pack_into(">H", e, 2, sum(e) & 65535)
            entries.append(e)
            offset += len(payload)
        encoded = bytes(header).translate(service.ENCODE) + b"".join(
            bytes(e).translate(service.ENCODE) for e in entries
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "U1.bin"
            path.write_bytes(encoded + b"".join(payloads))
            self.assertEqual(service.validate_u1(path)["version"], "2.1.0")
            data = bytearray(path.read_bytes())
            data[-1] ^= 1
            path.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "MD5"):
                service.validate_u1(path)
            path.write_bytes(encoded + payloads[0])
            with self.assertRaises(ValueError):
                service.validate_u1(path)

    def test_scan_blocks_symlink_escape_and_other_formats(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as outside,
        ):
            root = pathlib.Path(tmp)
            (root / "good.bin").write_bytes(b"123")
            (root / "print.gcode").write_text("G28")
            escape = pathlib.Path(outside) / "escape.bin"
            escape.write_bytes(b"123")
            (root / "link.bin").symlink_to(escape)
            files = service.scan_files([root])
            self.assertEqual([f["name"] for f in files], ["good.bin"])

    def test_idle_guard_fails_closed(self):
        for state in ["printing", "paused", "error", None]:
            with self.assertRaises(ValueError):
                service.require_idle(
                    {
                        "print_stats": {"state": state},
                        "webhooks": {"state": "ready"},
                        "idle_timeout": {"state": "Ready"},
                    }
                )
        service.require_idle(
            {
                "print_stats": {"state": "standby"},
                "webhooks": {"state": "ready"},
                "idle_timeout": {"state": "Ready"},
            }
        )
        with self.assertRaises(ValueError):
            service.require_idle(
                {
                    "print_stats": {"state": "standby"},
                    "webhooks": {"state": "ready"},
                    "idle_timeout": {"state": "Printing"},
                }
            )


class HttpContractTests(unittest.TestCase):
    def test_post_requires_json_and_get_returns_local_file_contract(self):
        import http.client
        import threading
        from http.server import ThreadingHTTPServer

        server = ThreadingHTTPServer(("127.0.0.1", 0), service.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with patch.object(service, "start") as start:
                conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
                conn.request(
                    "POST",
                    "/firmware/start",
                    body="{}",
                    headers={"Content-Type": "text/plain"},
                )
                response = conn.getresponse()
                self.assertEqual(response.status, 400)
                response.read()
                conn.close()
                start.assert_not_called()
            with patch.object(service, "scan_files", return_value=[]):
                conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
                conn.request("GET", "/firmware/files")
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b'{"files": []}')
                conn.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = pathlib.Path(self.tmp.name) / "stage"
        self.usb = pathlib.Path(self.tmp.name) / "usb"
        self.usb.mkdir()
        self.package = self.usb / "U1.bin"
        self.package.write_bytes(b"validated fixture")
        service.STATE.clear()
        service.STATE.update(state="idle", target="u1")
        service.PREPARED.clear()
        self.patches = [
            patch.object(service, "STORE", self.store),
            patch.object(
                service, "scan_files", return_value=[{"path": str(self.package)}]
            ),
            patch.object(
                service,
                "validate_u1",
                return_value={"size": self.package.stat().st_size, "version": "2.1.0"},
            ),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_stage_is_private_and_start_hash_is_bound_to_confirmation(self):
        info = service.prepare({"path": str(self.package), "target": "u1"})
        self.assertEqual(service.STATE["state"], "prepared")
        self.assertNotEqual(info["path"], str(self.package))
        self.assertEqual(
            pathlib.Path(info["path"]).read_bytes(), self.package.read_bytes()
        )
        pathlib.Path(info["path"]).write_bytes(b"changed")
        with (
            patch.object(service, "machine_idle"),
            self.assertRaisesRegex(ValueError, "变化"),
        ):
            service.start({"token": info["token"]})

    def test_rejects_stale_confirmation_and_parallel_upgrade(self):
        first = service.prepare({"path": str(self.package), "target": "u1"})
        second = service.prepare({"path": str(self.package), "target": "u1"})
        self.assertNotEqual(first["token"], second["token"])
        with self.assertRaises(ValueError):
            service.start({"token": first["token"]})
        service.STATE["state"] = "flashing"
        with self.assertRaises(ValueError):
            service.prepare({"path": str(self.package), "target": "u1"})

    def test_bad_package_never_reaches_prepared(self):
        with patch.object(service, "validate_u1", side_effect=ValueError("MD5")):
            with self.assertRaises(ValueError):
                service.prepare({"path": str(self.package), "target": "u1"})
        self.assertEqual(service.STATE["state"], "error")
        self.assertFalse(service.PREPARED)

    def test_installer_log_and_reboot_state_with_fake_executor(self):
        # This executable only emits output. No device is contacted.
        executable = pathlib.Path(self.tmp.name) / "fake-installer"
        executable.write_text(
            '#!/bin/sh\necho "upgrade soc finish, prepare to reboot"\n'
        )
        executable.chmod(0o700)
        self.store.mkdir()
        with patch.object(service, "INSTALLER", executable):
            service.run_u1(self.package)
        self.assertEqual(service.STATE["state"], "reboot_pending")
        self.assertIn("prepare to reboot", (self.store / "upgrade.log").read_text())

    def test_reboot_reports_version_verification_separately(self):
        service.STATE.update(state="reboot_pending", boot_id="previous-boot")
        with patch.object(service, "boot_id", return_value="new-boot"):
            result = service.status()
        self.assertEqual(result["state"], "verify_version")


if __name__ == "__main__":
    unittest.main()
