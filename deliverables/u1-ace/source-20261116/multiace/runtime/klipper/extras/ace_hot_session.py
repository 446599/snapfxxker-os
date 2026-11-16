"""Persistent fail-closed ownership of one calibrated normal hot unload."""
import json,os,stat,time,logging
from .ace_y_recovery import RecoveryActions
from .ace_hot_park import check_profile, ParkUnavailable, PROFILE_UID
from .ace_location_hold import PATH, PORT, LocationHold

class HotSession:
 token_key = '_hot_token'
 def __init__(self,ace):
  self.ace=ace;self.active=False;self.owner=None;self.transport=None;self.permits={};self.phase=None;self.deadline=0;self.observation_timer=None;self.observation_pending=False;self.observation_generation=None
 def begin(self,head):
  a=self.ace;src=a._head_source.get(head) or {}
  if head!=3 or src.get('ace_index')!=0 or src.get('slot')!=3 or a._swap_in_progress:return None
  if self.active or a._location_hold.active or not a._location_hold.valid_marker:raise ParkUnavailable('停泊参考不可用')
  base=check_profile(a,3,recovery=False)
  try:
   st=os.lstat(PATH)
   if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or stat.S_IMODE(st.st_mode)!=0o600:raise ParkUnavailable('停泊记录权限无效')
   with open(PATH,'rb') as f:old=f.read()
   data=json.loads(old)
   if data.get('state') not in ('verified_recovered','verified_ejected'):raise ParkUnavailable('未有已确认的几何记录')
   if data.get('state')=='verified_ejected':
    sensor=a.printer.lookup_object('filament_motion_sensor e3_filament',None)
    if sensor is None or getattr(sensor,'runout_buttun_state',None) is not True:raise ParkUnavailable('退出后未确认重新装到喷头')
   archive=PATH+'.before-hot-'+str(os.getpid())+'-'+str(time.time_ns())
   fd=os.open(archive,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
   with os.fdopen(fd,'wb') as f:f.write(old);f.flush();os.fsync(f.fileno())
   pending=dict(data,state='hot_in_progress',started_at=time.time());tmp=archive+'.pending'
   fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
   with os.fdopen(fd,'w') as f:json.dump(pending,f);f.flush();os.fsync(f.fileno())
   os.replace(tmp,PATH)
   d=os.open(os.path.dirname(PATH),os.O_RDONLY)
   try:os.fsync(d)
   finally:os.close(d)
   hold=LocationHold()
   if not hold.valid_marker or not hold.active:raise ParkUnavailable('热退保护未生效')
   a._location_hold=hold;self.transport=base.transport;self.owner=RecoveryActions(self,base);object.__setattr__(self.owner,'recovery',False)
   a._owned_hot_park=self.owner;self.base=base;self.active=True;self.phase='tipform';self.deadline=a.reactor.monotonic()+1800
   self.observation_generation=object();generation=self.observation_generation
   def observe(eventtime):
    try:
     if self.observation_generation is not generation or not self.active or self.ace._serials.get(0) is not self.transport:return self.ace.reactor.NEVER
     if not self.observation_pending:
      self.observation_pending=True
      def response(self,response):
       try:
        session=getattr(self,'_hot_session',None)
        if session is not None and session.observation_generation is generation:session.observation_pending=False
       except Exception:pass
      try:self.ace.send_request_to(0,{'method':'get_feed_info','params':{}},response)
      except Exception:self.observation_pending=False
    except Exception:pass
    return eventtime+1.0
   try:self.observation_timer=a.reactor.register_timer(observe,a.reactor.monotonic()+1.0)
   except Exception:pass
   return self.owner
  except Exception:
   a._location_hold=LocationHold();base.close_watch();raise
 def issue(self,method,params):
  if not self.active or self.ace._serials.get(0) is not self.transport or self.ace.reactor.monotonic()>self.deadline:raise ParkUnavailable('热退会话已失效')
  if params.get('index')!=3:raise ParkUnavailable('热退目标不符')
  speed=params.get('speed',0);length=params.get('length',0)
  if type(speed) is not int or speed<0 or speed>80 or type(length) is not int or length<0:raise ParkUnavailable('热退参数无效')
  if self.phase=='tipform': allowed=(method in ('start_feed_assist','unwind_filament','update_feeding_speed') and length<=150) or (method=='feed_or_rollback_raw' and params.get('mode')==3 and length==0)
  elif self.phase=='short': allowed=method=='unwind_filament' and length<=150
  elif self.phase=='reposition': allowed=((method=='feed_filament' and length<=900 and speed==10) or (method=='unwind_filament' and length<=2435 and speed==5) or (method=='update_feeding_speed' and speed in (10,60)))
  else: allowed=False
  if not allowed:raise ParkUnavailable('热退阶段或运动预算不符')
  token=object();self.permits[token]=(method,dict(params),self.phase);return token
 def allows(self,idx,request):
  try:
   if not self.active or idx!=0 or self.ace._serials.get(0) is not self.transport or not self.ace._connected_per_ace.get(0) or self.ace._serial_failed_per_ace.get(0) or self.ace.reactor.monotonic()>self.deadline:return False
   if self.ace._slot_actions.cancel or self.ace._stop_guard.message():return False
   source=self.ace._head_source.get(3) or {}
   if source.get('ace_index')!=0 or source.get('slot')!=3:return False
   ps=self.ace.printer.lookup_object('print_stats',None)
   if ps is None or getattr(ps,'state','').lower() not in ('standby','complete','cancelled'):return False
   return self.permits.get(request.get('_hot_token'))==(request.get('method'),request.get('params'),self.phase)
  except Exception:return False
 def consumed(self,request):self.permits.pop(request.get('_hot_token'),None)
 def send(self,idx,request,timeout=5):
  if idx!=0 or self.ace._serials.get(0) is not self.transport:raise ParkUnavailable('热退连接已改变')
  request=dict(request);method=request.get('method');params=request.get('params') or {}
  if method in ('stop_feed_assist','stop_feed_filament'):request['_insert_stop_transport']=self.transport
  else:request['_hot_token']=self.issue(method,params)
  done=[None]
  def cb(self,response):done[0]=response if response is not None else {}
  request['_insert_reject']=cb;self.ace.send_request_to(idx,request,cb);deadline=self.ace.reactor.monotonic()+timeout
  while done[0] is None and self.ace.reactor.monotonic()<deadline:
   if self.ace._serials.get(0) is not self.transport:raise ParkUnavailable('热退连接已改变')
   self.ace.reactor.pause(self.ace.reactor.monotonic()+.05)
  if done[0] is None:raise ParkUnavailable(method+' 响应未确认')
  response=done[0]
  expected=9 if method in ('stop_feed_assist','stop_feed_filament') else 8
  if (not isinstance(response,dict) or response.get('_v2_frame_valid') is not True or response.get('_v2_payload_valid') is not True or response.get('_v2_command')!=expected or self.ace._tipform_rejected(response)):
   raise ParkUnavailable(method+' 响应无效或被设备拒绝')
  return done[0]
 def complete(self,result):
  a=self.ace
  if (not self.active or self.ace._serials.get(0) is not self.transport or result.get('state')!='complete' or result.get('head_reached') is not True or result.get('head_clear') is not True or result.get('gate_present') is not True or result.get('encoder_target_reached') is not True):raise ParkUnavailable('热退停泊未确认')
  def monitor():
   if a._serials.get(0) is not self.transport:raise ParkUnavailable('热退连接已改变')
   a._require_no_stop_fault()
  uid=self.owner._request(0,'discover_device',{},0,monitor=monitor)
  if tuple(uid.get('uid%d'%i) for i in (1,2,3))!=PROFILE_UID:raise ParkUnavailable('热退后 ACE UID 不一致')
  gate,_=self.owner._stable(0,3,monitor=monitor)
  if not gate:raise ParkUnavailable('热退后入口未确认')
  self.owner._check(0,3,self.transport)
  proof={'reason':'position_unconfirmed_after_seek','state':'verified_recovered','ace_uid':list(PROFILE_UID),'slot':3,'head':3,'port':PORT,'head_triggered':True,'head_clear':True,'gate_present':True,'stopped':True,'encoder_target_reached':True,'target_mm':1393,'result':result,'time':time.time(),'origin':'normal_hot_unload'}
  tmp=PATH+'.hot-complete-'+str(time.time_ns());fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'w') as f:json.dump(proof,f);f.flush();os.fsync(f.fileno())
  hold=LocationHold(tmp)
  if not hold.valid_marker or hold.active:raise ParkUnavailable('热退结果证据无效')
  with open(PATH,'rb') as f:pending=f.read()
  pending_backup=PATH+'.hot-pending-backup-'+str(time.time_ns()); fd=os.open(pending_backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'wb') as f:f.write(pending);f.flush();os.fsync(f.fileno())
  source=a._head_source.get(3);replaced=False
  try:
   a._head_source[3]=None;a._save_head_source();os.replace(tmp,PATH);replaced=True
   d=os.open(os.path.dirname(PATH),os.O_RDONLY)
   try:os.fsync(d)
   finally:os.close(d)
   hold=LocationHold()
   if not hold.valid_marker or hold.active:raise ParkUnavailable('热退结果复核失败')
   a._location_hold=hold
   a._normal_load_speed.record_park(self.transport)
  except Exception:
   if replaced:os.replace(pending_backup,PATH)
   a._head_source[3]=source;a._save_head_source();raise
  self.close()
 def fail(self):
  if not self.active:return
  try:
   if self.ace._serials.get(0) is self.transport:
    session=self.ace._roundtrip_session;old_ctx=getattr(session,'_hot_context',None);old_transport=session.transport
    session._hot_context=self.owner;session.transport=self.transport
    try:session._stop_stable(self.ace.reactor.monotonic()-.3,'rollback')
    finally:session._hot_context=old_ctx;session.transport=old_transport
   else:raise ParkUnavailable('原连接已改变')
  except Exception as exc:self.ace._latch_stop_fault(0,3,'hot unload stop unconfirmed: %s'%exc)
  finally:self.close()
 def close(self):
  self.active=False;self.permits.clear();self.observation_generation=None;self.observation_pending=False
  if self.observation_timer is not None:
   try:self.ace.reactor.unregister_timer(self.observation_timer)
   except Exception:pass
   self.observation_timer=None
  if self.owner is not None:
   self.base.close_watch()
   if getattr(self.ace,'_owned_hot_park',None) is self.owner:self.ace._owned_hot_park=None
