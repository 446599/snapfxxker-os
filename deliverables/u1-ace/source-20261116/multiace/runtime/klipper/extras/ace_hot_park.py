"""Narrow, fail-closed identity/epoch policy for a supervised hot park.

No I/O or movement is performed here.  A one-time seed requires explicit
user-confirmed evidence supplied by an integration layer, not a slot default.
"""
from dataclasses import dataclass

@dataclass(frozen=True)
class Route:
    ace_uid: tuple
    slot: int
    head: int
    spool_uid: str

class ParkUnavailable(RuntimeError):
    pass

class ParkEpoch:
    def __init__(self):
        self.state = 'unknown'
        self.route = None
        self.transport = None
        self.reason = 'no observed park reference'
        self.generation = 0

    def seed(self, *, route, transport, user_confirmed, head_clear, gate_present):
        if (not isinstance(route, Route) or len(route.ace_uid) != 3 or
                any(type(x) is not int for x in route.ace_uid) or
                not isinstance(route.spool_uid,str) or not route.spool_uid or
                type(route.slot) is not int or not 0 <= route.slot < 4 or
                type(route.head) is not int or not 0 <= route.head < 4 or
                transport is None or user_confirmed is not True or
                head_clear is not True or gate_present is not True):
            raise ParkUnavailable('用户确认的同卷参考不可用')
        self.route, self.transport = route, transport
        self.state, self.reason = 'parked', None
        self.generation += 1

    def invalidate(self, reason):
        self.state, self.reason = 'unknown', str(reason)
        self.generation += 1

    def check(self, *, route, transport, gate_present, expected):
        if (self.state not in expected or route != self.route or transport is not self.transport
                or gate_present is not True):
            self.invalidate('来源、门状态、连接或会话不可确认')
            raise ParkUnavailable(self.reason)

    def begin_load(self, *, route, transport, gate_present):
        self.check(route=route,transport=transport,gate_present=gate_present,expected=('parked',))
        self.state = 'loading'

    def load_complete(self, *, route, transport, gate_present, head_loaded):
        self.check(route=route,transport=transport,gate_present=gate_present,expected=('loading',))
        if head_loaded is not True:
            self.invalidate('装载未确认')
            raise ParkUnavailable(self.reason)
        self.state = 'loaded'

    def begin_unload(self, *, route, transport, gate_present):
        self.check(route=route,transport=transport,gate_present=gate_present,expected=('loaded',))
        self.state = 'unloading'

    def park_complete(self, *, route, transport, gate_present, head_clear, stopped, target_reached):
        self.check(route=route,transport=transport,gate_present=gate_present,expected=('unloading',))
        if head_clear is not True or stopped is not True or target_reached is not True:
            self.invalidate('停泊未确认')
            raise ParkUnavailable(self.reason)
        self.state = 'parked'

# Geometric profile, measured on the same unit/slot/head and tubing.  It is
# not a statement about where a newly inserted spool currently sits.
PROFILE_UID = (808780566, 17445170, 942880330)
PROFILE_SLOT = 3
PROFILE_HEAD = 3
PROFILE_ANCHOR = 1397
PROFILE_ADJUST = -4

