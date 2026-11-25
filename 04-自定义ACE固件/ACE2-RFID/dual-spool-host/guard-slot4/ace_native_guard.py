"""Temporary local sensor stop guard for one native preload experiment.

No FEED, heating, RF or configuration write. ACE0 internal slot2 or slot3; explicit TARGET required.
Install/restart and arm only after review; inspect status before CLEAR.
"""
import logging
import importlib

class NativeGuard:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command('ACE_NATIVE_GUARD_ARM', self.cmd_arm)
        self.gcode.register_command('ACE_NATIVE_GUARD_CLEAR', self.cmd_clear)
        self.gcode.register_command('ACE_NATIVE_GUARD_SELFTEST', self.cmd_selftest)
        self.gcode.register_command('ACE_NATIVE_GUARD_TELEMETRY_TEST', self.cmd_telemetry_test)
        self.printer.register_event_handler('klippy:ready', self.ready)
        self.state = 'idle'; self.reason = None; self.timer = None
        self.event_seq = 0; self.stop_count = 0; self.triggered = []
        self.key = None; self.status = None; self.rfid_flags = None; self.slot = None

    def ready(self):
        self.ace = self.printer.lookup_object('ace')
        module=importlib.import_module(self.ace.__class__.__module__.rsplit('.',1)[0]+'.ace_protocol_v2')
        cls=module.AceProtocolV2
        original_decode=cls._v2_response_to_v1
        def decode(protocol,cmd,seq,payload):
            reply=original_decode(protocol,cmd,seq,payload)
            if cmd==6 and reply.get('_v2_payload_valid') is True:
                fields=module.pb_decode(payload)
                result=reply.get('result',{})
                if result.get('_status_wire_valid') is True:
                    result['rfid1_enable_raw']=module._fval(fields,5,0)
                    result['rfid2_enable_raw']=module._fval(fields,6,0)
            return reply
        cls._v2_response_to_v1=decode
        self.sensors=[self.printer.lookup_object('filament_motion_sensor e%d_filament'%i,None) for i in range(4)]
        for name in ('_insert_read_then_preload', '_insert_tag_read_safe'):
            original = getattr(self.ace, name)
            def wrapper(idx, slot, *args, _orig=original, **kwargs):
                if self.state == 'idle' or idx != 0:
                    return _orig(idx, slot, *args, **kwargs)
                if slot != self.slot:
                    self.stop(slot, 'other slot inserted')
                    self.stop(self.slot, 'other slot inserted',force=True)
                # Do not allow an already-queued callback to run after CLEAR.
                return None
            setattr(self.ace, name, wrapper)

    def heads(self, now):
        values=[]
        for i in range(4):
            sensor=self.sensors[i]
            if sensor is None: raise ValueError('head sensor missing')
            v=sensor.get_status(now).get('filament_detected')
            if type(v) is not bool: raise ValueError('head sensor invalid')
            values.append(v)
        return values

    def invalidate_pending(self):
        self.poll_token+=1;self.pending=None;self.poll_failures=0;self.retry_method=None

    def stop(self, slot, reason, force=False):
        if self.state=='telemetry_test':
            self.reason=reason;self.telemetry_result='failed';self.event_seq+=1;self.invalidate_pending();self.state='idle';return False
        if getattr(self,'stop_inflight',False):return False
        if self.state=='stopping' and not force:return self.stop_ack
        if self.state=='fault':
            # Still attempt the other lane's STOP; never clear a prior fault.
            self.stop_inflight=True
            try:return bool(self.ace._stop_feeding(slot,idx=0))
            except Exception as exc:
                self.stop_error={'type':type(exc).__name__,'message':str(exc)}
                logging.exception('[native-guard] additional STOP failed');return False
            finally:self.stop_inflight=False
        self.reason=reason; self.state='stopping'; self.event_seq+=1
        self.invalidate_pending()
        self.stop_count+=1;self.stop_inflight=True;self.stop_ack=False
        self.last_stop=self.reactor.monotonic();self.stop_error=None
        try: ok=self.ace._stop_feeding(slot,idx=0)
        except Exception as exc:
            self.stop_error={'type':type(exc).__name__,'message':str(exc)}
            logging.exception('[native-guard] STOP failed');ok=False
        finally:self.stop_inflight=False
        self.stop_ack=ok
        self.last_decoder=None;self.stable_since=None;self.stable_count=0
        if not ok:
            self.ace._latch_stop_fault(0,slot,'native guard STOP unconfirmed')
            self.state='fault'
        self.last_stop=self.reactor.monotonic()
        return ok

    def cmd_arm(self, gcmd):
        if self.state!='idle': raise gcmd.error('native guard already armed')
        target=gcmd.get_int('TARGET', minval=2, maxval=3)
        self.ace._require_no_stop_fault()
        if (self.ace._active_device_index!=0 or getattr(self.ace,'_ace_mode',None)!='multi'
            or not self.ace._connected_per_ace.get(0,False)):
            raise gcmd.error('ACE0 multi route not ready')
        if (getattr(self.ace,'_tag_read_busy',False) or getattr(self.ace,'_gen1_manual_busy',False)):
            raise gcmd.error('tag operation busy')
        if (getattr(self.ace,'_insert_read_running',None) or getattr(self.ace,'_insert_abort_running',None)
            or getattr(self.ace,'_insert_read_queue',None)):
            raise gcmd.error('existing insertion work pending')
        self.heads(self.reactor.monotonic())
        self.transport=self.ace._serials.get(0)
        if self.transport is None or not getattr(self.transport,'is_open',False):
            raise gcmd.error('serial unavailable')
        self.slot=target
        self.state='waiting_empty';self.reason=None;self.triggered=[]
        self.started=self.reactor.monotonic();self.inserted=None;self.last_stop=0
        self.key=None;self.key_time=0;self.status=None;self.status_time=0;self.rfid_flags=None
        self.pending=None;self.poll_token=0;self.poll_failures=0;self.poll_index=0;self.next_poll=0;self.event_seq+=1
        self.poll_timeouts=0;self.poll_requests=0;self.poll_replies=0;self.last_request_id=None;self.last_reply_id=None
        self.fail_counts={};self.retry_method=None;self.poll_events=[]
        self.last_decoder=None;self.stable_since=None;self.stable_count=0;self.stop_ack=False;self.saw_busy=False
        self.natural_decoder=None;self.natural_since=None;self.natural_count=0
        self.timer=self.reactor.register_timer(self.tick,self.reactor.NOW)
        gcmd.respond_info('Native guard armed; wait for ready_for_insert')

    def cmd_telemetry_test(self,gcmd):
        if self.state!='idle':raise gcmd.error('guard busy')
        target=gcmd.get_int('TARGET', minval=2, maxval=3)
        self.ace._require_no_stop_fault()
        if (self.ace._active_device_index!=0 or getattr(self.ace,'_ace_mode',None)!='multi'
            or not self.ace._connected_per_ace.get(0,False)):raise gcmd.error('ACE0 not ready')
        self.heads(self.reactor.monotonic());self.transport=self.ace._serials.get(0)
        if self.transport is None or not getattr(self.transport,'is_open',False):raise gcmd.error('serial unavailable')
        self.slot=target
        self.state='telemetry_test';self.telemetry_result='running';self.reason=None
        self.started=self.reactor.monotonic();self.last_tick=None;self.timer_max_gap=0.0
        self.key=None;self.key_time=0;self.status=None;self.status_time=0;self.rfid_flags=None
        self.pending=None;self.poll_token=0;self.poll_failures=0;self.poll_index=0;self.next_poll=0
        self.poll_timeouts=0;self.poll_requests=0;self.poll_replies=0;self.last_request_id=None;self.last_reply_id=None
        self.fail_counts={};self.retry_method=None;self.poll_events=[];self.event_seq+=1
        self.timer=self.reactor.register_timer(self.tick,self.reactor.NOW)
        gcmd.respond_info('Native guard telemetry self-test started; no motor commands')

    def cmd_selftest(self,gcmd):
        if self.state!='idle' or getattr(self,'selftest_active',False):raise gcmd.error('guard busy')
        self.selftest_active=True;self.selftest_start=self.reactor.monotonic()
        self.selftest_last=None;self.selftest_max_gap=0.0;self.selftest_count=0
        self.reactor.register_timer(self.selftest_tick,self.reactor.NOW)
        gcmd.respond_info('Native guard timer self-test started; no motor commands')

    def selftest_tick(self,now):
        if self.selftest_last is not None:self.selftest_max_gap=max(self.selftest_max_gap,now-self.selftest_last)
        self.selftest_last=now;self.selftest_count+=1
        if now-self.selftest_start>=60:
            self.selftest_active=False;return self.reactor.NEVER
        return now+.05

    def cmd_clear(self,gcmd):
        if self.state != 'idle':
            raise gcmd.error('STOP first and verify stable before CLEAR')
        if self.state=='fault':raise gcmd.error('STOP fault must be resolved first')
        if self.timer is not None:self.reactor.unregister_timer(self.timer);self.timer=None
        self.state='idle';self.event_seq+=1
        gcmd.respond_info('Native guard cleared')

    def get_status(self,eventtime):
        return {'state':self.state,'reason':self.reason,'triggered':list(self.triggered),
                'event_seq':self.event_seq,'stop_count':self.stop_count,'target':self.slot,
                'stop_inflight':getattr(self,'stop_inflight',False),'stop_error':getattr(self,'stop_error',None),
                'gate':self.key,'lane_states':self.status,'rfid_flags':getattr(self,'rfid_flags',None),
                'timer_max_gap':getattr(self,'timer_max_gap',0.0),
                'selftest_active':getattr(self,'selftest_active',False),
                'selftest_max_gap':getattr(self,'selftest_max_gap',None),
                'selftest_count':getattr(self,'selftest_count',0),
                'poll_timeouts':getattr(self,'poll_timeouts',0),'poll_requests':getattr(self,'poll_requests',0),
                'poll_replies':getattr(self,'poll_replies',0),'last_request_id':getattr(self,'last_request_id',None),
                'last_reply_id':getattr(self,'last_reply_id',None),
                'key_age':None if not getattr(self,'key_time',0) else max(0,eventtime-self.key_time),
                'status_age':None if not getattr(self,'status_time',0) else max(0,eventtime-self.status_time),
                'writer_queue':getattr(self.ace,'_writer_queues',{})[0].qsize() if hasattr(self,'ace') and 0 in getattr(self.ace,'_writer_queues',{}) else None,
                'callback_count':len(getattr(self.ace,'_callback_maps',{}).get(0,{})) if hasattr(self,'ace') else None,
                'poll_events':list(getattr(self,'poll_events',[])),
                'telemetry_result':getattr(self,'telemetry_result',None)}

    def record_poll(self,kind,method,token,request_id):
        row={'kind':kind,'method':method,'token':token,'id':request_id,'time':self.reactor.monotonic()}
        self.poll_events=(getattr(self,'poll_events',[])+[row])[-40:]

    def request(self,method,now):
        if self.pending is not None:return
        self.poll_token+=1;token=self.poll_token;self.pending=(method,token,now);generation=self.event_seq
        def callback(self,response):
            if guard.event_seq!=generation or guard.pending is None or guard.pending[1]!=token:
                guard.record_poll('late_reply',method,token,response.get('id') if isinstance(response,dict) else None);return
            guard.pending=None;guard.poll_failures=0;guard.poll_replies+=1
            guard.fail_counts[method]=0
            if guard.retry_method==method:guard.retry_method=None
            guard.record_poll('reply',method,token,response.get('id') if isinstance(response,dict) else None)
            guard.last_reply_id=response.get('id') if isinstance(response,dict) else None
            try:
                if (not isinstance(response,dict) or response.get('_v2_frame_valid') is not True
                    or response.get('_v2_payload_valid') is not True or response.get('code')!=0):raise ValueError('bad reply')
                result=response['result']
                if method=='get_key_state':
                    value=result['fields']['1']
                    if type(value) is not int:raise ValueError('gate missing')
                    guard.key=bool(value & (1<<(4*guard.slot)));guard.key_time=guard.reactor.monotonic()
                elif method=='get_feed_info':
                    lanes=result['feed_info']
                    if result.get('_feed_wire_valid') is not True or len(lanes)!=4:raise ValueError('decoder invalid')
                    row=lanes[guard.slot];value=row.get('decoder')
                    if (row.get('_wire_valid') is not True or type(value) is not int
                        or type(row.get('_decoder_present')) is not bool
                        or (row['_decoder_present'] is False and (value!=0 or (guard.saw_busy and guard.state=='stopping')))):
                        raise ValueError('decoder invalid')
                    if guard.state=='active' and guard.saw_busy:
                        t=guard.reactor.monotonic()
                        if (guard.status==['ready']*4 and t-guard.status_time<=.5
                            and row['_decoder_present'] is True):
                            if value==guard.natural_decoder:
                                guard.natural_count+=1
                                if guard.natural_since is None:guard.natural_since=t
                            else:
                                guard.natural_decoder=value;guard.natural_since=None;guard.natural_count=0
                            if guard.natural_count>=3 and guard.natural_since is not None and t-guard.natural_since>=2:
                                guard.stop(guard.slot,'native completed')
                        else:
                            guard.natural_decoder=None;guard.natural_since=None;guard.natural_count=0
                    if (guard.state=='stopping' and guard.stop_ack and guard.status==['ready']*4
                        and guard.reactor.monotonic()-guard.status_time<=.5):
                        t=guard.reactor.monotonic()
                        if value==guard.last_decoder:
                            guard.stable_count+=1
                            if guard.stable_since is None:guard.stable_since=t
                        else:guard.stable_count=0;guard.stable_since=None
                        guard.last_decoder=value
                        if guard.stable_count>=3 and guard.stable_since is not None and t-guard.stable_since>=1.5:
                            guard.state='confirmed'
                else:
                    lanes=result['slots']
                    if result.get('_status_wire_valid') is not True or len(lanes)!=4:raise ValueError('status invalid')
                    guard.status=[x.get('slot_status') for x in lanes];guard.status_time=guard.reactor.monotonic()
                    if guard.state=='active':
                        if guard.status[guard.slot]!='ready':
                            guard.saw_busy=True;guard.natural_decoder=None;guard.natural_since=None;guard.natural_count=0
                        elif guard.status!=['ready']*4:
                            guard.natural_decoder=None;guard.natural_since=None;guard.natural_count=0
                    guard.rfid_flags=[result.get('rfid1_enable_raw'),result.get('rfid2_enable_raw')]
            except Exception:
                guard.stop(guard.slot,'telemetry invalid')
        guard=self
        try:
            request={'method':method};self.poll_requests+=1
            self.ace.send_request_to(0,request,callback);self.last_request_id=request.get('id')
            self.record_poll('request',method,token,self.last_request_id)
        except Exception:self.pending=None;self.stop(self.slot,'telemetry dispatch failed')

    def expire_pending(self,now):
        if self.pending is not None and now-self.pending[2]>.8:
            method,token,_=self.pending;self.pending=None;self.poll_timeouts+=1
            self.fail_counts[method]=self.fail_counts.get(method,0)+1;self.poll_failures=self.fail_counts[method]
            self.retry_method=method;self.record_poll('timeout',method,token,self.last_request_id)
            if self.poll_failures>=3:self.stop(self.slot,'telemetry timeout')

    def tick(self,now):
        previous=getattr(self,'last_tick',None)
        if previous is not None:self.timer_max_gap=max(getattr(self,'timer_max_gap',0.0),now-previous)
        self.last_tick=now
        if self.state=='idle':return self.reactor.NEVER
        if self.state=='fault':return now+.05
        try:
            values=self.heads(now)
            ps=self.printer.lookup_object('print_stats',None)
            if (getattr(ps,'state',None)!='standby' or self.ace._active_device_index!=0
                or getattr(self.ace,'_ace_mode',None)!='multi'
                or getattr(self.ace,'_tag_read_busy',False) or getattr(self.ace,'_gen1_manual_busy',False)
                or self.ace._serials.get(0) is not self.transport):raise ValueError('session changed')
            if self.state=='stopping' and getattr(self,'stop_inflight',False):return now+.05
            if self.state=='telemetry_test' and now-self.started>=120:
                self.telemetry_result='complete';self.event_seq+=1;self.invalidate_pending();self.state='idle';return self.reactor.NEVER
            if self.state=='active' and any(values):
                self.triggered=[i for i,x in enumerate(values) if x];self.stop(self.slot,'head sensor')
            if self.state=='active' and now-self.inserted>=90:self.stop(self.slot,'deadline')
            if self.state in ('waiting_empty','waiting_insert') and now-self.started>=300:self.stop(self.slot,'waiting timeout')
            if self.state in ('stopping','confirmed'):
                if self.state=='stopping' and now-self.last_stop>=6:
                    self.ace._latch_stop_fault(0,self.slot,'native guard standstill unconfirmed');self.state='fault'
                if self.status and any(x!='ready' for x in self.status) and now-self.last_stop>=.5:
                    for slot,value in enumerate(self.status):
                        if value!='ready':self.stop(slot,'motion after STOP',force=True)
            self.expire_pending(now)
            if now>=self.next_poll and self.pending is None:
                self.next_poll=now+.1
                cycle=(('get_status','get_feed_info') if self.state=='stopping' else
                       ('get_key_state','get_status','get_feed_info') if self.state=='active' else
                       ('get_key_state','get_status'))
                method=self.retry_method or cycle[self.poll_index % len(cycle)]
                if self.retry_method is None:self.poll_index+=1
                self.request(method,now)
            if self.state in ('waiting_empty','waiting_insert','active','telemetry_test'):
                # Three .8s retry windows plus one scheduling interval; sensor checks remain every 50ms.
                if now-self.started>3.2 and (now-self.key_time>3.2 or now-self.status_time>3.2):raise ValueError('telemetry stale')
                if self.status:
                    for slot in (i for i in range(4) if i != self.slot):
                        if self.status[slot]!='ready':self.stop(slot,'other slot motion');self.stop(self.slot,'other slot motion',force=True);break
                if (self.state=='waiting_empty' and self.key is False and not any(values)
                    and self.status==['ready']*4 and now-self.status_time<=.5 and now-self.key_time<=.5):
                    self.state='waiting_insert';self.event_seq+=1;self.invalidate_pending();self.gcode.respond_info('Native guard ready_for_insert')
                elif self.state=='waiting_insert' and self.key is True:
                    self.state='active';self.inserted=now;self.event_seq+=1;self.invalidate_pending()
                elif self.state=='waiting_insert' and any(values):self.stop(self.slot,'head before insert')
        except Exception as e:self.stop(self.slot,'guard error: '+str(e))
        return now+.05

def load_config(config):return NativeGuard(config)
