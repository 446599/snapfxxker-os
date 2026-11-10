#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SNMK format/data map follow tools/upfile by @paxx12 and @james-coder (2025).
"""Loopback companion for the U1 native screen. Uses existing multiACE and U1 installers."""

import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import struct
import subprocess
import threading
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DECODE = bytes.fromhex(
    "00a2e90ede64ee12463be779a5805533323c0b7ececc5937019b4ec1ab1872119c5be58de80d699729d9c5af4a617a6339c6bcd092ae7fdd6c073ab991f7c7453440b52844e6cb4d3d9e942b60d22f3e67d652d3a8d4f81adaec707ca01d064f6e2db836f04322bdb4829deba45676e425159871c350497708fb4723aa8a20fcf68c85301ec962ba530ff257517b131f96c0357387dbb7a3ed905f9adce3e10a1b17ea41fe5826cd05c202886a9f9374e22a09ac815c1cf38bfa84a1d75dbe7538e0a62c2e6686ef5468b6a9f50cf9b3bf14b2fdbbd504f4a748f16d6b997d4bb0ad2195d1cfc424ca8eb18316df1910274c6f313f5a6578c8d803895e8f42ff"
)
ENCODE = bytes(DECODE.index(i) for i in range(256))
ROOTS = [Path(p) for p in ("/media", "/mnt", "/run/media", "/userdata/firmware")]
STORE = Path("/userdata/u1-interface/firmware")
INSTALLER = Path("/home/lava/bin/systemUpgrade.sh")
LOCK = threading.Lock()
STATE_LOCK = threading.RLock()
STATE = {"state": "idle", "pct": 0, "msg": "请选择本地固件", "error": ""}
PREPARED = {}
ACTIVE_PROCESS = None
BUSY = {"validating", "flashing", "reboot_pending"}

# The installed PAXX server is authoritative for available settings and values.
# These tables only arrange and translate the native screen's presentation.
PAXX_GROUPS = {
    "printing": "打印与调平",
    "materials": "耗材与识别",
    "camera": "摄像与录像",
    "motion": "运动与电机",
    "remote": "网络与远程",
    "interface": "界面与扩展",
    "maintenance": "维护与诊断",
    "updates": "固件更新",
}
PAXX_SOURCE_GROUPS = {
    "print_preferences": "printing",
    "camera": "camera",
    "tweaks": "motion",
    "remote_access": "remote",
    "web": "interface",
    "snapmaker_components": "interface",
    "troubleshooting": "maintenance",
    "monitoring_debugging": "maintenance",
}
PAXX_LABELS = {
    "bed_leveling_force": ("printing", "每次打印前调平"),
    "timelapse_force": ("printing", "强制延时摄影"),
    "object_processing": ("printing", "模型对象处理"),
    "rfid": ("materials", "耗材标签识别"),
    "spoolman": ("materials", "Spoolman 耗材库"),
    "spoolman_tag_store": ("materials", "耗材标签关联方式"),
    "filament_feeders": ("materials", "送料器配置"),
    "afc": ("materials", "AFC Lite 耗材管理"),
    "ace": ("materials", "multiACE 集成"),
    "multiace-upgrade": ("materials", "更新 multiACE 组件"),
    "camera-internal": ("camera", "内置摄像头"),
    "camera-internal-resolution": ("camera", "内置摄像头帧率"),
    "camera-usb": ("camera", "USB 摄像头"),
    "camera-usb-resolution": ("camera", "USB 摄像头画质"),
    "camera-rtsp": ("camera", "RTSP 视频流"),
    "max_speed": ("motion", "运动速度上限"),
    "tmc_autotune": ("motion", "TMC 驱动调优"),
    "tmc_reduce_current": ("motion", "降低 XY 电机电流"),
    "ssh": ("remote", "SSH 访问"),
    "vpn": ("remote", "Tailscale 远程连接"),
    "cloud": ("remote", "云端远程访问"),
    "remote-screen": ("remote", "远程屏幕"),
    "authorization": ("remote", "网页登录验证"),
    "web-frontend": ("interface", "网页界面"),
    "gui": ("interface", "触摸屏界面"),
    "firmware-config": ("interface", "PAXX 配置服务"),
    "chamber_heater": ("interface", "外接舱温加热器"),
    "klipper-metrics": ("maintenance", "运行指标导出"),
    "firmware-upgrade-channel": ("updates", "固件更新通道"),
    **{"toolhead%d" % i: ("maintenance", "工具头 %d 故障处理" % i)
       for i in range(1, 5)},
}
PAXX_OPTION_LABELS = {
    "Enabled": "启用", "Disabled": "关闭", "None": "关闭", "Normal": "正常",
    "External": "外部读卡器", "Snapmaker": "Snapmaker 原厂",
    "Force Full Mesh": "每次全床调平", "Force Adaptive Mesh": "每次自适应调平",
    "OpenRFID (force generic vendor)": "OpenRFID（通用品牌）",
    "Enabled (Force Generic Vendor)": "启用（通用品牌）",
    "Balanced": "均衡（实验）", "Aggressive": "激进（实验）",
    "Default": "默认送料器", "Stable (recommended)": "稳定版",
    "Testing (pre-releases)": "测试版", "Develop (bleeding edge)": "开发版",
    "Bypass Thermistor": "隔离故障温度传感器",
    "Bypass MCU (Disconnected)": "隔离断开的工具头主板",
    "Panda Breath (Manual mode)": "Panda Breath（手动）",
    "DragonBreath (Recommended)": "DragonBreath（推荐）",
    "Card UIDs": "标签 UID 字段", "Spoolman native tags": "Spoolman 原生标签",
}
PAXX_INPUT_LABELS = {
    "SPOOLMAN_URL": "Spoolman 服务器地址",
    "PANDA_BREATH_IP": "Panda Breath IP 地址",
    "DRAGONBREATH_IP": "DragonBreath IP 地址",
    "ACKNOWLEDGED": "输入 I UNDERSTAND 确认已阅读说明并理解风险",
}


