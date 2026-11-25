"""Exercise production parser with CMD13/CMD68 and malformed field evidence."""
import importlib.util,sys,types,pathlib,unittest
root=pathlib.Path(__file__).resolve().parent
pkg=types.ModuleType('candidate');pkg.__path__=[str(root)];sys.modules['candidate']=pkg
stub=types.ModuleType('candidate.ace_protocol');stub.AceProtocol=type('AceProtocol',(),{});sys.modules[stub.__name__]=stub
spec=importlib.util.spec_from_file_location('candidate.ace_protocol_v2',root/'ace_protocol_v2.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class TestRemainder(unittest.TestCase):
 def test_cmd_and_wire_identity(self):
  parser=m.AceProtocolV2.__new__(m.AceProtocolV2)
  payload=m.pb_uint32(1,1)+m.pb_uint32(11,1000)
  a=parser._v2_response_to_v1(m.Cmd.GET_FILAMENT_INFO,1,payload)['result']
  b=parser._v2_response_to_v1(m.Cmd.FILAMENT_IDENTIFY,2,payload)['result']
  self.assertEqual(a['native_remainder_raw'],1000)
  self.assertNotIn('native_remainder_raw',b)
  self.assertNotIn('native_remainder_raw',parser._v2_response_to_v1(m.Cmd.GET_FILAMENT_INFO,3,m.pb_uint32(11,1000))['result'])
  self.assertNotIn('native_remainder_raw',parser._v2_response_to_v1(m.Cmd.GET_FILAMENT_INFO,4,payload+m.pb_uint32(11,999))['result'])
 def test_reader_enable_bits_are_independent(self):
  parser=m.AceProtocolV2.__new__(m.AceProtocolV2)
  payload=m.pb_uint32(5,1)+m.pb_uint32(6,0)
  result=parser._v2_response_to_v1(m.Cmd.GET_STATUS,1,payload)['result']
  self.assertEqual((result['rfid1_enable_raw'],result['rfid2_enable_raw']),(1,0))
  result=parser._v2_response_to_v1(m.Cmd.GET_STATUS,1,m.pb_uint32(5,1))['result']
  self.assertIsNone(result['rfid2_enable_raw'])

if __name__=='__main__': unittest.main()
