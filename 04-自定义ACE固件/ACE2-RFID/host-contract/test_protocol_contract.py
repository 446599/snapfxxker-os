import importlib.util
import sys
import types
import unittest
from pathlib import Path
from test_native_bambu_contract import call, HASH, D, UID
pkg=types.ModuleType('testpkg'); pkg.__path__=[]; sys.modules['testpkg']=pkg
base=types.ModuleType('testpkg.ace_protocol'); base.AceProtocol=type('AceProtocol',(),{});sys.modules[base.__name__]=base
spec=importlib.util.spec_from_file_location('testpkg.ace_protocol_v2', Path(__file__).with_name('ace_protocol_v2.py'))
v=importlib.util.module_from_spec(spec);sys.modules[spec.name]=v;spec.loader.exec_module(v)

def blob(n,b): return v.pb_varint((n<<3)|2)+v.pb_varint(len(b))+b
def packet(payload,seq=17,cmd=68,broken_crc=False):
    inner=bytes((0x80,seq&255,seq>>8,cmd,len(payload)))+payload
    crc=v.crc16_kermit(inner) ^ int(broken_crc)
    return bytearray(b'\xff\xaa'+inner+bytes((crc&255,crc>>8,0xfe)))
def payload(color=0x000000ff):
    return (v.pb_uint32(1,3)+v.pb_uint32(2,0x0102)+blob(3,b'SM'+UID.encode())+
            blob(4,b'PETG Basic')+blob(5,v.pb_uint32(1,color))+v.pb_uint32(12,0))
class TestProtocolIntegration(unittest.TestCase):
    def decode(self,data): return v.AceProtocolV2().decode_frames(packet(data))
    def test_complete_crc_frame(self):
        result=self.decode(payload()); self.assertEqual(len(result),1); self.assertEqual(call(r=result[0])['color'],[0,0,0])
    def test_duplicate_fields_fail_closed(self):
        for extra in (v.pb_uint32(1,3),v.pb_uint32(2,0x0102),blob(3,b'SM'+UID.encode()),
                      blob(4,b'PETG Basic'),blob(5,v.pb_uint32(1,255)),v.pb_uint32(12,0)):
            result=self.decode(payload()+extra); self.assertEqual(len(result),1);self.assertIsNone(call(r=result[0]))
    def test_color_wrong_wire_or_alpha(self):
        base=payload(); c=blob(5,v.pb_uint32(1,255));
        for bad in (blob(5,blob(1,b'\x01')),blob(5,v.pb_uint32(1,0)),blob(5,b'\x08\x80'),blob(5,v.pb_uint32(1,255)+v.pb_uint32(1,255))):
            result=self.decode(base.replace(c,bad));self.assertEqual(len(result),1);self.assertIsNone(call(r=result[0]))
    def test_missing_zero_code_remains_unknown(self):
        result=self.decode(payload().removesuffix(v.pb_uint32(12,0)));self.assertIsNone(call(r=result[0]))
    def test_non_ascii_material_rejected(self):
        result=self.decode(payload().replace(blob(4,b'PETG Basic'),blob(4,b'\xff\xff')))
        self.assertEqual(len(result),1);self.assertIsNone(call(r=result[0]))
    def test_wrong_wire_types_do_not_crash_dispatch(self):
        self.assertEqual(self.decode(payload()+blob(8,b'bad')),[])
        malformed_status=blob(2,blob(1,b'bad'))
        self.assertEqual(v.AceProtocolV2().decode_frames(packet(malformed_status,cmd=6)),[])
        self.assertEqual(v.AceProtocolV2().decode_frames(packet(blob(8,b'bad'),cmd=6)),[])
    def test_wrong_wire_slot_not_ready(self):
        import struct
        wrong = bytes([0x0d]) + struct.pack('<f',0) + bytes([0x15]) + struct.pack('<f',0)
        frames=v.AceProtocolV2().decode_frames(packet(blob(9,wrong)*4,cmd=6))
        self.assertEqual(len(frames),1); self.assertFalse(frames[0]['result']['_status_wire_valid'])
        self.assertEqual(frames[0]['result']['slots'][0]['slot_status'],'unknown')
        duplicate=v.pb_uint32(1,0)+v.pb_uint32(1,1)
        frames=v.AceProtocolV2().decode_frames(packet(blob(9,duplicate)*4,cmd=6))
        self.assertFalse(frames[0]['result']['_status_wire_valid'])
        self.assertEqual(frames[0]['result']['slots'][0]['slot_status'],'unknown')
        valid_omission=blob(9,b'')*4
        frames=v.AceProtocolV2().decode_frames(packet(valid_omission,cmd=6))
        self.assertTrue(frames[0]['result']['_status_wire_valid'])
    def test_nested_malformed_and_overflow_rejected(self):
        result=self.decode(payload()+blob(6,b'\x08\x80'))
        self.assertEqual(len(result),1);self.assertIsNone(call(r=result[0]))
        self.assertFalse(v._pb_well_formed(v.pb_varint((1<<29)<<3)+b'\x00'))
        self.assertFalse(v._pb_well_formed(b'\x08'+b'\xff'*9+b'\x02'))
    def test_duplicate_feed_decoder_invalid(self):
        record=v.pb_uint32(3,10)+v.pb_uint32(3,99)
        result=v.AceProtocolV2().decode_frames(packet(blob(1,record)*4,cmd=76))
        self.assertFalse(result[0]['result']['_feed_wire_valid'])
    def test_extra_wrong_wire_slot_and_feed_rejected(self):
        result=v.AceProtocolV2().decode_frames(packet(blob(9,b'')*4+v.pb_uint32(9,0),cmd=6))
        self.assertFalse(result[0]['result']['_status_wire_valid'])
        result=v.AceProtocolV2().decode_frames(packet(blob(1,b'')*4+v.pb_uint32(1,0),cmd=76))
        self.assertFalse(result[0]['result']['_feed_wire_valid'])
    def test_generic_ack_wrong_wire_and_duplicate_rejected(self):
        for p in (blob(1,b'bad'),v.pb_uint32(1,0)+v.pb_uint32(1,1)):
            result=v.AceProtocolV2().decode_frames(packet(p,cmd=8))[0]
            self.assertFalse(result['_v2_payload_valid']);self.assertNotEqual(result['code'],0)
        empty=v.AceProtocolV2().decode_frames(packet(b'',cmd=8))[0]
        self.assertTrue(empty['_v2_payload_valid']);self.assertEqual(empty['code'],0)
    def test_corrupt_crc_is_dropped(self):
        self.assertEqual(v.AceProtocolV2().decode_frames(packet(payload(),broken_crc=True)),[])

if __name__=='__main__': unittest.main()
