"""UNDEPLOYED candidate: one guarded ACE0 slot3 / head3 relative-distance trial.

Requires explicit review and an integration adapter before installation.
"""
import logging
from .measurement_candidate import ForwardSpan, MeasurementError

FEED_MAX_MM = 2100
FEED_SPEED = 10
RETRACT_SPEED = 5
RETRACT_STEP = 20
RESERVE_MM = 100

def return_segment(remaining, first):
    length = min(20, remaining) if first or remaining <= 20 else min(100, remaining - 20)
    return length, (5 if length <= 20 else 20)

class HeadReached(Exception):
    pass

class MeasuredSession:
    def __init__(self, ace, actions):
        self.a, self.actions = ace, actions
        self.idx, self.slot, self.head = 0, 3, 3
        self.transport = None
        self.cancel = False
        self.state = {'state': 'idle'}
        ace.printer.lookup_object('webhooks').register_endpoint('multiace/measured_trial_cancel', self.cancel_endpoint)

    def cancel_endpoint(self, request):
        running = self.actions.state.get('state') == 'running' and self.actions.state.get('kind') == 'measure'
        if running:
            self.cancel = True
        request.send({'cancelled': running})

    def _sensor(self, head):
        obj = self.a.printer.lookup_object('filament_motion_sensor e%d_filament' % head, None)
        if obj is None:
            raise RuntimeError('喷头传感器不可用')
        pin = getattr(obj, 'runout_buttun_state', None)
        raw = pin if type(pin) is bool else obj.get_status(0).get('filament_detected')
        if type(raw) is not bool:
            raise RuntimeError('喷头传感器状态未知')
        return raw

    def _monitor(self, *, detect_head=False):
        a = self.a
        a._require_no_stop_fault()
        if self.cancel or self.actions.cancel: raise RuntimeError('用户停止')
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
                raise RuntimeError('其他喷头有料')

    def _stop_stable(self, started, direction):
        a, x = self.a, self.actions
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

    def _feed_row(self, detect_head=True):
        result = self.actions._request(self.idx, 'get_feed_info', {}, 76, monitor=lambda:self._monitor(detect_head=detect_head))
        rows = result.get('feed_info')
        if result.get('_feed_wire_valid') is not True or not isinstance(rows,list) or len(rows) != 4:
            raise RuntimeError('编码器数据无效')
        selected = [r for r in rows if isinstance(r,dict) and r.get('index') == self.slot]
        if len(selected) != 1: raise RuntimeError('编码器槽位无效')
        return selected[0]

    def run(self):
        a, x = self.a, self.actions
        x._check(self.idx,self.slot)
        for side in ('left', 'right'):
            feed = a.printer.lookup_object('filament_feed ' + side, None)
            if feed is None:
                raise RuntimeError('送料模块状态不可用')
            states = getattr(feed, 'channel_action_state', None)
            if not isinstance(states, list) or len(states) != 2 or any(
                    state not in ('none', 'load_finish', 'unload_finish', 'preload_finish') for state in states):
                raise RuntimeError('送料模块正在运行或状态未知')
        x._lanes_ready(self.idx)
        if not x._gate(self.idx,self.slot): raise RuntimeError('ACE入口无料')
        self.transport = a._serials.get(self.idx)
        if self.transport is None: raise RuntimeError('ACE未连接')
        if x.state.get('state') == 'running': raise RuntimeError('料槽操作忙')
        a._swap_in_progress = True
        x.state.update(state='running',kind='measure',ace=self.idx,slot=self.slot)
        x.cancel = False
        self.cancel = False
        tracker = ForwardSpan()
        moving = False
        started = 0
        result = {'ace':self.idx,'slot':self.slot,'head':self.head,'reserve_mm':RESERVE_MM,
                  'commanded_retract_mm':0,'forward_lower_bound_mm':None,'head_reached':False}
        try:
            self._monitor(detect_head=True)
            moving = True
            started = a.reactor.monotonic()
            try:
                x._request(self.idx,'feed_filament',{'index':self.slot,'length':FEED_MAX_MM,'speed':FEED_SPEED},8,
                           monitor=lambda:self._monitor(detect_head=True))
                deadline = started + FEED_MAX_MM / FEED_SPEED + 20
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
                        tracker.add(row.get('decoder'),frame_valid=True,row_valid=row.get('_wire_valid'),
                                    present=row.get('_decoder_present'),steps=row.get('steps'),length=row.get('length'))
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
                try:
                    self._stop_stable(started,'feeding')
                    moving = False
                except Exception as exc:
                    a._latch_stop_fault(self.idx,self.slot,'measured feed stop not confirmed: %s' % exc)
                    raise
            result['forward_lower_bound_mm'] = tracker.lower_bound()
            budget = max(0,result['forward_lower_bound_mm'] - RESERVE_MM)
            result['retract_budget_mm'] = budget
            while result['commanded_retract_mm'] < budget:
                self._monitor()
                if not x._gate(self.idx,self.slot,monitor=self._monitor): raise RuntimeError('入口耗材已离开')
                remaining = budget-result['commanded_retract_mm']
                length, speed = return_segment(remaining, result['commanded_retract_mm'] == 0)
                moving = True
                started = a.reactor.monotonic()
                x._request(self.idx,'unwind_filament',{'index':self.slot,'length':length,'speed':speed},8,monitor=self._monitor)
                result['commanded_retract_mm'] += length
                deadline = started + max(12,2*length/speed+4)
                seen = False
                first_decoder = None
                last_decoder = None
                reset_allowed = True
                clear_seen = not self._sensor(self.head)
                while a.reactor.monotonic() < deadline:
                    self._monitor()
                    lanes = x._lane_states(self.idx,monitor=self._monitor)
                    if any(s != 'ready' for i,s in lanes.items() if i != self.slot): raise RuntimeError('其他料槽忙')
                    seen = seen or lanes[self.slot] == 'rollback'
                    if lanes[self.slot] not in ('ready','rollback'): raise RuntimeError('退料状态异常')
                    if seen:
                        row = self._feed_row(detect_head=False)
                        raw = row.get('decoder')
                        if row.get('_wire_valid') is not True or type(raw) is not int or type(row.get('_decoder_present')) is not bool:
                            raise RuntimeError('退料编码器无效')
                        value = raw - (1 << 64) if raw >= (1 << 63) else raw
                        if value > 0 or value < -length-10 or (value != 0 and not row['_decoder_present']):
                            raise RuntimeError('退料编码器方向或幅度异常')
                        if value != 0 and not row['_decoder_present']:
                            raise RuntimeError('退料编码器缺失')
                        if first_decoder is None:
                            first_decoder = value
                        elif value > last_decoder:
                            if not reset_allowed or last_decoder < first_decoder:
                                raise RuntimeError('退料编码器反向或重置')
                            first_decoder = value
                            reset_allowed = False
                        if value < first_decoder:
                            reset_allowed = False
                        last_decoder = value
                        now_clear = not self._sensor(self.head)
                        if clear_seen and not now_clear: raise RuntimeError('喷头传感器重新触发')
                        clear_seen = clear_seen or now_clear
                    if seen and lanes[self.slot] == 'ready': break
                    if not x._gate(self.idx,self.slot,monitor=self._monitor): raise RuntimeError('入口耗材已离开')
                    a.reactor.pause(a.reactor.monotonic()+.1)
                else: raise RuntimeError('退料超时')
                gate, _decoder = self._stop_stable(started,'rollback')
                moving = False
                if not gate or first_decoder is None or last_decoder is None or last_decoder >= first_decoder:
                    raise RuntimeError('退料编码器无可证实进展或入口状态异常')
                result['verified_retract_mm'] = result.get('verified_retract_mm', 0) + first_decoder - last_decoder
                result['retract_measurement_complete'] = False
            result.update(head_clear=not self._sensor(self.head),gate_present=x._gate(self.idx,self.slot))
            if not result['head_clear'] or not result['gate_present']:
                raise RuntimeError('喷头未清空或入口未确认有料')
            result.update(state='complete')
        except Exception as exc:
            result.update(state='error',error=str(exc))
        finally:
            if moving:
                try: self._stop_stable(started,'feeding' if not result.get('head_reached') else 'rollback')
                except Exception as exc:
                    a._latch_stop_fault(self.idx,self.slot,'measured trial stop not confirmed: %s' % exc)
                    result.update(state='error',error='停止未确认')
            x.state.update(state=result['state'], message=str(result))
            a._swap_in_progress = False
            logging.info('[multiACE] measured trial %s',result)
            self.state = result
        return result