class OwnedActions:
    """Delegate validated protocol calls while preserving the unload owner."""
    def __init__(self, ace, actions, route, transport):
        object.__setattr__(self,'ace',ace)
        object.__setattr__(self,'actions',actions)
        object.__setattr__(self,'route',route)
        object.__setattr__(self,'transport',transport)
        object.__setattr__(self,'watch_timer',None)
        object.__setattr__(self,'watch_error',None)
        object.__setattr__(self,'recovery',False)
    def __getattr__(self, name): return getattr(self.actions,name)
    def __setattr__(self, name, value): setattr(self.actions,name,value)
    def _check(self, idx, slot, transport=None, own=False, allow_tag=False):
        a=self.ace
        a._require_no_stop_fault()
        if self.actions.cancel: raise ParkUnavailable('用户停止')
        if idx != 0 or slot != PROFILE_SLOT or (transport is not None and transport is not self.transport):
            raise ParkUnavailable('原设备或料槽改变')
        if a._serials.get(idx) is not self.transport or not a._connected_per_ace.get(idx) or not a._is_v2_idx(idx) or idx in a._fw_update_hold:
            raise ParkUnavailable('原 ACE 连接不可用')
        ps=a.printer.lookup_object('print_stats',None)
        if ps is None or getattr(ps,'state','').lower() not in ('standby','complete','cancelled'):
            raise ParkUnavailable('打印机不空闲')
        if (a._swap_in_progress and not own) or a._unload_all_active or getattr(a,'_tag_read_busy',False) or getattr(a,'_gen1_manual_busy',False):
            raise ParkUnavailable('其他作业正在运行')
        if (a._calibration or {}).get('state') not in ('idle','cancelled','complete','completed','error') or getattr(a,'_calibration_unload',{}).get('active'):
            raise ParkUnavailable('校准正在运行')
        bg=a.printer.lookup_object('ace_bg_swap',None)
        guard=a.printer.lookup_object('ace_native_guard',None)
        if (bg is not None and getattr(bg,'_busy',False)) or (guard is not None and getattr(guard,'state',None)!='idle'):
            raise ParkUnavailable('后台作业或原生保护正在运行')
        self.check_route()

        for head in range(4):
            sensor=a.printer.lookup_object('filament_motion_sensor e%d_filament'%head,None)
            pin=getattr(sensor,'runout_buttun_state',None) if sensor is not None else None
            if type(pin) is not bool:
                raise ParkUnavailable('喷头物理传感器状态未知')
            if head == PROFILE_HEAD and pin:
                raise ParkUnavailable('目标喷头尚未物理清空')
            if head != PROFILE_HEAD and pin and not self.other_head_safe(head):
                raise ParkUnavailable('其他喷头来源未知或与当前料槽冲突')

    def check_route(self):
        if self.watch_error is not None: raise ParkUnavailable(self.watch_error)
        source=self.ace._head_source.get(PROFILE_HEAD) or {}
        if (source.get('ace_index') != 0 or source.get('slot') != PROFILE_SLOT or
                self.ace._get_heads_for_ace_slot(0,PROFILE_SLOT) != [PROFILE_HEAD] or
                self.ace._serials.get(0) is not self.transport):
            raise ParkUnavailable('装载来源或原连接改变')

    def preflight(self):
        a=self.ace
        a._require_no_stop_fault()
        self.check_route()
        if not a._connected_per_ace.get(0) or not a._is_v2_idx(0) or 0 in a._fw_update_hold:
            raise ParkUnavailable('ACE不可用')
        ps=a.printer.lookup_object('print_stats',None)
        if ps is None or getattr(ps,'state','').lower() not in ('standby','complete','cancelled'):
            raise ParkUnavailable('打印机不空闲')
        if (a._swap_in_progress or a._unload_all_active or getattr(a,'_tag_read_busy',False)
                or getattr(a,'_gen1_manual_busy',False) or self.actions.state.get('state')=='running'):
            raise ParkUnavailable('其他作业正在运行')
        if (a._calibration or {}).get('state') not in ('idle','cancelled','complete','completed','error') or getattr(a,'_calibration_unload',{}).get('active'):
            raise ParkUnavailable('校准正在运行')
        bg=a.printer.lookup_object('ace_bg_swap',None)
        guard=a.printer.lookup_object('ace_native_guard',None)
        if (bg is not None and getattr(bg,'_busy',False)) or (guard is not None and getattr(guard,'state',None)!='idle'):
            raise ParkUnavailable('后台作业或原生保护正在运行')

    def start_watch(self):
        if getattr(self.ace,'_owned_hot_park',None) is not None:
            raise ParkUnavailable('已有受控卸载')
        self.preflight()
        self.ace._owned_hot_park=self
        self.actions.cancel=False
        self.actions.state.update(state='running',kind='hotpark',ace=0,slot=PROFILE_SLOT,message='正在执行受控热卸载')
        def tick(eventtime):
            try:
                self.check_route()
            except Exception as exc:
                object.__setattr__(self,'watch_error',str(exc))
                try:
                    self.ace._latch_stop_fault(0,PROFILE_SLOT,'hot unload owner invalid: %s'%exc)
                    if self.ace._serials.get(0) is self.transport:
                        self.ace.send_request_to(0,{'method':'stop_feed_filament','params':{'index':PROFILE_SLOT},'_insert_stop_transport':self.transport,'_insert_reject':lambda self,response:None},lambda self,response:None)
                except Exception:
                    pass
                return self.ace.reactor.NEVER
            return eventtime + .1
        try:
            object.__setattr__(self,'watch_timer',self.ace.reactor.register_timer(tick,self.ace.reactor.NOW))
        except Exception:
            self.ace._owned_hot_park=None
            self.actions.state.update(state='error',message='热卸载监控未能启动')
            raise

    def close_watch(self):
        if self.watch_timer is not None:
            self.ace.reactor.unregister_timer(self.watch_timer)
            object.__setattr__(self,'watch_timer',None)
        if getattr(self.ace,'_owned_hot_park',None) is self:
            self.ace._owned_hot_park=None
        if self.actions.state.get('state')=='running' and self.actions.state.get('kind')=='hotpark':
            self.actions.state.update(state='error',message='热卸载未完成')

    def other_head_safe(self, head):
        source=self.ace._head_source.get(head) or {}
        idx,slot=source.get('ace_index'),source.get('slot')
        return (type(idx) is int and 0 <= idx < len(self.ace._ace_devices) and type(slot) is int and 0 <= slot < 4
                and (idx,slot) != (0,PROFILE_SLOT))

    def validate_feed_states(self):
        for side in ('left','right'):
            feed=self.ace.printer.lookup_object('filament_feed '+side,None)
            if feed is None: raise ParkUnavailable('送料模块状态不可用')
            states=getattr(feed,'channel_action_state',None)
            heads=getattr(feed,'filament_ch',None)
            if not isinstance(states,list) or not isinstance(heads,list) or len(states)!=2 or len(heads)!=2:
                raise ParkUnavailable('送料模块状态不可用')
            for head,state in zip(heads,states):
                allowed=(('unload_fail','none','load_finish','preload_finish') if self.recovery else ('unload_doing',)) if head==PROFILE_HEAD else ('none','load_finish','unload_finish','preload_finish')
                if state not in allowed: raise ParkUnavailable('其他送料任务正在运行')


