import unittest,json
from unittest.mock import Mock
import readonly_macro as m
class TestRead(unittest.TestCase):
 def test_chinese_timeout_then_read_success(self):
  empty={'result':{'gcode_store':[]}};row=lambda s:{'result':{'gcode_store':[{'time':1,'message':'// [A get_status] '+s}]}}
  ok={'_v2_frame_valid':True,'_v2_payload_valid':True,'code':0,'result':{'ok':1}}
  req=Mock(side_effect=[empty,{},row('超时 (3.0s 内无响应)'),empty,{},row(json.dumps(ok))]);self.assertEqual(m.macro(req,'A_STATUS','get_status'),{'ok':1});self.assertEqual(req.call_count,6)
 def test_motion_never_retried(self):
  empty={'result':{'gcode_store':[]}};row={'result':{'gcode_store':[{'time':1,'message':'// [A feed_or_rollback_raw] 超时 (3.0s 内无响应)'}]}}
  req=Mock(side_effect=[empty,{},row]);
  with self.assertRaises(m.DeviceTimeout):m.macro(req,'A_FEED','feed_or_rollback_raw',SLOT=3)
  self.assertEqual(req.call_count,3)
 def test_ambiguous_is_error(self):
  row={'time':1,'message':'// [A get_status] {}'};req=Mock(side_effect=[{'result':{'gcode_store':[]}}, {}, {'result':{'gcode_store':[row,row]}}])
  with self.assertRaisesRegex(ValueError,'ambiguous'):m.macro(req,'A_STATUS','get_status')
