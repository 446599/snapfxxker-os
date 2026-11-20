"""Bounded, fail-closed ACE2 slot ejection; no heating or head motion."""
import logging
import secrets
from .ace_flow_guard import raw_head_state
STEP_MM = 20
SPEED_MM_S = 80
MAX_MM = 5000  # hard command budget; inlet sensor ends the move
MAX_SECONDS = 480
# flow.14 recovery budget: one short reverse, never another full-length push.
# These are command caps, not a calibrated absolute Y position.
RECOVERY_MM = 10
RECOVERY_SPEED_MM_S = 5
RECOVERY_SECONDS = 8.0
RECOVERY_MARGIN_MM = 1
STOP_SECONDS = 18.0
STOPPED_STATES = frozenset(('ready', 'feed_error', 'rollback_error',
    'assist_error', 'preload_error', 'stuck_error', 'tangled_error', 'motor_error'))

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
        self._eject_params = None
        self._eject_identity = None
        self._eject_epoch = None
        self._eject_heads = None
        self._last_eject_stop = None
        self._motor_pending = False
        self._recovery_owner = None
        self._route_token = None
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
        supervisor = (getattr(a, '_feed_supervisors', {}) or {}).get((idx, slot))
        if kind in ('feed', 'load') and supervisor is not None:
            actual_seq = getattr(supervisor, 'seq', None)
            if type(actual_seq) is not int or type(seq) is not int or actual_seq != seq or epoch != self.epoch:
                request.send({'cancelled': False, 'message': '送料任务已变化'})
                return
            supervisor.cancel()
            request.send({'cancelled': True, 'state': 'cancelling'})
            return
        if kind == 'rfid':
            matches = (getattr(a, '_tag_read_busy', False)
                       and getattr(a, '_tag_op_kind', None) in ('read', 'insert')
                       and (a._tag_op_ace, a._tag_op_slot, a._tag_op_seq) == (idx, slot, seq))
        else:
            matches = (kind in ('eject', 'insert', 'recover') and self.state.get('kind') == kind
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
               allow_target_loaded=False, allow_stop_fault=False):
        a = self.ace
        if not allow_stop_fault:
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
            present = raw_head_state(a, h)
            if type(present) is not bool:
                raise RuntimeError('目标喷头传感器暂不可用')
            if not present:
                source = a._head_source.get(h)
                if (for_eject and isinstance(source, dict)
                    and (source.get('ace_index'), source.get('slot')) != (idx, slot)):
                    raise RuntimeError('共用管仍由另一料槽占用，不能清除原来源')
                continue
            source = a._head_source.get(h)
            if not isinstance(source, dict):
                raise RuntimeError('请选择当前喷头的供料槽')
            target = (source.get('ace_index'), source.get('slot')) == (idx, slot)
            if for_eject and not target:
                raise RuntimeError('共用管对应喷头正在使用另一料槽')
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
                or self.state.get("kind") not in ("eject", "recover")
                or self.ace._head_source != self._eject_sources
            ):
                return False
            if request.get("_eject_token") is not self._eject_pending:
                return False
            if self.ace.reactor.monotonic() >= self._eject_command_deadline:
                return False
            if (request.get("method") != "unwind_filament"
                    or request.get("params") != self._eject_params
                    or self._eject_params not in (
                        {'index': slot, 'length': MAX_MM, 'speed': SPEED_MM_S},
                        {'index': slot, 'length': RECOVERY_MM, 'speed': RECOVERY_SPEED_MM_S})):
                return False
            if (self.ace._location_identity(idx) != self._eject_identity
                    or (getattr(self.ace, '_insert_epochs', {}) or {}).get((idx, slot)) != self._eject_epoch
                    or tuple(self.ace._rfid_route_heads(idx, slot) or ()) != self._eject_heads):
                return False
            ps = self.ace.printer.lookup_object("print_stats", None)
            if ps is None or getattr(ps, "state", "").lower() not in (
                "standby",
                "complete",
                "cancelled",
                "error",
            ):
                return False
            for head in self.ace._rfid_route_heads(idx, slot) or []:
                pin = raw_head_state(self.ace, head)
                source = self.ace._head_source.get(head)
                if type(pin) is not bool:
                    return False
                if pin or (isinstance(source, dict)
                           and (source.get('ace_index'), source.get('slot')) != (idx, slot)):
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

    def _feed_row(self, idx, slot, monitor, allow_zero=False):
        feed = self._request(idx, 'get_feed_info', {}, 76, monitor=monitor)
        rows = feed.get('feed_info')
        if feed.get('_feed_wire_valid') is not True or not isinstance(rows, list):
            raise RuntimeError('CMD76 编码器帧无效')
        rows = [row for row in rows if isinstance(row, dict)
                and type(row.get('index')) is int and row['index'] == slot]
        if len(rows) != 1 or rows[0].get('_wire_valid') is not True:
            raise RuntimeError('CMD76 原槽反馈缺失或重复')
        row = rows[0]
        if (any(type(row.get(key)) is not int for key in ('steps', 'length', 'decoder'))
                or not 0 <= row['decoder'] < (1 << 64)
                or row['steps'] < 0 or row['length'] < 0):
            raise RuntimeError('CMD76 原槽计数无效')
        zero = row['steps'] == row['length'] == row['decoder'] == 0
        if row.get('_decoder_present') is not True and not (
                allow_zero and zero and row.get('_decoder_present') is False):
            raise RuntimeError('CMD76 未提供有效耗材编码器，不能用电机长度代替')
        return {'steps': row['steps'], 'length': row['length'],
                'decoder': row['decoder'] - (1 << 64) if row['decoder'] >= (1 << 63) else row['decoder'],
                'decoder_present': row.get('_decoder_present') is True}

    def _eject_stable(self, idx, slot, monitor, allow_fault=False):
        """Fresh requests after STOP; ready is not a substitute for standstill."""
        samples = []
        for number in range(3):
            monitor()
            lanes = self._lane_states(idx, monitor=monitor)
            if any(status != 'ready' for lane, status in lanes.items() if lane != slot):
                raise RuntimeError('其他料槽未停稳: %s' % lanes)
            status = lanes[slot]
            allowed = STOPPED_STATES if allow_fault else ('ready',)
            if status not in allowed:
                raise RuntimeError('目标槽停止未确认，实际状态: %s' % status)
            gate = self._gate(idx, slot, monitor=monitor)
            row = self._feed_row(idx, slot, monitor,
                                 allow_zero=not gate and status == 'ready')
            after = self._lane_states(idx, monitor=monitor)
            if after != lanes:
                raise RuntimeError('STOP 后槽状态变化: %s -> %s' % (lanes, after))
            sample = (status, gate, row['steps'], row['length'], row['decoder'])
            samples.append(sample)
            if number < 2:
                end = self.ace.reactor.monotonic() + .25
                while self.ace.reactor.monotonic() < end:
                    monitor()
                    self.ace.reactor.pause(min(end, self.ace.reactor.monotonic() + .05))
        monitor()
        if len(set(samples)) != 1:
            raise RuntimeError('STOP 后入口或实际运动计数仍变化')
        self._last_eject_stop = dict(row, status=status, gate=gate,
            at=self.ace.reactor.monotonic(), samples=len(samples),
            transport=self.ace._serials.get(idx), identity=self.ace._location_identity(idx),
            slot=slot)
        return gate

    def _eject_stop_ready(self, idx, slot, transport, motion_start=None, allow_fault=False):
        a = self.ace
        self._last_eject_stop = None
        if motion_start is not None:
            while a.reactor.monotonic() - motion_start < 0.3:
                if a._serials.get(idx) is not transport:
                    raise RuntimeError("原ACE连接已改变")
                a.reactor.pause(min(motion_start + 0.3, a.reactor.monotonic() + 0.05))
        deadline = a.reactor.monotonic() + STOP_SECONDS
        identity = (self._eject_identity if self._eject_lease == (idx, slot, transport)
                    else a._location_identity(idx))

        def monitor():
            if (
                a._serials.get(idx) is not transport
                or a._location_identity(idx) != identity
                or a.reactor.monotonic() >= deadline
            ):
                raise RuntimeError("原连接变化或STOP确认超时")

        self._eject_stop(idx, slot, transport, monitor)
        acknowledged_at = a.reactor.monotonic()
        while True:
            monitor()
            lanes = self._lane_states(idx, monitor=monitor)
            if lanes[slot] == 'ready' or allow_fault and lanes[slot] in STOPPED_STATES:
                gate = self._eject_stable(idx, slot, monitor, allow_fault=allow_fault)
                self._last_eject_stop['acknowledged_at'] = acknowledged_at
                return gate
            if lanes[slot] not in ('rollback', 'feeding', 'assisting', 'rollback_assisting', 'preloading'):
                raise RuntimeError('目标槽停止未确认，实际状态: %s' % lanes[slot])
            a.reactor.pause(min(deadline, a.reactor.monotonic() + 0.1))

    def _bind_legacy_route(self, idx, slot, transport, allow_loaded=False):
        a = self.ace
        identity = a._location_identity(idx)
        if (not isinstance(identity, (tuple, list)) or len(identity) != 2
                or not isinstance(identity[0], str) or not identity[0]
                or not isinstance(identity[1], (tuple, list)) or len(identity[1]) != 3
                or any(type(value) is not int for value in identity[1])):
            raise RuntimeError('原 ACE UID 未确认，不能恢复运动')
        identity = identity[0], tuple(identity[1])
        def monitor():
            if a._serials.get(idx) is not transport or a._location_identity(idx) != identity:
                raise RuntimeError('原 ACE 连接或 UID 已改变')
        reply = self._request(idx, 'discover_device', {}, 0, monitor=monitor)
        observed = tuple(reply.get('uid%d' % n) for n in (1, 2, 3))
        if any(type(value) is not int for value in observed) or observed != identity[1]:
            raise RuntimeError('当前设备 UID 与原料路不一致')
        heads = tuple(a._rfid_route_heads(idx, slot) or ())
        if not heads:
            raise RuntimeError('原料路对应喷头未知')
        for head in heads:
            present = raw_head_state(a, head)
            if type(present) is not bool or present and not allow_loaded:
                raise RuntimeError('目标喷头仍有料或传感器未知，先完成热端卸料')
            source = a._head_source.get(head)
            if present and (not isinstance(source, dict)
                            or (source.get('ace_index'), source.get('slot')) != (idx, slot)):
                raise RuntimeError('喷头有料但原供料来源未知')
            if isinstance(source, dict) and (source.get('ace_index'), source.get('slot')) != (idx, slot):
                raise RuntimeError('共用管有其他供料来源，不能恢复目标槽')
            if source is not None:
                if not isinstance(source, dict):
                    raise RuntimeError('原来源记录不完整，请先确认原来源')
                bound = source.get('connection_identity')
                if not isinstance(bound, dict):
                    raise RuntimeError('旧来源没有设备 UID 记录，请先确认原来源；喷头为空不代表管内无料')
                if bound != {'port': identity[0], 'uid': list(identity[1])}:
                    raise RuntimeError('原料路属于先前的设备或端口，不能由当前 ACE 接管')
                if not a._source_insertion_matches(source, idx, slot):
                    raise RuntimeError('原来源的会话或插料代次已变化，请先确认原来源并核对管内耗材，未开始退出')
            travel = getattr(getattr(a, '_head_travel', None), 'records', {}).get(str(head))
            if isinstance(travel, dict) and (travel.get('ace'), travel.get('slot')) != (idx, slot):
                raise RuntimeError('共用管保留另一料槽的计距记录，需先核对占用')
        path = getattr(a, '_path_calibration', None)
        store = getattr(getattr(path, 'store', None), 'state', {}) or {}
        for head in heads:
            owner = (store.get('occupied_heads') or {}).get(str(head))
            if owner is not None:
                route = (store.get('routes') or {}).get(owner.get('route_id'), {})
                if (route.get('ace'), route.get('slot')) != (idx, slot):
                    raise RuntimeError('共用管的持久占用不属于目标槽')
        hold = getattr(a, '_location_hold', None)
        if hold is None or not hold.refresh_routes():
            raise RuntimeError('料路占用记录不可用，未开始运动')
        for route in hold.routes:
            if hold._same_route(route, identity[0], identity[1], slot):
                continue
            if (route.get('heads') is None or set(route['heads']).intersection(heads)
                    or route.get('scope', 'ace') == 'ace' and (
                        route.get('port') == identity[0] or tuple(route.get('uid', ())) == identity[1])):
                raise RuntimeError('共用管保留另一原槽的位置异常，不能替换其来源')
        self._eject_identity = identity
        self._eject_epoch = (getattr(a, '_insert_epochs', {}) or {}).get((idx, slot))
        self._eject_heads = heads
        self._eject_mode = getattr(a, '_ace_mode', None)
        self._eject_sources = {head: dict(source) if isinstance(source, dict) else source
                               for head, source in a._head_source.items()}
        self._eject_lease = idx, slot, transport

    def _original_monitor(self, idx, slot, transport, deadline, allow_removal=False,
                          allow_stop_fault=False, cancel=True):
        a = self.ace
        self._check(idx, slot, transport, own=True, for_eject=True,
                    allow_stop_fault=allow_stop_fault)
        if (a._location_identity(idx) != self._eject_identity
                or tuple(a._rfid_route_heads(idx, slot) or ()) != self._eject_heads
                or getattr(a, '_ace_mode', None) != self._eject_mode
                or a._head_source != self._eject_sources):
            raise RuntimeError('原设备、接管或供料来源在操作中改变')
        current_epoch = (getattr(a, '_insert_epochs', {}) or {}).get((idx, slot))
        allowed_epochs = (self._eject_epoch,)
        if allow_removal:
            allowed_epochs += ((self._eject_epoch or 0) + 1,)
        if current_epoch not in allowed_epochs:
            raise RuntimeError('原料槽插料代次已改变')
        if cancel and self.cancel:
            raise RuntimeError('本次操作已取消')
        if a.reactor.monotonic() >= deadline:
            raise RuntimeError('本次操作超过时限')

    def _other_routes_ready(self, idx, slot, monitor):
        a = self.ace
        if getattr(a, '_ace_mode', None) != 'multi':
            return
        for other, connected in sorted(a._connected_per_ace.items()):
            if other == idx or connected is not True:
                continue
            if slot in (a._rfid_route_heads(other, slot) or ()):
                peer_transport = a._serials.get(other)
                peer_identity = a._location_identity(other)
                def peer_monitor():
                    monitor()
                    if (peer_transport is None or a._serials.get(other) is not peer_transport
                            or a._location_identity(other) != peer_identity
                            or a._connected_per_ace.get(other) is not True):
                        raise RuntimeError('共用管的另一 ACE 在复核中改变连接')
                status = self._lane_states(other, monitor=peer_monitor).get(slot)
                if status != 'ready':
                    raise RuntimeError('同一共用管的 ACE %d 槽 %d 未就绪: %s' % (other + 1, slot + 1, status))

    def _arm_route_hold(self, idx, slot):
        hold = getattr(self.ace, '_location_hold', None)
        if hold is None:
            raise RuntimeError('料路占用记录不可用')
        port, uid = self._eject_identity
        try:
            hold.hold_route(port, uid, slot, list(self._eject_heads), scope='route')
        except Exception as exc:
            _latch_position_journal_failure(self.ace, idx, slot, self._eject_identity, exc)
            raise RuntimeError('位置记录无法落盘，未开始运动；保留来源，请勿用重启清除: %s' % exc)
        self._route_token = hold.route_token(port, uid, slot)
        if self._route_token is None:
            raise RuntimeError('原料路占用未持久化，未开始运动')
        travel = getattr(self.ace, '_head_travel', None)
        if travel is not None:
            for head in self._eject_heads:
                travel.unknown(head, idx, slot, 'original_slot_exit_or_recovery')

    def _release_verified_stop_fault(self, idx, slot, generation, require_global_clear=True):
        guard = self.ace._stop_guard
        if guard.snapshot() is None:
            return
        _current_generation, faults = guard.recovery_snapshot()
        if any((fault.get('ace_idx'), fault.get('slot')) == (idx, slot)
               and fault.get('reason', '').startswith('位置记录无法落盘') for fault in faults):
            # This STOP journal is also the surviving position checkpoint.
            # Migrate it to a durable physical hold before releasing the latch.
            self._arm_route_hold(idx, slot)
        if not guard.clear_verified(generation, idx, slot, self._eject_identity):
            raise RuntimeError('STOP 故障代次、原 UID 或持久记录变化，保留故障等待复核')
        if require_global_clear:
            self.ace._require_no_stop_fault()

    def _send_eject_motion(self, idx, slot, length, speed, monitor):
        monitor()
        self._eject_pending = object()
        self._eject_params = {'index': slot, 'length': length, 'speed': speed}
        self._eject_command_deadline = self.ace.reactor.monotonic() + 3.0
        self._motor_pending = True  # lost ACK is ambiguous; cleanup must STOP
        return self._request(idx, 'unwind_filament', dict(self._eject_params), 8,
                             monitor=monitor, eject_token=self._eject_pending,
                             recovery_token=self._recovery_owner)

    def _recover_feed_error(self, idx, slot, transport, before):
        """One finite original-slot reverse; never turn a fault into synthetic ready."""
        a = self.ace
        cached = (getattr(a, '_insert_encoder_direction', {}) or {}).get((idx, slot))
        if (not cached or len(cached) != 3 or cached[0] is not transport
                or cached[1] != self._eject_identity or type(cached[2]) is not int
                or cached[2] not in (-1, 1) or self._eject_epoch is None):
            raise RuntimeError('feed_error：原槽编码器方向或插料代次未确认，停止后保留来源')
        if before['gate'] is not True:
            raise RuntimeError('feed_error：原槽入口无料，未执行短退')
        polarity = cached[2]
        deadline = a.reactor.monotonic() + RECOVERY_SECONDS
        monitor = lambda: self._original_monitor(idx, slot, transport, deadline)
        self._other_routes_ready(idx, slot, monitor)
        self._arm_route_hold(idx, slot)
        self.state.update(message='feed_error：原槽低速短退一次', recovery_attempts=1,
                          recovery_limit_mm=RECOVERY_MM, recovery_speed_mm_s=RECOVERY_SPEED_MM_S)
        self._send_eject_motion(idx, slot, RECOVERY_MM, RECOVERY_SPEED_MM_S, monitor)
        first = self._feed_row(idx, slot, monitor, allow_zero=True)
        reset = (first['steps'] == first['length'] == first['decoder'] == 0
                 or first['steps'] < before['steps'] and first['length'] < before['length']
                 and first['decoder'] * polarity <= 0)
        base = {'steps': 0, 'length': 0, 'decoder': 0} if reset else before
        if not reset and (first['steps'] < before['steps'] or first['length'] < before['length']):
            raise RuntimeError('短退计数零点未确认')
        last = first
        seen_reverse = 0
        while True:
            monitor()
            row = last
            reverse = (base['decoder'] - row['decoder']) * polarity
            motor = row['length'] - base['length']
            if (reverse < 0 or reverse > RECOVERY_MM + RECOVERY_MARGIN_MM
                    or motor < 0 or motor > RECOVERY_MM + RECOVERY_MARGIN_MM):
                raise RuntimeError('短退方向、计数或有限行程异常')
            if row['decoder_present'] and reverse >= 2:
                seen_reverse = reverse
            status = self._lane_states(idx, monitor=monitor).get(slot)
            gate = self._gate(idx, slot, monitor=monitor)
            if not gate:
                raise RuntimeError('短退期间入口退空，已停止；请执行完全退出复核')
            if status not in ('ready', 'rollback', 'feed_error'):
                raise RuntimeError('短退遇到不可自动恢复的槽错误: %s' % status)
            if motor >= 4 and reverse < max(1, motor * .25):
                raise RuntimeError('短退机械无进度：电机 %s mm，耗材 %s mm' % (motor, reverse))
            if seen_reverse >= 2 and (status == 'ready' or reverse >= RECOVERY_MM):
                break
            if status == 'feed_error' and motor >= RECOVERY_MM:
                raise RuntimeError('短退后仍为 feed_error，保留来源等待机械处理')
            a.reactor.pause(min(deadline, a.reactor.monotonic() + .1))
            next_row = self._feed_row(idx, slot, monitor, allow_zero=True)
            if (next_row['steps'] < last['steps'] or next_row['length'] < last['length']
                    or (next_row['decoder'] - last['decoder']) * polarity > 0):
                raise RuntimeError('短退过程中计数重置或耗材反向')
            last = next_row
        self._eject_stop_ready(idx, slot, transport, allow_fault=True)
        self._motor_pending = False
        monitor()
        final = self._last_eject_stop
        reverse = (base['decoder'] - final['decoder']) * polarity
        if (final['status'] != 'ready' or final['gate'] is not True
                or not 2 <= reverse <= RECOVERY_MM + RECOVERY_MARGIN_MM
                or not 0 <= final['length'] - base['length'] <= RECOVERY_MM + RECOVERY_MARGIN_MM):
            raise RuntimeError('短退终态未确认，实际槽状态: %s；保留来源' % final['status'])
        self.state.update(recovered=True, recovery_reverse_mm=reverse,
                          message='原槽已停稳并恢复；共用管来源仍保留')
        return final

    def recover_slot(self, idx, slot, allow_probe=True, clear_latch=True, owner_token=None):
        """Synchronously reconcile one slot; reactor pauses keep cancellation alive."""
        if (type(idx) is not int or type(slot) is not int or not 0 <= idx < 4 or not 0 <= slot < 4
                or type(allow_probe) is not bool or type(clear_latch) is not bool):
            raise RuntimeError('恢复目标无效')
        a = self.ace
        recovery = getattr(a, '_error_recovery', None)
        owned = recovery is not None and owner_token is not None and recovery.owner is owner_token
        if self.state.get('state') in ('preparing', 'running') and not owned:
            raise RuntimeError('已有耗材操作正在进行')
        self._check(idx, slot, own=owned, for_eject=True, allow_target_loaded=True,
                    allow_stop_fault=True)
        if not owned:
            self.seq += 1
            self.cancel = False
        self.state = {'seq': self.seq, 'state': 'running', 'kind': 'recover',
                      'ace': idx, 'slot': slot, 'message': '正在复核原槽停止',
                      'ejected': False, 'path_cleared': False, 'source_retained': True}
        a._swap_in_progress = True
        self._run(idx, slot, a._serials.get(idx), recovery_only=True,
                  allow_probe=allow_probe, clear_latch=clear_latch, owner_token=owner_token)
        if self.state.get('state') != 'complete':
            raise RuntimeError(self.state.get('message') or '原槽恢复未完成')
        return dict(self.state)

    def exit_slot(self, gcmd):
        """Unload a positively identified source, then use the existing eject proof."""
        idx = gcmd.get_int('ACE', minval=0, maxval=3)
        slot = gcmd.get_int('SLOT', minval=0, maxval=3)
        a = self.ace
        if self.state.get('state') in ('preparing', 'running'):
            raise gcmd.error('料槽操作正在进行')
        try:
            self._check(idx, slot, for_eject=True, allow_target_loaded=True,
                        allow_stop_fault=True)
            transport = a._serials.get(idx)
            identity = a._location_identity(idx)
            path = getattr(a, '_path_calibration', None)
            physical = path.physical_sources() if path is not None else None
            heads = []
            for h in a._rfid_route_heads(idx, slot) or []:
                source = a._head_source.get(h)
                if raw_head_state(a, h) is not True:
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
                if a._stop_guard.snapshot() is not None:
                    self.recover_slot(idx, slot, allow_probe=False, clear_latch=True)
                self.seq += 1
                self.cancel = False
                self.state = {'seq': self.seq, 'state': 'preparing', 'kind': 'eject',
                              'ace': idx, 'slot': slot, 'message': '正在退出喷头，之后退出 ACE'}
                head = heads[0]
                sequence = self.seq
                command = 'ACE_UNLOAD_HEAD HEAD=%d' % head
                self._prepare_command = a.gcode.create_gcode_command(
                    'ACE_UNLOAD_HEAD', command, {'HEAD': str(head)})
                exit_without_park = False
                try:
                    a.cmd_ACE_UNLOAD_HEAD(self._prepare_command)
                except Exception:
                    source = a._head_source.get(head)
                    exit_without_park = (
                        getattr(a, '_last_unload_reason', None) == 'distance_unknown'
                        and raw_head_state(a, head) is False
                        and a._serials.get(idx) is transport
                        and a._location_identity(idx) == identity
                        and isinstance(source, dict)
                        and (source.get('ace_index'), source.get('slot')) == (idx, slot)
                        and a._stop_guard.snapshot() is None)
                    if not exit_without_park:
                        raise
                finally:
                    self._prepare_command = None
                if self.cancel:
                    self.state.update(state='cancelled', message='已停止后续退出 ACE，请核对喷头状态')
                    return
                if exit_without_park:
                    self.state['message'] = '喷头已退空但回停距离未知；保留原槽，继续退出 ACE 入口'
                    gcmd.respond_info(self.state['message'])
                    return self._start(gcmd, prepared=sequence)
                if (a._serials.get(idx) is not transport
                        or a._location_identity(idx) != identity
                        or not getattr(a, '_last_unload_ok', False)
                        or a._head_source.get(head) is not None
                        or raw_head_state(a, head) is not False):
                    raise RuntimeError('喷头卸载或原料路未确认，未开始退出 ACE')
                return self._start(gcmd, prepared=sequence)
            self._check(idx, slot, transport, for_eject=True, allow_stop_fault=True)
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
            self._check(idx, slot, for_eject=True, own=owned, allow_stop_fault=True)
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
        try:
            self.ace.reactor.register_async_callback(
                lambda _event: self._run(idx, slot, transport)
            )
        except Exception as exc:
            self.state.update(state='error', message=str(exc))
            self.ace._swap_in_progress = False
            raise gcmd.error(str(exc))
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

    def _commit_ejected(self, idx, slot, transport, controlled=None):
        """Only the inlet-empty terminal proof may release a physical hold."""
        a = self.ace
        self._check(idx, slot, transport, own=True, for_eject=True)
        if (a._serials.get(idx) is not transport
                or a._location_identity(idx) != self._eject_identity
                or tuple(a._rfid_route_heads(idx, slot) or ()) != self._eject_heads
                or getattr(a, '_ace_mode', None) != self._eject_mode
                or a._head_source != self._eject_sources):
            raise RuntimeError('退出提交前原设备、料路或供料来源变化')
        if controlled is None:
            current_epoch = (getattr(a, '_insert_epochs', {}) or {}).get((idx, slot))
            if current_epoch not in (self._eject_epoch, (self._eject_epoch or 0) + 1):
                raise RuntimeError('退出提交前发生额外插拔，原料槽已变化')
            proof = self._last_eject_stop or {}
            if (proof.get('transport') is not transport
                    or proof.get('identity') != self._eject_identity
                    or proof.get('slot') != slot or proof.get('gate') is not False
                    or proof.get('samples') != 3 or proof.get('status') not in STOPPED_STATES
                    or not 0 <= a.reactor.monotonic() - proof.get('at', -100) < 2
                    or proof.get('acknowledged_at', float('inf')) > proof.get('at', -100)):
                raise RuntimeError('缺少本次 STOP 后的新鲜入口退空和静止证明')
        else:
            motion = controlled.snapshot().get('motion') or {}
            live = controlled.controller.snapshot()
            if (controlled.phase != 'complete' or motion.get('complete') is not True
                    or live.get('complete') is not True or live.get('inlet') is not False
                    or any(live.get(key) != motion.get(key)
                           for key in ('action_id', 'motion_id', 'boot_epoch', 'insertion_epoch'))
                    or type(live.get('telemetry_age_s')) not in (int, float)
                    or not 0 <= live['telemetry_age_s'] < 2
                    or controlled.controller.device.motion_owners.get(slot) is not None):
                raise RuntimeError('受控退出尚无新鲜入口退空和停稳证明')
        for head in self._eject_heads:
            if raw_head_state(a, head) is not False:
                raise RuntimeError('目标喷头尚未退空')
        hold = a._location_hold
        port, uid = self._eject_identity
        current_token = hold.route_token(port, uid, slot)
        if current_token != self._route_token:
            raise RuntimeError('退出期间料路占用记录变化，未清除来源')
        old_sources = dict(a._head_source)
        for head, source in list(a._head_source.items()):
            if isinstance(source, dict) and (source.get('ace_index'), source.get('slot')) == (idx, slot):
                a._head_source[head] = None
        try:
            a._save_head_source()
            if getattr(a, '_head_travel', None) is not None:
                a._head_travel.reset_route(idx, slot)
            # The token covers the durable file and the exact physical route.
            # Release last: a failed metadata write must leave the hold intact.
            if self._route_token is not None:
                hold.release_route(port, uid, slot, self._route_token)
        except Exception:
            a._head_source = old_sources
            try:
                a._save_head_source()
            except Exception:
                logging.exception('[multiACE] source rollback failed; physical route remains held')
            raise
        if (idx, slot) == (0, 3) and getattr(a, '_normal_load_speed', None) is not None:
            a._normal_load_speed.park = None

    def _run_controlled(self, idx, slot, transport, operations):
        a = self.ace
        operation = None
        deadline = a.reactor.monotonic() + MAX_SECONDS
        try:
            self._check(idx, slot, transport, own=True, for_eject=True)
            self._bind_legacy_route(idx, slot, transport)
            port, uid = self._eject_identity
            self._route_token = a._location_hold.route_token(port, uid, slot)
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
            if operation.snapshot()['motion']['inlet'] is not False:
                raise RuntimeError('已达到本次退料上限，入口仍有料')
            self._commit_ejected(idx, slot, transport, controlled=operation)
            self.state.update(state='complete', message='耗材已退出 ACE 入口',
                              ejected=True, path_cleared=True, source_retained=False)
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
                    self.state.update(state='error', message='%s；停止未确认: %s' % (exc, stop_error))
                    a._latch_stop_fault(idx, slot, str(stop_error), identity=self._eject_identity)
        finally:
            if operation is not None:
                controller = operation.controller
                if (controller.device.motion_owners.get(slot) is controller
                        or controller.snapshot()['state'] in ('starting', 'running', 'stopping')):
                    a._latch_stop_fault(idx, slot, '受控退出停止仍未确认', identity=self._eject_identity)
                    self.state.update(state='error', message=self.state.get('message', '') + '；停止未确认')
            self._eject_lease = self._eject_pending = None
            a._swap_in_progress = False

    def _run(self, idx, slot, transport, recovery_only=False, allow_probe=True,
             clear_latch=True, owner_token=None):
        a = self.ace
        operations = getattr(a, '_path_operations', None)
        if not recovery_only and operations is not None and operations.handles(idx, slot):
            return self._run_controlled(idx, slot, transport, operations)
        recovery = getattr(a, '_error_recovery', None)
        stop_pending = False
        self._motor_pending = False
        self._last_eject_stop = None
        self._eject_identity = None
        self._route_token = None
        deadline = a.reactor.monotonic() + MAX_SECONDS
        try:
            if recovery is None:
                raise RuntimeError('原槽恢复授权不可用')
            if owner_token is None:
                owner_token = recovery.begin(idx, slot)
            elif recovery.owner is not owner_token or recovery.target != (idx, slot):
                raise RuntimeError('原槽恢复任务已变化')
            self._recovery_owner = owner_token
            self._check(idx, slot, transport, own=True, for_eject=True,
                        allow_target_loaded=recovery_only, allow_stop_fault=True)
            self._bind_legacy_route(idx, slot, transport, allow_loaded=recovery_only)
            stop_generation, _faults = a._stop_guard.recovery_snapshot()
            stop_pending = True
            gate = self._eject_stop_ready(idx, slot, transport, allow_fault=True)
            stop_pending = False
            proof = self._last_eject_stop
            self.state.update(stopped=True, gate=gate, slot_status=proof['status'],
                              recovered=False, ejected=False, path_cleared=False,
                              source_retained=True)
            if self.cancel:
                raise RuntimeError('本次操作已取消，原槽已停稳')
            if clear_latch:
                self._release_verified_stop_fault(idx, slot, stop_generation,
                                                  require_global_clear=not recovery_only)
            head_present = any(raw_head_state(a, head) is True for head in self._eject_heads)
            if recovery_only and head_present:
                held = a._location_hold.route_token(*self._eject_identity, slot) is not None
                self.state.update(state='complete', requires_hot_unload=True,
                    requires_position_review=held,
                    message=('原槽停止已确认；喷头仍有料且位置待核，请核对原料路；来源和占用仍保留'
                             if held else '原槽停止已确认；喷头仍有料，请继续热端卸料，来源和共用管占用保留'))
                return
            if proof['status'] == 'feed_error' and gate and allow_probe:
                if not clear_latch and a._stop_guard.snapshot() is not None:
                    raise RuntimeError('仅复核模式不能在 STOP 故障未解除时短退')
                a._require_no_stop_fault()
                proof = self._recover_feed_error(idx, slot, transport, proof)
                gate = proof['gate']
            elif proof['status'] != 'ready' and (not recovery_only or allow_probe):
                if gate:
                    raise RuntimeError('目标槽实际错误不可盲退: %s；来源仍保留' % proof['status'])
            self.state.update(stopped=True, gate=gate, slot_status=proof['status'])
            if recovery_only:
                self.state.update(state='complete',
                    message=('原槽停止已确认；来源和共用管占用保留，需要完全退出请执行 ACE_SLOT_EXIT'))
                return
            def check():
                self._original_monitor(idx, slot, transport, deadline, allow_removal=True)
            check()
            self._other_routes_ready(idx, slot, check)
            self._arm_route_hold(idx, slot)
            if gate:
                check()
                motion_start = a.reactor.monotonic()
                self._send_eject_motion(idx, slot, MAX_MM, SPEED_MM_S, check)
                self.state.update(message='正在退出 ACE', limit=MAX_MM)
                seen_motion = False
                previous = None
                progress_at = motion_start
                while True:
                    check()
                    state = self._lane_states(idx, monitor=check).get(slot)
                    gate = self._gate(idx, slot, monitor=check)
                    if not gate:
                        break
                    if (getattr(a, '_insert_epochs', {}) or {}).get((idx, slot)) != self._eject_epoch:
                        raise RuntimeError('退料期间原槽插料代次改变且入口仍有料')
                    if state not in ('ready', 'rollback'):
                        raise RuntimeError('目标槽退料实际错误: %s' % state)
                    row = self._feed_row(idx, slot, check, allow_zero=True)
                    if previous is not None and row['decoder'] != previous['decoder']:
                        seen_motion = True
                        progress_at = a.reactor.monotonic()
                    if row['length'] > MAX_MM + RECOVERY_MARGIN_MM:
                        raise RuntimeError('退出 ACE 超过本次电机行程上限')
                    if a.reactor.monotonic() - progress_at >= 5:
                        raise RuntimeError('退出 ACE 实际编码器 5 秒无进度')
                    if state == 'ready' and seen_motion:
                        break
                    previous = row
                    a.reactor.pause(min(deadline, a.reactor.monotonic() + .1))
                gate = self._eject_stop_ready(idx, slot, transport, motion_start, allow_fault=True)
                self._motor_pending = False
                if gate:
                    raise RuntimeError('已达到本次退料上限，入口仍有料')
            self._commit_ejected(idx, slot, transport)
            self.state.update(state='complete', message='耗材已退出 ACE 入口', stopped=True,
                              gate=False, ejected=True, path_cleared=True, source_retained=False,
                              slot_status=self._last_eject_stop['status'])
        except Exception as exc:
            self.state.update(state='cancelled' if self.cancel else 'error', message=str(exc))
        finally:
            if self._motor_pending or stop_pending:
                try:
                    self._eject_stop_ready(idx, slot, transport, allow_fault=True)
                    self.state.update(stopped=True, gate=self._last_eject_stop['gate'],
                                      slot_status=self._last_eject_stop['status'])
                except Exception as exc:
                    self.state.update(state='error', stopped=False,
                        message=self.state.get('message', '') + '；停止未确认: %s' % exc)
                    a._latch_stop_fault(idx, slot, str(exc), identity=self._eject_identity)
            self._motor_pending = False
            self._eject_pending = None
            self._eject_lease = None
            self._eject_sources = None
            self._eject_params = None
            self._recovery_owner = None
            if recovery is not None and owner_token is not None:
                recovery.end(owner_token)
            a._swap_in_progress = False