def paxx_auth_headers():
    headers = {}
    if os.environ.get("U1_PAXX_API_KEY"):
        headers["X-Api-Key"] = os.environ["U1_PAXX_API_KEY"]
    if os.environ.get("U1_PAXX_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["U1_PAXX_TOKEN"]
    return headers


def paxx_request(path, payload=None):
    """Use the authenticated nginx route, never the private 9091 backend."""
    base = os.environ.get(
        "U1_PAXX_API_URL", "http://127.0.0.1/firmware-config/api"
    ).rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme not in ("http", "https")
            or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
            or parsed.path != "/firmware-config/api"
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("PAXX 地址必须是本机 nginx 的 /firmware-config/api")
    headers = {"Accept": "application/json", **paxx_auth_headers()}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        base + "/" + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers=headers,
        method="GET" if payload is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20 if payload is None else 180) as response:
            body = response.read().decode("utf-8", errors="replace")
            if payload is not None:
                return body
            try:
                return json.loads(body)
            except ValueError as exc:
                raise ValueError("PAXX 返回了无效数据，请检查高级模式与登录状态") from exc
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ValueError("PAXX 需要登录，请配置有效的 API Key 或访问令牌") from exc
        raise ValueError("PAXX 请求失败（HTTP %d）" % exc.code) from exc
    except (OSError, http.client.HTTPException) as exc:
        raise ConnectionError("无法连接 PAXX，请确认高级模式、配置服务与网络状态") from exc


