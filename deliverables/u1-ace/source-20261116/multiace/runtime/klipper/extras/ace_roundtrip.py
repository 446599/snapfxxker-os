"""UNDEPLOYED candidate: one guarded ACE0 slot3 / head3 relative-distance trial.

Requires explicit review and an integration adapter before installation.
"""
import logging
from .measurement_candidate import ForwardSpan, MeasurementError

FEED_MAX_MM = 2100
FEED_SPEED = 10
RETRACT_SPEED = 5
RETRACT_STEP = 20
RESERVE_MM = 0

def return_segment(remaining, first):
    if first:
        length = min(20, remaining)
        return length, 5
    if remaining <= 120:
        return remaining, 5
    return min(1000, remaining - 80), 40

class HeadReached(Exception):
    pass

class MeasuredSession:
    def __init__(self, ace, actions):
        self.a, self.actions = ace, actions
        self.idx, self.slot, self.head = 0, 3, 3
        self.transport = None
        self.cancel = False
        self.state = {'state': 'idle'}
        ace.printer.lookup_object('webhooks').register_endpoint('multiace/roundtrip_cancel', self.cancel_endpoint)

    def cancel_endpoint(self, request):
        running = self.actions.state.get('state') == 'running' and self.actions.state.get('kind') in ('roundtrip', 'hotpark')
        if running:
            self.cancel = True
        request.send({'cancelled': running})

    def _sensor(self, head):
        obj = self.a.printer.lookup_object('filament_motion_sensor e%d_filament' % head, None)
        if obj is None:
            raise RuntimeError('喷头传感器不可用')
        pin = getattr(obj, 'runout_buttun_state', None)
        if getattr(self, '_hot_raw', False) and type(pin) is not bool:
            raise RuntimeError('喷头物理传感器状态未知')
        raw = pin if type(pin) is bool else obj.get_status(0).get('filament_detected')
        if type(raw) is not bool:
            raise RuntimeError('喷头传感器状态未知')
        return raw

    def _monitor(self, *, detect_head=False):
        a = self.a
        a._require_no_stop_fault()
        if self.cancel or self.actions.cancel: raise RuntimeError('用户停止')
        hot = getattr(self, '_hot_context', None)
        if hot is not None: hot.check_route()
        if a._serials.get(self.idx) is not self.transport or not a._connected_per_ace.get(self.idx):
            raise RuntimeError('ACE 连接改变')
        ps = a.printer.lookup_object('print_stats', None)
        if ps is None or getattr(ps, 'state', '').lower() not in ('standby', 'complete', 'cancelled'):
            raise RuntimeError('打印状态改变')
        for h in range(4):
            present = self._sensor(h)
            if h == self.head and detect_head and present:
                raise HeadReached()
            if h != self.head and present:
                hot = getattr(self, '_hot_context', None)
                if hot is None or not hot.other_head_safe(h):
                    raise RuntimeError('其他喷头来源未知或与当前槽冲突')
        fresh = getattr(self, '_seek_fresh', None)
        if fresh is not None and a.reactor.monotonic() - fresh[0] > 1.5:
            raise RuntimeError('送料编码器遥测超过1.5秒未更新')

    def _stop_stable(self, started, direction):
        a, x = self.a, (getattr(self, '_hot_context', None) or self.actions)
        # Send STOP immediately, then repeat after the firmware setup window.
        def original_transport():
            if a._serials.get(self.idx) is not self.transport:
                raise RuntimeError('原 ACE 连接已改变')
        original_transport()
        x._request(self.idx, 'stop_feed_filament', {'index':self.slot}, 9, monitor=original_transport)
        while a.reactor.monotonic() - started < .3:
            if a._serials.get(self.idx) is not self.transport: raise RuntimeError('原连接断开')
            a.reactor.pause(min(started + .3, a.reactor.monotonic() + .05))
        if a._serials.get(self.idx) is not self.transport: raise RuntimeError('原连接断开')
        original_transport()
        x._request(self.idx, 'stop_feed_filament', {'index':self.slot}, 9, monitor=original_transport)
        deadline = a.reactor.monotonic() + 8
        def check():
            if a._serials.get(self.idx) is not self.transport or a.reactor.monotonic() >= deadline:
                raise RuntimeError('停止确认超时或连接改变')
        while True:
            check()
            lanes = x._lane_states(self.idx, monitor=check)
            if any(s != 'ready' for i,s in lanes.items() if i != self.slot): raise RuntimeError('其他槽状态异常')
            if lanes[self.slot] == 'ready':
                return x._stable(self.idx, self.slot, monitor=check)
            if lanes[self.slot] != direction: raise RuntimeError('目标槽状态异常')
            a.reactor.pause(min(deadline, a.reactor.monotonic() + .1))

    def _feed_row(self, detect_head=True, monitor=None):
        if monitor is None: monitor = lambda:self._monitor(detect_head=detect_head)
        result = self.actions._request(self.idx, 'get_feed_info', {}, 76, monitor=monitor)
        rows = result.get('feed_info')
        if result.get('_feed_wire_valid') is not True or not isinstance(rows,list) or len(rows) != 4:
            raise RuntimeError('编码器数据无效')
        selected = [r for r in rows if isinstance(r,dict) and r.get('index') == self.slot]
        if len(selected) != 1: raise RuntimeError('编码器槽位无效')
        return selected[0]

    def run(self, carry=0, ref_length=None, anchor=None, adjust=0, hot_context=None, feed_budget=None):
        if anchor is not None and (type(anchor) is not int or not 100 <= anchor <= 2100):
            raise ValueError('参考锚长度无效')
        if type(adjust) is not int or not -30 <= adjust <= 30 or (anchor is None and adjust != 0):
            raise ValueError('几何调整无效')
        if ref_length is not None and (type(ref_length) is not int or not 100 <= ref_length <= 2100):
            raise ValueError('参考长度无效')
        if type(carry) is not int or not -10 <= carry <= 10:
            raise ValueError("累计残差超出允许范围")
        a, x = self.a, (hot_context if hot_context is not None else self.actions)
        if feed_budget is None: feed_budget = FEED_MAX_MM
        if type(feed_budget) is not int or not 1 <= feed_budget <= FEED_MAX_MM:
            raise ValueError('重新定位限程无效')
        cycle_start = a.reactor.monotonic()
        x._check(self.idx,self.slot)
        if hot_context is not None:
            hot_context.validate_feed_states()
        else:
            for side in ('left', 'right'):
                feed = a.printer.lookup_object('filament_feed ' + side, None)
                if feed is None:
                    raise RuntimeError('送料模块状态不可用')
                states = getattr(feed, 'channel_action_state', None)
                if not isinstance(states, list) or len(states) != 2 or any(
                        state not in ('none', 'load_finish', 'unload_finish', 'preload_finish') for state in states):
                    raise RuntimeError('送料模块正在运行或状态未知')
        self.transport = a._serials.get(self.idx)
        if self.transport is None: raise RuntimeError('ACE未连接')
        def baseline_check():
            x._check(self.idx, self.slot, self.transport)
        x._lanes_ready(self.idx)
        if not x._gate(self.idx,self.slot,monitor=baseline_check): raise RuntimeError('ACE入口无料')
        _, initial_decoder = x._stable(self.idx,self.slot,allow_initial_zero=True,monitor=baseline_check)
        raw_before = initial_decoder[1]
        before_signed = raw_before - (1 << 64) if raw_before >= (1 << 63) else raw_before
        baseline_check()
        if x.state.get('state') == 'running' and not (hot_context is not None and x.state.get('kind') == 'hotpark'):
            raise RuntimeError('料槽操作忙')
        a._swap_in_progress = True
        x.state.update(state='running',kind='roundtrip',ace=self.idx,slot=self.slot)
        x.cancel = False
        self.cancel = False
        tracker = ForwardSpan()
        saw_command_zero = False
        last_forward = None
        first_forward = None
        segments = []
        moving = False
        started = 0
        result = {'ace':self.idx,'slot':self.slot,'head':self.head,'reserve_mm':RESERVE_MM,
                  'commanded_retract_mm':0,'forward_lower_bound_mm':None,'head_reached':False, 'segments': segments, 'reference_valid': False, 'carry_in_mm':carry, 'ref_length_mm':ref_length, 'anchor_mm':anchor, 'anchor_adjust_mm':adjust, 'speed_change_ack':False}
        self._hot_raw = hot_context is not None
        self._hot_context = hot_context
        try:
            self._monitor(detect_head=True)
            x.state.update(phase='feeding', progress=0, message='正在送至喷头传感器')
            feed_speed = 40 if ref_length is not None else FEED_SPEED
            speed_changed = False
            moving = True
            started = a.reactor.monotonic()
            self._seek_fresh = [started]
            try:
                x._request(self.idx,'feed_filament',{'index':self.slot,'length':feed_budget,'speed':feed_speed},8,
                           monitor=lambda:self._monitor(detect_head=True))
                deadline = started + feed_budget / FEED_SPEED + 20
                last_progress = a.reactor.monotonic()
                last_value = None
                observed_feeding = False
                while a.reactor.monotonic() < deadline:
                    self._monitor(detect_head=True)
                    lanes = x._lane_states(self.idx,monitor=lambda:self._monitor(detect_head=True))
                    if any(s != 'ready' for i,s in lanes.items() if i != self.slot): raise RuntimeError('其他料槽不空闲')
                    if lanes[self.slot] == 'feeding': observed_feeding = True
                    elif lanes[self.slot] != 'ready' or observed_feeding: raise RuntimeError('送料提前结束')
                    if observed_feeding:
                        if not x._gate(self.idx,self.slot,monitor=lambda:self._monitor(detect_head=True)): raise RuntimeError('入口耗材已离开')
                        row = self._feed_row()
                        if row.get('_wire_valid') is True and type(row.get('_decoder_present')) is bool and row.get('decoder') == 0 and last_forward is None:
                            saw_command_zero = True
                        tracker.add(row.get('decoder'),frame_valid=True,row_valid=row.get('_wire_valid'),
                                    present=row.get('_decoder_present'),steps=row.get('steps'),length=row.get('length'))
                        self._seek_fresh[0] = a.reactor.monotonic()
                        if row.get('_decoder_present') and row['decoder'] > 0:
                            if first_forward is None: first_forward = row['decoder']
                            last_forward = row['decoder']
                            x.state['progress'] = last_forward
                            if (ref_length is not None and not speed_changed and
                                    last_forward >= max(0,ref_length - 200)):
                                x._request(self.idx,'update_feeding_speed',
                                           {'index':self.slot,'speed':FEED_SPEED},10,
                                           monitor=lambda:self._monitor(detect_head=True))
                                speed_changed = True
                                result['speed_change_ack'] = True
                                result['speed_change_encoder_mm'] = last_forward
                        if row['decoder'] != last_value:
                            last_value, last_progress = row['decoder'], a.reactor.monotonic()
                        if a.reactor.monotonic() - last_progress > 8: raise RuntimeError('送料编码器无进展')
                    elif a.reactor.monotonic() - started > 5:
                        raise RuntimeError('未观察到送料启动')
                    a.reactor.pause(a.reactor.monotonic()+.1)
                raise RuntimeError('送料超时')
            except HeadReached:
                result['head_reached'] = True
            finally:
                self._seek_fresh = None
                try:
                    _gate_after_feed, forward_terminal = self._stop_stable(started,'feeding')
                    moving = False
                except Exception as exc:
                    a._latch_stop_fault(self.idx,self.slot,'measured feed stop not confirmed: %s' % exc)
                    raise
            result['forward_lower_bound_mm'] = tracker.lower_bound()
            result['previous_decoder'] = before_signed
            result['forward_first'] = first_forward
            reset_verified = bool(saw_command_zero or (first_forward is not None and (before_signed < 0 or first_forward < before_signed)))
            result['forward_zero_observed'] = saw_command_zero
            result['forward_reset_verified'] = reset_verified
            result['forward_last_before_stop'] = last_forward
            result['forward_terminal'] = forward_terminal
            if (not _gate_after_feed or not reset_verified or last_forward is None
                    or forward_terminal[0] is not True or type(forward_terminal[1]) is not int
                    or not last_forward <= forward_terminal[1] <= FEED_MAX_MM + 100):
                raise RuntimeError('送料终值或起点未完整确认，禁止回抽')
            result['forward_exact_encoder_mm'] = forward_terminal[1]
            forward = result['forward_exact_encoder_mm']
            target = (anchor + adjust) if anchor is not None else (forward + carry)
            if target <= 0: raise RuntimeError('回抽目标无效')
            result['retract_target_mm'] = target
            x.state.update(phase='returning',progress=0,message='正在回抽至参考点')
            budget = int(max(forward, target) * 1.15) + 20 if hot_context is not None else int(forward * 1.15) + 20
            result['retract_budget_mm'] = budget
            previous_terminal = forward
            x.state.update(phase='returning',progress=0,message='正在单次回抽至参考点')
            moving = True
            started = a.reactor.monotonic()
            return_start = started
            last_encoder_time = [started]
            def return_monitor():
                self._monitor()
                if a.reactor.monotonic() - last_encoder_time[0] > 1.5:
                    raise RuntimeError('回抽编码器遥测超过1.5秒未更新')
            result['return_bulk_speed_mm_s'] = 60
            result['return_slow_margin_mm'] = 350
            result['return_slow_speed_mm_s'] = 10
            result['return_stop_lead_mm'] = 2
            result['commanded_retract_mm'] = budget  # command budget, STOP ends motion earlier
            result['single_return_budget_mm'] = budget
            x._request(self.idx,'unwind_filament',{'index':self.slot,'length':budget,'speed':5},8,monitor=return_monitor)
            deadline = started + budget / 5 + 20
            last_signed = None
            first_signed = None
            seen = False
            accelerated = False
            slowed = False
            head_cleared = False
            last_progress = started
            while a.reactor.monotonic() < deadline:
                return_monitor()
                if not x._gate(self.idx,self.slot,monitor=return_monitor): raise RuntimeError('入口耗材已离开')
                lanes = x._lane_states(self.idx,monitor=return_monitor)
                if any(st != 'ready' for i,st in lanes.items() if i != self.slot): raise RuntimeError('其他料槽忙')
                st = lanes[self.slot]
                if st not in ('ready','rollback'): raise RuntimeError('回抽状态异常')
                if st == 'rollback': seen = True
                if st == 'ready' and seen: raise RuntimeError('回抽预算提前结束')
                if not seen and a.reactor.monotonic() - started > 5: raise RuntimeError('未观察到回抽开始')
                if seen:
                    row = self._feed_row(detect_head=False,monitor=return_monitor)
                    raw = row.get('decoder')
                    present = row.get('_decoder_present')
                    if row.get('_wire_valid') is not True or type(raw) is not int or type(present) is not bool:
                        raise RuntimeError('回抽编码器无效')
                    value = raw - (1 << 64) if raw >= (1 << 63) else raw
                    if value > 0:
                        if first_signed is not None: raise RuntimeError('回抽编码器反向或重置')
                    else:
                        if value != 0 and not present: raise RuntimeError('回抽编码器缺失')
                        if first_signed is None: first_signed = value
                        if last_signed is not None and value > last_signed: raise RuntimeError('回抽编码器反向或重置')
                        last_encoder_time[0] = a.reactor.monotonic()
                        if value != last_signed: last_progress = a.reactor.monotonic()
                        last_signed = value
                        distance = -value
                        x.state['progress'] = distance
                        now_clear = not self._sensor(self.head)
                        if head_cleared and not now_clear: raise RuntimeError('喷头传感器重新触发')
                        head_cleared = head_cleared or now_clear
                        if distance >= target - 2:
                            break
                        if not accelerated and head_cleared and distance >= 20 and distance < target - 350:
                            x._request(self.idx,'update_feeding_speed',{'index':self.slot,'speed':60},10,monitor=return_monitor)
                            accelerated = True
                            result['return_accelerate_mm'] = distance
                        if accelerated and not slowed and distance >= target - 350:
                            x._request(self.idx,'update_feeding_speed',{'index':self.slot,'speed':10},10,monitor=return_monitor)
                            slowed = True
                            result['return_slow_mm'] = distance
                        if a.reactor.monotonic() - last_progress > 8: raise RuntimeError('回抽编码器无进展')
                a.reactor.pause(a.reactor.monotonic()+.1)
            else: raise RuntimeError('回抽时间上限')
            gate, terminal_decoder = self._stop_stable(started,'rollback')
            moving = False
            raw = terminal_decoder[1]
            terminal_signed = raw - (1 << 64) if raw >= (1 << 63) else raw
            if (not gate or not seen or first_signed is None or last_signed is None or
                    terminal_decoder[0] is not True or terminal_signed >= 0 or
                    terminal_signed > last_signed or terminal_signed < -budget-10):
                raise RuntimeError('回抽停止终值不完整')
            result['retract_exact_encoder_mm'] = -terminal_signed
            result['verified_retract_mm'] = max(0,first_signed-last_signed)
            result['retract_measurement_complete'] = True
            result['return_first'] = first_signed
            result['return_last_before_stop'] = last_signed
            result['return_terminal'] = terminal_signed
            result['return_duration_s'] = round(a.reactor.monotonic()-return_start, 3)
            result['return_stop_requested_at_mm'] = -last_signed
            result['return_speed_accelerated'] = accelerated
            result['return_speed_slowed'] = slowed
            x.state['progress'] = -terminal_signed
            result.update(head_clear=not self._sensor(self.head),gate_present=x._gate(self.idx,self.slot))
            if not result['head_clear'] or not result['gate_present']:
                raise RuntimeError('喷头未清空或入口未确认有料')
            result['carry_out_mm'] = carry + forward - result.get('retract_exact_encoder_mm', 0)
            result['encoder_target_reached'] = bool(result.get('retract_measurement_complete') and
                abs(target - result.get('retract_exact_encoder_mm', 0)) <= 2)
            # An encoder anchor is not proof of physical R0; only later e3
            # comparison can supply geometric repeatability evidence.
            result['reference_valid'] = bool(anchor is None and result['encoder_target_reached'])
            result.update(state='complete' if result['encoder_target_reached'] else 'partial')
        except Exception as exc:
            result.update(state='error',error=str(exc))
        finally:
            if moving:
                try: self._stop_stable(started,'feeding' if not result.get('head_reached') else 'rollback')
                except Exception as exc:
                    a._latch_stop_fault(self.idx,self.slot,'measured trial stop not confirmed: %s' % exc)
                    result.update(state='error',error='停止未确认')
            result['cycle_duration_s'] = round(a.reactor.monotonic()-cycle_start, 3)
            x.state.update(state=result['state'], message=str(result))
            a._swap_in_progress = False
            logging.info('[multiACE] roundtrip trial %s',result)
            self.state = result
            self._hot_raw = False
            self._hot_context = None
        return result
