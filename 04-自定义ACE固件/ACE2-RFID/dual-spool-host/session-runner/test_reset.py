import unittest
from types import SimpleNamespace as O
from ace_native_guard import NativeGuard
class RearmTests(unittest.TestCase):
 def setUp(self):
  g=self.g=NativeGuard.__new__(NativeGuard);self.now=10.
  g.state='confirmed';g.stop_ack=True;g.stop_inflight=False;g.stable_count=4;g.stable_since=8.;g.confirmed_at=9.9;g.feed_time=9.9;g.last_decoder=7
  g.status=['ready']*4;g.status_time=9.9;g.heads=lambda now:[False]*4;g.pending=None;g.event_seq=5;g.poll_token=9;g.saw_busy=True
  g.poll_failures=0;g.retry_method=None;g.timer='timer';g.trace_events=[1];g.trace_seq=1;g.poll_events=[1];g.fail_counts={};g.poll_requests=0;g.poll_replies=0;g.slot=3
  g.transport=O(is_open=True);g.ace=O(_serials={0:g.transport},_active_device_index=0,_ace_mode='multi',_connected_per_ace={0:True},_require_no_stop_fault=lambda:None)
  self.unregistered=[];self.registered=[];g.reactor=O(monotonic=lambda:self.now,unregister_timer=self.unregistered.append,register_timer=lambda *x:self.registered.append(x),NOW=0)
  self.cmd=O(error=lambda s:ValueError(s),respond_info=lambda s:None,get_int=lambda *a,**kw:3)
 def test_rearm_reuses_live_timer_and_clears_trace(self):
  self.g.cmd_rearm(self.cmd);g=self.g
  self.assertEqual(g.state,'waiting_empty');self.assertEqual(g.event_seq,6);self.assertEqual(g.timer,'timer');self.assertEqual(g.trace_events,[])
  self.assertEqual(self.unregistered,[]);self.assertEqual(self.registered,[])
  with self.assertRaises(ValueError):g.cmd_rearm(self.cmd)
 def test_unsafe_states_rejected(self):
  for state in ('idle','active','stopping','fault','waiting_insert'):
   with self.subTest(state=state):
    self.g.state=state
    with self.assertRaises(ValueError):self.g.cmd_rearm(self.cmd)
 def test_evidence_gates(self):
  for attr,value in [('stop_ack',False),('stop_inflight',True),('stable_count',2),('stable_since',9.5),('confirmed_at',8),('feed_time',8),('status_time',8),('status',['ready','ready','feeding','ready']),('pending',('get_status',1,9))]:
   with self.subTest(attr=attr):
    self.setUp();setattr(self.g,attr,value)
    with self.assertRaises(ValueError):self.g.cmd_rearm(self.cmd)
    self.assertEqual(self.g.state,'confirmed');self.assertEqual(self.unregistered,[])
 def test_transport_heads_fault_and_busy(self):
  for kind in ('transport','heads','fault','busy'):
   self.setUp()
   if kind=='transport':self.g.ace._serials[0]=O(is_open=True)
   if kind=='heads':self.g.heads=lambda now:[False,False,False,True]
   if kind=='fault':self.g.ace._require_no_stop_fault=lambda:(_ for _ in ()).throw(ValueError('fault'))
   if kind=='busy':self.g.ace._insert_read_queue=[3]
   with self.subTest(kind=kind),self.assertRaises(ValueError):self.g.cmd_rearm(self.cmd)
 def test_real_callback_changed_decoder_resets_window(self):
  g=self.g;callbacks=[];g.ace.send_request_to=lambda idx,req,cb:callbacks.append(cb)
  row={'_decoder_present':True,'_wire_valid':True,'decoder':8}
  def sample(t):
   self.now=t;g.status_time=t;g.request('get_feed_info',t)
   callbacks[-1](None,{'_v2_frame_valid':True,'_v2_payload_valid':True,'code':0,'result':{'_feed_wire_valid':True,'feed_info':[row]*4}})
  sample(10);self.assertIsNone(g.confirmed_at);self.assertEqual(g.stable_count,0)
  for t in (10.2,10.4,10.6,10.8):sample(t)
  self.assertIsNone(g.confirmed_at)
  with self.assertRaises(ValueError):g.cmd_rearm(self.cmd)
  for t in (11.,11.2,11.4,11.6,11.8):sample(t)
  g.cmd_rearm(self.cmd);self.assertEqual(g.state,'waiting_empty')
 def test_late_reply_cannot_replace_new_request(self):
  g=self.g;callbacks=[];g.ace.send_request_to=lambda idx,req,cb:callbacks.append(cb)
  g.request('get_status',9);old=callbacks[-1];g.pending=None;g.cmd_rearm(self.cmd)
  g.request('get_key_state',10);current=g.pending;old(None,{})
  self.assertEqual(g.pending,current)
 def test_idle_arm_with_old_timer_is_transactionally_rejected(self):
  g=self.g;g.state='idle';g.reason='preserve';g.slot=2;g.trace_events=['old'];before=(g.state,g.reason,g.slot,g.event_seq,list(g.trace_events),g.timer)
  with self.assertRaises(ValueError):g.cmd_arm(self.cmd)
  self.assertEqual((g.state,g.reason,g.slot,g.event_seq,g.trace_events,g.timer),before)
  self.assertEqual(self.registered,[])
 def test_clear_only_after_safe_confirmed(self):
  self.g.cmd_clear(self.cmd);self.assertEqual(self.g.state,'idle');self.assertEqual(self.unregistered,['timer'])
  self.setUp();self.g.state='fault'
  with self.assertRaises(ValueError):self.g.cmd_clear(self.cmd)
if __name__=='__main__':unittest.main()
