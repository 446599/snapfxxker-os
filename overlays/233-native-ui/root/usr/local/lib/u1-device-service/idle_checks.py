# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared, side-effect-free U1 upgrade checks for the service and Klipper reactor."""

import math

REQUIRED_OBJECTS = {"print_stats", "webhooks", "idle_timeout", "heaters"}
OPTIONAL_OBJECTS = {"ace", "ace_bg_swap", "ace_native_guard"}

FINISHED = {"idle", "complete", "completed", "cancelled", "error", "failed"}
# Stock feeder intermediate *_finish states (e.g. unload_heat_finish) still
# wait for a user action and are deliberately not idle.
FEEDER_IDLE = {"none", "inited", "wait_insert", "preload_finish", "load_finish",
               "unload_finish", "manual_sta_finish"}


def require_idle(status):
    if (
        status.get("webhooks", {}).get("state") != "ready"
        or status.get("print_stats", {}).get("state")
        not in ("standby", "complete", "cancelled")
        or status.get("idle_timeout", {}).get("state") not in ("Ready", "Idle")
    ):
        raise ValueError("请等待打印机就绪并结束打印、暂停或运动操作")


def require_ace_idle(ace):
    if not isinstance(ace, dict):
        raise ValueError("无法确认 ACE 状态")
    if not ace:
        raise ValueError("无法确认 ACE 状态")
    for key, flag in (("tag_op", "busy"), ("location_hold", "active")):
        if not isinstance(ace.get(key), dict) or ace[key].get(flag) is not False:
            raise ValueError("请先结束耗材操作并确认料路位置")
    if (ace.get("status") != "ready"
            or ace.get("swap_in_progress") is not False
            or ace.get("unload_all_active") is not False
            or any(ace.get(key) for key in ("busy", "swap_in_progress", "unload_all_active"))
            or (ace.get("tag_op") or {}).get("busy")
            or (ace.get("location_hold") or {}).get("active")
            or (ace.get("calibration_unload") or {}).get("active")):
        raise ValueError("请先结束耗材操作并确认料路位置")
    for key in ("slot_action", "calibration"):
        operation = ace.get(key)
        if (not isinstance(operation, dict) or operation.get("busy")
                or operation.get("state") not in FINISHED):
            raise ValueError("请先结束槽位操作、耗材校准或退料")
    devices = ace.get("aces")
    if not isinstance(devices, list) or ace.get("device_count", len(devices)) != len(devices):
        raise ValueError("无法确认 ACE 设备状态")
    for position, device in enumerate([ace] + devices):
        if not isinstance(device, dict):
            raise ValueError("无法确认 ACE 设备状态")
        if device is not ace and device.get("connected") is False:
            # A configured but unconnected port may be a spare. It is only
            # ignorable when its identity is well formed and no loaded head
            # depends on it; all global operations were checked above.
            idx = device.get("idx")
            sources = ace.get("head_source")
            if (type(idx) is not int or idx != position - 1
                    or not isinstance(sources, dict)
                    or set(sources) not in (set(range(4)), {str(i) for i in range(4)})
                    or device.get("status") != "unknown"
                    or device.get("fw_hold") is not False
                    or device.get("auto_dry_running") is not False
                    or device.get("slots") != []
                    or not isinstance(device.get("dryer_status"), dict)
                    or device["dryer_status"].get("rotisserie")
                    or (device["dryer_status"] and
                        device["dryer_status"].get("status") != "stop")):
                raise ValueError("无法确认未连接 ACE 的占用状态")
            for source in sources.values():
                if source is not None and (not isinstance(source, dict)
                        or type(source.get("ace_index")) is not int
                        or not 0 <= source["ace_index"] < len(devices)
                        or type(source.get("slot")) is not int
                        or not 0 <= source["slot"] < 4
                        or source.get("ace_index") == idx):
                    raise ValueError("未连接 ACE 仍有喷头依赖")
            continue
        if device is not ace and (device.get("status") != "ready"
                                 or device.get("connected") is not True
                                 or device.get("fw_hold")):
            raise ValueError("请先确认所有 ACE 已连接、空闲且没有固件更新")
        dryer = device.get("dryer_status")
        if (not isinstance(dryer, dict) or dryer.get("status") != "stop"
                or dryer.get("rotisserie") or device.get("auto_dry_running")):
            raise ValueError("请先停止 ACE 烘干与料盘旋转")
        slots = device.get("slots", [])
        if not isinstance(slots, list) or (device is not ace and not slots):
            raise ValueError("无法确认 ACE 设备状态")
        for slot in slots:
            if not isinstance(slot, dict):
                raise ValueError("无法确认 ACE 槽位状态")
            # Newer multiACE exposes its device-reported motor phase. Never
            # fall back to material identity when that phase is unavailable.
            # Older releases lack the key and retain their prior checks.
            if "slot_status" in slot:
                if slot["slot_status"] != "ready":
                    raise ValueError("请先结束 ACE 槽位进退料操作")
            elif slot.get("status") not in ("empty", "ready"):
                raise ValueError("请先结束 ACE 槽位进退料操作")


def require_machine_idle(status):
    """Validate one complete status snapshot, including all installed heaters."""
    require_idle(status)
    if "ace" in status:
        require_ace_idle(status["ace"])
    if (status.get("ace_bg_swap") or {}).get("busy"):
        raise ValueError("请先结束后台换料")
    native = status.get("ace_native_guard") or {}
    if native.get("state", "idle") != "idle" or native.get("selftest_active"):
        raise ValueError("请先结束耗材测试并确认料路位置")
    for name, feed in status.items():
        if not name.startswith("filament_feed "):
            continue
        for channel in feed.values():
            if not isinstance(channel, dict):
                raise ValueError("无法确认送料器状态")
            for key in ("channel_state", "channel_action_state"):
                if channel.get(key) not in FEEDER_IDLE:
                    raise ValueError("请先结束原厂送料器装料、退料或手动操作")
    heater_names = status.get("heaters", {}).get("available_heaters")
    if not isinstance(heater_names, list) or not heater_names:
        raise ValueError("无法确认加热器状态")
    for name in heater_names:
        heater = status.get(name)
        if not isinstance(heater, dict):
            raise ValueError("无法确认加热器状态：" + str(name))
        target, power = heater.get("target"), heater.get("power")
        if any(not isinstance(value, (int, float)) or not math.isfinite(value)
               or value < 0 for value in (target, power)):
            raise ValueError("无法确认加热器状态：" + str(name))
        if target > 0 or power > 0:
            raise ValueError("请先关闭所有喷头、热床及舱温加热")

