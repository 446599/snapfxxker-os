"""Scoped normal-load speed policy; unknown starting parks use 10 mm/s."""

import logging

UID = (808780566, 17445170, 942880330)
TARGET = 1397 - 200


class NormalLoadSpeed:
    def __init__(self, ace):
        self.ace = ace
        self.park = None
        self.active = False
        self.transport = None
        self.token = None
        self.timer = None
        self.ack = False
        self.ready = False
        self.pending = False
        self.fail = None
        self.slow_sent = False
        self.slow_ack = False
        self.value = None
        self.last_valid = None
        self.before = None
        self.epoch = None
        self.spool = None
        self.fast = False
        self.last_progress = None
        self.slow_start = None
        self.print_state = None

    def record_park(self, transport):
        a = self.ace
        epochs = getattr(a, "_insert_epochs", {}) or {}
        spool = (getattr(a, "_rc_last_uid", {}) or {}).get((0, 3))
        ident = (getattr(a, "_location_uid_by_idx", {}) or {}).get(0)
        gates = a._gate_status_per_ace.get(0) or []
        if (
            a._serials.get(0) is not transport
            or not a._connected_per_ace.get(0)
            or (0, 3) not in epochs
            or not spool
            or not ident
            or ident[0] is not transport
            or tuple(ident[1]) != UID
            or len(gates) <= 3
            or gates[3] != 1
        ):
            self.park = None
            return
        self.park = (transport, epochs[(0, 3)], spool)

    def _identity(self):
        a = self.ace
        g = a._gate_status_per_ace.get(0) or []
        return (
            a._serials.get(0) is self.transport
            and a._connected_per_ace.get(0)
            and not a._serial_failed_per_ace.get(0)
            and len(g) > 3
            and g[3] == 1
            and (getattr(a, "_insert_epochs", {}) or {}).get((0, 3), 0) == self.epoch
            and (getattr(a, "_rc_last_uid", {}) or {}).get((0, 3)) == self.spool
        )

    def _sensor(self):
        obj = self.ace.printer.lookup_object("filament_motion_sensor e3_filament", None)
        pin = getattr(obj, "runout_buttun_state", None) if obj is not None else None
        if type(pin) is not bool:
            raise RuntimeError("喷头原始传感器未知")
        return pin

    def should_use(self, idx, slot, head):
        if (idx, slot, head) != (0, 3, 3) or self.ace._swap_in_progress:
            return False
        state = self.ace.printer.lookup_object("print_stats", None)
        if state is None:
            raise RuntimeError("打印状态不可确认")
        value = (getattr(state, "state", "") or "").lower()
        if value == "printing":
            return False
        if value not in ("standby", "complete", "cancelled", "paused"):
            raise RuntimeError("打印状态不可确认")
        return True

    def begin(self, idx, slot, head, attempt):
        if (idx, slot, head) != (0, 3, 3):
            return None
        a = self.ace
        self.close()
        transport = a._serials.get(0)
        ref = self.park
        self.park = None
        ps = a.printer.lookup_object("print_stats", None)
        self.print_state = (
            (getattr(ps, "state", "") or "").lower() if ps is not None else ""
        )
        if self.print_state not in ("standby", "complete", "cancelled", "paused"):
            raise RuntimeError("打印状态不可确认")
        self.transport = transport
        self.epoch = (getattr(a, "_insert_epochs", {}) or {}).get((0, 3), 0)
        self.spool = (getattr(a, "_rc_last_uid", {}) or {}).get((0, 3))
        if self._sensor():
            raise RuntimeError("喷头已有料，拒绝送料")
        if not self._identity():
            raise RuntimeError("ACE连接或入口不可确认")

        def identity_monitor():
            if not self._identity() or self._sensor():
                raise RuntimeError("设备身份核对期间状态改变")

        identity = a._slot_actions._request(
            0, "discover_device", {}, 0, monitor=identity_monitor
        )
        uid = tuple(identity.get("uid%d" % i) for i in (1, 2, 3))
        if any(type(x) is not int for x in uid):
            raise RuntimeError("ACE硬件身份不可确认")
        if uid != UID:
            return None
        eligible = (
            attempt == 0
            and ref is not None
            and ref
            == (
                transport,
                self.epoch,
                (getattr(a, "_rc_last_uid", {}) or {}).get((0, 3)),
            )
            and not a._location_hold.active
        )

        def monitor():
            if not self._identity() or self._sensor():
                raise RuntimeError("送料起点已改变")

        try:
            gate, dec = a._slot_actions._stable(
                0, 3, allow_initial_zero=True, monitor=monitor
            )
            if not gate or type(dec[1]) is not int:
                raise RuntimeError("起点编码器不可确认")
            raw = dec[1]
            self.before = raw - (1 << 64) if raw >= (1 << 63) else raw
        except Exception:
            logging.exception("[multiACE] normal load baseline unavailable")
            raise RuntimeError("送料起点遥测不可确认")
        self.active = True
        self.token = object()
        self.ack = False
        self.ready = False
        self.pending = False
        self.fail = None
        self.slow_sent = False
        self.slow_ack = False
        self.value = None
        self.fast = eligible
        self.last_valid = a.reactor.monotonic()
        self.last_progress = self.last_valid
        token = self.token

        def tick(eventtime):
            try:
                if not self.active or self.token is not token:
                    return a.reactor.NEVER
                if not self._identity():
                    self.fail = "ACE连接或入口改变"
                    return a.reactor.NEVER
                if self._sensor():
                    return eventtime + 0.05
                if self.ack and not self.pending:
                    self.pending = True
                    self._poll_status(token)
            except Exception as exc:
                self.fail = str(exc)
                return a.reactor.NEVER
            return eventtime + 0.2

        self.timer = a.reactor.register_timer(tick, a.reactor.monotonic() + 0.1)
        return 40 if eligible else 10

    def stop_bound(self):
        a = self.ace
        transport = self.transport
        if transport is None or a._serials.get(0) is not transport:
            a._latch_stop_fault(0, 3, "normal load original transport unavailable")
            a._require_no_stop_fault()
        box = [None]

        def cb(self, response):
            box[0] = response if response is not None else {}

        req = {
            "method": "stop_feed_filament",
            "params": {"index": 3},
            "_insert_stop_transport": transport,
            "_insert_reject": cb,
        }
        a.send_request_to(0, req, cb)
        deadline = a.reactor.monotonic() + 2
        while box[0] is None and a.reactor.monotonic() < deadline:
            if a._serials.get(0) is not transport:
                break
            a.reactor.pause(min(deadline, a.reactor.monotonic() + 0.05))
        if a._serials.get(0) is not transport or not self._ok(box[0], 9):
            a._latch_stop_fault(0, 3, "normal load original transport STOP unconfirmed")
            a._require_no_stop_fault()

    def feed_ack(self, response, token):
        if not self.active or self.token is not token:
            return
        if not self._ok(response, 8):
            self.fail = "送料ACK未确认"
            return
        self.ack = True

    def _ok(self, r, cmd):
        return (
            isinstance(r, dict)
            and r.get("_v2_frame_valid") is True
            and r.get("_v2_payload_valid") is True
            and r.get("_v2_command") == cmd
            and type(r.get("code")) is int
            and r["code"] == 0
            and str(r.get("msg", "")).strip().upper() != "FORBIDDEN"
        )

    def _poll_status(self, token):
        a = self.ace

        def cb(self, response):
            try:
                if (
                    not self._normal_load_speed.active
                    or self._normal_load_speed.token is not token
                ):
                    return
                c = self._normal_load_speed
                if not c._identity() or not c._ok(response, 6):
                    c.fail = "送料状态不可确认"
                    c.pending = False
                    return
                data = response.get("result") or {}
                slots = data.get("slots") or []
                matches = [
                    s for s in slots if isinstance(s, dict) and s.get("index") == 3
                ]
                if (
                    data.get("_status_wire_valid") is not True
                    or len(matches) != 1
                    or matches[0].get("_wire_valid") is not True
                ):
                    c.fail = "送料状态无效"
                    c.pending = False
                    return
                if matches[0].get("slot_status") != "feeding":
                    c.pending = False
                    return
                c.ready = True
                c._poll_encoder(token)
            except Exception:
                self._normal_load_speed.fail = "送料状态读取异常"
                self._normal_load_speed.pending = False

        try:
            a.send_request_to(0, {"method": "get_status"}, cb)
        except Exception:
            self.pending = False
            self.fail = "送料状态查询失败"

    def _poll_encoder(self, token):
        a = self.ace

        def cb(self, response):
            try:
                c = self._normal_load_speed
                if not c.active or c.token is not token:
                    return
                c.pending = False
                if not c._identity() or not c._ok(response, 76):
                    c.fail = "送料编码器响应无效"
                    return
                data = response.get("result") or {}
                rows = data.get("feed_info") or []
                match = [r for r in rows if isinstance(r, dict) and r.get("index") == 3]
                if (
                    data.get("_feed_wire_valid") is not True
                    or len(match) != 1
                    or match[0].get("_wire_valid") is not True
                ):
                    c.fail = "送料编码器数据无效"
                    return
                row = match[0]
                value = row.get("decoder")
                present = row.get("_decoder_present")
                if (
                    type(value) is not int
                    or type(present) is not bool
                    or (not present and value != 0)
                ):
                    c.fail = "送料编码器不可用"
                    return
                value = value - (1 << 64) if value >= (1 << 63) else value
                if value < 0:
                    c.fail = "送料编码器方向错误"
                    return
                if c.value is None:
                    if c.fast and not (value == 0 or c.before < 0 or value < c.before):
                        return
                elif value < c.value:
                    c.fail = "送料编码器回退或重置"
                    return
                if value != c.value:
                    c.last_progress = c.ace.reactor.monotonic()
                c.value = value
                c.last_valid = c.ace.reactor.monotonic()
            except Exception:
                self._normal_load_speed.fail = "送料编码器读取异常"
                self._normal_load_speed.pending = False

        try:
            a.send_request_to(0, {"method": "get_feed_info"}, cb)
        except Exception:
            self.pending = False
            self.fail = "送料编码器查询失败"

    def feed_request(self, request):
        if self.active:
            request["_normal_load_token"] = self.token

    def allows_feed(self, idx, request, serial):
        try:
            params = request.get("params") or {}
            return bool(
                self.active
                and idx == 0
                and serial is self.transport
                and self._identity()
                and not self._sensor()
                and request.get("_normal_load_token") is self.token
                and request.get("method") == "feed_filament"
                and params.get("index") == 3
                and params.get("speed") == (40 if self.fast else 10)
                and type(params.get("length")) is int
                and 0 < params["length"] <= 2100
            )
        except Exception:
            return False

    def allows_speed(self, idx, request, serial):
        try:
            return bool(
                self.active
                and idx == 0
                and serial is self.transport
                and self._identity()
                and not self._sensor()
                and request.get("_normal_speed_token") is self.token
                and request.get("method") == "update_feeding_speed"
                and request.get("params") == {"index": 3, "speed": 10}
            )
        except Exception:
            return False

    def check(self):
        if not self.active:
            return
        if self._sensor():
            return
        self.ace._require_no_stop_fault()
        ps = self.ace.printer.lookup_object("print_stats", None)
        if ps is None or (getattr(ps, "state", "") or "").lower() != self.print_state:
            raise RuntimeError("打印状态改变")
        if not self._identity():
            raise RuntimeError("送料连接或入口改变")
        if self.fail:
            raise RuntimeError(self.fail)
        if self.ace.reactor.monotonic() - self.last_valid > 1.5:
            raise RuntimeError("送料编码器遥测超时")
        if self.ready and self.ace.reactor.monotonic() - self.last_progress > 8:
            raise RuntimeError("送料编码器无进展")
        if (
            self.slow_sent
            and not self.slow_ack
            and self.ace.reactor.monotonic() - self.slow_start > 0.5
        ):
            raise RuntimeError("降速ACK超时")
        if self.fast and self.value is not None and self.value >= 1497:
            raise RuntimeError("到达已知参考上限仍未触发喷头")
        if (
            self.fast
            and self.value is not None
            and self.value >= TARGET
            and not self.slow_sent
        ):
            self.slow_sent = True
            self.slow_start = self.ace.reactor.monotonic()
            token = self.token

            def cb(self, response):
                c = self._normal_load_speed
                if not c.active or c.token is not token:
                    return
                if not c._ok(response, 10):
                    c.fail = "降速ACK未确认"
                else:
                    c.slow_ack = True

            req = {
                "method": "update_feeding_speed",
                "params": {"index": 3, "speed": 10},
                "_normal_speed_token": token,
                "_insert_reject": cb,
            }
            self.ace.send_request_to(0, req, cb)

    def wrote(self, idx, request):
        if idx != 0:
            return
        method = request.get("method")
        params = request.get("params") or {}
        if (
            method
            in (
                "feed_filament",
                "unwind_filament",
                "feed_or_rollback_raw",
                "start_feed_assist",
                "update_feeding_speed",
            )
            and params.get("index") == 3
        ):
            own = (
                self.active
                and self.token is not None
                and (
                    request.get("_normal_load_token") is self.token
                    or request.get("_normal_speed_token") is self.token
                )
            )
            if not own:
                self.park = None
        if method in ("drying", "drying_raw") and params.get("auto_roll"):
            self.park = None
        if method == "raw":
            self.park = None

    def close(self):
        self.active = False
        self.token = None
        self.pending = False
        if self.timer is not None:
            try:
                self.ace.reactor.unregister_timer(self.timer)
            except Exception:
                pass
            self.timer = None