def _latch_position_journal_failure(ace, idx, slot, identity, error):
    """Use the independent STOP journal if physical-position storage fails."""
    try:
        ace._latch_stop_fault(idx, slot,
            '位置记录无法落盘，原料路仍待核；请勿用重启清除: %s' % error, identity=identity)
    except Exception:
        logging.exception('[multiACE] secondary position-fault journal also failed')


def persist_rfid_position_uncertain(ace, idx, slot, identity=None, heads=None, scope="route"):
    port, uid = identity if identity is not None else ace._location_identity(idx)
    logging.warning('[multiACE] RFID read incomplete ACE=%d slot=%d', idx, slot)
    if heads is None:
        heads = ace._rfid_route_heads(idx, slot)
    hold = ace._location_hold
    # The in-memory hold is effective even if fsync/rename fails.  Callers must
    # still issue STOP in their finally path when persistence raises.
    hold.hold_memory(port, uid, slot, heads, scope)
    if (idx, slot) == (0, 3) and getattr(ace, '_normal_load_speed', None) is not None:
        ace._normal_load_speed.park = None
    try:
        hold.hold_route(port, uid, slot, heads, scope)
    except Exception as exc:
        _latch_position_journal_failure(ace, idx, slot, (port, uid), exc)
        raise
    travel = getattr(ace, '_head_travel', None)
    if travel is not None:
        for head in heads or []:
            travel.unknown(head, idx, slot, 'rfid_position_unconfirmed')


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
            if (actions.ace._serials.get(idx) is not transport or transport is None
                    or actions.ace._location_identity(idx) != original_identity):
                raise RuntimeError('ACE transport changed; old motion stillness unknown')
            deadline = reactor.monotonic() + 8
            def monitor():
                if (actions.ace._serials.get(idx) is not transport
                        or actions.ace._location_identity(idx) != original_identity
                        or reactor.monotonic() >= deadline):
                    raise RuntimeError('ACE transport changed or STOP verification timed out')
            actions._eject_stop(idx, slot, transport, monitor)
            monitor()
            actions._eject_stable(idx, slot, monitor=monitor, allow_fault=True)
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
        persistence_error = None
        try:
            persist_rfid_position_uncertain(actions.ace, idx, slot,
                identity=original_identity, heads=original_heads, scope=original_scope)
        except Exception as exc:
            persistence_error = exc
            reader._motion_uncertain += '; 料路异常记录持久化失败: %s' % exc
            logging.exception('[multiACE] RFID physical hold persistence failed')
        finally:
            stop_and_verify()
        if persistence_error is not None:
            raise motion_uncertain_type(reader._motion_uncertain)

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
