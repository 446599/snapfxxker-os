import importlib.util
import http.client
import json
import os
import pathlib
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
        with (patch.object(service, "INSTALLER", executable),
              patch.object(service, "machine_idle"),
              patch.object(service, "UpgradeControls")):
            service.run_u1(self.package)
        self.assertEqual(service.STATE["state"], "reboot_pending")
        self.assertIn("prepare to reboot", (self.store / "upgrade.log").read_text())

    def test_reboot_reports_version_verification_separately(self):
        service.STATE.update(state="reboot_pending", boot_id="previous-boot")
        with patch.object(service, "boot_id", return_value="new-boot"):
            result = service.status()
        self.assertEqual(result["state"], "verify_version")


class FakePaxxHandler(BaseHTTPRequestHandler):
    """The only HTTP peer used by PAXX tests is this ephemeral loopback server."""

    def log_message(self, *args):
        pass

    def respond(self, code, content, content_type="application/json"):
        body = content.encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def record(self, data=None):
        self.server.requests.append({
            "method": self.command, "path": self.path, "data": data,
            "headers": {key.lower(): value for key, value in self.headers.items()},
        })

    def do_GET(self):
        self.record()
        if self.server.mode == "unauthorized":
            self.respond(401, "{}")
        elif self.path == "/firmware-config/api/settings":
            self.respond(200, json.dumps(self.server.settings))
        else:
            self.respond(404, "{}")

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.record(data)
        if self.server.mode == "disconnect":
            self.close_connection = True
            return
        if self.server.mode == "error_stream":
            self.respond(200, "Applying changes...\nERROR: validation failed\n",
                         "text/plain")
            return
        if self.server.mode != "wrong_readback":
            self.server.settings["snapmaker_components"]["items"][0]["current"] = "enabled"
        self.respond(200, "Applying changes...\nSUCCESS: Setting updated successfully\n",
                     "text/plain")


class PaxxSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakePaxxHandler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join()

    def setUp(self):
        self.server.mode = "success"
        self.server.requests = []
        self.server.settings = {
            "snapmaker_components": {"label": "Snapmaker Components", "items": [
                {"id": "spoolman", "label": "Spoolman Integration", "current": "disabled",
                 "options": {
                     "disabled": {"label": "Disabled"},
                     "enabled": {"label": "Enabled", "confirm": "Enable Spoolman?",
                                 "inputs": [{"name": "SPOOLMAN_URL", "label": "Spoolman URL",
                                             "placeholder": "http://192.168.1.100:7912",
                                             "regex": "^https?://.+"}]},
                     "future": {"label": "Future option", "hidden": True}}},
                {"id": "spoolman_tag_store", "label": "Tag storage", "current": "card_uids",
                 "hidden": True, "options": {"native": {"label": "Native tags"}}},
            ]},
            "new_component": {"label": "Future Component", "items": [
                {"id": "new-feature", "label": "New feature", "current": "special",
                 "options": {"special": {"label": "Special"}}},
            ]},
        }
        self.payload = {
            "id": "spoolman", "value": "enabled", "confirm": True,
            "inputs": {"SPOOLMAN_URL": "http://spoolman.local:7912"},
        }
        env = patch.dict(os.environ, {
            "U1_PAXX_API_URL": "http://127.0.0.1:%d/firmware-config/api" % self.server.server_port,
            "U1_PAXX_API_KEY": "fake-api-key", "U1_PAXX_TOKEN": "fake-access-token",
        })
        env.start()
        self.addCleanup(env.stop)
        self.idle = patch.object(service, "json_request", return_value={"result": {
            "objects": ["webhooks", "print_stats", "idle_timeout", "heaters", "extruder"],
            "status": {"heaters": {"available_heaters": ["extruder"]},
            "extruder": {"target": 0, "power": 0},
            "webhooks": {"state": "ready"}, "print_stats": {"state": "standby"},
            "idle_timeout": {"state": "Ready"},
        }}})
        self.idle_mock = self.idle.start()
        self.addCleanup(self.idle.stop)
        previous = dict(service.STATE)
        self.addCleanup(lambda: (service.STATE.clear(), service.STATE.update(previous)))
        service.STATE.update(state="idle")

    def test_settings_keep_dynamic_options_and_hidden_metadata(self):
        result = service.firmware_settings()
        self.assertEqual(result["groups"], [
            {"id": "materials", "label": "耗材与识别"},
            {"id": "interface", "label": "界面与扩展"},
        ])
        spoolman = service.firmware_setting(result, "spoolman")
        self.assertEqual(spoolman["label"], "Spoolman 耗材库")
        self.assertEqual(spoolman["current"], "disabled")
        self.assertTrue(spoolman["options"][2]["hidden"])
        field = spoolman["options"][1]["inputs"][0]
        self.assertEqual(field["name"], "SPOOLMAN_URL")
        self.assertEqual(field["regex"], "^https?://.+")
        self.assertTrue(field["required"])
        self.assertEqual(service.firmware_setting(result, "new-feature")["label"], "New feature")
        self.assertTrue(service.firmware_setting(result, "spoolman_tag_store")["hidden"])

    def test_authenticated_post_reads_back_actual_value(self):
        result = service.update_firmware_setting(self.payload)
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "applied")
        self.assertEqual(result["current"], "enabled")
        self.assertEqual([req["method"] for req in self.server.requests], ["GET", "POST", "GET"])
        posted = self.server.requests[1]
        self.assertEqual(posted["path"], "/firmware-config/api/settings/spoolman/enabled")
        self.assertEqual(posted["data"], self.payload["inputs"])
        for req in self.server.requests:
            self.assertEqual(req["headers"]["x-api-key"], "fake-api-key")
            self.assertEqual(req["headers"]["authorization"], "Bearer fake-access-token")
        self.assertEqual(self.idle_mock.call_args.kwargs["headers"]["X-Api-Key"], "fake-api-key")

    def test_http_200_error_stream_is_not_success(self):
        self.server.mode = "error_stream"
        result = service.update_firmware_setting(self.payload)
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "failed")
        self.assertIn("validation failed", result["error"])
        self.assertEqual(len([req for req in self.server.requests if req["method"] == "POST"]), 1)

    def test_changed_readback_is_unverified(self):
        self.server.mode = "wrong_readback"
        result = service.update_firmware_setting(self.payload)
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "unverified")
        self.assertEqual(result["current"], "disabled")

    def test_disconnect_is_not_retried_or_reported_applied(self):
        self.server.mode = "disconnect"
        result = service.update_firmware_setting(self.payload)
        self.assertEqual(result["state"], "unverified")
        self.assertFalse(result["ok"])
        self.assertEqual(len([req for req in self.server.requests if req["method"] == "POST"]), 1)

    def test_printing_and_paused_block_configuration_writes(self):
        for state in ("printing", "paused"):
            with self.subTest(state=state):
                self.idle_mock.return_value["result"]["status"]["print_stats"]["state"] = state
                with self.assertRaisesRegex(ValueError, "结束打印"):
                    service.update_firmware_setting(self.payload)
        self.assertFalse(any(req["method"] == "POST" for req in self.server.requests))

    def test_confirmation_inputs_and_hidden_capabilities_are_enforced(self):
        invalid_payloads = [
            {**self.payload, "confirm": False},
            {**self.payload, "inputs": {}},
            {**self.payload, "inputs": {"SPOOLMAN_URL": "file:///tmp/data"}},
            {**self.payload, "inputs": {"SPOOLMAN_URL": "http://localhost", "unknown": "value"}},
            {**self.payload, "value": "future"},
            {"id": "spoolman_tag_store", "value": "native"},
            {"id": "invented", "value": "enabled"},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                service.update_firmware_setting(payload)
        self.assertFalse(any(req["method"] == "POST" for req in self.server.requests))

    def test_auth_failure_does_not_fall_back_to_private_backend(self):
        self.server.mode = "unauthorized"
        with self.assertRaisesRegex(ValueError, "需要登录"):
            service.firmware_settings()
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.server.requests[0]["path"], "/firmware-config/api/settings")

    def test_companion_http_contract_returns_structured_application_result(self):
        companion = ThreadingHTTPServer(("127.0.0.1", 0), service.Handler)
        worker = threading.Thread(target=companion.serve_forever, daemon=True)
        worker.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", companion.server_port)
            conn.request("GET", "/firmware/settings")
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertTrue(json.loads(response.read())["available"])
            conn.close()
            conn = http.client.HTTPConnection("127.0.0.1", companion.server_port)
            conn.request("POST", "/firmware/settings", json.dumps(self.payload),
                         {"Content-Type": "application/json"})
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["state"], "applied")
            conn.close()
        finally:
            companion.shutdown()
            companion.server_close()
            worker.join()


if __name__ == "__main__":
    unittest.main()
