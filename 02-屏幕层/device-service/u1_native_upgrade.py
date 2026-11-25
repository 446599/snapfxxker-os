# SPDX-License-Identifier: GPL-3.0-or-later
"""Atomic idle-check/exit handoff for the native U1 full-system upgrader.

This endpoint runs on Klipper's reactor. It never yields between the final
status snapshot and process exit. A normal request_exit() alone would still
allow the reactor's current batch of timers to run (including ACE auto-dry).
The upgrade-specific hard exit deliberately skips disconnect cleanup; it is
only admitted with no motion, material operation, heater power or drying.
The companion must verify both the receipt and this process's exit before
starting the vendor installer, which owns MCU power and reboot handling.
"""

import json
import os
from pathlib import Path
import re
import stat
import sys

sys.path.insert(0, "/usr/local/lib/u1-device-service")
from idle_checks import OPTIONAL_OBJECTS, REQUIRED_OBJECTS, require_machine_idle


REQUEST_FILE = Path("/run/u1-native-upgrade/request.json")
RECEIPT_FILE = Path("/run/u1-native-upgrade/receipt.json")


class U1NativeUpgrade:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.printer.lookup_object("webhooks").register_endpoint(
            "u1_upgrade/prepare", self.prepare
        )

    def authorize(self, token):
        if not re.fullmatch(r"[0-9a-f]{48,128}", token):
            raise ValueError("升级确认令牌无效")
        # Root creates the request only after the UI's prepared-package
        # confirmation. A remotely called endpoint cannot invent its own lease.
        fd = os.open(str(REQUEST_FILE), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "r") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                    or info.st_mode & 0o022):
                raise ValueError("升级确认文件权限无效")
            request = json.load(stream)
        if (not isinstance(request, dict) or request.get("token") != token
                or request.get("pid") != os.getpid()):
            raise ValueError("升级确认已过期，请重新校验固件")

    def snapshot(self):
        # QueryStatusHelper intentionally yields between objects; collect the
        # fixed integration's get_status() values directly in this callback.
        objects = dict(self.printer.lookup_objects())
        names = REQUIRED_OBJECTS | (OPTIONAL_OBJECTS & objects.keys())
        names |= {name for name in objects if name.startswith("filament_feed ")}
        eventtime = self.reactor.monotonic()

        def status(name):
            obj = objects.get(name)
            if obj is None or not callable(getattr(obj, "get_status", None)):
                raise ValueError("无法确认打印机状态：" + str(name))
            result = obj.get_status(eventtime)
            if not isinstance(result, dict):
                raise ValueError("打印机状态格式无效：" + str(name))
            return result

        result = {name: status(name) for name in sorted(names)}
        heaters = result["heaters"].get("available_heaters")
        if not isinstance(heaters, list) or any(not isinstance(name, str) for name in heaters):
            raise ValueError("无法确认加热器列表")
        for name in heaters:
            result[name] = status(name)
        return result

    def write_receipt(self, token):
        # Root owns the parent directory and pre-creates this single regular
        # file for the Klipper uid. Never accept a caller-selected write path.
        fd = os.open(str(RECEIPT_FILE), os.O_WRONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "w") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError("升级回执文件权限无效")
            stream.seek(0)
            stream.truncate()
            json.dump({"accepted": True, "token": token, "pid": os.getpid()}, stream)
            stream.flush()
            os.fsync(stream.fileno())

    def prepare(self, web_request):
        mutex = self.gcode.get_mutex()
        locked = False
        try:
            token = web_request.get_str("token")
            self.authorize(token)
            # Do not wait behind an in-flight macro and then unexpectedly
            # upgrade when it finishes; return the busy result immediately.
            if mutex.test():
                raise ValueError("设备命令正在执行，请结束后重试升级")
            mutex.__enter__()
            locked = True
            require_machine_idle(self.snapshot())
            self.write_receipt(token)
        except Exception as exc:
            if locked:
                mutex.__exit__(None, None, None)
            raise web_request.error(str(exc))
        # Do not return to the timer loop, nor resume queued G-code. A socket
        # EOF is expected: receipt + PID exit is the acknowledgement contract.
        os._exit(0)


def load_config(config):
    return U1NativeUpgrade(config)