def firmware_settings():
    upstream = paxx_request("settings")
    if not isinstance(upstream, dict):
        raise ValueError("PAXX 设置数据格式不正确")
    settings = []
    used_groups = set()
    for source_group, source in upstream.items():
        if not isinstance(source, dict) or not isinstance(source.get("items"), list):
            raise ValueError("PAXX 设置分组格式不正确")
        for item in source["items"]:
            if not isinstance(item, dict) or not isinstance(item.get("options"), dict):
                raise ValueError("PAXX 设置选项格式不正确")
            setting_id = item.get("id")
            if not isinstance(setting_id, str):
                raise ValueError("PAXX 设置缺少标识")
            group, label = PAXX_LABELS.get(setting_id, (
                PAXX_SOURCE_GROUPS.get(source_group, "interface"),
                item.get("label", setting_id),
            ))
            options = []
            for value, raw_option in item["options"].items():
                if not isinstance(raw_option, dict):
                    raise ValueError("PAXX 设置选项格式不正确")
                raw_label = raw_option.get("label", str(value))
                inputs = []
                for raw_input in raw_option.get("inputs", []):
                    field = dict(raw_input)
                    field.setdefault("type", "text")
                    field.setdefault("required", True)
                    field["source_label"] = field.get("label", field.get("name", ""))
                    field["label"] = PAXX_INPUT_LABELS.get(field.get("name"), field["source_label"])
                    inputs.append(field)
                options.append({
                    **raw_option,
                    "value": str(value),
                    "source_label": raw_label,
                    "label": PAXX_OPTION_LABELS.get(raw_label, raw_label),
                    "confirm": raw_option.get("confirm") or "",
                    "hidden": bool(raw_option.get("hidden", False)),
                    "inputs": inputs,
                })
            settings.append({
                "id": setting_id, "group": group, "source_group": source_group,
                "label": label, "source_label": item.get("label", setting_id),
                "description": item.get("description") or "",
                "help_url": item.get("help_url") or "",
                "current": str(item.get("current", "")),
                "hidden": bool(item.get("hidden", False)),
                # PAXX does not expose restart impact in its settings schema.
                "requires_idle": True, "options": options,
            })
            if not item.get("hidden", False):
                used_groups.add(group)
    return {
        "available": True, "source": "paxx",
        "groups": [{"id": group, "label": label}
                   for group, label in PAXX_GROUPS.items() if group in used_groups],
        "settings": settings,
    }


def firmware_setting(data, setting_id):
    return next((item for item in data["settings"] if item["id"] == setting_id), None)


def update_firmware_setting(payload):
    if not isinstance(payload, dict):
        raise ValueError("设置请求必须为 JSON 对象")
    if not LOCK.acquire(blocking=False):
        raise ValueError("另一项设备操作正在进行，请等待完成")
    try:
        if STATE["state"] in BUSY:
            raise ValueError("固件升级正在进行")
        setting = firmware_setting(firmware_settings(), payload.get("id"))
        if setting is None or setting["hidden"]:
            raise ValueError("当前固件没有开放此设置，请刷新页面")
        selected = next((option for option in setting["options"]
                         if option["value"] == payload.get("value") and not option["hidden"]), None)
        if selected is None:
            raise ValueError("当前固件不支持此选项，请刷新页面")
        if selected["confirm"] and payload.get("confirm") is not True:
            raise ValueError("请先阅读并确认此选项的说明")
        inputs = payload.get("inputs", {})
        if not isinstance(inputs, dict):
            raise ValueError("额外设置必须为 JSON 对象")
        field_names = {field["name"] for field in selected["inputs"]}
        if set(inputs) - field_names:
            raise ValueError("包含此选项不接受的输入字段")
        for field in selected["inputs"]:
            value = inputs.get(field["name"], "")
            if not isinstance(value, str) or not value:
                raise ValueError("请填写：" + field["label"])
            if field.get("regex") and not re.match(field["regex"], value):
                raise ValueError("输入格式不正确：" + field["label"])
        machine_idle()
        path = "settings/%s/%s" % (quote(setting["id"], safe=""), quote(selected["value"], safe=""))
        try:
            output = paxx_request(path, inputs)
        except ConnectionError:
            return {"ok": False, "state": "unverified", "id": setting["id"],
                    "message": "连接已中断，尚未确认设置结果；请重连后刷新，勿重复提交",
                    "output": ""}
        errors = [line.strip() for line in output.splitlines()
                  if re.match(r"\s*(?:ERROR\b|Error:|Traceback \(most recent call last\))", line)]
        if errors:
            return {"ok": False, "state": "failed", "id": setting["id"],
                    "error": errors[-1], "message": "PAXX 未能应用此设置", "output": output}
        if "SUCCESS: Setting updated successfully" not in output.splitlines():
            return {"ok": False, "state": "unverified", "id": setting["id"],
                    "message": "PAXX 未返回完成结果，请刷新确认当前设置", "output": output}
        try:
            updated = firmware_setting(firmware_settings(), setting["id"])
        except (ConnectionError, ValueError):
            updated = None
        if updated is None or updated["current"] != selected["value"]:
            return {"ok": False, "state": "unverified", "id": setting["id"],
                    "current": updated["current"] if updated else None,
                    "message": "操作已返回，等待重连后确认实际生效值", "output": output}
        return {"ok": True, "state": "applied", "id": setting["id"],
                "current": updated["current"], "setting": updated,
                "message": "设置已应用并读回确认", "output": output}
    finally:
        LOCK.release()


