"""Single-use, writer-bound recovery of the physically repaired slot-4 path."""
import json, os, stat, time, logging, hashlib, re, copy
from .ace_hot_park import OwnedActions, PROFILE_UID, PROFILE_ANCHOR, PROFILE_ADJUST, ParkUnavailable, Route
from .ace_location_hold import PATH, PORT, LocationHold
DIAGNOSTIC_PATH='/home/lava/printer_data/controlled-exploration/y-recovery-current.json'

class RecoveryActions(OwnedActions):
    def __init__(self, owner, base):
        super().__init__(base.ace, base.actions, base.route, base.transport)
        object.__setattr__(self,'recovery',True)
        object.__setattr__(self,'owner',owner)
        object.__setattr__(self,'watch_timer',base.watch_timer)
        object.__setattr__(self,'watch_error',base.watch_error)
        object.__setattr__(self,'source_snapshot',copy.deepcopy(base.ace._head_source))
        object.__setattr__(self,'insert_epoch',getattr(base.ace,'_insert_epochs',{}).get((0,3)))
        object.__setattr__(self,'spool_uid',(getattr(base.ace,'_rc_last_uid',{}) or {}).get((0,3)))
    def check_route(self):
        if not self.recovery: return super().check_route()
        a=self.ace
        if (self.watch_error is not None or a._head_source != self.source_snapshot or
            getattr(a,'_insert_epochs',{}).get((0,3)) != self.insert_epoch or
            (getattr(a,'_rc_last_uid',{}) or {}).get((0,3)) != self.spool_uid or
            a._serials.get(0) is not self.transport or
            a._get_heads_for_ace_slot(0,3) != ([] if self.source_snapshot.get(3) is None else [3])):
            raise ParkUnavailable('恢复来源、插入身份或原连接改变')
    def _request(self, idx, method, params, command, monitor=None):
        if method not in ('feed_filament','unwind_filament','update_feeding_speed','stop_feed_filament'):
            return self.actions._request(idx,method,params,command,monitor=monitor)
        if self.ace._serials.get(idx) is not self.transport: raise ParkUnavailable('原连接已改变')
        request={'method':method,'params':dict(params)}
        stop=method=='stop_feed_filament'
        if stop: request['_insert_stop_transport']=self.transport
        else: request[getattr(self.owner,'token_key','_y_recovery_token')]=self.owner.issue(method,params)
        box=[None]
        def cb(self,response,target=box):target[0]=response if response is not None else {}
        request['_insert_reject']=cb
        if monitor is not None: monitor()
        self.ace.send_request_to(idx,request,cb)
        deadline=self.ace.reactor.monotonic()+3
        while box[0] is None and self.ace.reactor.monotonic()<deadline:
            if monitor is not None: monitor()
            elif self.ace._serials.get(idx) is not self.transport: raise ParkUnavailable('原连接已改变')
            self.ace.reactor.pause(min(deadline,self.ace.reactor.monotonic()+.05))
        if monitor is not None: monitor()
        r=box[0]
        if (not isinstance(r,dict) or r.get('_v2_frame_valid') is not True or
            r.get('_v2_payload_valid') is not True or r.get('_v2_command')!=command or
            type(r.get('code')) is not int or r['code']!=0 or str(r.get('msg','')).upper()=='FORBIDDEN' or not isinstance(r.get('result'),dict)):
            raise ParkUnavailable(method+' 响应未确认')
        return r['result']

