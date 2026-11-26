import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock
from host_native_exit import HostNativeExit, ParkedProof
from test_native_exit_worker import NativeExitWorkerTests


class HostNativeExitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        NativeExitWorkerTests.setUpClass()
    @classmethod
    def tearDownClass(cls):
        NativeExitWorkerTests.tearDownClass()

    def setUp(self):
        f=self.fixture=NativeExitWorkerTests()
        f.setUp()
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'journal.json'
        a=self.ace=types.SimpleNamespace(
            reactor=types.SimpleNamespace(monotonic=lambda:f.now[0]),
            _serials={0:f.link},_connected_per_ace={0:True},
            _ace_devices=['usb0','usb1'],_unit_key=lambda n:f.uid if n==0 else 'uid:00000004-00000005-00000006',
            _protocols={0:types.SimpleNamespace(NAME='ace2k')},_ace_mode='multi',
            head_uses_ace=lambda n:True,printer=types.SimpleNamespace(lookup_object=lambda *a:types.SimpleNamespace(state='standby')),
            _get_heads_for_ace_slot=lambda *a:[],_bg_lanes_on=lambda *a:set(),
            _bg_staged={},_loading_heads=set(),_calibration_route_occupied=lambda:None)
        self.calls=[]
        self.queue=[]
        def send(idx,request,cb):
            self.calls.append((idx,request,cb))
            self.queue.append((idx,request,cb))
        a.send_request_to=send
        self.host=HostNativeExit(a,self.path)
        self.addCleanup(self.host.close)

    def proof(self,slot=0):
        f=self.fixture
        return ParkedProof(0,slot,f.uid,'usb0',f.link,[55,66,77,88][slot],[3,4,5,6][slot],f.now[0])

    def drain(self):
        while self.queue:
            idx,req,cb=self.queue.pop(0)
            response=self.fixture.rpc(req['method'].removeprefix('native_exit_'),req['params'])
            if cb: cb(self=self.ace,response=response)

    def tick(self,t,**kw):
        self.fixture.report(t,**kw)
        self.host.tick(t)
        self.drain()

    def test_real_writer_path_and_durable_completion(self):
        token=self.host.begin_parked(self.proof(),60000)
        self.assertEqual(self.host.owner.snapshot()[0].state,'reserved')
        self.drain()
        self.tick(11); self.tick(12)
        self.assertEqual(self.host.jobs[token].state,'running')
        self.assertEqual(self.host.owner.snapshot()[0].state,'dispatched')
        f=self.fixture
        f.report(13,switches=14)
        f.link._dispatch('ace2k_feed_event',{'lane':0,'seq':17,'mode':3,'kind':6})
        self.tick(14,switches=14); self.tick(15,switches=14)
        self.assertEqual(self.host.jobs[token].state,'complete')
        self.assertEqual(self.host.owner.snapshot(),())

    def test_independent_lane_not_locked_by_native_exit(self):
        a=self.host.begin_parked(self.proof(0),60000)
        b=self.host.begin_parked(self.proof(1),60000)
        self.assertNotEqual(a,b)
        self.assertEqual(self.host.mask(0),3)
        self.assertFalse(self.host.blocks(0,{'method':'feed_filament','params':{'index':2}}))
        self.assertTrue(self.host.blocks(0,{'method':'feed_filament','params':{'index':1}}))
        self.assertFalse(self.host.blocks(0,{'method':'stop_feed_filament','params':{'index':1}}))

    def test_counter_change_never_sends_start(self):
        token=self.host.begin_parked(self.proof(),60000); self.drain()
        self.tick(11,fg=(4,4,5,6))
        self.assertEqual(self.host.jobs[token].state,'recovery')
        self.fixture.raw.write.assert_not_called()
        self.assertEqual(self.host.mask(0,True),1)

    def test_reconnect_holds_only_affected_lane(self):
        token=self.host.begin_parked(self.proof(),60000); self.drain()
        self.ace._serials[0]=object()
        self.host.tick(11)
        self.assertEqual(self.host.jobs[token].state,'recovery')
        self.assertEqual(self.host.mask(0,True),1)
        self.assertFalse(self.host.blocks(0,{'method':'tag_read_load','params':{'index':1}}))

    def test_recovery_on_restart_preserves_slot_claim(self):
        self.host.begin_parked(self.proof(),60000)
        self.host.close()
        reopened=HostNativeExit(self.ace,self.path)
        try:
            self.assertEqual(reopened.mask(0,True),1)
            self.assertEqual(reopened.status()[0]['state'],'recovery')
        finally:
            reopened.close()

    def test_unknown_route_refused(self):
        self.ace._get_heads_for_ace_slot=lambda *a:[0]
        with self.assertRaises(ValueError):
            self.host.begin_parked(self.proof(),60000)
        self.assertEqual(self.queue,[])
        self.assertEqual(self.host.owner.snapshot(),())

    def test_cancel_before_dispatch(self):
        token=self.host.begin_parked(self.proof(),60000); self.drain()
        self.assertTrue(self.host.cancel(token)); self.drain()
        self.assertEqual(self.host.jobs[token].state,'cancelled')
        self.fixture.raw.write.assert_not_called()
        self.assertEqual(self.host.owner.snapshot(),())

if __name__=='__main__':unittest.main()