def json_request(url, data=None, method=None, headers=None):
    req = urllib.request.Request(
        url,
        data=None if data is None else json.dumps(data).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("detail", str(exc))
        except (ValueError, AttributeError):
            detail = str(exc)
        raise ValueError(str(detail)) from exc


def web(path, data=None, method=None):
    return json_request("http://127.0.0.1:7126/api/" + path, data, method)


def scan_files(roots=ROOTS):
    files = []
    for root in roots:
        if not root.is_dir():
            continue
        base = root.resolve()
        for parent, dirs, names in os.walk(base, followlinks=False):
            dirs[:] = sorted(
                d
                for d in dirs
                if not d.startswith(".") and not (Path(parent) / d).is_symlink()
            )
            if len(Path(parent).relative_to(base).parts) >= 4:
                dirs[:] = []
            for name in sorted(names):
                path = Path(parent) / name
                if path.suffix.lower() != ".bin" or path.is_symlink():
                    continue
                try:
                    path.resolve().relative_to(base)
                    if path.is_file():
                        files.append(
                            {
                                "path": str(path),
                                "name": name,
                                "size": path.stat().st_size,
                            }
                        )
                except (OSError, ValueError):
                    continue
                if len(files) >= 128:
                    return files
    return files


def checked_block(raw, checksum_offset):
    if len(raw) < checksum_offset + 2:
        raise ValueError("固件头不完整")
    block = bytearray(raw.translate(DECODE))
    expected = struct.unpack_from(">H", block, checksum_offset)[0]
    block[checksum_offset : checksum_offset + 2] = b"\0\0"
    if sum(block) & 65535 != expected:
        raise ValueError("固件头校验失败")
    return block


def validate_u1(path):
    size = path.stat().st_size
    with path.open("rb") as stream:
        header = checked_block(stream.read(64), 6)
        if header[:4] != b"SNMK" or struct.unpack_from(">H", header, 4)[0] not in (
            1,
            2,
        ):
            raise ValueError("请选择 U1 完整升级 .bin 包")
        count = struct.unpack_from(">H", header, 46)[0]
        if count != 4:
            raise ValueError("U1 完整升级包需要四个标准分区")
        ranges = []
        for i in range(count):
            stream.seek(64 + 32 * i)
            entry = checked_block(stream.read(32), 2)
            kind, _, offset, length, expected = struct.unpack(">HHQI16s", entry)
            if (
                kind != i
                or length == 0
                or offset < 64 + 32 * count
                or offset + length > size
            ):
                raise ValueError("固件分区范围错误")
            ranges.append((offset, offset + length))
            stream.seek(offset)
            digest = hashlib.md5()
            remaining = length
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError("固件文件不完整")
                digest.update(chunk)
                remaining -= len(chunk)
            if digest.digest() != expected:
                raise ValueError("固件分区 MD5 校验失败")
        ext_offset, ext_size = struct.unpack_from(">II", header, 48)
        if ext_offset or ext_size:
            if (
                ext_offset < 192
                or ext_size < 34
                or ext_size > 98
                or ext_offset + ext_size > size
            ):
                raise ValueError("扩展固件范围错误")
            stream.seek(ext_offset)
            ext = checked_block(stream.read(ext_size), 4)
            ver, length, _, kind, name_len, offset, length_data, expected = (
                struct.unpack_from(">HHHHHII16s", ext)
            )
            if (
                ver != 1
                or length != ext_size - 4
                or 34 + name_len != ext_size
                or offset < ext_offset + ext_size
                or offset + length_data > size
            ):
                raise ValueError("扩展固件头错误")
            stream.seek(offset)
            digest = hashlib.md5()
            remaining = length_data
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError("扩展固件不完整")
                digest.update(chunk)
                remaining -= len(chunk)
            if digest.digest() != expected:
                raise ValueError("扩展固件 MD5 校验失败")
            ranges.append((ext_offset, offset + length_data))
        ordered = sorted(ranges)
        if any(a[1] > b[0] for a, b in zip(ordered, ordered[1:])):
            raise ValueError("固件分区重叠")
    return {
        "version": bytes(header[8:32]).split(b"\0")[0].decode("ascii").strip(),
        "size": size,
    }


def require_idle(status):
    if (
        status.get("webhooks", {}).get("state") != "ready"
        or status.get("print_stats", {}).get("state")
        not in ("standby", "complete", "cancelled")
        or status.get("idle_timeout", {}).get("state") not in ("Ready", "Idle")
    ):
        raise ValueError("请等待打印机就绪并结束打印、暂停或运动操作")


def machine_idle():
    result = json_request(
        "http://127.0.0.1:7125/printer/objects/query?print_stats&webhooks&idle_timeout&ace",
        headers=paxx_auth_headers(),
    )
    status = result.get("result", {}).get("status", {})
    require_idle(status)
    ace = status.get("ace", {})
    if (
        (ace and ace.get("status") != "ready")
        or ace.get("busy")
        or (ace.get("tag_op") or {}).get("busy")
        or (ace.get("location_hold") or {}).get("active")
    ):
        raise ValueError("请先结束耗材操作并确认料路位置")


def set_state(**values):
    with STATE_LOCK:
        STATE.update(values)
        STORE.mkdir(parents=True, exist_ok=True)
        tmp = STORE / "status.tmp"
        tmp.write_text(json.dumps(STATE, ensure_ascii=False))
        os.replace(tmp, STORE / "status.json")


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare(payload):
    with LOCK:
        if STATE["state"] in BUSY or (
            ACTIVE_PROCESS is not None and ACTIVE_PROCESS.poll() is None
        ):
            raise ValueError("升级任务正在进行")
        path = Path(payload.get("path", ""))
        if str(path) not in {f["path"] for f in scan_files()}:
            raise ValueError("文件已移除，请重新选择")
        target = payload.get("target")
        if target != "u1":
            raise ValueError("当前本地升级目标为 U1 打印机")
        set_state(state="validating", pct=0, msg="正在复制并校验本地文件", error="")
        PREPARED.clear()
        try:
            if path.stat().st_size == 0 or path.stat().st_size > 2 * 1024**3:
                raise ValueError("固件大小无效")
            if shutil.disk_usage(STORE).free < 3 * path.stat().st_size + 128 * 1024**2:
                raise ValueError("本地暂存空间不足")
            staged = STORE / "package.tmp"
            with path.open("rb") as source, staged.open("wb") as dest:
                shutil.copyfileobj(source, dest, 1024 * 1024)
                dest.flush()
                os.fsync(dest.fileno())
            sha = sha256_file(staged)
            info = validate_u1(staged)
            os.replace(staged, STORE / "package.bin")
            token = secrets.token_hex(24)
            PREPARED.update(
                target=target,
                path=str(STORE / "package.bin"),
                name=path.name,
                sha256=sha,
                token=token,
            )
            PREPARED.update(info)
            PREPARED["size"] = (STORE / "package.bin").stat().st_size
            set_state(
                state="prepared",
                pct=100,
                msg="校验完成，请确认设备与目标版本",
                target=target,
                file=path.name,
                sha256=sha,
            )
            return dict(PREPARED)
        except Exception as exc:
            set_state(state="error", error=str(exc), msg="校验失败")
            raise


def run_u1(path):
    global ACTIVE_PROCESS
    try:
        set_state(state="flashing", pct=None, msg="正在执行 U1 系统升级，请保持供电")
        # The installer owns its flash/reboot lifecycle. The UI never kills this process.
        with (STORE / "upgrade.log").open("w") as log:
            proc = subprocess.Popen(
                [str(INSTALLER), "upgrade", "all", str(path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                bufsize=1,
            )
            ACTIVE_PROCESS = proc
            reboot_seen = False
            for line in proc.stdout:
                log.write(line)
                log.flush()
                if "upgrade soc finish, prepare to reboot" in line:
                    reboot_seen = True
                    set_state(
                        state="reboot_pending",
                        pct=100,
                        msg="写入完成，等待重启后核对版本",
                    )
                elif line.strip():
                    set_state(msg=line.strip()[-240:])
            code = proc.wait()
            proc.stdout.close()
        if code != 0:
            raise ValueError("系统升级脚本退出码 " + str(code))
        set_state(
            state="reboot_pending",
            pct=100,
            msg="等待重启后核对版本"
            if reboot_seen
            else "升级脚本已退出，请核对机器版本",
        )
    except Exception as exc:
        set_state(state="error", error=str(exc), msg="升级失败，请查看日志")


def start(payload):
    with LOCK:
        if (
            STATE["state"] != "prepared"
            or not PREPARED
            or payload.get("token") != PREPARED["token"]
        ):
            raise ValueError("请重新选择并校验固件")
        machine_idle()
        path = Path(PREPARED["path"])
        if sha256_file(path) != PREPARED["sha256"]:
            raise ValueError("暂存固件发生变化，请重新校验")
        if not INSTALLER.is_file() or not os.access(INSTALLER, os.X_OK):
            raise ValueError("当前系统缺少 U1 升级执行器")
        set_state(state="flashing", pct=0, msg="启动 U1 升级", boot_id=boot_id())
        threading.Thread(target=run_u1, args=(path,), daemon=True).start()
        return {"ok": True}


def boot_id():
    path = Path("/proc/sys/kernel/random/boot_id")
    return path.read_text().strip() if path.exists() else "development"


def status():
    if (
        STATE.get("target") == "u1"
        and STATE["state"] in BUSY
        and STATE.get("boot_id")
        and STATE["boot_id"] != boot_id()
    ):
        set_state(state="verify_version", msg="打印机已重启，请核对当前版本", pct=100)
    return dict(
        STATE,
        current_version=Path("/etc/FULLVERSION").read_text().strip()
        if Path("/etc/FULLVERSION").exists()
        else "unknown",
    )


class Handler(BaseHTTPRequestHandler):
    def handle_request(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            if (
                self.command == "POST"
                and self.headers.get("Content-Type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
                != "application/json"
            ):
                raise ValueError("请求需要 application/json")
            if length < 0 or length > 65536:
                raise ValueError("请求过大")
            data = json.loads(self.rfile.read(length)) if length else {}
            route = self.path.split("?", 1)[0]
            if self.command == "GET" and route == "/firmware/files":
                result = {"files": scan_files()}
            elif self.command == "GET" and route == "/firmware/status":
                result = status()
            elif self.command == "GET" and route == "/firmware/settings":
                result = firmware_settings()
            elif self.command == "POST" and route == "/firmware/settings":
                result = update_firmware_setting(data)
            elif self.command == "POST" and route == "/firmware/prepare":
                result = prepare(data)
            elif self.command == "POST" and route == "/firmware/start":
                result = start(data)
            elif self.command == "GET" and route == "/state":
                result = web("state")
            elif self.command == "POST" and route == "/spoolman/sync":
                result = web("spoolman/sync", data)
            else:
                self.send_error(404)
                return
            body = json.dumps(result, ensure_ascii=False).encode()
            self.send_response(200)
        except Exception as exc:
            body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode()
            self.send_response(400)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = handle_request
    do_POST = handle_request


if __name__ == "__main__":
    STORE.mkdir(parents=True, exist_ok=True)
    saved = STORE / "status.json"
    if saved.exists():
        STATE.update(json.loads(saved.read_text()))
        if STATE["state"] in BUSY:
            STATE.update(msg="正在核对上次升级任务，请保持供电")
    ThreadingHTTPServer(("127.0.0.1", 7136), Handler).serve_forever()
