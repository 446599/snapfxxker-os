"""One-episode durable STOP-fault preservation and guarded recovery.

Recovery sends one independent STOP, then reads a fixed reverse-trial state.
A protobuf default zero is not evidence of movement or physical standstill.
"""
import json, os, stat, hashlib
from .ace_exploration import ExistingMachineIO, ExplorationPlan
from .ace_rc522 import AceTagReader

PATH='/home/lava/printer_data/controlled-exploration/fault-checkpoint4.json'
LEGACY_PATH='/home/lava/printer_data/controlled-exploration/fault-checkpoint3.json'
FAULT={'ace_idx':0,'slot':3,'reason':'post-STOP telemetry invalid: encoder absent'}

def load_checkpoint():
    st=os.lstat(PATH)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode)!=0o600 or st.st_size>8192:
        raise ValueError('checkpoint file permissions or size invalid')
    with open(PATH,encoding='utf8') as f: d=json.load(f)
    if (not isinstance(d,dict) or d.get('fault') != FAULT
        or d.get('stop_acknowledged') is not True
        or type(d.get('commanded_mm')) is not int or d['commanded_mm'] != 20
        or d.get('direction') != 'rollback'
        or type(d.get('decoder')) is not int or d['decoder'] != 0
        or d.get('decoder_field_absent') is not True
        or type(d.get('length')) is not int or d['length'] != 1
        or type(d.get('steps')) is not int or d['steps'] != 14
        or type(d.get('raw_buffer')) is not int or d['raw_buffer'] != 21845
        or d.get('episode') != 'slot4-20261107-0423'):
        raise ValueError('checkpoint does not identify the verified reverse trial')
    return d

def release_token():
    # Bind the release receipt to the exact recovered checkpoint.
    st=os.lstat(PATH+'.released')
    if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or stat.S_IMODE(st.st_mode)!=0o600 or st.st_size>512:
        raise ValueError('invalid release receipt permissions')
    with open(PATH,'rb') as f: digest=hashlib.sha256(f.read()).hexdigest()
    with open(PATH+'.released',encoding='ascii') as f: receipt=f.read().strip()
    if receipt!=digest:raise ValueError('release receipt does not match checkpoint')
    return True

def restore_checkpoint(guard):
    # Current unresolved markers survive even when the checkpoint is missing.
    # Preserve any existing fault and generation; never erase newer evidence.
    reason=None
    if os.path.lexists(PATH+'.additional-fault'):
        reason='invalid recovery checkpoint: additional STOP fault requires review'
    elif os.path.lexists(PATH+'.release-pending'):
        reason='invalid recovery checkpoint: incomplete durable release requires review'
    if reason is not None:
        with guard._lock:
            if guard._fault is None:
                guard._fault={'ace_idx':0,'slot':3,'reason':reason}
                guard._generation+=1
        return
    # Invalid or missing new checkpoint with an unresolved prior additional
    # fault must fail closed; never silently ignore the earlier durable mark.
    if not os.path.lexists(PATH) and os.path.lexists(LEGACY_PATH+'.additional-fault'):
        with guard._lock:
            if guard._fault is None:
                guard._fault={'ace_idx':0,'slot':3,'reason':'new checkpoint missing while prior additional fault exists'}
                guard._generation+=1
        return
    if os.path.lexists(PATH):
        try:
            if os.path.lexists(PATH+'.additional-fault'):raise ValueError('additional STOP fault requires review')
            if os.path.lexists(PATH+'.release-pending'):raise ValueError('incomplete durable release requires review')
            d=load_checkpoint()
            if os.path.lexists(PATH+'.released'):
                release_token()
                return
            f=d['fault']
        except Exception as e: f={'ace_idx':0,'slot':3,'reason':'invalid recovery checkpoint: '+str(e)}
        with guard._lock:
            if guard._fault is None:
                guard._fault=dict(f)
                guard._generation+=1
            else:
                raise ValueError('cannot overwrite existing STOP fault on restore')

def target_lane(rows):
    if (not isinstance(rows,list) or len(rows)!=4 or any(not isinstance(x,dict) for x in rows)
        or any(type(x.get('index')) is not int for x in rows)
        or sorted(x['index'] for x in rows)!=[0,1,2,3]):
        raise ValueError('four unique indexed lane records required')
    return next(x for x in rows if x['index']==3)