def check_profile(ace, head, recovery=False):
    """Fresh discovery binds geometric profile to actual ACE hardware."""
    if head != PROFILE_HEAD: raise ParkUnavailable('该喷头尚无已验证停泊配置，未执行长退料')
    source=ace._head_source.get(head) or {}
    idx,slot=source.get('ace_index'),source.get('slot')
    if idx != 0 or slot != PROFILE_SLOT: raise ParkUnavailable('当前来源尚无已验证停泊配置')
    transport=ace._serials.get(idx)
    if transport is None: raise ParkUnavailable('ACE未连接')
    def monitor():
        if ace._serials.get(idx) is not transport: raise ParkUnavailable('ACE连接改变')
        ace._require_no_stop_fault()
    result=ace._slot_actions._request(idx,'discover_device',{},0,monitor=monitor)
    uid=tuple(result.get('uid%d'%i) for i in (1,2,3))
    if uid != PROFILE_UID: raise ParkUnavailable('ACE硬件与停泊配置不匹配')
    monitor()
    owned=OwnedActions(ace,ace._slot_actions,Route(uid,slot,head,''),transport)
    object.__setattr__(owned,'recovery',bool(recovery))
    owned.start_watch()
    return owned

def run_hot_park(ace, owned, short_commanded_mm):
    """Re-anchor at the positive head edge, then return to known park.

    Called only after stock hot release and sensor-clear verification.  The
    original source remains owned until the caller commits unload success.
    """
    if not isinstance(owned,OwnedActions): raise ParkUnavailable('缺少本次卸载身份上下文')
    if type(short_commanded_mm) is not int or short_commanded_mm <= 0:
        raise ParkUnavailable('短退长度未知')
    owned._check(0,PROFILE_SLOT,owned.transport)
    owned.validate_feed_states()
    try:
        ace._v2_active_rev_assist=False
        session=ace._roundtrip_session
        old_ctx,old_transport=session._hot_context if hasattr(session,'_hot_context') else None,session.transport
        session._hot_context=owned;session.transport=owned.transport
        try:session._stop_stable(ace.reactor.monotonic()-.3,'rollback')
        finally:session._hot_context=old_ctx;session.transport=old_transport
        ace._clear_fa_cache_for(0,PROFILE_SLOT)
    except Exception as exc:
        ace._latch_stop_fault(0,PROFILE_SLOT,'hot park pre-stop not confirmed: %s'%exc)
        raise ParkUnavailable('预定位停止未确认') from exc
    owned._check(0,PROFILE_SLOT,owned.transport)
    # A bounded additional search permits tipform displacement without using
    # the ordinary 2100 mm feed budget.  It does not seat into the extruder.
    limit=min(600,short_commanded_mm+150)
    result=ace._roundtrip_session.run(anchor=PROFILE_ANCHOR,adjust=PROFILE_ADJUST,
                                      hot_context=owned,feed_budget=limit)
    if result.get('state')!='complete' or result.get('head_clear') is not True or result.get('gate_present') is not True or result.get('encoder_target_reached') is not True:
        raise ParkUnavailable('未确认返回已验证停泊位：%s'%result.get('error','位置未知'))
    return result

