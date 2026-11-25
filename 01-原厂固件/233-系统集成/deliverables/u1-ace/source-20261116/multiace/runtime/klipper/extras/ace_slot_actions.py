"""Bounded, fail-closed ACE2 slot ejection; no heating or head motion."""
import logging, os, json, stat, time, hashlib, secrets
from .ace_location_hold import PATH, PORT, UID, LocationHold, RouteChanged
STEP_MM = 20
SPEED_MM_S = 80
MAX_MM = 5000  # hard command budget; inlet sensor ends the move
MAX_SECONDS = 480

class SlotActions:
    def __init__(self, ace):
        self.ace = ace
        self.seq = 0
        self.epoch = secrets.token_hex(8)
        self.state = {'seq': 0, 'state': 'idle', 'kind': '', 'ace': None,
                      'slot': None, 'progress': 0, 'limit': MAX_MM, 'message': ''}
        self.cancel = False
        self._eject_lease = None
        self._eject_pending = None
        self._prepare_command = None
        ace.printer.lookup_object('webhooks').register_endpoint('multiace/slot_eject_cancel', self.cancel_endpoint)
        ace.printer.lookup_object('webhooks').register_endpoint('multiace/slot_operation_cancel', self.operation_cancel_endpoint)

    def operation_cancel_endpoint(self, request):
        """Cancel only the task the screen actually observed, outside gcode's lock."""
        epoch = request.get_str('epoch', '')
        if epoch and epoch != self.epoch:
            request.send({'cancelled': False, 'message': '设备已重连，旧任务不能停止新任务'})
            return
        kind = request.get_str('kind')
        idx, slot, seq = (request.get_int(key) for key in ('ace', 'slot', 'seq'))
        a = self.ace
        if kind == 'rfid':
            matches = (getattr(a, '_tag_read_busy', False)
                       and getattr(a, '_tag_op_kind', None) in ('read', 'insert')
                       and (a._tag_op_ace, a._tag_op_slot, a._tag_op_seq) == (idx, slot, seq))
        else:
            matches = (kind in ('eject', 'insert') and self.state.get('kind') == kind
                       and self.state.get('state') in ('preparing', 'running')
                       and (self.state.get('ace'), self.state.get('slot'), self.state.get('seq')) == (idx, slot, seq))
        if not matches:
            request.send({'cancelled': False, 'message': '任务已结束或已变化'})
            return
        self.cancel = True
        if kind == 'eject' and self.state.get('state') == 'running':
            self._stop_controlled_eject()
        self.state['message'] = ('喷头卸载结束后停止，不再退出 ACE'
                                 if self.state.get('state') == 'preparing'
                                 else '正在停止，请等待确认')
        request.send({'cancelled': True, 'state': 'cancelling'})

    def cancel_endpoint(self, request):
        if self.state['state'] != 'running' or self.state['kind'] != 'eject':
            request.send({'cancelled': False, 'state': self.state['state']})
            return
        self.cancel = True
        self._stop_controlled_eject()
        self.state['message'] = '正在停止，请等待确认'
        request.send({'cancelled': True, 'state': 'cancelling'})

    def status(self):
        return dict(self.state, epoch=self.epoch)

    def _check(self, idx, slot, transport=None, own=False, allow_tag=False, for_eject=False,
               allow_target_loaded=False):
        a = self.ace
        a._require_no_stop_fault()
        if self.state.get('state') == 'preparing' and not own:
            raise RuntimeError('正在退出喷头，请等待本次退出流程结束')
        path = getattr(a, '_path_calibration', None)
        if path is not None and path.task.get('active'):
            raise RuntimeError('后台料管校准正在进行')
        if (allow_tag and self.cancel and getattr(a, '_tag_read_busy', False)
                and getattr(a, '_tag_op_kind', None) in ('read', 'insert')
                and (getattr(a, '_tag_op_ace', None), getattr(a, '_tag_op_slot', None)) == (idx, slot)):
            raise RuntimeError('用户已停止识别')
        ps = a.printer.lookup_object('print_stats', None)
        if ps is None or getattr(ps, 'state', '').lower() not in ('standby', 'complete', 'cancelled', 'error'):
            raise RuntimeError('打印机不空闲或状态未知')
        if not a._is_v2_idx(idx) or not a._connected_per_ace.get(idx) or idx in a._fw_update_hold:
            raise RuntimeError('ACE 离线或不支持此操作')
        if a._serials.get(idx) is None:
            raise RuntimeError('ACE 通信不可用')
        if transport is not None and a._serials.get(idx) is not transport:
            raise RuntimeError('ACE 已断开或重新连接')
        if (a._swap_in_progress and not own) or a._unload_all_active or (not allow_tag and (getattr(a, '_tag_read_busy', False) or getattr(a, '_gen1_manual_busy', False))):
            raise RuntimeError('换料、卸载或标签操作正在进行')
        if (a._calibration or {}).get('state') not in ('idle', 'cancelled', 'complete', 'completed', 'error'):
            raise RuntimeError('校准正在进行')
        if getattr(a, '_calibration_unload', {}).get('active'):
            raise RuntimeError('校准退料正在进行')
        if not for_eject and a._get_heads_for_ace_slot(idx, slot):
            raise RuntimeError('请先从喷头卸载')
        bg = a.printer.lookup_object('ace_bg_swap', None)
        if bg is not None and getattr(bg, '_busy', False):
            raise RuntimeError('后台换料正在进行')
        guard = a.printer.lookup_object('ace_native_guard', None)
        if guard is not None and getattr(guard, 'state', None) != 'idle':
            raise RuntimeError('料槽保护正在运行或状态未知')
        heads = a._rfid_route_heads(idx, slot)
        if not heads:
            raise RuntimeError('请先设置 Multi 或 Head 绑定')
        for h in heads:
            sensor = a.printer.lookup_object('filament_motion_sensor e%d_filament' % h, None)
            present = getattr(sensor, 'runout_buttun_state', None)
            if type(present) is not bool:
                raise RuntimeError('目标喷头传感器暂不可用')
            if not present:
                continue
            source = a._head_source.get(h)
            if not isinstance(source, dict):
                raise RuntimeError('请选择当前喷头的供料槽')
            target = (source.get('ace_index'), source.get('slot')) == (idx, slot)
            if target and not (for_eject and allow_target_loaded):
                raise RuntimeError('请先从目标喷头卸载')

    def _request(self, idx, method, params, command, monitor=None, stop_transport=None, eject_token=None, policy_token=None, recovery_token=None):
        request = {'method': method, 'params': params}
        if monitor is None and stop_transport is None and eject_token is None and policy_token is None and recovery_token is None:
            r = self.ace._tipform_send(idx, request, timeout=3.0)
        else:
            attempts = 2 if method in ('get_status', 'get_key_state', 'get_feed_info') else 1
            r = None
            for attempt in range(attempts):
                request = {'method': method, 'params': params}
                box = [None]
                def callback(self, response, target=box):
                    target[0] = response if response is not None else {}
                if stop_transport is not None:
                    request['_insert_stop_transport'] = stop_transport
                    request['_insert_reject'] = callback
                if recovery_token is not None:
                    request['_recovery_owner'] = recovery_token
                    request['_insert_reject'] = callback
                    if method == 'insert_policy_set' and params.get('enabled') is False:
                        request['_recovery_mode_disable'] = True
                if policy_token is not None:
                    request['_insert_policy_owner'] = policy_token
                    request['_insert_reject'] = callback
                if eject_token is not None:
                    request['_eject_token'] = eject_token
                    request['_insert_reject'] = callback
                if monitor is None:
                    monitor = lambda: None
                monitor()
                self.ace.send_request_to(idx, request, callback)
                deadline = self.ace.reactor.monotonic() + 3.0
                while box[0] is None and self.ace.reactor.monotonic() < deadline:
                    monitor()
                    self.ace.reactor.pause(min(deadline, self.ace.reactor.monotonic() + .05))
                monitor()
                r = box[0]
                if r is not None:
                    if attempt:
                        logging.info('[multiACE] slot_eject telemetry reply ace=%d method=%s id=%s attempt=%d',
                                     idx, method, request.get('id'), attempt + 1)
                    break
                if attempt + 1 < attempts:
                    logging.info('[multiACE] slot_eject telemetry timeout ace=%d method=%s id=%s retry=%d',
                                 idx, method, request.get('id'), attempt + 1)
        if r is None:
            raise RuntimeError('%s 响应超时' % method)
        if not isinstance(r, dict) or r.get('_v2_frame_valid') is not True or r.get('_v2_payload_valid') is not True or r.get('_v2_command') != command:
            raise RuntimeError('%s 响应帧无效' % method)
        if type(r.get('code')) is not int or r['code'] != 0 or str(r.get('msg','')).upper() == 'FORBIDDEN':
            raise RuntimeError('%s 设备拒绝或响应码无效' % method)
        if not isinstance(r.get('result'), dict):
            raise RuntimeError('%s 数据缺失' % method)
        return r['result']

    def _lane_states(self, idx, monitor=None):
        st = self._request(idx, 'get_status', {}, 6, monitor=monitor)
        slots = st.get('slots')
        if st.get('_status_wire_valid') is not True or not isinstance(slots, list) or len(slots) != 4 or any(not isinstance(x, dict) for x in slots) or sorted(x.get('index') for x in slots if type(x.get('index')) is int) != [0, 1, 2, 3] or any(x.get('_wire_valid') is not True or not isinstance(x.get('slot_status'), str) or not x['slot_status'] for x in slots):
            raise RuntimeError('槽位状态数据不完整或无效')
        return {x['index']: x['slot_status'] for x in slots}

    def _lanes_ready(self, idx):
        lanes = self._lane_states(idx)
        busy = [(i + 1, st) for i, st in lanes.items() if st != 'ready']
        if busy:
            raise RuntimeError('槽位忙碌: ' + ', '.join('%s=%s' % x for x in busy))

    def _gate(self, idx, slot, monitor=None):
        keys = self._request(idx, 'get_key_state', {}, 73, monitor=monitor).get('fields')
        if not isinstance(keys, dict) or type(keys.get('1')) is not int:
            raise RuntimeError('入口传感器数据缺失')
        return bool(keys['1'] & (1 << (4 * slot)))

    def _sample(self, idx, slot, allow_initial_zero=False, monitor=None):
        lanes = self._lane_states(idx, monitor=monitor)
        if any(st != 'ready' for st in lanes.values()):
            raise RuntimeError('槽位状态不稳定或未知')
        gate = self._gate(idx, slot, monitor=monitor)
        feed = self._request(idx, 'get_feed_info', {}, 76, monitor=monitor)
        rows = feed.get('feed_info')
        if feed.get('_feed_wire_valid') is not True or not isinstance(rows, list):
            raise RuntimeError('编码器数据缺失')
        row = [x for x in rows if x.get('index') == slot]
        if len(row) != 1 or row[0].get('_wire_valid') is not True or type(row[0].get('decoder')) is not int or type(row[0].get('_decoder_present')) is not bool:
            raise RuntimeError('编码器数据不可用')
        present, decoder = row[0]['_decoder_present'], row[0]['decoder']
        if not present and decoder != 0:
            raise RuntimeError('编码器数据不可用')
        if allow_initial_zero:
            steps, length = row[0].get('steps'), row[0].get('length')
            if type(steps) is not int or steps < 0 or type(length) is not int or length < 0:
                raise RuntimeError('编码器基线数据不可用')
            return gate, (present, decoder), (steps, length)
        return gate, (present, decoder)

    def _stop(self, idx, slot, timeout=2.0):
        fault_identity = self.ace._location_identity(idx)
        a = self.ace
        r = a._checked_stop_request(idx, {'method': 'stop_feed_assist', 'params': {'index': slot}}, timeout=timeout)
        if r.get('_v2_frame_valid') is not True or r.get('_v2_payload_valid') is not True or r.get('_v2_command') != 9:
            a._latch_stop_fault(idx, slot, 'STOP response frame not confirmed', identity=fault_identity)
            a._require_no_stop_fault()
        return r

    def _stable(self, idx, slot, allow_initial_zero=False, monitor=None):
        samples = []
        for _ in range(3):
            samples.append(self._sample(idx, slot, allow_initial_zero=allow_initial_zero, monitor=monitor))
            end = self.ace.reactor.monotonic() + .25
            while self.ace.reactor.monotonic() < end:
                if monitor: monitor()
                self.ace.reactor.pause(min(end, self.ace.reactor.monotonic() + .05))
            if monitor: monitor()
        if len(set(samples)) != 1:
            raise RuntimeError('停止后入口或编码器不稳定')
        return samples[-1][:2]

    def _stop_and_wait_ready(self, idx, slot, transport, motion_start=None):
        a = self.ace
        if motion_start is not None:
            while a.reactor.monotonic() - motion_start < .3:
                if a._serials.get(idx) is not transport: raise RuntimeError('原 ACE 连接已断开')
                a.reactor.pause(min(motion_start + .3, a.reactor.monotonic() + .05))
        if a._serials.get(idx) is not transport: raise RuntimeError('原 ACE 连接已断开')
        self._stop(idx, slot)
        deadline = a.reactor.monotonic() + 8.0
        def cleanup_monitor():
            if a._serials.get(idx) is not transport: raise RuntimeError('ACE 连接改变')
            if a.reactor.monotonic() >= deadline: raise RuntimeError('停止确认超过时限')
        retry_at = a.reactor.monotonic() + 1.0
        retried = False
        while a.reactor.monotonic() < deadline:
            cleanup_monitor()
            lanes = self._lane_states(idx, monitor=cleanup_monitor)
            if any(st != 'ready' for i, st in lanes.items() if i != slot):
                raise RuntimeError('其他料槽正在运动或状态未知')
            target = lanes[slot]
            if target == 'ready':
                result = self._stable(idx, slot, monitor=cleanup_monitor)
                cleanup_monitor()
                return result
            if target != 'rollback': raise RuntimeError('目标料槽状态异常: %s' % target)
            if not retried and a.reactor.monotonic() >= retry_at:
                if deadline - a.reactor.monotonic() < 3.0:
                    raise RuntimeError('停止确认剩余时间不足')
                self._stop(idx, slot, timeout=.5)
                cleanup_monitor()
                retried = True
            a.reactor.pause(min(deadline, a.reactor.monotonic() + .1))
        raise RuntimeError('停止后料槽未在时限内恢复就绪')

    def eject_allows(self, idx, request, serial=None):
        try:
            lease = self._eject_lease
            if lease is None or self._eject_pending is None or self.cancel:
                return False
            target_idx, slot, transport = lease
            if serial is not None and serial is not transport:
                return False
            if (
                idx != target_idx
                or self.ace._serials.get(idx) is not transport
                or not self.ace._connected_per_ace.get(idx)
                or self.ace._serial_failed_per_ace.get(idx)
                or self.ace._stop_guard.message()
            ):
                return False
            if (
                self.state.get("state") != "running"
                or self.state.get("kind") != "eject"
                or self.ace._head_source != self._eject_sources
            ):
                return False
            if request.get("_eject_token") is not self._eject_pending:
                return False
            if request.get("method") != "unwind_filament" or request.get("params") != {
                "index": slot,
                "length": MAX_MM,
                "speed": SPEED_MM_S,
            }:
                return False
            ps = self.ace.printer.lookup_object("print_stats", None)
            if ps is None or getattr(ps, "state", "").lower() not in (
                "standby",
                "complete",
                "cancelled",
            ):
                return False
            for head in self.ace._rfid_route_heads(idx, slot) or []:
                sensor = self.ace.printer.lookup_object(
                    "filament_motion_sensor e%d_filament" % head, None
                )
                pin = (
                    getattr(sensor, "runout_buttun_state", None)
                    if sensor is not None
                    else None
                )
                source = self.ace._head_source.get(head)
                if type(pin) is not bool:
                    return False
                if pin and (
                    not isinstance(source, dict)
                    or (source.get("ace_index"), source.get("slot")) == (idx, slot)
                ):
                    return False
            return True
        except Exception:
            return False

    def eject_consumed(self, request):
        if (
            self._eject_pending is not None
            and request.get("_eject_token") is self._eject_pending
        ):
            self._eject_pending = None

    def _eject_stop(self, idx, slot, transport, monitor):
        last = None
        for attempt in range(4):
            if self.ace._serials.get(idx) is not transport:
                raise RuntimeError('原 ACE 连接已改变')
            try:
                return self._request(idx, 'stop_feed_filament', {'index': slot}, 9,
                                     monitor=monitor, stop_transport=transport)
            except Exception as exc:
                last = exc
                if attempt < 3:
                    self.ace.reactor.pause(self.ace.reactor.monotonic() + .2)
        raise RuntimeError('停止命令 4 次未确认: %s' % last)

    def _eject_stable(self, idx, slot, monitor):
        gates = []
        for _ in range(3):
            monitor()
            lanes = self._lane_states(idx, monitor=monitor)
            if lanes.get(slot) != "ready":
                raise RuntimeError("目标槽尚未就绪")
            gates.append(self._gate(idx, slot, monitor=monitor))
            end = self.ace.reactor.monotonic() + 0.25
            while self.ace.reactor.monotonic() < end:
                monitor()
                self.ace.reactor.pause(min(end, self.ace.reactor.monotonic() + 0.05))
        if len(set(gates)) != 1:
            raise RuntimeError("入口状态不稳定")
        return gates[-1]

    def _eject_stop_ready(self, idx, slot, transport, motion_start=None):
        a = self.ace
        if motion_start is not None:
            while a.reactor.monotonic() - motion_start < 0.3:
                if a._serials.get(idx) is not transport:
                    raise RuntimeError("原ACE连接已改变")
                a.reactor.pause(min(motion_start + 0.3, a.reactor.monotonic() + 0.05))
        deadline = a.reactor.monotonic() + 18

        def monitor():
            if (
                a._serials.get(idx) is not transport
                or a.reactor.monotonic() >= deadline
            ):
                raise RuntimeError("原连接变化或STOP确认超时")

        self._eject_stop(idx, slot, transport, monitor)
        while True:
            monitor()
            lanes = self._lane_states(idx, monitor=monitor)
            if lanes[slot] == "ready":
                return self._eject_stable(idx, slot, monitor)
            if lanes[slot] != "rollback":
                raise RuntimeError("目标槽状态未知")
            a.reactor.pause(min(deadline, a.reactor.monotonic() + 0.1))

    def exit_slot(self, gcmd):
        """Unload a positively identified source, then use the existing eject proof."""
        idx = gcmd.get_int('ACE', minval=0, maxval=3)
        slot = gcmd.get_int('SLOT', minval=0, maxval=3)
        a = self.ace
        if self.state.get('state') in ('preparing', 'running'):
            raise gcmd.error('料槽操作正在进行')
        try:
            self._check(idx, slot, for_eject=True, allow_target_loaded=True)
            transport = a._serials.get(idx)
            identity = a._location_identity(idx)
            path = getattr(a, '_path_calibration', None)
            physical = path.physical_sources() if path is not None else None
            heads = []
            for h in a._rfid_route_heads(idx, slot) or []:
                source = a._head_source.get(h)
                if a.printer.lookup_object('filament_motion_sensor e%d_filament' % h).runout_buttun_state is not True:
                    continue
                target = source is not None and (source['ace_index'], source['slot']) == (idx, slot)
                if not a.head_uses_ace(h):
                    if target:
                        raise RuntimeError('该喷头不使用 ACE，旧来源记录不可用于退料')
                    continue
                observed = physical.get(str(h)) if isinstance(physical, dict) else source
                if (not isinstance(observed, dict) or source is None
                        or (observed.get('ace_index'), observed.get('slot')) != (source['ace_index'], source['slot'])):
                    raise RuntimeError('喷头物理来源待核，未开始退料')
                if target:
                    heads.append(h)
            # One lane cannot safely be treated as feeding two toolheads.
            if len(heads) > 1:
                raise RuntimeError('目标耗材对应多个喷头，请先核对料路')
            if heads:
                self.seq += 1
                self.cancel = False
                self.state = {'seq': self.seq, 'state': 'preparing', 'kind': 'eject',
                              'ace': idx, 'slot': slot, 'message': '正在退出喷头，之后退出 ACE'}
                head = heads[0]
                sequence = self.seq
                command = 'ACE_UNLOAD_HEAD HEAD=%d' % head
                self._prepare_command = a.gcode.create_gcode_command(
                    'ACE_UNLOAD_HEAD', command, {'HEAD': str(head)})
                try:
                    a.cmd_ACE_UNLOAD_HEAD(self._prepare_command)
                finally:
                    self._prepare_command = None
                if self.cancel:
                    self.state.update(state='cancelled', message='已停止后续退出 ACE，请核对喷头状态')
                    return
                if (a._serials.get(idx) is not transport 
                        or not getattr(a, '_last_unload_ok', False)
                        or a._head_source.get(head) is not None
                        or a.printer.lookup_object('filament_motion_sensor e%d_filament' % head).runout_buttun_state is not False):
                    raise RuntimeError('喷头卸载或原料路未确认，未开始退出 ACE')
                return self._start(gcmd, prepared=sequence)
            self._check(idx, slot, transport, for_eject=True)
            return self.start(gcmd)
        except Exception as exc:
            if self.state.get('state') == 'preparing':
                self.state.update(state='error', message=str(exc))
            raise gcmd.error(str(exc))

    def start(self, gcmd):
        return self._start(gcmd)

    def _start(self, gcmd, prepared=None):
        idx = gcmd.get_int("ACE", minval=0, maxval=3)
        slot = gcmd.get_int("SLOT", minval=0, maxval=3)
        owned = (prepared is not None and self.state.get('state') == 'preparing'
                 and (self.state.get('seq'), self.state.get('ace'), self.state.get('slot')) == (prepared, idx, slot))
        if prepared is not None and (not owned or self.cancel):
            raise gcmd.error('退出任务已停止或已变化')
        if self.state["state"] in ('preparing', 'running') and not owned:
            raise gcmd.error("料槽操作正在进行")
        try:
            self._check(idx, slot, for_eject=True, own=owned)
        except Exception as e:
            raise gcmd.error(str(e))
        if not owned:
            self.seq += 1
            self.cancel = False
        self.state = {
            "seq": self.seq,
            "state": "running",
            "kind": "eject",
            "ace": idx,
            "slot": slot,
            "message": "准备退出 ACE",
        }
        self.ace._swap_in_progress = True
        transport = self.ace._serials.get(idx)
        self.ace.reactor.register_async_callback(
            lambda _event: self._run(idx, slot, transport)
        )
        gcmd.respond_info("料槽退出已开始")

    def stop(self, gcmd):
        if self.state["state"] != "running" or self.state["kind"] != "eject":
            raise gcmd.error("没有正在进行的料槽退出")
        self.cancel = True
        self._stop_controlled_eject()
        self.state["message"] = "正在停止，请等待确认"
        gcmd.respond_info("已请求停止")

    def _stop_controlled_eject(self):
        operations = getattr(self.ace, '_path_operations', None)
        idx, slot = self.state.get('ace'), self.state.get('slot')
        if operations is not None and operations.handles(idx):
            operations.stop(idx, slot, 'user cancelled ACE eject')

    def _arm_eject_pending(self, marker_hash):
        a = self.ace
        with open(PATH, 'rb') as f:
            old = f.read()
        if hashlib.sha256(old).hexdigest() != marker_hash:
            raise RuntimeError('位置记录在操作中改变')
        if a._location_hold.active:
            return marker_hash
        data = json.loads(old)
        data['state'] = 'eject_in_progress'
        data['eject_started_at'] = time.time()
        nonce = os.urandom(16).hex()
        archive = PATH + '.before-eject-start-' + nonce
        tmp = PATH + '.eject-pending-' + nonce
        fd = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(old)
            f.flush()
            os.fsync(f.fileno())
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        staged = LocationHold(tmp)
        if not staged.valid_marker or not staged.active:
            raise RuntimeError('退出中保护记录无效')
        os.replace(tmp, PATH)
        a._location_hold = LocationHold(routes_path=a._location_hold.routes_path)
        if not a._location_hold.valid_marker or not a._location_hold.active:
            raise RuntimeError('退出中保护未生效')
        directory = os.open(os.path.dirname(PATH), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        with open(PATH, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()

    def _commit_ejected(self, idx, slot, transport, uid, marker_hash, sources):
        a = self.ace
        old_sources = dict(a._head_source)
        for head, source in list(a._head_source.items()):
            if isinstance(source, dict) and (source.get('ace_index'), source.get('slot')) == (idx, slot):
                sensor = a.printer.lookup_object('filament_motion_sensor e%d_filament' % head, None)
                if getattr(sensor, 'runout_buttun_state', None) is not False:
                    raise RuntimeError('目标喷头尚未退空')
                a._head_source[head] = None
        try:
            a._save_head_source()
        except Exception:
            a._head_source = old_sources
            raise
        if getattr(a, '_head_travel', None) is not None:
            a._head_travel.reset_route(idx, slot)
        if (idx, slot) == (0, 3) and getattr(a, '_normal_load_speed', None) is not None:
            a._normal_load_speed.park = None

    def _run_controlled(self, idx, slot, transport, operations):
        a = self.ace
        operation = None
        deadline = a.reactor.monotonic() + MAX_SECONDS
        try:
            self._check(idx, slot, transport, own=True, for_eject=True)
            if self.cancel:
                raise RuntimeError('退出已取消')
            operation = operations.eject(idx, slot, length=MAX_MM, speed=SPEED_MM_S)
            self.state.update(message='正在退出 ACE', limit=MAX_MM)
            while operation.phase not in ('complete', 'cancelled', 'error'):
                self._check(idx, slot, transport, own=True, for_eject=True)
                if self.cancel or a.reactor.monotonic() >= deadline:
                    operations.stop(idx, slot, 'ACE eject cancelled or timed out')
                if a.reactor.monotonic() >= deadline + 10:
                    raise RuntimeError('退料停止等待超时')
                a.reactor.pause(a.reactor.monotonic() + .02)
            if operation.phase != 'complete':
                raise RuntimeError(operation.error or '本次退出已停止')
            if operation.snapshot()['motion']['inlet']:
                raise RuntimeError('已达到本次退料上限，入口仍有料')
            self._commit_ejected(idx, slot, transport, None, None, None)
            self.state.update(state='complete', message='耗材已退出 ACE 入口')
        except Exception as exc:
            self.state.update(state='cancelled' if self.cancel else 'error', message=str(exc))
            if operation is not None:
                try:
                    operations.stop(idx, slot, 'ACE eject cleanup')
                    controller = operation.controller
                    if (operation.phase == 'error' and
                            controller.device.motion_owners.get(slot) is controller):
                        controller.stop(reason='ACE eject cleanup')
                    operations.wait_ready(idx, timeout=10)
                except Exception as stop_error:
                    self.state.update(state='error', message='停止未确认: %s' % stop_error)
                    a._latch_stop_fault(idx, slot, str(stop_error))
        finally:
            # A failed task may still own a motor while STOP is asynchronous.
            # Release this current operation only when the controller settles.
            def release_when_stopped(eventtime):
                if operation is not None:
                    controller = operation.controller
                    if (controller.device.motion_owners.get(slot) is controller or
                            controller.snapshot()['state'] in ('starting', 'running', 'stopping')):
                        return eventtime + .1
                a._swap_in_progress = False
                return a.reactor.NEVER
            next_check = release_when_stopped(a.reactor.monotonic())
            if next_check != a.reactor.NEVER:
                a.reactor.register_timer(release_when_stopped, next_check)

    def _run(self, idx, slot, transport):
        a = self.ace
        operations = getattr(a, '_path_operations', None)
        if operations is not None and operations.handles(idx, slot):
            return self._run_controlled(idx, slot, transport, operations)
        moving = False
        motion_start = None
        deadline = a.reactor.monotonic() + MAX_SECONDS
        try:
            self._check(idx, slot, transport, own=True, for_eject=True)
            self._eject_sources = {head: dict(source) if source is not None else None
                                   for head, source in a._head_source.items()}
            self._eject_lease = idx, slot, transport

            def check():
                self._check(idx, slot, transport, own=True, for_eject=True)
                if self.cancel:
                    raise RuntimeError('本次退出已取消')
                if a.reactor.monotonic() >= deadline:
                    raise RuntimeError('本次退出超过时限')

            gate = self._eject_stop_ready(idx, slot, transport)
            if gate:
                check()
                self._eject_pending = object()
                moving = True
                motion_start = a.reactor.monotonic()
                self._request(idx, 'unwind_filament',
                    {'index': slot, 'length': MAX_MM, 'speed': SPEED_MM_S}, 8,
                    monitor=check, eject_token=self._eject_pending)
                self.state.update(message='正在退出 ACE', limit=MAX_MM)
                seen_motion = False
                while True:
                    check()
                    state = self._lane_states(idx, monitor=check).get(slot)
                    if state == 'rollback':
                        seen_motion = True
                    gate = self._gate(idx, slot, monitor=check)
                    if not gate or state == 'ready' and seen_motion:
                        break
                    if not seen_motion and a.reactor.monotonic() - motion_start > 5:
                        raise RuntimeError('未观察到退料启动')
                    if state not in ('ready', 'rollback'):
                        raise RuntimeError('目标槽退料状态异常')
                    a.reactor.pause(a.reactor.monotonic() + .1)
                gate = self._eject_stop_ready(idx, slot, transport, motion_start)
                moving = False
                if gate:
                    raise RuntimeError('已达到本次退料上限，入口仍有料')
            self._commit_ejected(idx, slot, transport, None, None, None)
            self.state.update(state='complete', message='耗材已退出 ACE 入口')
        except Exception as exc:
            self.state.update(state='cancelled' if self.cancel else 'error', message=str(exc))
        finally:
            if moving:
                try:
                    self._eject_stop_ready(idx, slot, transport, motion_start)
                except Exception as exc:
                    self.state.update(state='error', message='停止未确认: %s' % exc)
                    a._latch_stop_fault(idx, slot, str(exc))
            self._eject_pending = None
            self._eject_lease = None
            self._eject_sources = None
            a._swap_in_progress = False

def persist_rfid_position_uncertain(ace, idx, slot, identity=None, heads=None, scope="route"):
    port, uid = identity if identity is not None else ace._location_identity(idx)
    logging.warning('[multiACE] RFID read incomplete ACE=%d slot=%d', idx, slot)
    if (idx, slot) == (0, 3) and getattr(ace, '_normal_load_speed', None) is not None:
        ace._normal_load_speed.park = None


def supervise_rfid_motion(reader, actions, idx, slot, transport, motion_uncertain_type):
    """Interrupt only this web RFID reader's active moves; never guess restore."""
    reactor = actions.ace.reactor
    original_identity = actions.ace._location_identity(idx)
    original_heads = actions.ace._rfid_route_heads(idx, slot)
    original_mode = getattr(actions.ace, '_ace_mode', None)
    original_epoch = getattr(actions.ace, '_insert_epochs', {}).get((idx, slot))
    original_scope = "ace" if getattr(actions.ace, "_ace_mode", None) != "multi" else "route"
    def manual_session_guard():
        return (transport is not None and actions.ace._serials.get(idx) is transport
                and actions.ace._location_identity(idx) == original_identity
                and actions.ace._rfid_route_heads(idx, slot) == original_heads
                and getattr(actions.ace, '_ace_mode', None) == original_mode
                and getattr(actions.ace, '_insert_epochs', {}).get((idx, slot)) == original_epoch)
    reader._manual_session_guard = manual_session_guard
    original_pause = reader._pause
    original_move = reader._move_and_confirm
    original_read = reader.read_slot
    original_request = reader._req
    handled = [False]
    active = [False]
    stopping = [False]
    stopped = [False]
    reading = [False]
    pending_motor = [False]
    read_deadline = [None]

    def check_read():
        if not reading[0] or stopping[0]:
            return
        reason = None
        if actions.cancel:
            reason = '用户已停止识别'
        elif reactor.monotonic() >= read_deadline[0]:
            reason = 'RFID 识别达到 180 秒时限'
        elif not manual_session_guard():
            reason = 'RFID 原设备或料路发生变化'
        if reason:
            # Until the read's final stationary proof returns, even a zero
            # net displacement belongs to the original transport and identity.
            displaced = (any(value for value in reader._encoder_ledger.values())
                         or bool(getattr(reader, '_motion_ledger', {})))
            if pending_motor[0] or displaced:
                reader._motion_uncertain = reason + '，本次识别已停止'
                raise motion_uncertain_type(reader._motion_uncertain)
            raise RuntimeError(reason)

    def guarded_request(request_idx, method, params, *args, **kwargs):
        check_read()
        if reading[0] and method == 'feed_or_rollback_raw':
            # Queueing is already an ambiguous dispatch; a lost ACK must never
            # be interpreted as no movement or cause a motor retry.
            pending_motor[0] = True
        response = original_request(request_idx, method, params, *args, **kwargs)
        if (reading[0] and method == 'feed_or_rollback_raw' and isinstance(response, dict)
                and response.get('_v2_frame_valid') is True
                and response.get('_v2_payload_valid') is True
                and response.get('_v2_command') == 8 and type(response.get('code')) is int
                and (response['code'] != 0 or str(response.get('msg', '')).upper() == 'FORBIDDEN')):
            pending_motor[0] = False
        return response

    def uncertain_after(exc):
        return (isinstance(exc, motion_uncertain_type) or reader._motion_uncertain
                or pending_motor[0] or any(reader._encoder_ledger.values())
                or bool(getattr(reader, '_motion_ledger', {})))

    def stop_and_verify():
        if stopped[0]:
            return
        stopped[0] = True
        stopping[0] = True
        try:
            if actions.ace._serials.get(idx) is not transport or transport is None:
                raise RuntimeError('ACE transport changed; old motion stillness unknown')
            deadline = reactor.monotonic() + 8
            def monitor():
                if actions.ace._serials.get(idx) is not transport or reactor.monotonic() >= deadline:
                    raise RuntimeError('ACE transport changed or STOP verification timed out')
            actions._eject_stop(idx, slot, transport, monitor)
            monitor()
            actions._stable(idx, slot, monitor=monitor)
            if actions.ace._serials.get(idx) is not transport:
                raise RuntimeError('ACE transport changed during stillness check')
        except Exception as stop_exc:
            actions.ace._latch_stop_fault(idx, slot,
                'RFID stop or stillness unconfirmed: %s' % stop_exc, identity=original_identity)
            reader._motion_uncertain += '; STOP or stillness unconfirmed'
        finally:
            stopping[0] = False

    def guarded_pause(seconds):
        if (not active[0] and not reading[0]) or stopping[0]:
            return original_pause(seconds)
        deadline = reactor.monotonic() + seconds
        while reactor.monotonic() < deadline:
            check_read()
            try:
                if active[0]:
                    actions._check(idx, slot, transport, allow_tag=True)
            except Exception as exc:
                # Set before STOP: the reader's finally must never restore a
                # movement whose end position can no longer be inferred.
                reader._motion_uncertain = 'RFID motion interrupted: %s' % exc
                raise motion_uncertain_type(reader._motion_uncertain)
            original_pause(min(0.05, deadline - reactor.monotonic()))
        check_read()

    def guarded_move(*args, **kwargs):
        active[0] = True
        stopped[0] = False
        try:
            if (transport is None or actions.ace._serials.get(idx) is not transport
                    or original_identity != actions.ace._location_identity(idx)
                    or original_heads != actions.ace._rfid_route_heads(idx, slot)
                    or not original_identity[0] or original_identity[1] is None):
                raise RuntimeError('RFID original route identity unavailable or changed')
            if (idx, slot) == (0, 3) and getattr(actions.ace, '_normal_load_speed', None) is not None:
                actions.ace._normal_load_speed.park = None
            result = original_move(*args, **kwargs)
            pending_motor[0] = False
            return result
        except Exception as exc:
            if uncertain_after(exc):
                if not reader._motion_uncertain:
                    reader._motion_uncertain = 'RFID motion uncertain: %s' % exc
                handle_uncertain()
            raise
        finally:
            active[0] = False

    def handle_uncertain():
        if handled[0]:
            return
        handled[0] = True
        if (idx, slot) == (0, 3) and getattr(actions.ace, '_normal_load_speed', None) is not None:
            actions.ace._normal_load_speed.park = None
        stop_and_verify()

    def guarded_read(*args, **kwargs):
        reading[0] = True
        read_deadline[0] = reactor.monotonic() + 180.0
        try:
            return original_read(*args, **kwargs)
        except Exception as exc:
            if uncertain_after(exc):
                if not reader._motion_uncertain:
                    reader._motion_uncertain = 'RFID motion uncertain: %s' % exc
                handle_uncertain()
            raise
        finally:
            reading[0] = False

    reader._pause = guarded_pause
    reader._req = guarded_request
    reader._move_and_confirm = guarded_move
    reader.read_slot = guarded_read