class Recovery:
    def __init__(self,ace):
        self.ace=ace;self.guard=ace._stop_guard;self.reactor=ace.reactor
        self.io=ExistingMachineIO(AceTagReader(ace),self)
        self.plan=ExplorationPlan('recovery',slot=3,head=3)
        self.started=self.reactor.monotonic();self.pending=None;self.serial=0
        self.index=0;self.data={};self.samples=[];self.state='checking';self.reason='';self.need_stop=True
        self.checkpoint=load_checkpoint()
        if self.guard.snapshot()!=FAULT or os.path.lexists(PATH+'.additional-fault'): raise ValueError('fault does not match checkpoint')
        with self.guard._lock:self.generation=self.guard._generation
        self.transport=self.ace._serials.get(0)
    def start(self):
        self.reactor.register_timer(self.tick,self.started+.05)
    def check_environment(self,now):
        if self.guard.snapshot()!=FAULT or os.path.lexists(PATH+'.additional-fault'): raise ValueError('fault changed')
        with self.guard._lock:
            if self.guard._generation!=self.generation:raise ValueError('new STOP fault during recovery')
        if getattr(self.ace,'_ace_mode',None)!='multi' or self.ace._connected_per_ace.get(0) is not True: raise ValueError('ACE disconnected')
        s=self.ace._serials.get(0)
        if s is None or getattr(s,'is_open',False) is not True: raise ValueError('serial unavailable')
        if s is not self.transport:raise ValueError('ACE connection changed during recovery')
        # A triggered/unknown sensor must never suppress the protective STOP.
        # Idle, head and telemetry checks gate only later fault release.
        if self.need_stop:
            return
        if getattr(self.ace,'_tag_read_busy',False) or getattr(self.ace,'_insert_read_running',None) or getattr(self.ace,'_insert_read_queue',None): raise ValueError('another operation active')
        p=self.ace.printer; ps=p.lookup_object('print_stats',None); web=p.lookup_object('webhooks',None)
        if ps is None or ps.state not in ('standby','complete','cancelled') or web is None or web.get_status(now).get('state')!='ready': raise ValueError('printer not idle')
        verdict,_=self.io.head(self.plan,now)
        if verdict!='clear':raise ValueError('head sensor not clear or healthy')
    def send(self,method,now):
        self.serial+=1; token=self.serial; self.pending=(token,method,now+1.5)
        def cb(*args,**kwargs):
            response=kwargs.get('response',args[-1] if args else None)
            self.reactor.register_async_callback(lambda when:self.reply(token,method,response,when))
        self.ace.send_request_to(0,{'method':method,'params':({'index':3} if method=='stop_feed_filament' else {})},cb)
    def reply(self,token,method,response,now):
        if self.state!='checking' or self.pending is None or self.pending[0]!=token:return
        if now>self.pending[2]:self.fail('late '+method);return
        self.pending=None
        try:
            if (not isinstance(response,dict) or type(response.get('code')) is not int or response['code']!=0
                or response.get('_v2_payload_valid') is not True
                or str(response.get('msg','')).strip().upper()=='FORBIDDEN'):
                raise ValueError('bad '+method+' reply')
            if method=='stop_feed_filament':
                if str(response.get('msg','')).strip().upper()=='FORBIDDEN':raise ValueError('independent STOP forbidden')
                self.need_stop=False
                return
            data=response.get('result')
            if not isinstance(data,dict):raise ValueError('missing '+method+' result')
            if response.get('_v2_frame_valid') is not True:raise ValueError('complete CRC-checked telemetry frame required')
            if method=='get_status':
                if data.get('_status_wire_valid') is not True:raise ValueError('invalid status')
                lane=target_lane(data.get('slots'))
                if lane.get('slot_status')!='ready':raise ValueError('lane not ready')
            elif method=='get_feed_info':
                if data.get('_feed_wire_valid') is not True:raise ValueError('invalid feed telemetry')
                lane=target_lane(data.get('feed_info'))
                if (lane.get('_wire_valid') is not True or lane.get('_decoder_present') is not False
                    or any(type(lane.get(k)) is not int or lane[k]!=v
                           for k,v in (('decoder',0),('length',1),('steps',14)))):
                    raise ValueError('fixed steps/length or explicit protobuf default-zero evidence changed')
            elif method=='get_key_state':
                word=data.get('fields',{}).get('1')
                if type(word) is not int or word!=self.checkpoint['raw_buffer']:raise ValueError('buffer raw value changed')
            self.data[method]=now
            self.index+=1
        except Exception as exc:self.fail(str(exc))
    def fail(self,reason):self.state='blocked';self.reason=reason
    def tick(self,now):
        if self.state!='checking':return self.reactor.NEVER
        try:
            self.check_environment(now)
            if now-self.started>20:raise ValueError('recovery timed out')
            if self.pending:
                if now>self.pending[2]:raise ValueError('read timed out: '+self.pending[1])
            elif self.need_stop:self.send('stop_feed_filament',now)
            elif self.index < 3:self.send(('get_status','get_feed_info','get_key_state')[self.index],now)
            else:
                if max(self.data.values())-min(self.data.values())>3:raise ValueError('read set stale')
                self.samples.append(now);self.data={};self.index=0
                if len(self.samples)>=3 and self.samples[-1]-self.samples[0]>=3:
                    self.release();return self.reactor.NEVER
        except Exception as exc:self.fail(str(exc));return self.reactor.NEVER
        return now+.15
    def release(self):
        # Persist an in-progress marker BEFORE any receipt. A failed attempt
        # cannot leave a receipt that boot mistakes for committed recovery.
        if load_checkpoint()!=self.checkpoint:raise ValueError('checkpoint changed during recovery')
        pending=PATH+'.release-pending';done=PATH+'.released'
        with self.guard._lock:
            if self.guard._fault!=FAULT or self.guard._generation!=self.generation or os.path.lexists(PATH+'.additional-fault'):raise ValueError('fault changed before release')
            if self.ace._serials.get(0) is not self.transport or getattr(self.transport,'is_open',False) is not True:
                raise ValueError('ACE connection changed before release')
            try:
                fd=os.open(pending,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                try:
                    marker=b'release not committed\n'
                    if os.write(fd,marker)!=len(marker):raise IOError('short pending-marker write')
                    os.fsync(fd)
                finally:os.close(fd)
                fsync_directory()
                with open(PATH,'rb') as f:digest=hashlib.sha256(f.read()).hexdigest()
                fd=os.open(done,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                try:
                    receipt=(digest+'\n').encode('ascii')
                    if os.write(fd,receipt)!=len(receipt):raise IOError('short release-receipt write')
                    os.fsync(fd)
                finally:os.close(fd)
                fsync_directory()
                os.unlink(pending)
                fsync_directory()
                if self.ace._serials.get(0) is not self.transport or getattr(self.transport,'is_open',False) is not True:
                    raise ValueError('ACE connection changed while persisting release')
            except Exception:
                # Best-effort persistent fail-closed rollback. Persistent disk
                # or unlink failure cannot provide a cross-restart guarantee.
                try:
                    fd=os.open(pending,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
                    try:
                        os.write(fd,b'failed release requires review\n')
                        os.fsync(fd)
                    finally:os.close(fd)
                except Exception:pass
                try:
                    if os.path.lexists(done):os.unlink(done)
                except Exception:pass
                try:fsync_directory()
                except Exception:pass
                raise
            self.guard._fault=None
        self.state='recovered';self.reason='fresh STOP acknowledged and repeated read-only telemetry stable; decoder is absent protobuf default zero, not movement or physical standstill proof'

def fsync_directory():
    fd=os.open(os.path.dirname(PATH),os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)

def command(ace,gcmd):
    old=getattr(ace,'_fault_recovery',None)
    if gcmd.get_int('RECOVERY_STATUS',0,minval=0,maxval=1):
        result={'state':getattr(old,'state','not_started'),'reason':getattr(old,'reason',''),'fault':ace._stop_guard.snapshot()}
        gcmd.respond_info(json.dumps(result));return
    if old is not None and old.state=='checking':raise gcmd.error('recovery already running')
    try:
        ace._fault_recovery=Recovery(ace);ace._fault_recovery.start()
        gcmd.respond_info('Recovery starts with one STOP then read-only checks; fault remains locked until they pass')
    except Exception as e:raise gcmd.error('Recovery refused: '+str(e))
