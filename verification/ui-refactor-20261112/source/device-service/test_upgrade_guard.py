"""Offline regressions for idle checks and the installer handoff. No hardware."""
import copy
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import device_service as service
from idle_checks import require_machine_idle


def idle_snapshot(with_ace=True):
    snapshot = {
        "webhooks": {"state": "ready"},
        "print_stats": {"state": "standby"},
        "idle_timeout": {"state": "Ready"},
        "heaters": {"available_heaters": ["extruder", "extruder3", "heater_bed",
                                          "heater_generic chamber"]},
        "extruder": {"target": 0, "power": 0},
        "extruder3": {"target": 0, "power": 0},
        "heater_bed": {"target": 0, "power": 0},
        "heater_generic chamber": {"target": 0, "power": 0},
        "filament_feed left": {"extruder": {"channel_state": "load_finish",
                                            "channel_action_state": "load_finish"}},
    }
    if with_ace:
        device = {"status": "ready", "connected": True, "fw_hold": False,
                  "dryer_status": {"status": "stop"}, "auto_dry_running": False,
                  "slots": [{"status": "empty"}, {"status": "ready"}]}
        snapshot["ace"] = {
            "status": "ready", "swap_in_progress": False, "unload_all_active": False,
            "slot_action": {"state": "idle"}, "calibration": {"state": "idle"},
            "calibration_unload": {"active": False}, "tag_op": {"busy": False},
            "location_hold": {"active": False}, "dryer_status": {"status": "stop"},
            "aces": [copy.deepcopy(device), copy.deepcopy(device)],
        }
    return snapshot


