"""Background, per-ACE calibration using measured continuous trajectories."""

import copy
import hashlib
import json
import logging
import math
import re
import uuid

from .ace_controlled_motion import (AceMotionTransport, ControlledMotion, MotionError,
                                    REQUEST, QUERY, STOP, CLOSE, HELD, REVOKED,
                                    INLET_REMOVED, GRIP_COMPLETE, request_operation)
from .ace_path_store import (PathStore, PathStateError, REFERENCE, MAX_ACTIONS,
                             action_fingerprint, configured_route_heads, finite_mm, route_key)


FINISHED_STAGES = frozenset(("idle", "complete", "cancelled", "error"))
MOVING_STATES = frozenset(("opening", "starting", "running", "stopping"))
TERMINAL_MOTION_STATES = frozenset(("complete", "cancelled"))
STEPS = (10, 50, 100)
LONG_SPEED = 80
JOG_SPEED = 50
MAX_PATH_MM = 5000
# The stock encoder conversion is 12342 tenths of a micrometre per count.
ENCODER_QUANTUM_MM = 1.2342


def idle_task():
    return {"task_id": "", "version": 0, "stage": "idle", "active": False,
            "ace": None, "slot": None, "head": None, "busy": False,
            "message": "请选择 ACE 开始料管校准", "wait_reason": "none",
            "position_mm": None, "position_trusted": False, "lanes": [],
            "last_action_id": "", "last_action_state": "", "error": None}


