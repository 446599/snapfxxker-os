import unittest,json
from unittest.mock import patch
import unwind003 as u
class TestTimeout(unittest.TestCase):
 def reply(self,msg):return {'result':{'gcode_store':[{'time':1,'message':msg}]}}
 def test_real_timeout_retries_read_once(self):
  prefix='// [A get_status] '
  ok={'_v2_frame_valid':True,'_v2_payload_valid':True,'_v2_command':6,'code':0,'result':{'ok':1}}
  empty={'result':{'gcode_store':[]}}
  responses=[empty,{},self.reply(prefix+'超时 (3.0s 内无响应)'),empty,{},self.reply(prefix+json.dumps(ok))]
  with patch.object(u.probe,'request',side_effect=responses) as req,patch.object(u.probe,'record'):
   self.assertEqual(u.macro('A_STATUS','get_status'),{'ok':1});self.assertEqual(req.call_count,6)
 def test_motion_never_retried(self):
  with patch.object(u,'macro_once',side_effect=u.ReadTimeout('timeout')) as once:
   with self.assertRaises(u.ReadTimeout):u.macro('A_FEED','feed_or_rollback_raw',SLOT=3)
   once.assert_called_once()
 def test_second_read_timeout_propagates(self):
  with patch.object(u,'macro_once',side_effect=u.ReadTimeout('timeout')) as once,patch.object(u.probe,'record'):
   with self.assertRaises(u.ReadTimeout):u.macro('A_STATUS','get_status')
   self.assertEqual(once.call_count,2)

class TestEmptyDecoder(unittest.TestCase):
 def run_verify(self,gate):
  row={'index':3,'_wire_valid':True,'_decoder_present':False,'decoder':0}
  with patch.object(u,'check',return_value=(['ready']*4,gate)),patch.object(u.probe,'macro',return_value={'_feed_wire_valid':True,'feed_info':[row]}),patch.object(u.time,'sleep'):
   return u.verify()
 def test_empty_gate_accepts_absent_zero(self):
  self.assertEqual(len(self.run_verify(False)),3)
 def test_present_gate_rejects_absent_zero(self):
  with self.assertRaisesRegex(RuntimeError,'decoder missing'):self.run_verify(True)