# One specific, recorded failure; cannot authorize generic unload failures.
RECOVERY_PROOF = '/home/lava/printer_data/controlled-exploration/hot-park-pending-slot4-20261109.json'

def validate_and_consume_recovery(ace, owned, path=RECOVERY_PROOF):
    import json, os, stat
    st=os.lstat(path)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode)!=0o600 or st.st_size>8192:
        raise ParkUnavailable('恢复凭据权限或格式无效')
    with open(path,encoding='utf8') as f: proof=json.load(f)
    expected={'reason':'seek_budget_end','ace_uid':list(PROFILE_UID),'ace':0,'slot':3,'head':3,
              'seek_command_mm':450,'decoder':431,'steps':5552,'length':449,
              'stop_ack_ids':[971,972], 'three_stable':True,'heater_target':0,
              'serial_path':'/dev/serial/by-path/platform-fed00000.usb-usb-0:1.3:1.0'}
    if not isinstance(proof,dict) or any(proof.get(k)!=v for k,v in expected.items()):
        raise ParkUnavailable('恢复凭据不匹配本次故障')
    if type(proof.get('old_pid')) is not int or proof['old_pid']==os.getpid():
        raise ParkUnavailable('恢复必须使用新进程会话')
    try:
        with open('/proc/sys/kernel/random/boot_id',encoding='ascii') as f: boot=f.read().strip()
    except OSError as exc:
        raise ParkUnavailable('无法验证设备启动会话') from exc
    if proof.get('boot_id') != boot:
        raise ParkUnavailable('设备已重启，位置账失效')
    if len(ace._ace_devices) < 1 or str(ace._ace_devices[0]) != proof['serial_path']:
        raise ParkUnavailable('设备端口改变')
    owned._check(0,PROFILE_SLOT,owned.transport)
    owned.validate_feed_states()
    monitor=lambda:owned._check(0,PROFILE_SLOT,owned.transport)
    samples=[]
    for _ in range(3):
        samples.append(ace._slot_actions._sample(0,PROFILE_SLOT,allow_initial_zero=True,monitor=monitor))
        ace.reactor.pause(ace.reactor.monotonic()+.25)
        monitor()
    if len(set(samples))!=1 or samples[0] != (True,(True,431),(5552,449)):
        raise ParkUnavailable('恢复前静止编码器或入口与故障证据不符')
    monitor()
    consumed=path+'.consumed-'+str(proof['old_pid'])
    if os.path.lexists(consumed): raise ParkUnavailable('恢复凭据已消费')
    # Same directory and same owner; destination is unique to this episode.
    os.rename(path,consumed)
    return consumed
