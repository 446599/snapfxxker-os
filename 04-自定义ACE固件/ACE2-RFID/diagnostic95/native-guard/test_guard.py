import unittest
from unittest.mock import patch
from ace_native_guard import NativeGuard
class Obj:pass
class T(unittest.TestCase):
 def guard(self):
  g=NativeGuard.__new__(NativeGuard);g.fail_counts={};g.retry_method=None;g.poll_events=[];g.last_request_id=None;g.pending=None;g.poll_token=0;g.poll_failures=0;g.poll_requests=0;g.poll_replies=0;g.poll_timeouts=0;g.state='waiting_empty';g.reason=None;g.triggered=[];g.event_seq=0;g.stop_count=0;g.timer=None
  g.reactor=Obj();g.reactor.monotonic=lambda:1;g.ace=Obj();g.ace.calls=[]
  g.ace._stop_feeding=lambda slot,idx:g.ace.calls.append((slot,idx)) or True
  g.ace._latch_stop_fault=lambda *x:None
  return g
 def test_get_status_immediately_after_construct(self):
  class FakePrinter:
   def get_reactor(self):return Obj()
   def lookup_object(self,name,*args):
    x=Obj();x.register_command=lambda *a,**k:None;return x
   def register_event_handler(self,*args):pass
  config=Obj();config.get_printer=lambda:FakePrinter()
  g=NativeGuard(config);value=g.get_status(0)
  self.assertEqual(value['state'],'idle');self.assertIsNone(value['gate']);self.assertIsNone(value['lane_states'])
 def test_idle_status_after_ready(self):
  ace=Obj();ace._insert_read_then_preload=lambda *a:None;ace._insert_tag_read_safe=lambda *a:None
  class FakePrinter:
   def get_reactor(self):return Obj()
   def lookup_object(self,name,*args):
    if name=='ace':return ace
    if name=='gcode':
     x=Obj();x.register_command=lambda *a,**k:None;return x
    return None
   def register_event_handler(self,*args):pass
  config=Obj();config.get_printer=lambda:FakePrinter();g=NativeGuard(config)
  mod=Obj();mod.AceProtocolV2=type('Protocol',(),{'_v2_response_to_v1':lambda *a:{}})
  with patch('ace_native_guard.importlib.import_module',return_value=mod):g.ready()
  self.assertEqual(g.get_status(0)['state'],'idle')
 def test_telemetry_test_failure_never_sends_stop(self):
  g=self.guard();g.state='telemetry_test';g.stop(2,'telemetry timeout');self.assertEqual(g.state,'idle');self.assertEqual(g.ace.calls,[]);self.assertEqual(g.telemetry_result,'failed')
 def test_stop_freezes_before_call_and_pins_device(self):
  g=self.guard()
  def stop(slot,idx):self.assertEqual(g.state,'stopping');self.assertEqual((slot,idx),(2,0));return True
  g.ace._stop_feeding=stop;self.assertTrue(g.stop(2,'sensor'));self.assertEqual(g.state,'stopping')
 def test_wrapper_stops_both_slots(self):
  g=self.guard();g.state='active';g.ace._insert_read_then_preload=lambda *a:None;g.ace._insert_tag_read_safe=lambda *a:None
  g.printer=Obj();g.printer.lookup_object=lambda *a:g.ace;g.sensors=[]
  mod=Obj();mod.AceProtocolV2=type('Protocol',(),{'_v2_response_to_v1':lambda *a:{}})
  with patch('ace_native_guard.importlib.import_module',return_value=mod):g.ready()
  g.ace._insert_read_then_preload(0,1);self.assertEqual(g.ace.calls,[(1,0),(2,0)])
 def test_flag_wrapper_does_not_invent_enabled(self):
  g=self.guard();g.ace._insert_read_then_preload=lambda *a:None;g.ace._insert_tag_read_safe=lambda *a:None
  g.printer=Obj();g.printer.lookup_object=lambda *a:g.ace
  mod=Obj();mod.AceProtocolV2=type('Protocol',(),{'_v2_response_to_v1':lambda *a:{'_v2_payload_valid':True,'result':{'_status_wire_valid':True}}})
  mod.pb_decode=lambda payload:{5:[(0,1)]};mod._fval=lambda fields,k,default:fields.get(k,[(0,default)])[0][1]
  with patch('ace_native_guard.importlib.import_module',return_value=mod):g.ready()
  result=mod.AceProtocolV2()._v2_response_to_v1(6,1,b'x')['result']
  self.assertEqual((result['rfid1_enable_raw'],result['rfid2_enable_raw']),(1,0))
 def test_failed_stop_fault(self):
  g=self.guard();g.ace._stop_feeding=lambda *a,**k:False;g.stop(2,'sensor');self.assertEqual(g.state,'fault')
 def test_active_head_calls_stop_before_telemetry(self):
  g=self.guard();g.state='active';g.inserted=0;g.started=0;g.transport=object();g.next_poll=9;g.status=None;g.key_time=1;g.status_time=1
  g.heads=lambda now:[False,False,True,False];g.printer=Obj();ps=Obj();ps.state='standby';g.printer.lookup_object=lambda *a:ps
  g.ace._active_device_index=0;g.ace._ace_mode='multi';g.ace._serials={0:g.transport};g.tick(1)
  self.assertEqual(g.ace.calls,[(2,0)]);self.assertEqual(g.triggered,[2])
 def test_late_ready_does_not_count_stable(self):
  g=self.guard();g.state='stopping';g.stop_ack=True;g.status=['ready']*4;g.status_time=0;g.last_decoder=7;g.stable_count=0;g.pending=None;g.poll_token=0;g.poll_failures=0;g.event_seq=1
  captured=[];g.ace.send_request_to=lambda idx,req,cb:captured.append(cb);g.request('get_feed_info',1)
  captured[0](None,{'_v2_frame_valid':True,'_v2_payload_valid':True,'code':0,'result':{'_feed_wire_valid':True,'feed_info':[{'_decoder_present':True,'_wire_valid':True,'decoder':7}]*4}})
  self.assertEqual(g.stable_count,0)
 def test_default_zero_is_valid_only_with_valid_row(self):
  g=self.guard();g.state='stopping';g.stop_ack=True;g.status=['ready']*4;g.status_time=1;g.last_decoder=0;g.stable_count=0;g.stable_since=None;g.saw_busy=False;g.pending=None;g.poll_token=0;g.poll_failures=0;g.event_seq=1
  captured=[];g.ace.send_request_to=lambda idx,req,cb:captured.append(cb)
  g.request('get_feed_info',1);rows=[{'_decoder_present':False,'_wire_valid':True,'decoder':0}]*4
  captured[0](None,{'_v2_frame_valid':True,'_v2_payload_valid':True,'code':0,'result':{'_feed_wire_valid':True,'feed_info':rows}})
  self.assertEqual(g.stable_count,1)
 def test_default_zero_after_motion_is_not_accepted(self):
  g=self.guard();g.state='stopping';g.stop_ack=True;g.status=['ready']*4;g.status_time=1;g.last_decoder=0;g.stable_count=0;g.stable_since=None;g.saw_busy=True;g.pending=None;g.poll_token=0;g.poll_failures=0;g.event_seq=1
  captured=[];g.ace.send_request_to=lambda idx,req,cb:captured.append(cb)
  g.request('get_feed_info',1);rows=[{'_decoder_present':False,'_wire_valid':True,'decoder':0}]*4
  captured[0](None,{'_v2_frame_valid':True,'_v2_payload_valid':True,'code':0,'result':{'_feed_wire_valid':True,'feed_info':rows}})
  self.assertEqual(g.stable_count,0)
 def test_state_transition_invalidates_inflight_reply(self):
  g=self.guard();g.pending=('get_status',1,0);g.poll_token=1;g.poll_failures=1;g.event_seq=5
  g.event_seq+=1;g.invalidate_pending();self.assertIsNone(g.pending);self.assertEqual(g.poll_token,2);self.assertEqual(g.poll_failures,0)
 def test_late_reply_cannot_clear_new_request(self):
  g=self.guard();g.pending=None;g.poll_token=0;g.poll_failures=0;g.event_seq=1;captured=[]
  g.ace.send_request_to=lambda idx,req,cb:captured.append(cb)
  g.request('get_status',0);g.pending=None;g.request('get_key_state',1);captured[0](None,{})
  self.assertEqual(g.pending[0],'get_key_state')
 def polling_guard(self):
  g=self.guard();clock=[0.0];g.reactor.monotonic=lambda:clock[0];g.state='waiting_insert';g.started=0;g.transport=object();g.ace._serials={0:g.transport};g.ace._active_device_index=0;g.ace._ace_mode='multi';g.ace._tag_read_busy=False;g.ace._gen1_manual_busy=False
  ps=Obj();ps.state='standby';g.printer=Obj();g.printer.lookup_object=lambda *a:ps;g.heads=lambda t:[False]*4
  g.status=['ready']*4;g.status_time=.01;g.key=False;g.key_time=.01;g.next_poll=0;g.poll_index=0;g.saw_busy=False;g.rfid_flags=[0,1];g.triggered=[];g.timer_max_gap=0;g.last_tick=None
  captured=[];g.ace.send_request_to=lambda idx,req,cb:captured.append((req,cb));return g,clock,captured
 def test_stop_pause_reentrant_tick_cannot_fault(self):
  g,t,c=self.polling_guard();g.state='active';g.inserted=90;g.last_stop=0;t[0]=100
  def stop(slot,idx):g.tick(100.1);return True
  g.ace._stop_feeding=stop;self.assertTrue(g.stop(2,'native completed'));self.assertEqual(g.state,'stopping');self.assertTrue(g.stop_ack)
 def test_stop_exception_records_and_latches(self):
  g=self.guard();g.ace._stop_feeding=lambda *a,**k:(_ for _ in ()).throw(ValueError('lost ACK'))
  self.assertFalse(g.stop(2,'sensor'));self.assertEqual(g.state,'fault');self.assertEqual(g.stop_error,{'type':'ValueError','message':'lost ACK'})
 def test_tick_single_drop_later_than_old_stale_limit_recovers(self):
  g,t,c=self.polling_guard();g.key_time=10.01;g.status_time=10.01;t[0]=10.05;g.tick(t[0]);self.assertEqual(c[-1][0]['method'],'get_key_state')
  t[0]=10.9;g.tick(t[0]);self.assertEqual(len(c),2);self.assertEqual(c[-1][0]['method'],'get_key_state')
  t[0]=11.2;c[-1][1](None,{'_v2_frame_valid':True,'_v2_payload_valid':True,'code':0,'id':12,'result':{'fields':{'1':0}}});g.tick(t[0]);self.assertEqual(g.state,'waiting_insert');self.assertEqual(g.poll_timeouts,1)
 def test_tick_three_drops_stop_within_bound(self):
  g,t,c=self.polling_guard()
  for now in (.05,.9,1.75,2.6):t[0]=now;g.tick(now)
  self.assertEqual(g.state,'stopping');self.assertEqual(g.ace.calls,[(2,0)]);self.assertEqual(g.poll_timeouts,3)
 def test_one_lost_reply_then_recovery(self):
  g=self.guard();g.state='waiting_insert';g.pending=('get_status',1,0);g.expire_pending(.81)
  self.assertEqual(g.poll_timeouts,1);self.assertEqual(g.state,'waiting_insert')
  captured=[];g.ace.send_request_to=lambda idx,req,cb:captured.append(cb);g.request('get_status',.9)
  captured[0](None,{'_v2_frame_valid':True,'_v2_payload_valid':True,'code':0,'id':5,'result':{'_status_wire_valid':True,'slots':[{'slot_status':'ready'}]*4}})
  self.assertEqual(g.poll_failures,0);self.assertEqual(g.state,'waiting_insert')
 def test_other_method_does_not_clear_failure_budget(self):
  g=self.guard();g.state='waiting_insert';g.pending=('get_key_state',1,0);g.expire_pending(.81)
  self.assertEqual(g.retry_method,'get_key_state')
  captured=[];g.ace.send_request_to=lambda idx,req,cb:captured.append(cb);g.request('get_status',1)
  captured[0](None,{'_v2_frame_valid':True,'_v2_payload_valid':True,'code':0,'id':8,'result':{'_status_wire_valid':True,'slots':[{'slot_status':'ready'}]*4}})
  self.assertEqual(g.fail_counts['get_key_state'],1);self.assertEqual(g.retry_method,'get_key_state')
 def test_three_lost_replies_stop(self):
  g=self.guard();g.state='waiting_insert'
  for i in range(3):g.pending=('get_status',i,i);g.expire_pending(i+.81)
  self.assertEqual(g.state,'stopping');self.assertEqual(g.ace.calls,[(2,0)])
 def test_native_completion_stops(self):
  g=self.guard();g.state='active';g.saw_busy=True;g.pending=None;g.poll_token=0;g.poll_failures=0;g.event_seq=1;captured=[];g.ace.send_request_to=lambda idx,req,cb:captured.append(cb)
  g.request('get_status',1);captured[0](None,{'_v2_frame_valid':True,'_v2_payload_valid':True,'code':0,'result':{'_status_wire_valid':True,'slots':[{'slot_status':'ready'}]*4}})
  self.assertEqual(g.ace.calls,[]);self.assertEqual(g.state,'active')
 def test_native_stable_two_seconds_stops(self):
  g=self.guard();g.state='active';g.saw_busy=True;g.pending=None;g.poll_token=0;g.poll_failures=0;g.event_seq=1;g.natural_decoder=None;g.natural_since=None;g.natural_count=0;g.status=['ready']*4;clock=[0];g.reactor.monotonic=lambda:clock[0];captured=[];g.ace.send_request_to=lambda idx,req,cb:captured.append(cb)
  for t in (0,.5,1.,1.5,2.,2.5):
   clock[0]=t;g.status_time=t;g.request('get_feed_info',t);row={'_decoder_present':True,'_wire_valid':True,'decoder':42};captured[-1](None,{'_v2_frame_valid':True,'_v2_payload_valid':True,'code':0,'result':{'_feed_wire_valid':True,'feed_info':[row]*4}})
   if t<2.5:self.assertEqual(g.ace.calls,[])
  self.assertEqual(g.ace.calls,[(2,0)])
 def test_native_busy_resets_quiet_window(self):
  g=self.guard();g.state='active';g.saw_busy=True;g.natural_decoder=42;g.natural_since=1;g.natural_count=5;g.pending=None;g.event_seq=1;captured=[];g.ace.send_request_to=lambda idx,req,cb:captured.append(cb)
  g.request('get_status',1);lanes=[{'slot_status':'ready'}]*4;lanes[2]={'slot_status':'preloading'}
  captured[-1](None,{'_v2_frame_valid':True,'_v2_payload_valid':True,'code':0,'result':{'_status_wire_valid':True,'slots':lanes}})
  self.assertIsNone(g.natural_since);self.assertEqual(g.natural_count,0);self.assertEqual(g.ace.calls,[])
 def test_clear_rejects_unverified(self):
  g=self.guard();cmd=Obj();cmd.error=lambda x:ValueError(x)
  for state in ('waiting_empty','waiting_insert','active','stopping','confirmed'):
   g.state=state
   with self.assertRaises(ValueError):g.cmd_clear(cmd)
if __name__=='__main__':unittest.main()