class IdleChecksTests(unittest.TestCase):
    def test_idle_with_and_without_ace(self):
        require_machine_idle(idle_snapshot())
        require_machine_idle(idle_snapshot(False))

    def test_busy_filament_states_that_used_to_pass(self):
        changes = [
            ("slot_action", {"state": "running"}),
            ("slot_action", {"state": "cancelling"}),
            ("swap_in_progress", True),
            ("unload_all_active", True),
            ("calibration", {"state": "feeding"}),
            ("calibration_unload", {"active": True}),
            ("tag_op", {"busy": True}),
            ("location_hold", {"active": True}),
        ]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                snapshot = idle_snapshot()
                snapshot["ace"][key] = value
                with self.assertRaises(ValueError):
                    require_machine_idle(snapshot)

    def test_every_device_dryer_slot_and_ota_is_checked(self):
        changes = [
            ("dryer_status", {"status": "drying"}),
            ("dryer_status", {"status": "stop", "rotisserie": True}),
            ("auto_dry_running", True), ("connected", False), ("fw_hold", True),
            ("slots", [{"status": "feeding"}]),
        ]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                snapshot = idle_snapshot()
                snapshot["ace"]["aces"][1][key] = value
                with self.assertRaises(ValueError):
                    require_machine_idle(snapshot)

    def test_controlled_background_task_blocks_upgrade_while_waiting_for_user(self):
        for stage in ("preparing", "measuring", "marking", "cancelling"):
            snapshot = idle_snapshot()
            snapshot["ace"]["path_calibration"] = {
                "stage": stage, "active": True, "busy": False,
                "wait_reason": "insert"}
            with self.subTest(stage=stage), self.assertRaisesRegex(ValueError, "料管校准"):
                require_machine_idle(snapshot)
        snapshot = idle_snapshot()
        snapshot["ace"]["path_calibration"] = {"stage": "complete", "active": False,
                                                  "busy": False}
        require_machine_idle(snapshot)

    def test_controlled_operation_admission_precedes_native_phase_cache(self):
        for phase in ("queued", "claiming", "binding", "starting", "running", "stopping",
                      "rebasing", "committing"):
            snapshot = idle_snapshot()
            snapshot["ace"]["path_operations"] = [{"kind": "feed", "state": phase}]
            with self.subTest(phase=phase), self.assertRaisesRegex(ValueError, "受控"):
                require_machine_idle(snapshot)
        snapshot = idle_snapshot()
        snapshot["ace"]["path_operations"] = [{"state": "complete"}]
        require_machine_idle(snapshot)

    def test_disconnected_spare_requires_no_loaded_or_active_dependency(self):
        snapshot = idle_snapshot()
        ace = snapshot["ace"]
        ace["head_source"] = {str(i): None for i in range(4)}
        ace["aces"].append({"idx": 2, "connected": False, "status": "unknown",
                            "fw_hold": False, "auto_dry_running": False,
                            "dryer_status": {}, "slots": []})
        ace["device_count"] = 3
        ace["head_ace"] = {str(i): 2 for i in range(4)}
        require_machine_idle(snapshot)
        for value in ({"ace_index": 2, "slot": 0}, {"ace_index": 2}, "invalid"):
            bad = copy.deepcopy(snapshot)
            bad["ace"]["head_source"]["0"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                require_machine_idle(bad)
        for key, value in (("fw_hold", True), ("auto_dry_running", True),
                           ("slots", [{"status": "ready"}]), ("status", "busy"),
                           ("dryer_status", {"status": "stop", "rotisserie": True})):
            bad = copy.deepcopy(snapshot)
            bad["ace"]["aces"][2][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                require_machine_idle(bad)

    def test_native_phase_overrides_material_identity_but_must_be_ready(self):
        snapshot = idle_snapshot()
        slot = snapshot["ace"]["aces"][0]["slots"][0]
        slot.update(status="unknown", slot_status="ready")
        require_machine_idle(snapshot)
        for phase in (None, "unknown", "feeding", "preloading", "rollback"):
            slot["slot_status"] = phase
            with self.subTest(phase=phase), self.assertRaises(ValueError):
                require_machine_idle(snapshot)
        del slot["slot_status"]
        with self.assertRaises(ValueError):
            require_machine_idle(snapshot)

    def test_background_and_stock_feeder_wait_are_not_idle(self):
        for name, value in (
                ("ace_bg_swap", {"busy": [3]}),
                ("ace_native_guard", {"state": "running"}),
                ("filament_feed left", {"extruder": {
                    "channel_state": "unload_heat_finish", "channel_action_state": "none"}})):
            with self.subTest(name=name):
                snapshot = idle_snapshot()
                snapshot[name] = value
                with self.assertRaises(ValueError):
                    require_machine_idle(snapshot)

    def test_all_discovered_heaters_must_be_off_and_reported(self):
        for name in idle_snapshot()["heaters"]["available_heaters"]:
            for value in ({"target": 200, "power": 0}, {"target": 0, "power": 0.1},
                          {"target": None, "power": 0}, {"target": float("nan"), "power": 0}):
                with self.subTest(name=name, value=value):
                    snapshot = idle_snapshot()
                    snapshot[name] = value
                    with self.assertRaises(ValueError):
                        require_machine_idle(snapshot)
        snapshot = idle_snapshot()
        snapshot["heaters"]["available_heaters"].append("heater_generic added_late")
        with self.assertRaisesRegex(ValueError, "加热器"):
            require_machine_idle(snapshot)

    def test_present_empty_ace_is_not_an_absent_ace(self):
        for invalid in ({}, None, {"status": "ready"}):
            with self.subTest(ace=invalid):
                snapshot = idle_snapshot()
                snapshot["ace"] = invalid
                with self.assertRaises(ValueError):
                    require_machine_idle(snapshot)

    def test_missing_ace_operation_and_slot_fields_fail_closed(self):
        for key in ("swap_in_progress", "unload_all_active", "tag_op", "location_hold",
                    "slot_action", "calibration", "aces", "dryer_status"):
            with self.subTest(missing=key):
                snapshot = idle_snapshot()
                del snapshot["ace"][key]
                with self.assertRaises(ValueError):
                    require_machine_idle(snapshot)
        for slots in (None, {}, [], [None]):
            with self.subTest(slots=slots):
                snapshot = idle_snapshot()
                snapshot["ace"]["aces"][0]["slots"] = slots
                with self.assertRaises(ValueError):
                    require_machine_idle(snapshot)

    def test_discovery_rejects_omitted_and_new_heaters(self):
        snapshot = idle_snapshot(False)
        replies = [
            {"result": {"objects": list(snapshot)}},
            {"result": {"status": {"heaters": copy.deepcopy(snapshot["heaters"])}}},
            {"result": {"status": snapshot}},
        ]
        with patch.object(service, "json_request", side_effect=copy.deepcopy(replies)):
            service.machine_idle()
        replies[-1]["result"]["status"]["heaters"]["available_heaters"].append("new_heater")
        with (patch.object(service, "json_request", side_effect=replies),
              self.assertRaisesRegex(ValueError, "加热器")):
            service.machine_idle()


class ProcessExitTests(unittest.TestCase):
    def setUp(self):
        self.process = {"pid": 1444, "identity": ("123", "/usr/bin/python3 moonraker.py")}

    def test_exit_wait_tracks_start_time_through_command_line_changes(self):
        with (patch.object(service, "process_identity", side_effect=[
                ("123", ""), ("123", "shutdown"), None]) as identity,
              patch.object(service.time, "monotonic", return_value=100),
              patch.object(service.time, "sleep") as sleep):
            service.wait_process_exit(self.process)
        self.assertEqual(identity.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_exit_wait_rejects_reused_pid(self):
        with (patch.object(service, "process_identity", return_value=("456", "other")),
              self.assertRaisesRegex(ValueError, "无法确认控制服务已退出")):
            service.wait_process_exit(self.process)

    def test_exit_wait_timeout_survives_an_empty_command_line(self):
        with (patch.object(service, "process_identity", return_value=("123", "")),
              patch.object(service.time, "monotonic", side_effect=[100, 100, 121]),
              patch.object(service.time, "sleep") as sleep,
              self.assertRaisesRegex(ValueError, "无法确认控制服务已退出")):
            service.wait_process_exit(self.process)
        sleep.assert_called_once_with(0.05)


class UpgradeHandoffTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = self.root / "store"
        self.store.mkdir()
        self.handshake = self.root / "handshake"
        self.handshake.mkdir()
        service.STATE.clear()
        service.STATE.update(state="prepared", target="u1")
        service.PREPARED.clear()
        self.package = self.store / "package.bin"
        self.package.write_bytes(b"offline fixture")
        self.installer = self.root / "installer"
        self.installer.write_text("#!/bin/sh\nexit 0\n")
        self.installer.chmod(0o700)
        service.PREPARED.update(token="confirmation", path=str(self.package),
                                sha256=service.sha256_file(self.package))
        for item in (patch.object(service, "STORE", self.store),
                     patch.object(service, "UPGRADE_HANDSHAKE", self.handshake),
                     patch.object(service, "INSTALLER", self.installer),
                     patch.object(service, "ACTIVE_PROCESS", None)):
            item.start()
            self.addCleanup(item.stop)
        self.original = {name: {"pid": i, "identity": ("tick", name)}
                         for i, name in enumerate(("klipper", "moonraker", "multiace"), 101)}
        self.running = copy.deepcopy(self.original)
        self.events = []

    def process(self, name, required=False):
        value = self.running.get(name)
        if required and value is None:
            raise ValueError("service absent")
        return value

    def control(self, name, action):
        self.events.append((name, action))
        if action == "stop":
            self.running[name] = None
        else:
            self.assertFalse((self.handshake / "request.json").exists())
            self.running[name] = self.original[name]

    def prepare_request(self, token, pid):
        (self.handshake / "request.json").write_text(json.dumps({"token": token, "pid": pid}))
        (self.handshake / "receipt.json").write_text("{}")

    def accept(self, token):
        self.assertIsNone(self.running["moonraker"])
        self.assertIsNotNone(self.running["multiace"])
        self.events.append(("klipper", "atomic_check_and_exit"))
        (self.handshake / "receipt.json").write_text(json.dumps({
            "accepted": True, "token": token, "pid": self.original["klipper"]["pid"]}))
        self.running["klipper"] = None

    def mocks(self, endpoint=None):
        for item in (
                patch.object(service, "service_process", side_effect=self.process),
                patch.object(service, "control_service", side_effect=self.control),
                patch.object(service, "prepare_upgrade_request", side_effect=self.prepare_request),
                patch.object(service, "klippy_prepare", side_effect=endpoint or self.accept),
                patch.object(service, "wait_process_exit"),
                patch.object(service, "machine_idle")):
            item.start()
            self.addCleanup(item.stop)

    def fake_process(self, code=0):
        proc = Mock()
        proc.stdout = io.StringIO("upgrade soc finish, prepare to reboot\n")
        proc.wait.return_value = code
        proc.poll.return_value = code
        return proc

    def test_handoff_quiesces_before_installer_and_retains_lease(self):
        self.mocks()
        def launch(*args, **kwargs):
            self.assertTrue(all(value is None for value in self.running.values()))
            self.assertTrue((self.handshake / "request.json").exists())
            self.assertEqual(args[0], [str(self.installer), "upgrade", "all", str(self.package)])
            return self.fake_process()
        with patch.object(service.subprocess, "Popen", side_effect=launch) as popen:
            service.run_u1(self.package)
        popen.assert_called_once()
        self.assertEqual(self.events, [("moonraker", "stop"),
                                      ("klipper", "atomic_check_and_exit"), ("multiace", "stop")])
        self.assertEqual(service.STATE["state"], "reboot_pending")
        self.assertTrue((self.handshake / "request.json").exists())

    def test_new_slot_or_ota_operation_before_atomic_check_never_flashes(self):
        def reject(token):
            self.assertIsNone(self.running["moonraker"])
            self.assertIsNotNone(self.running["multiace"])
            raise ValueError("slot operation or ACE firmware hold became active")
        self.mocks(reject)
        with patch.object(service.subprocess, "Popen") as popen:
            service.run_u1(self.package)
        popen.assert_not_called()
        self.assertEqual(self.events, [("moonraker", "stop"), ("moonraker", "start")])
        self.assertEqual(service.STATE["state"], "error")
        self.assertFalse((self.handshake / "request.json").exists())

    def test_bad_receipt_never_launches_and_restores_only_stopped_services(self):
        def bad_receipt(token):
            self.accept("wrong-token")
        self.mocks(bad_receipt)
        with patch.object(service.subprocess, "Popen") as popen:
            service.run_u1(self.package)
        popen.assert_not_called()
        self.assertNotIn(("multiace", "stop"), self.events)
        self.assertEqual(self.events[-2:], [("klipper", "start"), ("moonraker", "start")])
        self.assertFalse((self.handshake / "request.json").exists())

    def test_installer_launch_error_restores_services_and_releases_lease(self):
        self.mocks()
        with patch.object(service.subprocess, "Popen", side_effect=OSError("exec failed")):
            service.run_u1(self.package)
        self.assertEqual(self.events[-3:], [("klipper", "start"), ("moonraker", "start"),
                                            ("multiace", "start")])
        self.assertFalse((self.handshake / "request.json").exists())
        self.assertEqual(service.STATE["state"], "error")

    def test_restore_waits_for_background_service_start(self):
        controls = service.UpgradeControls()
        controls.original = {"moonraker": self.original["moonraker"]}
        controls.touched = ["moonraker"]
        # First lookup decides to start it; two subsequent lookups happen
        # before the background daemon has published its final pid/cmdline.
        with (patch.object(service, "service_process", side_effect=[
                None, None, ValueError("控制服务进程与 PID 不匹配：moonraker"),
                self.original["moonraker"]]) as process,
              patch.object(service, "control_service") as control,
              patch.object(service.time, "sleep") as sleep):
            controls.restore()
        control.assert_called_once_with("moonraker", "start")
        self.assertEqual(process.call_count, 4)
        self.assertEqual(sleep.call_count, 2)

    def test_restore_waits_for_terminating_process_before_restart(self):
        controls = service.UpgradeControls()
        controls.original = {"moonraker": self.original["moonraker"]}
        controls.touched = ["moonraker"]
        with (patch.object(service, "service_process", side_effect=[
                ValueError("控制服务进程与 PID 不匹配：moonraker"),
                None, self.original["moonraker"]]) as process,
              patch.object(service, "process_identity", side_effect=[
                ("tick", ""), None]) as identity,
              patch.object(service, "control_service") as control,
              patch.object(service.time, "sleep") as sleep):
            controls.restore()
        self.assertEqual(identity.call_count, 2)
        self.assertEqual(process.call_count, 3)
        sleep.assert_called_once_with(0.05)
        control.assert_called_once_with("moonraker", "start")

    def test_restore_start_timeout_retains_recovery_error(self):
        def reject(token):
            raise ValueError("slot operation became active")
        self.mocks(reject)
        # The existing restore path must report recovery_required when a
        # restarted process never appears, preserving the startup error.
        with (patch.object(service, "wait_service_start", side_effect=ValueError(
                "控制服务没有运行：moonraker")),
              patch.object(service.subprocess, "Popen") as popen):
            service.run_u1(self.package)
        popen.assert_not_called()
        self.assertEqual(service.STATE["state"], "recovery_required")
        self.assertIn("控制服务没有运行：moonraker", service.STATE["error"])

    def test_service_start_wait_is_bounded_and_preserves_failure(self):
        failure = ValueError("控制服务没有运行：moonraker")
        with (patch.object(service, "service_process", side_effect=failure) as process,
              patch.object(service.time, "monotonic", side_effect=[100, 100, 106]),
              patch.object(service.time, "sleep") as sleep,
              self.assertRaisesRegex(ValueError, "控制服务没有运行：moonraker")):
            service.wait_service_start("moonraker")
        self.assertEqual(process.call_count, 2)
        sleep.assert_called_once_with(0.05)

    def test_installer_failure_does_not_restart_or_release_controls(self):
        self.mocks()
        with patch.object(service.subprocess, "Popen", return_value=self.fake_process(1)):
            service.run_u1(self.package)
        self.assertEqual(service.STATE["state"], "recovery_required")
        self.assertFalse(any(action == "start" for _, action in self.events))
        self.assertTrue((self.handshake / "request.json").exists())
        with self.assertRaises(ValueError):
            service.sync_spoolman({})

    def test_hash_finishes_before_live_idle_check(self):
        def hashed(path):
            self.events.append(("hash", "finished"))
            return service.PREPARED["sha256"]
        def now_busy():
            self.assertEqual(self.events[-1], ("hash", "finished"))
            raise ValueError("slot became busy during package hash")
        with (patch.object(service, "sha256_file", side_effect=hashed),
              patch.object(service, "machine_idle", side_effect=now_busy),
              patch.object(service.threading, "Thread") as worker,
              self.assertRaisesRegex(ValueError, "slot became busy")):
            service.start({"token": "confirmation"})
        worker.assert_not_called()

    def test_active_installer_excludes_all_companion_mutations(self):
        entered, release = threading.Event(), threading.Event()
        self.mocks()
        proc = self.fake_process()
        def lines():
            entered.set()
            if not release.wait(5):
                raise AssertionError("test did not release installer")
            yield "upgrade soc finish, prepare to reboot\n"
        proc.stdout = Mock()
        proc.stdout.__iter__ = Mock(side_effect=lines)
        with patch.object(service.subprocess, "Popen", return_value=proc):
            worker = threading.Thread(target=service.run_u1, args=(self.package,))
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                for action in (lambda: service.sync_spoolman({}),
                               lambda: service.update_firmware_setting({}),
                               lambda: service.prepare({}),
                               lambda: service.start({"token": "confirmation"})):
                    with self.assertRaisesRegex(ValueError, "另一项设备操作"):
                        action()
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertTrue(service.LOCK.acquire(blocking=False))
        service.LOCK.release()


if __name__ == "__main__":
    unittest.main()
