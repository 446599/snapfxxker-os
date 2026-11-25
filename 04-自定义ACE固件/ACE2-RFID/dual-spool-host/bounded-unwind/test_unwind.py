import unittest
from unittest.mock import patch
import unwind as u
class Tests(unittest.TestCase):
 def test_wrong_lane_stops_other(self):
  with patch.object(u.probe,'heads',return_value=[False]*4),patch.object(u.probe,'status',return_value={'slots':[{'slot_status':s} for s in ['ready','preloading','ready','ready']]}),patch.object(u,'stop') as stop:
   with self.assertRaises(RuntimeError):u.check()
   stop.assert_called_once_with(1)
 def run_fake(self,gate_sequence,feed_error=False,limit=600):
  cmds=[];gates=iter(gate_sequence)
  state={'printer_state':'idle','aces':[{'fw_hold':False}],'tag_op':{'busy':False},'swap_in_progress':False,'calibration':{'state':'idle'}}
  def macro(name,label,**kw):
   cmds.append((name,kw))
   if name=='A_INFO':return {'firmware':'V1.1.97O'}
   if name=='A_DISCOVER':return dict(zip(('uid1','uid2','uid3'),u.probe.UID))
   if name=='A_FEED' and feed_error:raise TimeoutError('lost ack')
   return {}
  def check():return (['ready']*4,next(gates))
  sample=[{'decoder':1,'gate':False}]*3
  with patch.object(u.probe,'request',return_value=state),patch.object(u.probe,'macro',side_effect=macro),patch.object(u,'check',side_effect=check),patch.object(u,'verify',side_effect=[[{'decoder':1,'gate':True}]*3,sample]),patch.object(u,'LIMIT',limit),patch.object(u.OUT.__class__,'write_text',return_value=None),patch.object(u.time,'sleep'):
   result=u.run()
  return cmds,result
 def test_ack_lost_no_reissue(self):
  cmds,result=self.run_fake([True],True);self.assertEqual(sum(n=='A_FEED' for n,_ in cmds),1);self.assertTrue(result['motion_unconfirmed']);self.assertTrue(result['exception_stop_ack'])
 def test_gate_empty_stops_no_more_motion(self):
  cmds,result=self.run_fake([True,False]);self.assertEqual(result['outcome'],'gate_empty');self.assertEqual(sum(n=='A_FEED' for n,_ in cmds),1)
 def test_zero_budget_sends_no_motion(self):
  cmds,result=self.run_fake([True],limit=0);self.assertEqual(result['outcome'],'bounded_limit');self.assertNotIn('A_FEED',[n for n,_ in cmds])
if __name__=='__main__':unittest.main()

class ProtocolTests(unittest.TestCase):
 def test_info_actual_macro_checks_command(self):
  import json
  for command,accepted in ((7,True),(6,False)):
   reply={'_v2_frame_valid':True,'_v2_payload_valid':True,'_v2_command':command,'code':0,'result':{'firmware':'V1.1.97O'}}
   values=[{'result':{'gcode_store':[]}}, {}, {'result':{'gcode_store':[{'time':1,'message':'// [A get_info] '+json.dumps(reply)}]}}]
   with patch.object(u.probe,'request',side_effect=values),patch.object(u.probe,'record'):
    if accepted:self.assertEqual(u.macro('A_INFO','get_info')['firmware'],'V1.1.97O')
    else:
     with self.assertRaises(RuntimeError):u.macro('A_INFO','get_info')