class PathCalibration:
    def __init__(self, ace, store=None, motion_factory=None):
        self.ace = ace
        self.reactor = ace.reactor
        self.store = store or PathStore(
            ace.paths["config_dir"] + "/persistent/multiace-managed-paths.json")
        self.motion_factory = motion_factory or self._make_motion
        self.controllers = {}
        self.task = copy.deepcopy(self.store.state.get("task") or idle_task())
        self._known_indices = {
            int(key) for key, port in self.task.get("group_ports", {}).items()
            if str(key).isdigit() and 0 <= int(key) < len(ace._ace_devices)
            and ace._ace_devices[int(key)] == port}
        self.actions = copy.deepcopy(self.store.state["actions"])
        self.motion = None
        self.motion_kind = None
        self.motion_route = None
        self.motion_target = None
        self.edge_position = None
        self.after_commit = None
        self.after_commit_owner = None
        self.stop_requested = False
        self.cancel_requested = False
        self.thermal_active = False
        self.confirmed_heads = set()
        self._identifying = {}
        self._print_hooks = {}
        self._saved_environment = None
        self.quiescing_assists = False
        self.operations = None
        # A restarted host never resumes a motor task. Retain measured
        # geometry, but release historical ownership before accepting new work.
        interrupted = self.task.get("active") or bool(self.store.state["occupied_heads"])
        self.store.state["occupied_heads"].clear()
        if self.task.get("active"):
            self.task.update(stage="cancelled", active=False, busy=False, error=None,
                             message="上次任务已结束，可重新操作", position_trusted=False)
        for record in self.actions.values():
            if record.get("state") not in ("complete", "error"):
                record.update(state="error", error="主机重启，旧动作已取消")
        if interrupted and not self.store.error:
            self.commit_state(self.store.snapshot())
        ace.gcode.register_command("ACE_PATH_CALIBRATION", self.command)
        ace.gcode.register_command("ACE_PATH_REFERENCE_VALIDATION", self.validation_command)
        hooks = ace.printer.lookup_object("webhooks")
        hooks.register_endpoint("multiace/path_calibration_stop", self.stop_endpoint)
        hooks.register_endpoint("multiace/path_calibration_cancel", self.cancel_endpoint)
        ace.printer.register_event_handler("klippy:ready", self._install_print_hooks)
        ace.printer.register_event_handler("klippy:shutdown", self.shutdown)
        self.timer = self.reactor.register_timer(self.tick, self.reactor.NOW)

    def _make_motion(self, idx, slot):
        return ControlledMotion(AceMotionTransport(self.ace, idx, slot),
                                clock=self.reactor.monotonic)

    def controller(self, idx, slot):
        key = idx, slot
        current = self.controllers.get(key)
        serial = self.ace._serials.get(idx)
        device = getattr(current, "device", None)
        boot_changed = (device is not None and current.boot_epoch is not None
                        and device.boot_epoch is not None and device.boot_epoch != current.boot_epoch)
        if current is None or current.transport.original is not serial or boot_changed:
            if current is self.motion and self.motion is not None:
                raise PathStateError("当前管路的 ACE 连接或启动代次已改变")
            if device is not None:
                current._generation += 1
                current._pending.clear()
                current._failed("ACE connection or boot changed", send_stop=False)
                device.abandon(current.token)
                if current in device.controllers:
                    device.controllers.remove(current)
                registry = getattr(self.ace, "_controlled_motion_devices", {})
                previous = current.transport.original
                if (previous is not serial and not device.controllers and not device.motion_owners
                        and registry.get(id(previous)) is device):
                    registry.pop(id(previous))
            if boot_changed:
                self._identifying.pop(idx, None)
                getattr(self.ace, '_location_uid_by_idx', {}).pop(idx, None)
            current = self.motion_factory(idx, slot)
            self.controllers[key] = current
        return current

    def _head(self, idx, slot, requested=None):
        if requested is not None and type(requested) is not int:
            raise PathStateError("目标喷头编号无效")
        mode = getattr(self.ace, "_ace_mode", None)
        heads = configured_route_heads(self.ace, idx, slot)
        if mode == "multi":
            if not heads:
                raise PathStateError("该喷头当前使用手动或侧供料")
            # The legacy hint cannot change Multi's fixed slot-to-head route.
            return slot
        if mode != "head":
            raise PathStateError("请先选择多 ACE 或按喷头绑定模式")
        if requested in heads:
            return requested
        if len(heads) == 1:
            return heads[0]
        _port, uid = self.ace._location_identity(idx)
        if uid is not None:
            saved = self.store.state["routes"].get(route_key(uid, slot), {})
            if (saved.get("head") in heads
                    and saved.get("topology_version") == self._topology_version()):
                return saved["head"]
        raise PathStateError("请明确选择这台 ACE 对应的喷头")

    def _topology_version(self):
        shape = {"mode": getattr(self.ace, "_ace_mode", None),
                 "head_ace": getattr(self.ace, "head_ace", {}),
                 "head_manual": getattr(self.ace, "head_manual", {}),
                 "head_feeder": getattr(self.ace, "head_feeder", {})}
        encoded = json.dumps(shape, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def route(self, idx, slot, head):
        port, uid = self.ace._location_identity(idx)
        if port is None or uid is None:
            raise PathStateError("ACE 硬件身份尚未确认")
        return {"route_id": route_key(uid, slot), "uid": list(uid),
                "port": port, "ace": idx, "slot": slot, "head": head,
                "topology_version": self._topology_version()}

    def refresh_identity(self, idx, expected_uid, callback):
        original = self.ace._serials.get(idx)
        finished = [False]
        authority = self

        def finish(error):
            if not finished[0]:
                finished[0] = True
                callback(error)

        def expired(eventtime):
            finish(PathStateError("ACE 硬件身份读取超过时限"))
            return self.reactor.NEVER

        def receive(self, response):
            valid = (isinstance(response, dict)
                     and response.get('_v2_frame_valid') is True
                     and response.get('_v2_payload_valid') is True
                     and response.get('_v2_command') == 0
                     and type(response.get('code')) is int and response['code'] == 0)
            values = response.get('result') if valid else None
            uid = tuple(values.get('uid%d' % field) for field in (1, 2, 3)) if isinstance(values, dict) else ()
            if (authority.ace._serials.get(idx) is not original or original is None
                    or any(type(value) is not int for value in uid)
                    or uid != tuple(expected_uid)):
                finish(PathStateError("ACE 实际硬件身份与授权管路不一致"))
            else:
                finish(None)

        self.reactor.register_timer(expired, self.reactor.monotonic() + 3)
        self.ace.send_request_to(idx, {'method': 'discover_device'}, receive)

    def head_sensor(self, head):
        sensor = self.ace.printer.lookup_object(
            "filament_motion_sensor e%d_filament" % head, None)
        value = getattr(sensor, "runout_buttun_state", None)
        if type(value) is not bool:
            raise PathStateError("喷头物理传感器状态不可确认")
        return value

    def physical_sources(self):
        sources = {str(head): None for head in range(4)}
        for head in range(4):
            try:
                if not self.head_sensor(head):
                    continue
                source = self.ace._head_source.get(head)
                if not isinstance(source, dict) or source.get('load_failed'):
                    # A manual calibration may have reached the head before
                    # a normal load/source declaration exists.
                    source = self._calibration_physical_source(head)
                    if source is None:
                        continue
                idx, slot = source.get('ace_index'), source.get('slot')
                if (type(idx) is int and type(slot) is int
                        and 0 <= idx < len(self.ace._ace_devices) and 0 <= slot < 4
                        and self._head(idx, slot, head) == head):
                    sources[str(head)] = {'ace_index': idx, 'slot': slot}
            except PathStateError:
                continue
        return sources

    def _calibration_physical_source(self, head):
        if not self.task.get('active'):
            return None
        owner = self.store.state['occupied_heads'].get(str(head))
        lane = self._current_lane()
        if (owner is None or lane is None or owner['task_id'] != self.task.get('task_id')
                or lane.get('route_id') != owner['route_id']):
            return None
        route = self.store.state['routes'].get(owner['route_id'], {})
        idx, slot = route.get('ace'), route.get('slot')
        controller = self.controllers.get((idx, slot))
        if controller is None or not controller.transport.connected():
            return None
        sample = controller.snapshot()
        age = sample.get('telemetry_age_s')
        if (type(age) not in (int, float) or not 0 <= age <= .5
                or route.get('topology_version') != self._topology_version()
                or lane.get('reference_boot_epoch') != sample['boot_epoch']
                or lane.get('reference_insertion_epoch') != sample['insertion_epoch']):
            return None
        if self.edge_position is not None or (lane.get('position_trusted')
                and lane.get('position_mm') == lane.get('length_mm')):
            return {'ace_index': idx, 'slot': slot}
        return None

    def allows_wire(self, idx, request, serial=None):
        """Revalidate ownership at the writer's actual transmission boundary."""
        try:
            operation = request_operation(request)
        except MotionError:
            return False
        if operation is not None:
            original = request.get("_controlled_transport")
            if original is not None and original is not self.ace._serials.get(idx):
                return False
            if serial is not None and serial is not self.ace._serials.get(idx):
                return False
            if operation in (QUERY, STOP, CLOSE):
                return True
            slot = REQUEST.unpack(bytes.fromhex(request["params"]["hex"]))[3]
            controller = self.controllers.get((idx, slot))
            if (controller is None or request.get("_controlled_owner") is not controller.token
                    or original is not controller.transport.original):
                return False
            if controller is self.motion and self.motion_route is not None:
                if self.store.error:
                    return False
                route = self.motion_route
                if route["topology_version"] != self._topology_version():
                    return False
                owner = self.store.state["occupied_heads"].get(str(route["head"]))
                return (self.task.get("active") and owner is not None
                        and owner["task_id"] == self.task["task_id"]
                        and owner["route_id"] == route["route_id"])
            return bool(self.operations and self.operations.allows_wire(idx, request, serial))
        method = request.get("method")
        params = request.get("params") or {}
        moving = method in ("feed_filament", "unwind_filament", "feed_or_rollback_raw",
                            "start_feed_assist", "update_feeding_speed", "insert_policy_start")
        if method == "raw":
            moving = params.get("cmd") in (8, 10, 77)
        if method in ("drying", "drying_raw"):
            moving = params.get("auto_roll") is True
        if moving and self.task.get("active"):
            return bool(self.operations and self.operations.allows_wire(idx, request, serial))
        return True

    def _require_idle(self):
        self.ace._require_no_stop_fault()
        printer = self.ace.printer.lookup_object("print_stats", None)
        if printer is None or getattr(printer, "state", "").lower() not in (
                "standby", "complete", "cancelled"):
            raise PathStateError("校准需要打印机空闲")
        if (self.ace._swap_in_progress or self.ace._unload_all_active
                or getattr(self.ace, "_tag_read_busy", False)):
            raise PathStateError("请等待当前换料或识别动作结束")
        action = getattr(getattr(self.ace, "_slot_actions", None), "state", {})
        if action.get("state") in ("preparing", "running"):
            raise PathStateError("请等待当前料槽动作结束")
        bg = self.ace.printer.lookup_object("ace_bg_swap", None)
        if bg is not None and getattr(bg, "_busy", False):
            raise PathStateError("请等待后台换料结束")
        if not self.task.get("active") and self.operations is not None:
            if any(operation["state"] not in FINISHED_STAGES
                   and (operation["kind"] != "assist" or operation["state"] != "running")
                   for operation in self.operations.status()):
                raise PathStateError("请等待当前受控进退料动作结束")

    def _group_capable(self, head):
        members = []
        indices = self._known_indices | {idx for idx, connected in self.ace._connected_per_ace.items()
                                         if connected}
        for idx in sorted(indices):
            if getattr(self.ace, "_ace_mode", None) == "head" and self.ace.head_ace_for(head) != idx:
                continue
            if idx in getattr(self.ace, "_fw_update_hold", ()):
                mode = getattr(self.ace, "_ace_mode", None)
                if mode == "multi" or self.ace.head_ace_for(head) == idx:
                    raise PathStateError("共享管路的一台 ACE 正在固件更新")
            if not self.ace._connected_per_ace.get(idx):
                raise PathStateError("共享 Y 的一台 ACE 已断开，请先核对管路")
            for slot in range(4):
                try:
                    mapped = self._head(idx, slot, head)
                except PathStateError:
                    continue
                if mapped == head:
                    members.append((idx, slot))
        if not members:
            raise PathStateError("共享管路设备尚未就绪")
        samples = [self.controller(idx, slot).snapshot() for idx, slot in members]
        if any(not sample["available"] or sample["state"] in ("unknown", "unavailable")
               or sample.get("telemetry_age_s") is None or sample["telemetry_age_s"] > .5
               for sample in samples):
            raise PathStateError("同一 Y 接头的全部 ACE 支路需要受控固件握手")

    def status(self):
        result = copy.deepcopy(self.task)
        result["busy"] = result["busy"] or self.store.pending is not None
        connected = [idx for idx in range(len(self.ace._ace_devices))
                     if self.ace._connected_per_ace.get(idx)]
        capable = [idx for idx in connected if all(
            self.controllers.get((idx, slot)) is not None
            and self.controllers[idx, slot].snapshot()["available"]
            for slot in range(4))]
        result["available"] = bool(capable) and self.store.error is None
        result["capable_aces"] = capable
        result["mode"] = getattr(self.ace, "_ace_mode", "")
        result["heads_by_ace"] = {
            str(idx): [head for head in range(4) if self.ace.head_uses_ace(head)
                       and self.ace.head_ace_for(head) == idx]
            for idx in connected} if result["mode"] == "head" else {
                str(idx): list(range(4)) for idx in connected}
        result["capability_reason"] = self.store.error or (
            "" if result["available"] else "等待 ACE 受控运动固件和能力握手")
        result["minimum_margin_mm"] = self._minimum_margin()
        result["can_jog_forward"] = self._can_extend(1)
        result["can_jog_back"] = self._can_extend(-1)
        result["lanes_by_ace"] = self._saved_lanes()
        if self.motion is not None:
            sample = self.motion.snapshot()
            result["position_mm"] = sample["position_mm"]
            result["position_trusted"] = sample["trusted"]
        return result

    def _saved_lanes(self):
        groups = {}
        for (idx, slot), controller in self.controllers.items():
            try:
                head = self._head(idx, slot)
                route = self.route(idx, slot, head)
            except PathStateError:
                continue
            saved = self.store.state["routes"].get(route["route_id"], {})
            geometry = saved.get("geometry") or {}
            basic = saved.get("basic") or {}
            if saved.get("topology_version") != route["topology_version"]:
                geometry, basic = {}, {}
            sample = controller.snapshot()
            groups.setdefault(str(idx), []).append({
                "slot": slot, "head": head, "inlet": sample["inlet"],
                "status": "complete" if geometry else ("measured" if basic else "pending_insert"),
                "length_mm": geometry.get("length_mm", basic.get("length_mm")),
                "park_mm": geometry.get("park_mm")})
        return groups

    def _can_extend(self, direction):
        if (self.task.get("stage") != "marking" or self.store.pending is not None
                or self.task.get("wait_reason") != "none"):
            return False
        if self.motion is None:
            lane = self._current_lane()
            if lane is None or not self.task.get("position_trusted"):
                return False
            return (lane["position_mm"] < lane["length_mm"] if direction > 0
                    else lane["position_mm"] > lane["grip_mm"])
        if self.motion_kind != "jog" or self.motion.snapshot()["state"] != "running":
            return False
        return direction == self.task.get("jog_direction")

    def _current_lane(self):
        slot = self.task.get("slot")
        return next((lane for lane in self.task.get("lanes", [])
                     if lane["slot"] == slot), None)

    def _minimum_margin(self):
        lane = self._current_lane()
        if lane is None:
            return None
        error = lane.get("reference_uncertainty_mm")
        if type(error) not in (int, float):
            return None
        required = error + float(lane.get("stop_uncertainty_mm", 0)) + 2 * ENCODER_QUANTUM_MM
        return math.ceil(required * 1000) / 1000

    def _stop_corridor(self, lane):
        return max(ENCODER_QUANTUM_MM, float(lane.get("stop_uncertainty_mm", 0))) + 0.001

    def _commit(self, continuation=None):
        candidate = self.store.snapshot()
        self.commit_state(candidate, continuation, owner=self.task["task_id"])

    def commit_state(self, candidate, continuation=None, owner=None):
        if self.operations is not None:
            self.operations.prune_receipts()
        self.task["group_ports"] = {str(idx): self.ace._ace_devices[idx]
                                     for idx in sorted(self._known_indices)}
        candidate["task"] = copy.deepcopy(self.task)
        candidate["actions"] = copy.deepcopy(self.actions)
        self.store.submit(candidate)
        self.after_commit = continuation
        self.after_commit_owner = owner

    def _set_message(self, message, wait_reason="none", busy=False):
        exited = self.task.get('preparation_ejected') or []
        if exited:
            labels = ','.join('A%d·%d' % (route['ace'] + 1, route['slot'] + 1) for route in exited)
            message += ' · ' + labels + ' 已退出，请重新插入'
        self.task.update(message=message, wait_reason=wait_reason, busy=busy,
                         version=self.task["version"] + 1)

    def _remember_action(self, action_id, task_id, parameters):
        if (not isinstance(action_id, str) or not 1 <= len(action_id) <= 96
                or not isinstance(task_id, str) or not 1 <= len(task_id) <= 96):
            raise PathStateError("任务或动作标识无效")
        previous = self.actions.get(action_id)
        fingerprint = action_fingerprint(parameters)
        if previous is not None:
            if (previous["task_id"] != task_id
                    or previous["fingerprint"] != fingerprint):
                raise PathStateError("动作标识已用于另一请求")
            return False
        if len(self.actions) >= MAX_ACTIONS:
            removable = [key for key, value in self.actions.items()
                         if value["task_id"] != self.task.get("task_id")
                         and value["state"] in ("complete", "error")]
            if not removable:
                raise PathStateError("本次动作记录已满，请结束任务后继续")
            del self.actions[removable[0]]
        self.actions[action_id] = {"task_id": task_id, "fingerprint": fingerprint,
                                   "state": "accepted"}
        self.task.update(last_action_id=action_id, last_action_state="accepted")
        return True

    def _finish_actions(self, error=None):
        for action_id, action in self.actions.items():
            if action["task_id"] == self.task.get("task_id") and action["state"] in (
                    "accepted", "running"):
                action.update(state="error" if error else "complete", error=error)
        self.task["last_action_state"] = "error" if error else "complete"

    def command(self, gcmd):
        try:
            op = str(gcmd.get("OP", "")).lower()
            parameters = {"op": op, "ace": gcmd.get_int("ACE", -1),
                          "slot": gcmd.get_int("SLOT", -1),
                          "head": gcmd.get_int("HEAD", -1),
                          "step": gcmd.get_int("STEP", 10)}
            self.request(op, str(gcmd.get("TASK_ID", "")),
                         str(gcmd.get("ACTION_ID", "")),
                         gcmd.get_int("EXPECTED_VERSION", 0), parameters)
        except (PathStateError, MotionError, ValueError) as exc:
            raise gcmd.error(str(exc))

    def validation_command(self, gcmd):
        try:
            self.record_reference_validation(
                gcmd.get_int("ACE"), gcmd.get_int("SLOT"),
                gcmd.get_float("REFERENCE_ERROR_MM"), gcmd.get_float("STOP_ERROR_MM"),
                str(gcmd.get("EVIDENCE_SHA256", "")), gcmd.get_int("HEAD", -1))
        except (PathStateError, MotionError, ValueError) as exc:
            raise gcmd.error(str(exc))

    def record_reference_validation(self, idx, slot, reference_error_mm, stop_error_mm,
                                    evidence_sha256, head=-1):
        """Record measured commissioning evidence before enabling Y parking."""
        self._require_idle()
        if self.motion is not None or self.store.pending is not None or self.thermal_active:
            raise PathStateError("请先等待当前测量与保存结束")
        if self.operations is not None and any(operation["state"] not in FINISHED_STAGES
                                                for operation in self.operations.status()):
            raise PathStateError("请先结束当前受控动作")
        if type(idx) is not int or not 0 <= idx < len(self.ace._ace_devices):
            raise PathStateError("ACE 编号无效")
        if type(slot) is not int or not 0 <= slot < 4:
            raise PathStateError("槽位编号无效")
        reference_error = finite_mm(reference_error_mm, "入料重复定位误差")
        stop_error = finite_mm(stop_error_mm, "停止过冲")
        if not isinstance(evidence_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", evidence_sha256):
            raise PathStateError("需要本次实测记录的 SHA-256")
        if not self.controller(idx, slot).snapshot()["available"]:
            raise PathStateError("请先完成该 ACE 的受控固件握手")
        route = self.route(idx, slot, self._head(idx, slot, head))
        candidate = self.store.snapshot()
        saved = candidate["routes"].get(route["route_id"], {})
        if saved and (saved.get("head") != route["head"]
                      or saved.get("topology_version") != route["topology_version"]):
            raise PathStateError("实测证据与当前管路绑定不一致")
        saved = dict(saved, **route)
        saved["commissioning"] = {
            "reference": dict(REFERENCE), "reference_uncertainty_mm": reference_error,
            "stop_uncertainty_mm": stop_error, "evidence_sha256": evidence_sha256}
        geometry = saved.get("geometry")
        uncertainty = reference_error + stop_error + ENCODER_QUANTUM_MM
        if geometry is not None and geometry["margin_mm"] <= uncertainty:
            saved["previous_geometry"] = geometry
            saved["geometry"] = None
        elif geometry is not None:
            saved["geometry"] = dict(geometry, uncertainty_mm=max(
                geometry["uncertainty_mm"], uncertainty))
        candidate["routes"][route["route_id"]] = saved
        for lane in self.task.get("lanes", []):
            if lane.get("route_id") == route["route_id"]:
                lane["reference_uncertainty_mm"] = reference_error
                lane["stop_uncertainty_mm"] = max(lane.get("stop_uncertainty_mm", 0), stop_error)
        self.task["version"] += 1
        self.commit_state(candidate, owner=self.task["task_id"])

    def request(self, op, task_id, action_id, expected_version, parameters):
        if op in ("stop", "cancel"):
            return self.request_stop(task_id, action_id, op == "cancel")
        if self.store.error:
            raise PathStateError(self.store.error)
        if not task_id or not action_id or len(task_id) > 96 or len(action_id) > 96:
            raise PathStateError("任务和动作标识无效")
        previous = self.actions.get(action_id)
        if previous is not None:
            self._remember_action(action_id, task_id, parameters)
            return self.status()
        if self.store.pending is not None:
            raise PathStateError("正在保存任务，请读取当前状态后继续")
        if expected_version != self.task["version"]:
            raise PathStateError("校准状态已变化，请刷新原任务")
        if op == "prepare":
            if task_id == self.task.get("task_id"):
                raise PathStateError("该任务已经建立，请返回原任务")
            if any(value["task_id"] == task_id for value in self.actions.values()):
                raise PathStateError("任务标识已经使用")
            self._prepare(task_id, action_id, parameters)
            return self.status()
        if task_id != self.task.get("task_id") or not self.task.get("active"):
            raise PathStateError("请求对应的校准任务已结束或改变")
        if self.task["topology_version"] != self._topology_version():
            raise PathStateError("校准期间管路绑定已改变，请先停止并核对")
        self._require_idle()
        handlers = {"start": self._continue, "insert": self._continue,
                    "resume": self._continue, "skip": self._skip,
                    "select": self._select, "save": self._save,
                    "confirm_route": self._confirm_route,
                    "confirm_source": self._confirm_source,
                    "jog_forward": self._jog_forward,
                    "jog_back": self._jog_back, "return": self._return}
        handler = handlers.get(op)
        if handler is None:
            raise PathStateError("校准动作无效")
        try:
            handler(action_id, parameters)
        except Exception as exc:
            action = self.actions.get(action_id)
            if action is not None and action["state"] == "accepted":
                action.update(state="error", error=str(exc))
                self.task["last_action_state"] = "error"
            raise
        return self.status()

    def _prepare(self, task_id, action_id, parameters):
        self._require_idle()
        if self.task.get("active"):
            raise PathStateError("请先完成或取消当前校准任务")
        idx = parameters["ace"]
        if not 0 <= idx < len(self.ace._ace_devices):
            raise PathStateError("ACE 编号无效")
        selected_slot = parameters["slot"]
        if selected_slot not in (-1, 0, 1, 2, 3):
            raise PathStateError("槽位编号无效")
        lanes = []
        candidate = self.store.snapshot()
        for slot in range(4):
            if (getattr(self.ace, "_ace_mode", None) == "multi"
                    and not self.ace.head_uses_ace(slot)):
                if selected_slot == slot:
                    raise PathStateError("所选喷头当前使用手动或侧供料")
                lanes.append({"ace": idx, "slot": slot, "head": slot,
                              "status": "unmapped", "included": False,
                              "inlet": self.controller(idx, slot).snapshot()["inlet"],
                              "length_mm": None, "park_mm": None})
                continue
            head = self._head(idx, slot, parameters["head"])
            self._group_capable(head)
            route = self.route(idx, slot, head)
            sample = self.controller(idx, slot).snapshot()
            saved = candidate["routes"].get(route["route_id"], {})
            if saved and (saved.get("head") != head or
                          saved.get("topology_version") != route["topology_version"]):
                saved = {}
            candidate["routes"][route["route_id"]] = {
                **saved, **route, "geometry": saved.get("geometry")}
            included = selected_slot in (-1, slot)
            evidence = saved.get("commissioning", {})
            lanes.append({**route, "included": included,
                          "status": "pending_insert" if sample["inlet"] else "empty",
                          "inlet": sample["inlet"], "length_mm": None,
                          "park_mm": None, "position_mm": sample["position_mm"],
                          "grip_mm": None,
                          "position_trusted": sample["trusted"],
                          "reference_uncertainty_mm": evidence.get("reference_uncertainty_mm"),
                          "stop_uncertainty_mm": evidence.get("stop_uncertainty_mm", 0)})
        self.store.validate(candidate)
        self.task = idle_task()
        self.task.update(task_id=task_id, version=1, stage="preparing", active=True,
                         ace=idx, lanes=lanes, topology_version=self._topology_version())
        self.quiescing_assists = True
        self._saved_environment = {
            "dry_auto_roll": bool(getattr(self.ace, "dry_auto_roll", False)),
            "heaters": {}, "dryers": []}
        self.task["environment"] = self._saved_environment
        self.stop_requested = self.cancel_requested = False
        self.confirmed_heads.clear()
        self._remember_action(action_id, task_id, parameters)
        self._set_message("正在检查喷头与准备料管", busy=True)
        self.commit_state(candidate, self._prepare_hardware, owner=task_id)

    def _prepare_hardware(self):
        if self.quiescing_assists and self.operations is not None:
            pending = [operation for operation in self.operations.operations.values()
                       if operation.kind == 'assist' and operation.phase not in FINISHED_STAGES]
            for operation in pending:
                if operation.phase == 'running':
                    self.operations.stop(operation.route['ace'], operation.route['slot'],
                                         'prepare path calibration')
            if pending:
                return
        self.quiescing_assists = False
        if self._saved_environment["dry_auto_roll"]:
            for idx, info in self.ace._info_per_ace.items():
                dryer = info.get("dryer_status") or {}
                if dryer.get("status") not in ("drying", "keeping"):
                    continue
                target, remaining = dryer.get("target_temp"), dryer.get("remain_time")
                if type(target) is not int or type(remaining) is not int or remaining <= 0:
                    raise PathStateError("当前滚动烘干剩余任务需要先确认")
                self._saved_environment["dryers"].append({
                    "ace": idx, "target_temp": target, "remain_time": remaining})
        # The existing material database determines each known source's unload
        # temperature. Warm independent heads while the first head prepares.
        heads = sorted({lane["head"] for lane in self.task["lanes"] if lane["included"]})
        for head in heads:
            if not self.head_sensor(head) or self.ace._head_source.get(head) is None:
                continue
            mapping = getattr(self.ace, "EXTRUDER_MAP", {}).get(head)
            if mapping is None:
                continue
            module, channel = mapping
            feed = self.ace.printer.lookup_object("filament_feed " + module, None)
            name = "extruder" if head == 0 else "extruder%d" % head
            extruder = self.ace.printer.lookup_object(name, None)
            if feed is None or extruder is None:
                raise PathStateError("喷头准备所需送料模块不可用")
            heater = extruder.get_heater()
            target = feed._get_filament_unload_temp(channel)
            previous = heater.target_temp
            self._saved_environment["heaters"][str(head)] = {
                "previous": previous, "target": target}
        self._commit(self._apply_hardware_preparation)

    def _apply_hardware_preparation(self):
        self.ace.dry_auto_roll = False
        disable = getattr(self.ace, "_disable_feed_assist_all", None)
        if disable is not None:
            disable()
        for dryer in self._saved_environment["dryers"]:
            response = self.ace._tipform_send(dryer["ace"], {"method": "drying", "params": {
                "temp": dryer["target_temp"],
                "duration": max(1, math.ceil(dryer["remain_time"] / 60)),
                "auto_roll": False, "fan_speed": 7000}})
            if self.ace._tipform_rejected(response):
                raise PathStateError("暂停滚动烘干尚未确认")
        for head, temperatures in self._saved_environment["heaters"].items():
            name = "extruder" if int(head) == 0 else "extruder" + head
            self.ace.printer.lookup_object(name).get_heater().set_temp(temperatures["target"])
        self._prepare_next()

    def _prepare_next(self):
        if self.stop_requested or self.cancel_requested:
            self._stopped_without_motion()
            return
        for lane in self.task["lanes"]:
            if not lane["included"] or lane["status"] in ("empty", "skipped"):
                continue
            head = lane["head"]
            if self.head_sensor(head):
                source = self.ace._head_source.get(head)
                self.task.update(slot=lane["slot"], head=head)
                if source is None:
                    self._set_message("喷头有料，请确认所选槽是实际供料来源",
                                      "confirm_source")
                    self._finish_actions()
                    self._commit()
                    return
                self._thermal_unload(lane, source)
                return
        self.task["stage"] = "measuring"
        self._advance_measurement()

    def _thermal_unload(self, lane, source=None):
        self.thermal_active = True
        self._set_message("正在准备喷头并完成必要的热卸载", busy=True)
        head = lane["head"]
        source = source or {"ace_index": lane["ace"], "slot": lane["slot"]}
        idx, slot = source.get("ace_index"), source.get("slot")
        if type(idx) is not int or type(slot) is not int or not 0 <= slot < 4:
            raise PathStateError("喷头当前来源身份无效")
        task_id = self.task["task_id"]
        self.task["preparing_source"] = {"ace": idx, "slot": slot, "head": head}
        self.ace._calibration_unload_begin(head, idx, slot)
        self.reactor.register_callback(lambda eventtime: self._run_thermal_unload(
            task_id, head))

    def _run_thermal_unload(self, task_id, head):
        try:
            if self.task.get("task_id") != task_id or not self.task.get("active"):
                return
            if self.stop_requested or self.cancel_requested:
                return
            self.ace.gcode.run_script("ACE_UNLOAD_HEAD HEAD=%d" % head)
            if self.head_sensor(head) or not getattr(self.ace, "_last_unload_ok", False):
                raise PathStateError("喷头热卸载尚未确认完成")
        except Exception as exc:
            if not self.stop_requested and not self.cancel_requested:
                self._fail(str(exc))
                return
        finally:
            self.thermal_active = False
            self.ace._calibration_unload_finish()
        if self.task.get("task_id") != task_id:
            return
        if self.stop_requested or self.cancel_requested:
            if self.store.pending is None:
                self._stopped_without_motion()
        else:
            self.task.pop("preparing_source", None)
            self._prepare_next()

    def _confirm_source(self, action_id, parameters):
        if self.task.get("wait_reason") != "confirm_source":
            raise PathStateError("当前无需确认供料来源")
        lane = self._current_lane()
        if parameters["slot"] != lane["slot"] or parameters["ace"] != lane["ace"]:
            raise PathStateError("确认来源与所选管路不一致")
        existing = self.ace._head_source.get(lane["head"])
        if existing is not None and (existing.get("ace_index"), existing.get("slot")) != (
                lane["ace"], lane["slot"]):
            raise PathStateError("喷头已有另一条已确认来源，请先卸载该来源")
        self._remember_action(action_id, self.task["task_id"], parameters)
        self.ace._head_source[lane["head"]] = {"ace_index": lane["ace"],
                                                 "slot": lane["slot"]}
        self.ace._save_head_source()
        self._commit(lambda: self._thermal_unload(lane))

    def _advance_measurement(self):
        pending = [lane for lane in self.task["lanes"] if lane["included"]
                   and lane["status"] not in ("empty", "skipped", "measured", "complete")]
        if not pending:
            measured = [lane for lane in self.task["lanes"] if lane["included"]
                        and lane["status"] == "measured"]
            self._finish_actions()
            if not measured:
                self.task.update(stage="complete", active=False)
                self._set_message("本台所选槽位为空，之后可单路补测")
                self._commit(self._restore_environment)
                return
            self.task["stage"] = "marking"
            self._choose_lane(measured[0])
            self._set_message("本台测长已完成，请逐管标记 Y 前停车点", "select")
            self._commit()
            return
        lane = pending[0]
        self._choose_lane(lane)
        motion = self.controller(lane["ace"], lane["slot"])
        sample = motion.snapshot()
        fresh_grip = (sample["inlet"] and sample["trusted"]
                      and sample.get("device_state") == HELD
                      and sample.get("motion_id") == 0
                      and sample.get("reference_flags", 0) & GRIP_COMPLETE
                      and 0 <= sample["position_mm"] <= 20)
        if fresh_grip:
            self._wait_for_insertion(already_gripped=True)
        elif sample["inlet"]:
            self._begin_motion("eject", -MAX_PATH_MM, -MAX_PATH_MM, MAX_PATH_MM)
        else:
            self._wait_for_insertion()

    def _choose_lane(self, lane):
        self.task.update(slot=lane["slot"], head=lane["head"],
                         position_mm=lane.get("position_mm"),
                         position_trusted=lane.get("position_trusted", False))

    def _wait_for_insertion(self, already_gripped=False):
        lane = self._current_lane()
        lane["status"] = "pending_insert"
        self.motion = self.motion_route = self.motion_kind = None
        message = ("本管已完成入口抓料，点击继续测长" if already_gripped
                   else "请将本管耗材重新插入 ACE，抓料后点击继续")
        self._set_message(message, "insert")
        self._finish_actions()
        self._commit()

    def _continue(self, action_id, parameters):
        if self.motion is not None or self.task.get("busy"):
            raise PathStateError("当前动作正在进行")
        lane = self._current_lane()
        motion = self.controller(lane["ace"], lane["slot"])
        sample = motion.snapshot()
        if self.task["wait_reason"] == "insert":
            if not sample["inlet"] or not sample["trusted"]:
                raise PathStateError("等待本次重新插入与抓料基准确认")
            if sample.get("device_state") != HELD:
                raise PathStateError("请等待设备完成短抓料并停稳")
            lane["grip_mm"] = finite_mm(sample["position_mm"], "本次抓料停放位置")
            lane["reference_boot_epoch"] = sample["boot_epoch"]
            lane["reference_insertion_epoch"] = sample["insertion_epoch"]
            self._remember_action(action_id, self.task["task_id"], parameters)
            if lane["head"] not in self.confirmed_heads:
                self._set_message("请目视确认通往该喷头的其他支路均停在 Y 前",
                                  "confirm_route")
                self._finish_actions()
                self._commit()
                return
            self._begin_motion("measure", MAX_PATH_MM, lane["grip_mm"], MAX_PATH_MM)
            return
        if self.task["wait_reason"] == "paused":
            self._remember_action(action_id, self.task["task_id"], parameters)
            if self.task["stage"] == "marking":
                self._begin_motion("seek_mark", MAX_PATH_MM, lane["grip_mm"], MAX_PATH_MM)
            else:
                self._begin_motion("measure", MAX_PATH_MM, lane["grip_mm"], MAX_PATH_MM)
            return
        raise PathStateError("当前阶段无需继续")

    def _confirm_route(self, action_id, parameters):
        if self.task.get("wait_reason") != "confirm_route":
            raise PathStateError("当前无需确认 Y 支路")
        lane = self._current_lane()
        self._group_capable(lane["head"])
        if self.head_sensor(lane["head"]):
            raise PathStateError("目标喷头仍有料")
        self.confirmed_heads.add(lane["head"])
        self._remember_action(action_id, self.task["task_id"], parameters)
        self._begin_motion("measure", MAX_PATH_MM, lane["grip_mm"], MAX_PATH_MM)

    def _skip(self, action_id, parameters):
        if self.motion is not None or self.task.get("busy"):
            raise PathStateError("请先停止当前动作后跳过")
        lane = self._current_lane()
        if lane is None or self.task.get("stage") != "measuring":
            raise PathStateError("当前路线无法跳过")
        sample = self.controller(lane["ace"], lane["slot"]).snapshot()
        grip = lane.get("grip_mm")
        if sample["inlet"] and (not sample["trusted"] or grip is None
                                or sample["position_mm"] > grip):
            raise PathStateError("先将该管退出到安全位，再跳过")
        self._remember_action(action_id, self.task["task_id"], parameters)
        lane["status"] = "skipped"
        self._commit(self._advance_measurement)

    def _select(self, action_id, parameters):
        if self.task.get("stage") != "marking" or self.motion is not None:
            raise PathStateError("请等待当前管路停稳后选择")
        lane = next((lane for lane in self.task["lanes"]
                     if lane["slot"] == parameters["slot"] and lane["included"]), None)
        if lane is None or lane["status"] not in ("measured", "complete"):
            raise PathStateError("该管需要先测长")
        self._choose_lane(lane)
        self._remember_action(action_id, self.task["task_id"], parameters)
        self._begin_motion("seek_mark", MAX_PATH_MM, lane["grip_mm"], MAX_PATH_MM)

    def _begin_motion(self, kind, target, lower, upper, speed=LONG_SPEED):
        lane = self._current_lane()
        route = self.route(lane["ace"], lane["slot"], lane["head"])
        if route["route_id"] != lane["route_id"] or route["topology_version"] != self.task["topology_version"]:
            raise PathStateError("本次校准的设备或接管方式已改变")
        if kind != "eject":
            self._group_capable(lane["head"])
        candidate = self.store.snapshot()
        head_key = str(lane["head"])
        owner = candidate["occupied_heads"].get(head_key)
        if owner is not None and owner["route_id"] != lane["route_id"]:
            raise PathStateError("另一条路线仍可能占用该 Y 公共段")
        candidate["occupied_heads"][head_key] = {
            "route_id": lane["route_id"], "task_id": self.task["task_id"],
            "state": "moving", "kind": kind}
        self.motion = self.controller(lane["ace"], lane["slot"])
        self.motion_kind, self.motion_route, self.motion_target = kind, route, target
        self.edge_position = None
        self.task.update(busy=True, wait_reason="none", position_trusted=False)
        lane["status"] = "marking" if self.task["stage"] == "marking" else "measuring"
        self._set_message({"eject": "正在连续退空本管，随后重新插入",
                           "measure": "正在连续测长至喷头传感器",
                           "seek_mark": "正在连续寻回喷头参考点",
                           "return": "正在连续退回 ACE 安全位",
                           "jog": "正在定位停车点"}[kind], busy=True)
        motion = self.motion
        action = uuid.uuid4().hex
        task_id = self.task["task_id"]

        def begin(snapshot, error):
            if (self.task.get("task_id") != task_id or self.motion is not motion
                    or not self.task.get("active")):
                return
            if error:
                self._fail(str(error))
                return
            if self.stop_requested or (self.cancel_requested and kind != "return"):
                self.request_stop(self.task["task_id"], uuid.uuid4().hex,
                                  self.cancel_requested)
                return
            if kind == "eject":
                motion.eject_to_empty(action, MAX_PATH_MM, speed)
            else:
                motion.start(action, target, speed)

        def authorize():
            if (self.task.get("task_id") != task_id or self.motion is not motion
                    or self.stop_requested or not self.task.get("active")):
                return
            direction = -1 if kind == "eject" else None
            corridor = self._stop_corridor(lane)
            hard_lower = lower if kind == "eject" else max(0, lower - corridor)
            hard_upper = min(MAX_PATH_MM, upper + corridor)

            def identified(error):
                if self.motion is not motion or self.task.get('task_id') != task_id:
                    return
                if error is not None:
                    self._fail(str(error))
                    return
                if not self.stop_requested:
                    motion.bind(lower_mm=hard_lower, upper_mm=hard_upper, direction=direction,
                                callback=begin)

            self.refresh_identity(idx=route['ace'], expected_uid=route['uid'], callback=identified)

        self.commit_state(candidate, authorize, owner=self.task["task_id"])

    def _jog_forward(self, action_id, parameters):
        self._jog(action_id, parameters, 1)

    def _jog_back(self, action_id, parameters):
        self._jog(action_id, parameters, -1)

    def _jog(self, action_id, parameters, direction):
        if not self._can_extend(direction):
            raise PathStateError("请等待所选管路参考点确认或停止后换向")
        step = parameters["step"]
        if step not in STEPS:
            raise PathStateError("定位步长应为 10、50 或 100 mm")
        lane = self._current_lane()
        if direction > 0 and self.head_sensor(lane["head"]):
            raise PathStateError("喷头已到料，请先后退定位")
        start = self.motion_target if self.motion is not None else lane["position_mm"]
        target = finite_mm(start + direction * step, "定位目标",
                           minimum=lane["grip_mm"],
                           maximum=lane["length_mm"])
        if target == start:
            raise PathStateError("已达到该方向的定位范围")
        self._remember_action(action_id, self.task["task_id"], parameters)
        self.task["jog_direction"] = direction
        if self.motion is None:
            self._begin_motion("jog", target, lane["grip_mm"], lane["length_mm"], JOG_SPEED)
        else:
            motion = self.motion
            current_action = motion.snapshot()["action_id"]
            self.motion_target = target
            self._set_message("正在连续定位停车点", busy=True)
            self._commit(lambda: motion.update(current_action, target_mm=target))

    def _return(self, action_id, parameters):
        if self.motion is not None:
            raise PathStateError("请先停止当前动作")
        lane = self._current_lane()
        if lane is None or not lane.get("position_trusted"):
            raise PathStateError("当前位置待核，请先重新建立参考")
        self._remember_action(action_id, self.task["task_id"], parameters)
        self._begin_motion("return", lane["grip_mm"], lane["grip_mm"], MAX_PATH_MM)

    def _save(self, action_id, parameters):
        lane = self._current_lane()
        if (self.task.get("stage") != "marking" or self.motion is not None
                or not lane.get("position_trusted")):
            raise PathStateError("请等待所选管路停稳后保存")
        margin = self._minimum_margin()
        if margin is None:
            raise PathStateError("该入料基准的重复精度尚待实测验证")
        length = finite_mm(lane["length_mm"], "测长", minimum=1)
        park = finite_mm(lane["position_mm"], "停车位置", minimum=0.1)
        if park >= length or park <= lane["grip_mm"] or self.head_sensor(lane["head"]):
            raise PathStateError("请将料头退到 Y 前独立支路内")
        geometry = {"reference": dict(REFERENCE), "length_mm": length,
                    "return_mm": length - park, "park_mm": park,
                    "uncertainty_mm": (lane["reference_uncertainty_mm"]
                                       + lane.get("stop_uncertainty_mm", 0) + ENCODER_QUANTUM_MM),
                    "margin_mm": margin,
                    "reference_speed_mm_s": LONG_SPEED}
        if park <= 20 + geometry["uncertainty_mm"]:
            raise PathStateError("停车位置需要留出完整入口抓料范围")
        PathStore.validate_geometry(geometry)
        self._remember_action(action_id, self.task["task_id"], parameters)
        lane.update(park_mm=park, status="complete")
        candidate = self.store.snapshot()
        candidate["routes"][lane["route_id"]]["geometry"] = geometry
        candidate["routes"][lane["route_id"]]["position"] = self._position_record(lane, "parked")
        candidate["occupied_heads"].pop(str(lane["head"]), None)
        remaining = [value for value in self.task["lanes"] if value["included"]
                     and value["status"] == "measured"]
        self._finish_actions()
        if remaining:
            self._choose_lane(remaining[0])
            self._set_message("停车点已保存，请选择下一管定位", "select")
        else:
            self.task.update(stage="complete", active=False)
            self._set_message("本台所选路线校准已完成")
        self.commit_state(candidate, self._restore_environment, owner=self.task["task_id"])

    def _position_record(self, lane, state="held"):
        sample = self.controller(lane["ace"], lane["slot"]).snapshot()
        return {"position_mm": sample["position_mm"], "trusted": sample["trusted"],
                "state": state, "task_id": self.task["task_id"],
                "boot_epoch": sample["boot_epoch"],
                "insertion_epoch": sample["insertion_epoch"],
                "motion_id": sample["motion_id"]}

    def request_stop(self, task_id, action_id, cancel=False):
        if task_id != self.task.get("task_id") or not task_id:
            raise PathStateError("停止请求对应的任务已改变")
        parameters = {"op": "cancel" if cancel else "stop"}
        if action_id in self.actions:
            self._remember_action(action_id, task_id, parameters)
            return self.status()
        self._remember_action(action_id, task_id, parameters)
        if not self.task.get("active") and self.motion is None:
            self._finish_actions()
            if self.store.pending is None and not self.store.error:
                self._commit()
            return self.status()
        self.cancel_requested = self.cancel_requested or cancel
        self.stop_requested = True
        if self.after_commit_owner == task_id:
            self.after_commit = None
            self.after_commit_owner = None
        self.task.update(busy=True, stage="cancelling" if cancel else self.task["stage"],
                         message="正在停止并确认当前位置", wait_reason="none",
                         version=self.task["version"] + 1)
        if self.thermal_active:
            self.ace._calibration_unload["cancel_requested"] = True
            self.ace._calibration_unload_stop_hardware()
        if self.motion is not None:
            state = self.motion.snapshot()["state"]
            if state in ("starting", "running", "stopping"):
                self.motion.stop(reason="task cancelled" if cancel else "user stop")
            else:
                sample = self.motion.snapshot()
                lane = self._current_lane()
                if lane is not None:
                    lane.update(position_mm=sample["position_mm"],
                                position_trusted=sample["trusted"])
                self.motion = self.motion_kind = self.motion_route = None
                if self.store.pending is None:
                    self._stopped_without_motion()
        elif self.store.pending is None and not self.thermal_active:
            self._stopped_without_motion()
        return self.status()

    def _stopped_without_motion(self):
        lane = self._current_lane()
        if (self.cancel_requested and lane is not None
                and lane.get("position_trusted") and lane.get("grip_mm") is not None):
            geometry = self.store.state["routes"][lane["route_id"]].get("geometry")
            target = geometry["park_mm"] if geometry else lane["grip_mm"]
            if abs(lane["position_mm"] - target) > self._stop_corridor(lane):
                self.stop_requested = False
                self._begin_motion("return", target, lane["grip_mm"], MAX_PATH_MM)
                return
        self.task["busy"] = False
        self._finish_actions()
        if self.cancel_requested:
            self.task.update(stage="cancelled", active=False)
            sample = self.controller(lane['ace'], lane['slot']).snapshot() if lane is not None else {}
            verified_empty = (sample.get('inlet') is False
                              and sample.get('state') in ('complete', 'cancelled')
                              and sample.get('stop_reason') in (REVOKED, INLET_REMOVED))
            unknown = lane is not None and not verified_empty and (
                not lane.get("position_trusted") or lane.get("grip_mm") is None)
            if unknown:
                lane.update(status="position_unknown", position_trusted=False)
            elif verified_empty:
                lane['status'] = 'empty'
            self._set_message("已停止，所选路线位置待核，已完成进度已保存" if unknown
                              else "校准已安全收尾，已完成进度已保存")
        else:
            if self.task["stage"] == "marking" and lane is not None and lane.get("position_trusted"):
                self._set_message("已停稳，可继续定位或保存停车点")
            else:
                self._set_message("当前动作已停止，可继续原任务", "paused")
        self.stop_requested = False
        self._commit(self._restore_environment if self.cancel_requested else None)

    def stop_endpoint(self, request):
        self._control_endpoint(request, False)

    def cancel_endpoint(self, request):
        self._control_endpoint(request, True)

    def _control_endpoint(self, request, cancel):
        try:
            result = self.request_stop(request.get_str("task_id"),
                                       request.get_str("action_id"), cancel)
            request.send({"ok": True, "path_calibration": result})
        except (PathStateError, MotionError) as exc:
            raise request.error(str(exc))

    def _motion_finished(self, sample):
        lane = self._current_lane()
        lane.update(position_mm=sample["position_mm"], position_trusted=sample["trusted"])
        kind = self.motion_kind
        self.motion = self.motion_route = self.motion_kind = None
        self.task.update(position_mm=sample["position_mm"],
                         position_trusted=sample["trusted"], busy=False)
        if self.stop_requested:
            self._stopped_without_motion()
            return
        if kind == "eject":
            if sample["inlet"]:
                raise PathStateError("退空行程结束后入口仍有料")
            candidate = self.store.snapshot()
            candidate["routes"][lane["route_id"]]["position"] = self._position_record(lane, "empty")
            candidate["occupied_heads"].pop(str(lane["head"]), None)
            self.commit_state(candidate, self._wait_for_insertion, owner=self.task["task_id"])
        elif kind in ("measure", "seek_mark"):
            if not self.head_sensor(lane["head"]) or not sample["trusted"]:
                raise PathStateError("完整测长行程结束，喷头到料参考尚未确认")
            length = finite_mm(sample["position_mm"], "实测管长", minimum=1)
            if self.edge_position is not None:
                lane["stop_uncertainty_mm"] = max(
                    lane.get("stop_uncertainty_mm", 0), abs(length - self.edge_position))
            old_length = lane.get("length_mm")
            if old_length is not None:
                lane["stop_uncertainty_mm"] = max(
                    lane.get("stop_uncertainty_mm", 0), abs(length - old_length))
            lane.update(length_mm=length, status="measured")
            candidate = self.store.snapshot()
            candidate["routes"][lane["route_id"]]["basic"] = {
                "length_mm": length, "reference": dict(REFERENCE),
                "grip_mm": lane["grip_mm"],
                "reference_speed_mm_s": LONG_SPEED,
                "boot_epoch": sample["boot_epoch"],
                "insertion_epoch": sample["insertion_epoch"]}
            if kind == "measure":
                self.commit_state(candidate, lambda: self._begin_motion(
                    "return", lane["grip_mm"], lane["grip_mm"], MAX_PATH_MM),
                    owner=self.task["task_id"])
            else:
                self._finish_actions()
                self._set_message("请逐管退到 Y 前，并保留所示停车间隙")
                self.commit_state(candidate)
        elif kind == "return":
            if not sample["trusted"] or self.head_sensor(lane["head"]):
                raise PathStateError("回程的安全位置尚未确认")
            candidate = self.store.snapshot()
            candidate["routes"][lane["route_id"]]["position"] = self._position_record(lane)
            candidate["occupied_heads"].pop(str(lane["head"]), None)
            self._finish_actions()
            if self.cancel_requested:
                self.task.update(stage="cancelled", active=False)
                self._set_message("校准已安全收尾，进度已保存")
                self.commit_state(candidate, self._restore_environment, owner=self.task["task_id"])
            elif self.task["stage"] == "measuring":
                lane["status"] = "measured"
                self.commit_state(candidate, self._advance_measurement, owner=self.task["task_id"])
            else:
                lane["status"] = "measured"
                self._set_message("已回到 ACE 安全位，可重新选择该管", "select")
                self.commit_state(candidate)
        else:
            self._finish_actions()
            self._set_message("已停稳，可继续定位或保存停车点")
            candidate = self.store.snapshot()
            candidate["routes"][lane["route_id"]]["position"] = self._position_record(lane)
            self.commit_state(candidate)

    def _probe(self):
        for idx in range(len(self.ace._ace_devices)):
            if not self.ace._connected_per_ace.get(idx) or not self.ace._is_v2_idx(idx):
                continue
            self._known_indices.add(idx)
            original = self.ace._serials.get(idx)
            if self._identifying.get(idx) is not original:
                self._identifying[idx] = original
                self.ace.send_request_to(idx, {"method": "discover_device"}, None)
            # Capability is per device. Only probe one lane until the
            # handshake succeeds; each controller owns its poll deadline.
            first = self.controller(idx, 0)
            if first.snapshot()['available']:
                for slot in range(1, 4):
                    self.controller(idx, slot)

    def _probe_idle(self):
        a = self.ace
        busy = ('_tag_read_busy', '_gen1_manual_busy', '_slot_web_rfid_pending',
                '_swap_in_progress', '_unload_all_active', '_insert_read_running',
                '_insert_abort_running')
        actions = getattr(a, '_slot_actions', None)
        printing = a.printer.lookup_object('print_stats', None)
        return (not any(getattr(a, name, False) for name in busy)
                and not any(getattr(a, '_slot_flight_active', lambda i: False)(i)
                            for i in a._connected_per_ace)
                and (actions is None or actions.state.get('state') not in ('preparing', 'running'))
                and (printing is None or printing.state not in ('printing', 'paused')))

    def tick(self, now):
        try:
            self._probe()
            probe_idle = self._probe_idle()
            for (idx, slot), controller in list(self.controllers.items()):
                sample = controller.snapshot()
                first = self.controllers.get((idx, 0))
                probe_lane = slot == 0 or (first is not None and first.snapshot()['available'])
                if sample['available'] or (probe_idle and probe_lane):
                    controller.tick()
            if self.operations is not None:
                self.operations.tick(now)
            if self.store.pending is not None:
                if not self.store.finish():
                    return now + 0.02
                follow = self.after_commit
                owner = self.after_commit_owner
                self.after_commit = None
                self.after_commit_owner = None
                if follow is not None and (owner is None or not self.stop_requested):
                    follow()
                if (self.stop_requested and self.motion is None and not self.thermal_active
                        and self.store.pending is None):
                    self._stopped_without_motion()
            if (self.stop_requested and self.motion is None and not self.thermal_active
                    and self.store.pending is None):
                self._stopped_without_motion()
            if (self.quiescing_assists and self.task.get('stage') == 'preparing'
                    and self.store.pending is None):
                self._prepare_hardware()
            if self.motion is not None:
                self._require_idle()
                if self.task["topology_version"] != self._topology_version():
                    raise PathStateError("运动期间管路绑定已改变")
                self._group_capable(self.task["head"])
                sample = self.motion.snapshot()
                self.task.update(position_mm=sample["position_mm"],
                                 position_trusted=sample["trusted"])
                if sample["state"] == "unknown":
                    raise PathStateError(sample.get("error") or "运动位置证据不足")
                sensor_terminated = (self.motion_kind in ("measure", "seek_mark")
                                     or self.motion_kind == "jog"
                                     and self.task.get("jog_direction") == 1)
                if (sensor_terminated
                        and sample["state"] in ("starting", "running")
                        and self.head_sensor(self.task["head"])):
                    self.edge_position = sample["position_mm"]
                    self.motion.stop(reason="head sensor reached")
                elif sample["state"] in TERMINAL_MOTION_STATES:
                    if self.store.pending is None:
                        self._motion_finished(sample)
            return now + 0.05
        except Exception as exc:
            logging.exception("[multiACE] managed path calibration failed")
            self._fail(str(exc))
            return now + 0.1

    def _fail(self, message):
        motion = self.motion
        route = self.motion_route or {}
        self.motion = self.motion_kind = self.motion_route = None
        if self.after_commit_owner == self.task["task_id"]:
            self.after_commit = None
            self.after_commit_owner = None
        self.stop_requested = self.cancel_requested = False
        if motion is not None:
            try:
                motion.stop(reason=message)
            except Exception:
                self.ace._latch_stop_fault(self.task.get("ace", 0),
                                           self.task.get("slot", 0), message)
        self.task.update(stage="error", active=False, busy=False, error=message,
                         message=message, position_trusted=False,
                         version=self.task["version"] + 1)
        lane = self._current_lane()
        if lane:
            lane.update(status="position_unknown", position_trusted=False)
        self._finish_actions(message)
        if self.store.error:
            if self.operations is not None:
                self.operations.persistence_failed(message)
            idx = route.get('ace', self.task.get('ace'))
            slot = route.get('slot', self.task.get('slot'))
            self.ace._latch_stop_fault(idx if type(idx) is int else 0,
                                       slot if type(slot) is int else 0,
                                       '管路持久状态无法确认 ' + message)
        if self.store.pending is None and not self.store.error:
            try:
                self._commit(self._restore_environment)
            except Exception:
                self.ace._latch_stop_fault(self.task.get("ace", 0),
                                           self.task.get("slot", 0),
                                           "管路故障状态保存失败")
        self._restore_environment()

    def _restore_environment(self):
        if self.task.get("active"):
            return
        environment = self._saved_environment or self.task.get("environment")
        if not environment:
            return
        for head, temperatures in environment.get("heaters", {}).items():
            name = "extruder" if int(head) == 0 else "extruder" + head
            extruder = self.ace.printer.lookup_object(name, None)
            if extruder is not None:
                target = temperatures["previous"] if self.task.get("stage") == "complete" else 0
                extruder.get_heater().set_temp(target)
        self.ace.dry_auto_roll = environment["dry_auto_roll"]
        self._saved_environment = None
        self.quiescing_assists = False
        self.task.pop("environment", None)
        self.confirmed_heads.clear()

    def _install_print_hooks(self):
        if self.task.get("stage") == "error" and self.task.get("environment"):
            self._restore_environment()
        for name in ("SDCARD_PRINT_FILE", "M24", "ACE_PRINT_PREFLIGHT"):
            if name in self._print_hooks:
                continue
            previous = self.ace.gcode.register_command(name, None)
            if previous is None:
                continue
            self._print_hooks[name] = previous
            def guarded(gcmd, original=previous):
                if self.task.get("active"):
                    raise gcmd.error("请先完成或取消料管校准，再开始打印")
                return original(gcmd)
            self.ace.gcode.register_command(name, guarded)

    def shutdown(self):
        if self.motion is not None:
            try:
                self.motion.stop(reason="printer shutdown")
            except Exception:
                logging.exception("[multiACE] managed motion shutdown STOP failed")
        self.store.close()
