# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the Klipper upgrade handoff with no hardware or service commands."""

import copy
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "overlays/233-native-ui/root"
sys.path.insert(0, str(OVERLAY / "usr/local/lib/u1-device-service"))
spec = importlib.util.spec_from_file_location(
    "u1_native_upgrade_tests",
    OVERLAY / "home/lava/klipper/klippy/extras/u1_native_upgrade.py",
)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

TOKEN = "b7" * 24
IDLE = {
    "webhooks": {"state": "ready"},
    "print_stats": {"state": "standby"},
    "idle_timeout": {"state": "Ready"},
    "heaters": {"available_heaters": ["extruder", "heater_bed"]},
    "extruder": {"target": 0, "power": 0},
    "heater_bed": {"target": 0, "power": 0},
}


class RequestError(Exception):
    pass


class Request:
    def __init__(self, token=TOKEN):
        self.token = token

    def get_str(self, name):
        assert name == "token"
        return self.token

    error = RequestError


class Mutex:
    def __init__(self):
        self.locked = False

    def test(self):
        return self.locked

    def __enter__(self):
        if self.locked:
            raise AssertionError("upgrade must reject, never wait for the mutex")
        self.locked = True

    def __exit__(self, *args):
        self.locked = False


class Object:
    def __init__(self, value):
        self.value = value

    def get_status(self, eventtime):
        return self.value


class Printer:
    def __init__(self, statuses):
        self.objects = {name: Object(value) for name, value in statuses.items()}
        self.mutex = Mutex()
        self.objects["gcode"] = type("GCode", (), {"get_mutex": lambda obj: self.mutex})()
        self.objects["webhooks"].register_endpoint = self.register_endpoint
        self.endpoints = {}

    def register_endpoint(self, name, handler):
        self.endpoints[name] = handler

    def get_printer(self):
        return self

    def get_reactor(self):
        return self

    def monotonic(self):
        return 123.0

    def pause(self, *args):
        raise AssertionError("the final handoff must not yield")

    def lookup_object(self, name):
        return self.objects[name]

    def lookup_objects(self):
        return list(self.objects.items())


class UpgradeQuiesceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.request = self.tmp / "request.json"
        self.receipt = self.tmp / "receipt.json"
        self.request.write_text(json.dumps({"token": TOKEN, "pid": os.getpid()}))
        self.request.chmod(0o644)
        self.receipt.write_text("")
        self.receipt.chmod(0o600)
        self.printer = Printer(copy.deepcopy(IDLE))
        self.module = guard.U1NativeUpgrade(self.printer)
        self.real_fstat = os.fstat
        self.request_inode = self.request.stat().st_ino
        for setting, value in [("REQUEST_FILE", self.request), ("RECEIPT_FILE", self.receipt)]:
            item = patch.object(guard, setting, value)
            item.start()
            self.addCleanup(item.stop)
        # Tests run as an ordinary host user. Only emulate the request file's
        # root ownership; all path/type/mode checks and file IO remain real.
        item = patch.object(guard.os, "fstat", self.root_request_stat)
        item.start()
        self.addCleanup(item.stop)

    def root_request_stat(self, fd):
        result = self.real_fstat(fd)
        if result.st_ino == self.request_inode:
            values = list(result)
            values[4] = 0
            return os.stat_result(values)
        return result

    def assert_rejected(self, message):
        with patch.object(guard.os, "_exit") as terminate:
            with self.assertRaisesRegex(RequestError, message):
                self.module.prepare(Request())
            terminate.assert_not_called()
        self.assertEqual(self.receipt.read_text(), "")

    def test_rejects_live_macro_without_waiting(self):
        self.printer.mutex.locked = True
        self.assert_rejected("命令正在执行")
        self.assertTrue(self.printer.mutex.locked)

    def test_discovers_all_heaters_and_rejects_missing_or_active_heater(self):
        self.printer.objects["heaters"].value["available_heaters"].append("heater_generic chamber")
        self.assert_rejected("无法确认打印机状态")
        self.assertFalse(self.printer.mutex.locked)
        self.printer.objects["heater_generic chamber"] = Object({"target": 40, "power": 0})
        self.assert_rejected("关闭所有")
        self.assertFalse(self.printer.mutex.locked)

    def test_rejects_material_operation_in_final_reactor_snapshot(self):
        self.printer.objects["ace"] = Object({"status": "ready", "swap_in_progress": True})
        self.assert_rejected("结束耗材操作")
        self.assertFalse(self.printer.mutex.locked)

    def test_rejects_stale_token_or_pid(self):
        self.request.write_text(json.dumps({"token": "c2" * 24, "pid": os.getpid()}))
        self.assert_rejected("确认已过期")
        self.request.write_text(json.dumps({"token": TOKEN, "pid": os.getpid() + 1}))
        self.assert_rejected("确认已过期")

    def test_rejects_writable_lease_and_symlink_receipt(self):
        self.request.chmod(0o666)
        self.assert_rejected("确认文件权限")
        self.request.chmod(0o644)
        target = self.tmp / "untouched"
        target.write_text("keep")
        self.receipt.unlink()
        self.receipt.symlink_to(target)
        with patch.object(guard.os, "_exit") as terminate:
            with self.assertRaises(RequestError):
                self.module.prepare(Request())
            terminate.assert_not_called()
        self.assertEqual(target.read_text(), "keep")
        self.assertFalse(self.printer.mutex.locked)

    def test_receipt_failure_releases_mutex_without_exit(self):
        self.receipt.chmod(0o644)
        self.assert_rejected("回执文件权限")
        self.assertFalse(self.printer.mutex.locked)

    def test_unexpected_status_exception_releases_mutex_and_keeps_printer_running(self):
        heater = self.printer.objects["heater_bed"]
        with patch.object(heater, "get_status", side_effect=RuntimeError("status unavailable")):
            self.assert_rejected("status unavailable")
        self.assertFalse(self.printer.mutex.locked)
        with self.printer.mutex:
            self.assertTrue(self.printer.mutex.locked)
        self.assertFalse(self.printer.mutex.locked)

    def test_real_child_exit_prevents_pending_timer_and_writes_bound_receipt(self):
        after = self.tmp / "timer-ran"

        def child():
            self.request.write_text(json.dumps({"token": TOKEN, "pid": os.getpid()}))
            self.module.prepare(Request())
            # A normal reactor request_exit() can return to another timer in
            # this batch. This code must never run after successful admission.
            after.write_text("would start automatic drying")

        process = multiprocessing.get_context("fork").Process(target=child)
        process.start()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join()
            self.fail("quiescence did not terminate the child")
        self.assertEqual(process.exitcode, 0)
        self.assertFalse(after.exists())
        self.assertEqual(json.loads(self.receipt.read_text()), {
            "accepted": True, "token": TOKEN, "pid": process.pid,
        })


class UpgradeHookTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.lease = self.tmp / "request.json"
        self.config = self.tmp / "extended/klipper"

    def run_hook(self, service, action):
        source = (OVERLAY / "etc/hooks" / (service + ".d") /
                  "30-u1-native-upgrade.sh").read_text()
        # Only filesystem destinations are redirected. No real init script,
        # service control, printer configuration or hardware is executed.
        source = source.replace("/run/u1-native-upgrade/request.json", str(self.lease))
        source = source.replace("/oem/printer_data/config/extended/klipper", str(self.config))
        script = self.tmp / (service + "-hook.sh")
        script.write_text(source)
        return subprocess.run(["sh", str(script), action], capture_output=True, text=True)

    def test_loading_hook_creates_only_its_config_and_preserves_existing_section(self):
        result = self.run_hook("klipper", "start")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = self.config / "u1_native_upgrade.cfg"
        self.assertIn("[u1_native_upgrade]", config.read_text())
        self.assertEqual(config.stat().st_mode & 0o777, 0o644)
        custom = config.read_text() + "# retain operator note\n"
        config.write_text(custom)
        self.assertEqual(self.run_hook("klipper", "start").returncode, 0)
        self.assertEqual(config.read_text(), custom)

    def test_lease_blocks_service_restarts_but_allows_stop_and_explicit_recovery(self):
        self.lease.write_text(json.dumps({"token": TOKEN, "pid": 123}))
        for service in ("klipper", "moonraker"):
            for action in ("start", "restart"):
                with self.subTest(service=service, action=action):
                    result = self.run_hook(service, action)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("upgrade owns the device", result.stderr)
            self.assertEqual(self.run_hook(service, "stop").returncode, 0)
        self.assertFalse(self.config.exists())
        self.lease.unlink()  # Companion only does this before installer launch.
        for service in ("klipper", "moonraker"):
            self.assertEqual(self.run_hook(service, "start").returncode, 0)


if __name__ == "__main__":
    unittest.main()