class Recovery:
    def __init__(self,ace):
        self.ace=ace;self.transport=None;self.permits={};self.active=False
        self.feed_count=0;self.return_count=0;self.deadline=0;self.owner=None;self.feed_limit=None
        ace.printer.lookup_object('webhooks').register_endpoint('multiace/y_recovery_cancel',self.cancel)
        ace.printer.lookup_object('webhooks').register_endpoint('multiace/y_recovery_sensors',self.sensors)
    def sensors(self,request):
        raw=[]
        for h in range(4):
            obj=self.ace.printer.lookup_object('filament_motion_sensor e%d_filament'%h,None)
            value=getattr(obj,'runout_buttun_state',None) if obj is not None else None
            raw.append(value if type(value) is bool else None)
        request.send({'raw_head':raw,'hold_active':self.ace._location_hold.active})
    def cancel(self,request):
        running=self.active
        if running: self.ace._roundtrip_session.cancel=True
        request.send({'cancelled':running})
    def issue(self,method,params):
        if self.owner is None: raise ParkUnavailable('恢复上下文不可用')
        self.owner.check_route()
        if not self.active or self.ace._serials.get(0) is not self.transport or self.ace.reactor.monotonic()>self.deadline:
            raise ParkUnavailable('恢复会话已失效')
        if params.get('index') != 3: raise ParkUnavailable('恢复目标错误')
        if method=='feed_filament':
            if self.feed_count or params.get('length')!=self.feed_limit or params.get('speed')!=10: raise ParkUnavailable('送料预算无效')
            self.feed_count+=1
        elif method=='unwind_filament':
            if self.return_count or self.feed_count!=1 or type(params.get('length')) is not int or not 1393<=params['length']<=2435 or params.get('speed')!=5: raise ParkUnavailable('回程预算无效')
            self.return_count+=1
        elif method=='update_feeding_speed':
            if self.return_count!=1 or params.get('speed') not in (10,60): raise ParkUnavailable('恢复速度无效')
        else: raise ParkUnavailable('恢复命令无效')
        token=object();self.permits[token]=(method,dict(params));return token
    def allows(self,idx,request):
        try:
            token=request.get('_y_recovery_token');entry=self.permits.get(token)
            if self.owner is None: return False
            self.owner.check_route()
            if not self.ace._connected_per_ace.get(0) or self.ace._serial_failed_per_ace.get(0): return False
            ps=self.ace.printer.lookup_object('print_stats',None)
            if ps is None or getattr(ps,'state','').lower() not in ('standby','complete','cancelled'): return False
            if self.ace._stop_guard.message(): return False
            if self.ace._roundtrip_session.cancel or self.ace._slot_actions.cancel: return False
            for head in range(4):
                sensor=self.ace.printer.lookup_object('filament_motion_sensor e%d_filament'%head,None)
                pin=getattr(sensor,'runout_buttun_state',None) if sensor is not None else None
                if type(pin) is not bool or (head != 3 and pin): return False
                if head == 3 and request.get('method')=='feed_filament' and pin:return False
            return bool(self.active and idx==0 and self.ace._serials.get(0) is self.transport and
                self.ace.reactor.monotonic()<=self.deadline and entry==(request.get('method'),request.get('params')))
        except Exception:return False
    def consumed(self,request):
        self.permits.pop(request.get('_y_recovery_token'),None)
    def run(self,gcmd):
        try:
            return self._run(gcmd)
        except Exception as exc:
            logging.exception('[multiACE] controlled Y recovery failed')
            raise gcmd.error('Y恢复未完成：%s' % exc)
    def _run(self,gcmd):
        a=self.ace
        if self.active or not a._location_hold.active or not a._location_hold.valid_marker: raise gcmd.error('位置待核标记不可用或恢复已进行')
        with open(PATH,'rb') as f: marker_hash=hashlib.sha256(f.read()).hexdigest()
        if len(marker_hash)!=64: raise ParkUnavailable('待核 marker 不可确认')
        if len(a._ace_devices)<1 or str(a._ace_devices[0])!=PORT: raise gcmd.error('设备端口不匹配')
        for h in range(4):
            sensor=a.printer.lookup_object('filament_motion_sensor e%d_filament'%h,None)
            if sensor is None or type(getattr(sensor,'runout_buttun_state',None)) is not bool: raise gcmd.error('原始头传感器未知')
            if sensor.runout_buttun_state: raise gcmd.error('喷头有料，禁止冷态恢复')
        original=a._serials.get(0)
        source=a._head_source.get(3)
        if source is not None and (not isinstance(source,dict) or source.get('ace_index') != 0 or source.get('slot') != 3):
            raise ParkUnavailable('恢复来源与配置不匹配')
        if original is None: raise ParkUnavailable('ACE未连接')
        base=OwnedActions(a,a._slot_actions,Route(PROFILE_UID,3,3,''),original)
        owned=RecoveryActions(self,base);self.owner=owned
        def discover_monitor():
            owned.check_route()
            a._require_no_stop_fault()
        discovered=a._slot_actions._request(0,'discover_device',{},0,monitor=discover_monitor)
        discover_monitor()
        if tuple(discovered.get('uid%d'%i) for i in (1,2,3)) != PROFILE_UID:
            raise ParkUnavailable('ACE硬件与停泊配置不匹配')
        owned.start_watch()
        try:
            owned._check(0,3,original);owned.validate_feed_states()
            def preflight_monitor():
                owned.check_route()
                if a._serials.get(0) is not original: raise ParkUnavailable('原连接已改变')
                a._require_no_stop_fault()
            owned._request(0,'stop_feed_filament',{'index':3},9,monitor=preflight_monitor)
            if any(v != 'ready' for v in owned._lane_states(0,monitor=preflight_monitor).values()):
                raise ParkUnavailable('槽位未静止')
            gate,_ = owned._stable(0,3,allow_initial_zero=True,monitor=preflight_monitor)
            if not gate: raise ParkUnavailable('入口无料')
            owned._check(0,3,original)
            proof_path=DIAGNOSTIC_PATH
            st=os.lstat(proof_path)
            if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or stat.S_IMODE(st.st_mode)!=0o600 or st.st_size>2048:
                raise ParkUnavailable('恢复凭据不可用')
            with open(proof_path,encoding='utf8') as f: diagnostic=json.load(f)
            nonce=diagnostic.get('nonce');limit=diagnostic.get('limit_mm');sample=diagnostic.get('sample')
            with open('/proc/sys/kernel/random/boot_id',encoding='ascii') as f: boot_id=f.read().strip()
            if (not isinstance(nonce,str) or re.fullmatch('[0-9a-f]{32}',nonce) is None or
                diagnostic.get('version')!=1 or diagnostic.get('marker_sha256')!=marker_hash or
                diagnostic.get('uid')!=list(PROFILE_UID) or diagnostic.get('port')!=PORT or
                diagnostic.get('slot')!=3 or diagnostic.get('head')!=3 or diagnostic.get('pid')!=os.getpid() or diagnostic.get('boot_id')!=boot_id or
                type(limit) is not int or not 1<=limit<=2100 or diagnostic.get('speed_mm_s')!=10 or
                not isinstance(sample,list) or len(sample)!=3 or sample[0] is not True or
                not isinstance(sample[1],list) or len(sample[1])!=2 or type(sample[1][0]) is not bool or type(sample[1][1]) is not int or
                not isinstance(sample[2],list) or len(sample[2])!=2 or any(type(x) is not int or x<0 for x in sample[2]) or
                type(diagnostic.get('expires_at')) not in (int,float) or not time.time()<=diagnostic['expires_at']<=time.time()+600):
                raise ParkUnavailable('恢复凭据或预算不匹配')
            samples=[]
            for _ in range(3):
                current=owned._sample(0,3,allow_initial_zero=True,monitor=preflight_monitor)
                samples.append([current[0],list(current[1]),list(current[2])])
                a.reactor.pause(a.reactor.monotonic()+.25)
            if any(current!=sample for current in samples):raise ParkUnavailable('恢复基线已改变')
            if time.time()>diagnostic['expires_at']:raise ParkUnavailable('恢复凭据已过期')
            used=proof_path+'.used-'+nonce
            if os.path.lexists(used):raise ParkUnavailable('恢复凭据已消费')
            os.rename(proof_path,used)
            directory=os.open(os.path.dirname(proof_path),os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)
            self.feed_limit=limit;self.feed_count=0;self.return_count=0
            self.transport=original;self.deadline=a.reactor.monotonic()+600;self.active=True
            result=a._roundtrip_session.run(anchor=PROFILE_ANCHOR,adjust=PROFILE_ADJUST,hot_context=owned,feed_budget=self.feed_limit)
            if (result.get('state')!='complete' or result.get('head_reached') is not True or
                result.get('head_clear') is not True or result.get('gate_present') is not True or
                result.get('encoder_target_reached') is not True or a._serials.get(0) is not original):
                raise ParkUnavailable('恢复未完成：%s'%result.get('error','状态不完整'))
            owned._check(0,3,original);owned.validate_feed_states()
            uid_result=owned._request(0,'discover_device',{},0,monitor=preflight_monitor)
            if tuple(uid_result.get('uid%d'%i) for i in (1,2,3)) != PROFILE_UID:
                raise ParkUnavailable('恢复后 ACE UID 不一致')
            gate,_=owned._stable(0,3,monitor=preflight_monitor)
            if not gate: raise ParkUnavailable('恢复后入口未确认有料')
            owned._check(0,3,original)
            proof={'reason':'position_unconfirmed_after_seek','state':'verified_recovered','ace_uid':list(PROFILE_UID),
                   'slot':3,'head':3,'port':PORT,'head_triggered':True,'head_clear':True,'gate_present':True,
                   'stopped':True,'encoder_target_reached':True,'target_mm':1393,'result':result,'time':time.time()}
            st=os.lstat(PATH)
            if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or stat.S_IMODE(st.st_mode)!=0o600: raise ParkUnavailable('marker 权限改变')
            with open(PATH,'rb') as f: old=f.read()
            if hashlib.sha256(old).hexdigest()!=marker_hash: raise ParkUnavailable('恢复期间 marker 已改变')
            archive=PATH+'.before-y-recovery-'+str(os.getpid())+'-'+nonce
            fd=os.open(archive,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as f:f.write(old);f.flush();os.fsync(f.fileno())
            temp=PATH+'.new-'+str(os.getpid())+'-'+nonce;fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(proof,f);f.flush();os.fsync(f.fileno())
            staged=LocationHold(temp)
            if not staged.valid_marker or staged.active: raise ParkUnavailable('待提交恢复证据无效')
            old_source=a._head_source.get(3)
            replaced=False
            try:
                a._head_source[3]=None
                a._save_head_source()
                os.replace(temp,PATH)
                replaced=True
                directory=os.open(os.path.dirname(PATH),os.O_RDONLY)
                try:os.fsync(directory)
                finally:os.close(directory)
                validated=LocationHold()
                if not validated.valid_marker or validated.active: raise ParkUnavailable('恢复证据复核失败')
                a._location_hold=validated
                a._normal_load_speed.record_park(original)
            except Exception:
                if replaced and os.path.exists(archive): os.replace(archive,PATH)
                a._head_source[3]=old_source
                a._save_head_source()
                raise
            logging.info('[multiACE] Y repair recovery verified %s',result)
            gcmd.respond_info('槽4位置已重新定位并返回已验证停泊位')
        finally:
            self.active=False;self.permits.clear();owned.close_watch()
            if getattr(a,'_owned_hot_park',None) is self.owner:a._owned_hot_park=None
