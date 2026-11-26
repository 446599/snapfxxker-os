import ast
import os
import tarfile
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock
from patch_scoped_rfid import patch_engine

HERE=Path(__file__).resolve().parent

def source():
    root=os.environ.get('U1_FIELD_EXPORT')
    if root:
        return (Path(root)/'provider-1902.7-route-files/klipper/extras/ace.py').read_text()
    with tarfile.open(HERE.parent/'multiace-190/artifacts/multiace-1.90.2-233.20261126.tar.gz') as t:
        return t.extractfile('multiace-1.90.2-233.20261126/klipper/extras/ace.py').read().decode()

class ScopedRFIDTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s=patch_engine(source()); cls.text=s
        names={'_native_tag_jobs','_insert_tag_busy','_claim_native_tag','_finish_native_tag','_native_tag_status','_insert_drain_queue','_cmd_ace_tag_read_host','_insert_uid_read'}
        methods=[n for n in ast.walk(ast.parse(s)) if isinstance(n,ast.FunctionDef) and n.name in names]
        ns={'logging':__import__('logging'),'GATE_UNKNOWN':-1,'GATE_EMPTY':0}
        node=ast.ClassDef(name='Subject',bases=[],keywords=[],body=methods,decorator_list=[])
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),'subject','exec'),ns)
        cls.subject=ns['Subject']
    def setUp(self):
        a=self.a=self.subject(); a._cap=lambda idx,flag:idx!=3 and flag=='host_tag_session'; a._tag_read_busy=False
        a._insert_read_queue=[]; a._insert_queue_stale_reason=lambda *args:None; a._active_device_index=0
        a.printer=types.SimpleNamespace(lookup_object=lambda *a:None)
        a.reactor=types.SimpleNamespace(register_async_callback=Mock()); a._insert_tag_read_safe=Mock(); a._insert_read_then_preload=Mock()
        a._connected_per_ace={0:True,1:True,2:True}; a._gate_status_per_ace={i:[1]*4 for i in range(3)}
        a._get_heads_for_ace_slot=lambda *a:[]; a._t=lambda s,**k:s; a.log_warn=Mock(); a._disp=lambda n:n+1
        a._v2_filament_info_per_ace={}; a._host_tag_read=Mock(return_value=(True,'tag read'))
    def test_reader_conflict_and_independent_units(self):
        a=self.a; x=a._claim_native_tag(0,0,'read')
        self.assertIsNone(a._claim_native_tag(0,1,'read'))
        self.assertIsNotNone(a._claim_native_tag(0,2,'read'))
        self.assertIsNotNone(a._claim_native_tag(1,0,'read'))
        self.assertFalse(a._insert_tag_busy(3,0)); self.assertFalse(a._tag_read_busy)
        a._finish_native_tag(0,0,x); self.assertFalse(a._insert_tag_busy(0,1))
    def test_legacy_lock_does_not_block_native_reader(self):
        a=self.a; a._tag_read_busy=True
        self.assertTrue(a._insert_tag_busy(3,1)); self.assertFalse(a._insert_tag_busy(1,1))
    def test_old_completion_cannot_erase_newest_result(self):
        a=self.a; old=a._claim_native_tag(0,0,'read'); new=a._claim_native_tag(0,2,'read')
        result={'seq':new,'ok':True}; a._finish_native_tag(0,2,new,result)
        a._finish_native_tag(0,0,old,{'seq':old,'ok':False}); self.assertEqual(a._tag_op_result,result)
    def test_queue_skips_busy_reader(self):
        a=self.a; a._claim_native_tag(0,0,'read'); a._insert_read_queue=[(0,1,None),(1,1,None)]
        a._insert_drain_queue(); self.assertEqual(a._insert_read_queue,[(0,1,None)])
        a.reactor.register_async_callback.call_args.args[0](0); a._insert_tag_read_safe.assert_called_once_with(1,1,None)
    def test_manual_reads_have_separate_scope_and_result(self):
        a=self.a; cmd=Mock(); a._cmd_ace_tag_read_host(cmd,0,0); a._cmd_ace_tag_read_host(cmd,0,2); a._cmd_ace_tag_read_host(cmd,0,1)
        self.assertEqual(a.reactor.register_async_callback.call_count,2)
        self.assertEqual(len(a._native_tag_status()),2)
        for call in a.reactor.register_async_callback.call_args_list: call.args[0](0)
        self.assertEqual(a._native_tag_status(),[]); self.assertEqual(a._tag_op_result['seq'],2)
        self.assertEqual(a._host_tag_read.call_count,2)
    def test_insert_native_uses_scope_not_global_flag(self):
        a=self.a; a._tag_read_busy=True; a._host_lane_moved_mm=lambda *x:0; a.HOST_TAG_MOVED_MM=10
        a._insert_uid_read(0,0); a._host_tag_read.assert_called_once_with(0,0,load=True)
        self.assertTrue(a._tag_read_busy); self.assertEqual(a._native_tag_status(),[])

if __name__=='__main__':unittest.main()
