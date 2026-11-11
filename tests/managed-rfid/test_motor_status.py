import ast
import pathlib
import tarfile
import time
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
ARCHIVE = ROOT/'overlays/233-dev.2-perms.1/root/usr/local/share/233-firmware/multiace-managed-1.27b-233.20261111-single.5.tar.gz'
with tarfile.open(ARCHIVE,'r:gz') as archive:
    source = archive.extractfile('multiace-1.27b-233.20261111-single.5/klipper/extras/ace.py').read()
tree = ast.parse(source)
cls = next(node for node in tree.body if isinstance(node,ast.ClassDef) and any(isinstance(x,ast.FunctionDef) and x.name=='get_status' for x in node.body))
status = next(x for x in cls.body if isinstance(x,ast.FunctionDef) and x.name=='get_status')
slot_dict = next(node for node in ast.walk(status) if isinstance(node,ast.Dict) and any(isinstance(key,ast.Constant) and key.value=='slot_status' for key in node.keys))
pos = next(i for i,key in enumerate(slot_dict.keys) if isinstance(key,ast.Constant) and key.value=='slot_status')
slot_expr = compile(ast.Expression(slot_dict.values[pos]),'<public-slot-status>','eval')
heartbeat = next(x for x in cls.body if isinstance(x,ast.FunctionDef) and x.name=='_make_heartbeat_tick_for')
assignment = next(node for node in ast.walk(heartbeat) if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='valid_motor_status' for t in node.targets))
frame_expr = compile(ast.Expression(assignment.value),'<valid-status-frame>','eval')

class SlotMotorStatusTests(unittest.TestCase):
    def setUp(self):
        self.transport=object()
        self.owner=types.SimpleNamespace(_serials={0:self.transport},_connected_per_ace={0:True},_serial_failed_per_ace={})
        self.info={'_slot_status_frame_valid':True}
        self.slot={'status':'unknown','slot_status':'ready','_wire_valid':True}
        self.provenance=(self.transport,time.monotonic())
    def evaluate(self):
        return eval(slot_expr,{'time':time},{'self':self.owner,'i':0,'s':self.slot,'info':self.info,'motor_provenance':self.provenance})
    def test_unknown_identity_does_not_hide_real_ready(self):
        self.assertEqual(self.evaluate(),'ready')
    def test_nonready_is_preserved_and_invalid_is_unknown(self):
        self.slot['slot_status']='rollback';self.assertEqual(self.evaluate(),'rollback')
        self.slot['_wire_valid']=False;self.assertIsNone(self.evaluate())
    def test_stale_reconnected_or_bad_frame_is_unknown(self):
        self.provenance=(self.transport,time.monotonic()-6);self.assertIsNone(self.evaluate())
        self.provenance=(object(),time.monotonic());self.assertIsNone(self.evaluate())
        self.provenance=(self.transport,time.monotonic());self.info['_slot_status_frame_valid']=False;self.assertIsNone(self.evaluate())
        self.info['_slot_status_frame_valid']=True;self.owner._connected_per_ace[0]=False;self.assertIsNone(self.evaluate())
    def test_frame_provenance_requires_cmd6_crc_payload_and_original_transport(self):
        response={'_v2_command':6,'_v2_frame_valid':True,'_v2_payload_valid':True,'code':0}
        result={'_status_wire_valid':True}
        env={'self':self.owner,'idx':0,'ser':self.transport,'response':response,'result':result}
        self.assertTrue(eval(frame_expr,{},env))
        for key,value in (('_v2_command',13),('_v2_frame_valid',False),('_v2_payload_valid',False),('code',False)):
            old=response[key];response[key]=value;self.assertFalse(eval(frame_expr,{},env));response[key]=old
        env['ser']=object();self.assertFalse(eval(frame_expr,{},env))

if __name__=='__main__':unittest.main()
