import importlib
import json
from pathlib import Path
import struct
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import Mock
from patch_native_worker import stage


class NativeExitWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        root=Path(cls.tmp.name)
        arc=Path(__file__).resolve().parents[1]/'multiace-190/artifacts/multiace-1.90.2-233.20261126.tar.gz'
        with tarfile.open(arc,'r:gz') as t:
            for name in ('ace_protocol.py','ace_protocol_ace2k.py'):
                (root/name).write_bytes(t.extractfile('multiace-1.90.2-233.20261126/klipper/extras/'+name).read())
        stage(root)
        pkg=types.ModuleType('native_worker_test'); pkg.__path__=[str(root)]; sys.modules[pkg.__name__]=pkg
        cls.driver=importlib.import_module(pkg.__name__+'.ace_protocol_ace2k')
    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()
    def setUp(self):
        self.now=[10.]; self.raw=types.SimpleNamespace(is_open=True,write=Mock(side_effect=len),close=Mock())
        link=self.link=self.driver.Ace2kLink(self.raw,clock=lambda:self.now[0]); link._ready=True
        link.uid=struct.pack('<3I',1,2,3); link._in_use=0
        link.dictionary=types.SimpleNamespace(has=lambda n:True,encode=Mock(return_value=b'payload'),constant=lambda n,d:d)
        link._xfer=Mock(return_value=True); link._wait=Mock(return_value=({'lane':0,'accepted':1,'reason':0},None))
        link._pump=Mock(); link._serve_sessions=Mock()
        link._tick=Mock(); link._tag_tick=Mock(); link._dryer_tick=Mock()
        self.uid='uid:00000001-00000002-00000003'; self.report(10)
    def report(self,t,modes=(0,0,0,0),switches=15,fg=(3,4,5,6),seqs=(16,16,16,16)):
        self.now[0]=t
        self.link._dispatch('ace2k_sensors_state',{'switches':switches,'age_ms':0})
        self.link._dispatch('ace2k_feed_state',{'mode':bytes(modes),'seq':bytes(seqs)})
        self.link._dispatch('ace2k_lane_counters',{'encoder_um':struct.pack('<4i',55,66,77,88),'fg':struct.pack('<4I',*fg)})
    def rpc(self,method,params):
        payload=json.dumps({'id':7,'method':'native_exit_'+method,'params':params}).encode()
        self.link.write(payload)
        return json.loads(self.link._out.get_nowait())
    def result(self,method,params):
        r=self.rpc(method,params); self.assertEqual(r['code'],0,r); return r['result']
    def prepare(self,lane=0):
        return self.result('prepare',{'index':lane,'uid':self.uid,'token':'task-'+str(lane)})
    def start(self):
        p=self.prepare(); self.report(11); self.assertEqual(self.result('status',p)['state'],'preparing')
        self.report(12); self.assertEqual(self.result('status',p)['state'],'ready')
        self.assertEqual(self.result('start',dict(p,speed_um_s=60000))['state'],'running'); return p
    def test_real_writer_queue_native_start_event_and_release(self):
        p=self.start(); self.raw.write.assert_called_once()
        self.link.dictionary.encode.assert_any_call('ace2k_feed_start',lane=0,mode=4,length_um=0,speed_um_s=60000,seq=17,free_tip=0)
        self.assertEqual(self.result('start',dict(p,speed_um_s=60000))['state'],'running')
        self.raw.write.assert_called_once()
        self.report(13,switches=14); self.link._dispatch('ace2k_feed_event',{'lane':0,'seq':17,'mode':3,'kind':6})
        self.report(14,switches=14); self.assertEqual(self.result('status',p)['state'],'verifying')
        self.report(15,switches=14); done=self.result('status',p)
        self.assertEqual(done['state'],'verified'); self.assertEqual(done['evidence']['event']['kind'],6)
        self.assertTrue(self.link._native_exit.blocked(0))
        self.assertNotEqual(self.rpc('release',dict(p,proof='wrong'))['code'],0)
        self.assertEqual(self.result('release',dict(p,proof=done['proof']))['state'],'released')
        self.assertFalse(self.link._native_exit.blocked(0))
        self.assertNotEqual(self.rpc('prepare',p)['code'],0)
    def test_four_jobs_start_before_any_completion(self):
        jobs=[self.prepare(n) for n in range(4)]; self.report(11)
        for p in jobs: self.assertEqual(self.result('status',p)['state'],'preparing')
        self.report(12)
        for n,p in enumerate(jobs):
            self.link._wait.return_value=({'lane':n,'accepted':1,'reason':0},None)
            self.assertEqual(self.result('start',dict(p,speed_um_s=60000))['state'],'running')
        self.assertEqual(self.raw.write.call_count,4)
    def test_stale_stop_never_targets_other_lane(self):
        p=self.start(); self.link.dictionary.encode.reset_mock()
        self.assertNotEqual(self.rpc('stop',dict(p,index=1))['code'],0)
        self.assertNotEqual(self.rpc('stop',dict(p,seq=16))['code'],0)
        self.assertEqual(self.result('stop',p)['state'],'stopping')
        self.link.dictionary.encode.assert_any_call('ace2k_feed_stop',lane=0)
        self.link._dispatch('ace2k_feed_event',{'lane':0,'seq':17,'mode':3,'kind':1})
        self.report(13); self.result('status',p); self.report(14)
        self.assertEqual(self.result('status',p)['state'],'stopped')
        self.assertTrue(self.link._native_exit.blocked(0))
    def test_lost_start_ack_not_replayed_and_holds_lane(self):
        p=self.prepare(); self.report(11); self.result('status',p); self.report(12)
        self.link._wait.return_value=(None,None)
        self.assertEqual(self.result('start',dict(p,speed_um_s=60000))['state'],'recovery')
        self.result('start',dict(p,speed_um_s=60000)); self.raw.write.assert_called_once()
        self.assertTrue(self.link._native_exit.blocked(0))
    def test_prepare_or_abort_never_moves(self):
        p=self.prepare(); self.assertEqual(self.result('abort',p)['state'],'aborted')
        self.raw.write.assert_not_called(); self.assertFalse(self.link._native_exit.blocked(0))
    def test_lost_terminal_query_waits_for_our_sequence(self):
        p=self.start(); self.report(13,switches=14)
        self.link.dictionary.encode.reset_mock()
        self.result('status',p)
        self.assertFalse(any(c.args[0]=='ace2k_feed_last_query' for c in self.link.dictionary.encode.call_args_list))
        self.report(14,switches=14,seqs=(17,16,16,16))
        self.link._xfer.return_value={'lane':0,'seq':17,'mode':3,'kind':6}
        self.result('status',p)
        self.link.dictionary.encode.assert_any_call('ace2k_feed_last_query',lane=0)
        self.report(15,switches=14,seqs=(17,16,16,16)); self.result('status',p)
        self.report(16,switches=14,seqs=(17,16,16,16))
        self.assertEqual(self.result('status',p)['state'],'verified')
    def test_empty_without_event_never_success(self):
        p=self.start(); self.report(13,switches=14)
        self.assertNotEqual(self.result('status',p)['state'],'verified')
        self.report(14,switches=14); self.assertNotEqual(self.result('status',p)['state'],'verified')
        self.assertTrue(self.link._native_exit.blocked(0))

if __name__=='__main__': unittest.main()
