import unittest
from test_guard import T

class Trace(T):
 def test_actual_callback_records_all_lanes_and_stop_target(self):
  g=self.guard();g.slot=3;g.state='active';g.saw_busy=True;g.event_seq=1;g.status=['ready']*4;g.status_time=1;g.natural_decoder=None;g.natural_since=None;g.natural_count=0
  callbacks=[];g.ace.send_request_to=lambda idx,req,cb:callbacks.append(cb)
  rows=[{'_decoder_present':True,'_wire_valid':True,'decoder':n,'length':n+10,'steps':n+20} for n in range(4)]
  g.request('get_feed_info',1);callbacks[-1](None,{'_v2_frame_valid':True,'_v2_payload_valid':True,'code':0,'result':{'_feed_wire_valid':True,'feed_info':rows}})
  self.assertEqual(g.trace_events[-1]['data']['feed_info'],rows);self.assertEqual(g.natural_decoder,3)
  g.stop(3,'head sensor');self.assertEqual(g.ace.calls,[(3,0)]);self.assertEqual(g.trace_events[-1]['data'],{'slot':3,'reason':'head sensor'})
