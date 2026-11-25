"""Offline tests for the packaged MultiACE bundled-firmware web routes.

Run with the unpacked runtime's web/backend on PYTHONPATH and the backend's
Python dependencies installed. No test contacts Moonraker or a serial device.
"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import sys
from packaged_runtime import runtime_root

sys.path.insert(0, str(runtime_root() / "web/backend"))
import ace2_ota
import ace2k_update
import main


PORT = "/dev/serial/by-id/test-ace-2"
OPEN = "V1.2.04O"
ACE2K = "ace2k 0.16.0-233b"


class FirmwareTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enter_patch(patch.dict(main._acefw, state="idle", error="", result=None))
        self.unit = dict(idx=0, connected=True, fw_hold=False, protocol="v2",
                         serial_path=PORT, firmware_live=OPEN)
        self.state = {"printer_state": "idle", "aces": [self.unit]}
        self.query = self.enter_patch(patch.object(main, "_query_state", new_callable=AsyncMock))
        self.query.return_value = self.state
        self.enter_patch(patch.object(main, "_parse_state", side_effect=lambda state: state))

    def enter_patch(self, context):
        value = context.start()
        self.addCleanup(context.stop)
        return value


class BundledRoutes(FirmwareTest):
    def setUp(self):
        super().setUp()
        self.release = self.enter_patch(patch.object(main, "_acefw_release", new_callable=AsyncMock))
        self.classic = self.enter_patch(patch.object(main, "_acefw_run", new_callable=AsyncMock))
        self.cross = self.enter_patch(patch.object(main, "_acefw_run_cross", new_callable=AsyncMock))
        self.verify_files = self.enter_patch(patch.object(ace2k_update, "verify_files"))
        self.image = SimpleNamespace(version="1.2.04O")
        self.enter_patch(patch.object(ace2_ota, "load_bundled_image", return_value=self.image))

    async def test_advertises_both_images_for_two_protocols_and_keeps_legacy_version(self):
        status = await main.acefw_status()
        self.assertEqual(status["bundled_version"], OPEN)
        self.assertEqual({t["id"]: (t["version"], set(t["protocols"])) for t in status["targets"]}, {
            "open": (OPEN, {"v2", "ace2k"}),
            "ace2k": ("0.16.0-233b", {"v2", "ace2k"}),
        })

    async def test_v2_open_keeps_classic_engine_for_explicit_and_old_clients(self):
        for payload in ({"ace": 0}, {"ace": 0, "target": "open"}):
            with self.subTest(payload=payload):
                self.assertEqual(await main.acefw_flash_bundled(payload), {"ok": True})
                await asyncio.sleep(0)
                self.release.assert_awaited_once_with(0, PORT)
                self.classic.assert_awaited_once_with(0, PORT, self.image.version, None, None,
                                                       dry_run=False, force=True, bundled_image=self.image)
                self.cross.assert_not_awaited()
                self.release.reset_mock()
                self.classic.reset_mock()

    async def test_explicit_cross_routes_use_the_requested_image_and_selected_port(self):
        for protocol, target in (("v2", "ace2k"), ("ace2k", "ace2k"), ("ace2k", "open")):
            with self.subTest(protocol=protocol, target=target):
                self.unit.update(protocol=protocol, firmware_live=OPEN if protocol == "v2" else ACE2K)
                result = await main.acefw_flash_bundled({"ace": 0, "target": target})
                await asyncio.sleep(0)
                self.assertTrue(result["ok"])
                self.verify_files.assert_called_once_with(target)
                self.release.assert_awaited_once_with(0, PORT, switch_driver=True)
                self.cross.assert_awaited_once_with(0, PORT, target)
                self.classic.assert_not_awaited()
                for mocked in (self.verify_files, self.cross, self.release):
                    mocked.reset_mock()

    async def test_bad_requests_do_not_claim_a_port(self):
        cases = [
            ({"ace": False, "target": "open"}, {}, {}, 400),
            ({"ace": 0, "target": ["ace2k"]}, {}, {}, 400),
            ({"ace": 0}, {"protocol": "ace2k"}, {}, 400),
            ({"ace": 0, "target": "ace2k"}, {"protocol": "v1"}, {}, 400),
            ({"ace": 0, "target": "ace2k"}, {"connected": False}, {}, 409),
            ({"ace": 0, "target": "ace2k"}, {"firmware_live": None}, {}, 409),
            ({"ace": 0, "target": "open"}, {"fw_hold": True}, {}, 409),
            ({"ace": 0, "target": "ace2k"}, {}, {"printer_state": "printing"}, 409),
        ]
        for payload, unit, state, status in cases:
            with self.subTest(payload=payload, unit=unit, state=state), patch.dict(self.unit, unit), patch.dict(self.state, state):
                with self.assertRaises(main.HTTPException) as error:
                    await main.acefw_flash_bundled(payload)
                self.assertEqual(error.exception.status_code, status)
        self.release.assert_not_awaited()
        self.verify_files.assert_not_called()

    async def test_hash_failure_does_not_release_or_run(self):
        self.verify_files.side_effect = ValueError("wrong digest")
        with self.assertRaises(main.HTTPException) as error:
            await main.acefw_flash_bundled({"ace": 0, "target": "ace2k"})
        self.assertEqual(error.exception.status_code, 503)
        self.release.assert_not_awaited()
        self.cross.assert_not_awaited()

    async def test_failed_release_does_not_start_a_writer(self):
        self.release.side_effect = main.HTTPException(500, "port unavailable")
        with self.assertRaises(main.HTTPException):
            await main.acefw_flash_bundled({"ace": 0, "target": "ace2k"})
        self.cross.assert_not_awaited()
        self.classic.assert_not_awaited()


class CrossCompletion(FirmwareTest):
    def setUp(self):
        super().setUp()
        self.post = self.enter_patch(patch.object(main, "_mr_post", new_callable=AsyncMock))
        self.flash = self.enter_patch(patch.object(ace2k_update, "flash", new_callable=AsyncMock))

    async def test_live_reconnected_versions_are_required_for_success_in_both_directions(self):
        for target, protocol, version in (("ace2k", "ace2k", ACE2K), ("open", "v2", OPEN)):
            with self.subTest(target=target):
                self.unit.update(protocol=protocol, firmware_live=version)
                await main._acefw_run_cross(0, PORT, target)
                self.assertEqual(self.post.await_args.args[1]["script"], "ACE_FW_RESUME ACE=0 PROTOCOL="+protocol)
                self.assertEqual(main._acefw["state"], "done")
                self.assertEqual(main._acefw["result"]["new"], version)
                self.assertTrue(main._acefw["result"]["verified"])
                self.assertEqual(self.flash.await_args.args[:2], (target, PORT))

    async def test_tool_error_releases_hold_without_switching_protocol(self):
        self.flash.side_effect = RuntimeError("target image rejected")
        await main._acefw_run_cross(0, PORT, "ace2k")
        self.assertEqual(main._acefw["state"], "error")
        self.assertIn("target image rejected", main._acefw["error"])
        self.assertIsNone(main._acefw["result"])
        self.assertEqual(self.post.await_args.args[1]["script"], "ACE_FW_RESUME ACE=0")
        self.query.assert_not_awaited()

    async def test_reconnect_failure_is_not_a_completed_flash(self):
        self.post.side_effect = RuntimeError("no Moonraker connection")
        await main._acefw_run_cross(0, PORT, "open")
        self.assertEqual(main._acefw["state"], "error")
        self.assertIn("no Moonraker connection", main._acefw["error"])
        self.assertIsNone(main._acefw["result"])

    async def test_cached_version_wrong_port_or_different_box_cannot_pass_verification(self):
        self.unit.update(protocol="ace2k", firmware=ACE2K, firmware_live=ACE2K)
        for change in ({"firmware_live": ""}, {"serial_path": "/dev/tty-other"}, {"idx": 1},
                       {"protocol": "v2"}, {"connected": False}):
            with self.subTest(change=change), patch.dict(self.unit, change), patch.object(
                    main, "time", SimpleNamespace(monotonic=Mock(side_effect=[0, 31]))):
                with self.assertRaises(RuntimeError):
                    await main._acefw_verify_reconnected(0, PORT, "ace2k")

    async def test_held_device_must_still_match_selected_port(self):
        self.unit.update(fw_hold=True, serial_path="/dev/tty-other")
        with self.assertRaises(main.HTTPException):
            await main._acefw_release(0, PORT, switch_driver=True)
        self.assertEqual([c.args[1]["script"] for c in self.post.await_args_list],
                         ["ACE_FW_RELEASE ACE=0 SWITCH=1", "ACE_FW_RESUME ACE=0"])
        self.assertEqual(main._acefw["state"], "error")


class ToolExitHandling(unittest.IsolatedAsyncioTestCase):
    async def test_exit_two_is_deferred_only_for_ace2k_and_never_confirms_the_live_version(self):
        async def lines():
            yield b"configuration follows\n"

        with tempfile.TemporaryDirectory() as tmp:
            # The CLI never launches in this test. Simulate its "written, unverified"
            # result and exercise the handoff to the separate reconnect check.
            for target, expect_error in (("ace2k", False), ("open", True)):
                with self.subTest(target=target):
                    child = SimpleNamespace(stdout=lines(), wait=AsyncMock(return_value=2), returncode=2)
                    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=child)):
                        update = ace2k_update.flash(target, PORT, Path(tmp), "http://localhost:7125", Mock())
                        if expect_error:
                            with self.assertRaises(RuntimeError):
                                await update
                        else:
                            await update


if __name__ == "__main__":
    unittest.main()
