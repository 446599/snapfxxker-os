"""Durable route geometry and motion ownership for managed ACE paths."""

import copy
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


SCHEMA_VERSION = 1
REFERENCE = {"type": "managed_insertion", "version": 1}
MAX_FILE_BYTES = 2097152
MAX_ACTIONS = 4096


class PathStateError(RuntimeError):
    pass


def configured_route_heads(ace, idx, slot):
    """Resolve plumbing by mode, independently of a load's cached source."""
    if type(idx) is not int or idx < 0 or type(slot) is not int or not 0 <= slot < 4:
        return ()
    mode = getattr(ace, '_ace_mode', None)
    if mode in ('multi', 'normal'):
        return (slot,) if ace.head_uses_ace(slot) else ()
    if mode == 'head':
        return tuple(head for head in range(4)
                     if ace.head_uses_ace(head) and ace.head_ace_for(head) == idx)
    return ()


def route_key(uid, slot):
    if (not isinstance(uid, (list, tuple)) or len(uid) != 3
            or any(type(value) is not int or not 0 <= value <= 0xffffffff
                   for value in uid)
            or type(slot) is not int or not 0 <= slot < 4):
        raise PathStateError("设备和槽位身份无效")
    return "-".join(str(value) for value in uid) + "/" + str(slot)


def finite_mm(value, name, minimum=0, maximum=5000):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or not minimum <= value <= maximum):
        raise PathStateError(name + "超出有效范围")
    return float(value)


def action_fingerprint(parameters):
    raw = json.dumps(parameters, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def empty_state():
    return {"schema_version": SCHEMA_VERSION, "units": "mm",
            "reference": dict(REFERENCE), "revision": 0, "routes": {},
            "occupied_heads": {}, "task": None, "actions": {}}


class PathStore:
    """Serialize bounded durable commits outside Klipper's reactor thread.

    The reactor owns the current snapshot. A single worker writes immutable
    candidates. Callers must await a successful commit before authorizing
    motion, and retain the preceding ownership record until terminal proof.
    """

    def __init__(self, path=None):
        directory = os.environ.get("MULTIACE_CONFIG_DIR",
                                   "/home/lava/printer_data/config")
        self.path = Path(path or Path(directory) / "persistent" /
                         "multiace-managed-paths.json")
        self.state = empty_state()
        self.error = None
        self.pending = None
        self.executor = ThreadPoolExecutor(max_workers=1,
                                           thread_name_prefix="ace-path-state")
        try:
            self.state = self._read()
        except (OSError, ValueError, TypeError, PathStateError) as exc:
            self.error = "管路持久状态需要核对 " + str(exc)

    def _read(self):
        try:
            descriptor = os.open(self.path, os.O_RDONLY |
                                 getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            return empty_state()
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_size > MAX_FILE_BYTES):
                raise PathStateError("文件身份或大小无效")
            raw = stream.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise PathStateError("状态文件过大")
        data = json.loads(raw)
        json.dumps(data, allow_nan=False)
        self.validate(data)
        return data

    @staticmethod
    def validate(data):
        if (not isinstance(data, dict)
                or data.get("schema_version") != SCHEMA_VERSION
                or data.get("units") != "mm"
                or data.get("reference") != REFERENCE
                or type(data.get("revision")) is not int
                or data["revision"] < 0
                or any(not isinstance(data.get(key), dict)
                       for key in ("routes", "occupied_heads", "actions"))
                or len(data["actions"]) > MAX_ACTIONS
                or (data.get("task") is not None
                    and not isinstance(data["task"], dict))):
            raise PathStateError("状态格式无效")
        if data.get("task") is not None and "group_ports" in data["task"]:
            ports = data["task"]["group_ports"]
            if (not isinstance(ports, dict) or any(key not in ("0", "1", "2", "3")
                    or not isinstance(port, str) or not port for key, port in ports.items())):
                raise PathStateError("共享管路设备记录无效")
        for key, route in data["routes"].items():
            if (not isinstance(route, dict)
                    or key != route_key(route.get("uid"), route.get("slot"))
                    or type(route.get("head")) is not int
                    or not 0 <= route["head"] < 4
                    or not isinstance(route.get("topology_version"), str)
                    or not route["topology_version"]):
                raise PathStateError("管路绑定无效")
            geometry = route.get("geometry")
            if geometry is not None:
                PathStore.validate_geometry(geometry)
            basic = route.get("basic")
            if basic is not None:
                if not isinstance(basic, dict) or basic.get("reference") != REFERENCE:
                    raise PathStateError("基础停放参考无效")
                length = finite_mm(basic.get("length_mm"), "基础测长", minimum=1)
                grip = finite_mm(basic.get("grip_mm"), "基础抓料位置")
                if (grip >= length or type(basic.get("boot_epoch")) is not int
                        or basic["boot_epoch"] <= 0
                        or type(basic.get("insertion_epoch")) is not int
                        or basic["insertion_epoch"] <= 0):
                    raise PathStateError("基础停放与插入代次不一致")
                speed = basic.get("reference_speed_mm_s", 80)
                if type(speed) is not int or not 1 <= speed <= 100:
                    raise PathStateError("基础到料参考速度无效")
            commissioning = route.get("commissioning")
            if commissioning is not None:
                if (not isinstance(commissioning, dict)
                        or commissioning.get("reference") != REFERENCE
                        or not isinstance(commissioning.get("evidence_sha256"), str)
                        or not re.fullmatch(r"[0-9a-f]{64}", commissioning["evidence_sha256"])):
                    raise PathStateError("入料基准实测证据无效")
                finite_mm(commissioning.get("reference_uncertainty_mm"), "入料基准误差")
                finite_mm(commissioning.get("stop_uncertainty_mm"), "停止误差")
        for head, owner in data["occupied_heads"].items():
            if (head not in ("0", "1", "2", "3")
                    or not isinstance(owner, dict)
                    or owner.get("route_id") not in data["routes"]
                    or not isinstance(owner.get("task_id"), str)
                    or not owner["task_id"]):
                raise PathStateError("公共段占用记录无效")
            if data["routes"][owner["route_id"]]["head"] != int(head):
                raise PathStateError("公共段与管路喷头不匹配")
        for key, action in data["actions"].items():
            if (not isinstance(key, str) or not key
                    or not isinstance(action, dict)
                    or not isinstance(action.get("fingerprint"), str)
                    or len(action["fingerprint"]) != 64
                    or not isinstance(action.get("task_id"), str)
                    or action.get("state") not in
                    ("accepted", "running", "complete", "error")):
                raise PathStateError("动作回执无效")

    @staticmethod
    def validate_geometry(geometry):
        if (not isinstance(geometry, dict)
                or geometry.get("reference") != REFERENCE):
            raise PathStateError("长度参考无效")
        speed = geometry.get("reference_speed_mm_s", 80)
        if type(speed) is not int or not 1 <= speed <= 100:
            raise PathStateError("喷头停止参考速度无效")
        length = finite_mm(geometry.get("length_mm"), "测长", minimum=1)
        retraction = finite_mm(geometry.get("return_mm"), "回退长度")
        park = finite_mm(geometry.get("park_mm"), "停车位置")
        margin = finite_mm(geometry.get("margin_mm"), "停车间隙", minimum=0.1)
        uncertainty = finite_mm(geometry.get("uncertainty_mm"), "位置误差")
        if (abs(length - retraction - park) > 0.01
                or not 0 < park < length or margin <= uncertainty or retraction < margin):
            raise PathStateError("停车位置与误差预算不一致")

    def snapshot(self):
        return copy.deepcopy(self.state)

    def check_action(self, action_id, task_id, parameters):
        if self.error:
            raise PathStateError(self.error)
        if (not isinstance(action_id, str) or not 1 <= len(action_id) <= 96
                or not isinstance(task_id, str) or not 1 <= len(task_id) <= 96):
            raise PathStateError("任务或动作标识无效")
        previous = self.state["actions"].get(action_id)
        if previous is None:
            return None
        if (previous["task_id"] != task_id
                or previous["fingerprint"] != action_fingerprint(parameters)):
            raise PathStateError("动作标识已用于另一请求")
        return copy.deepcopy(previous)

    def submit(self, candidate):
        if self.error:
            raise PathStateError(self.error)
        if self.pending is not None:
            raise PathStateError("正在保存上一个稳定状态")
        candidate = copy.deepcopy(candidate)
        candidate["revision"] = self.state["revision"] + 1
        self.validate(candidate)
        raw = (json.dumps(candidate, sort_keys=True, ensure_ascii=False,
                          allow_nan=False) + "\n").encode()
        if len(raw) > MAX_FILE_BYTES:
            raise PathStateError("状态文件容量不足")
        future = self.executor.submit(self._write, raw, self.state["revision"])
        self.pending = (future, candidate)
        return future

    def finish(self):
        if self.pending is None:
            return False
        future, candidate = self.pending
        if not future.done():
            return False
        self.pending = None
        try:
            future.result()
        except (OSError, PathStateError) as exc:
            self.error = "管路状态保存失败 " + str(exc)
            raise PathStateError(self.error) from exc
        self.state = candidate
        return True

    def _write(self, raw, expected_revision):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock = os.open(str(self.path) + ".lock", os.O_RDWR | os.O_CREAT |
                       getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            info = os.fstat(lock)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise PathStateError("管路状态锁文件无效")
            deadline = time.monotonic() + 1.0
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise PathStateError("管路状态锁等待超时")
                    time.sleep(0.01)
            if self._read()["revision"] != expected_revision:
                raise PathStateError("管路持久状态已由其他进程修改")
            self._replace(raw)
        finally:
            os.close(lock)

    def _replace(self, raw):
        descriptor, temporary = tempfile.mkstemp(prefix=self.path.name + ".",
                                                  dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            descriptor = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
