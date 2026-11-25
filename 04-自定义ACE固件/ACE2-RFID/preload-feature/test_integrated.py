import importlib, pathlib, sys, types, unittest
p=pathlib.Path(__file__).parent
pkg=types.ModuleType('preload_integrated_candidate'); pkg.__path__=[str(p)]; sys.modules[pkg.__name__]=pkg
m=importlib.import_module('preload_integrated_candidate.ace_preload_integrated')

class IO:
    def __init__(self, ace, reader, idx, slot, valid):
        self.total=0; self.identity_uid='55F328E6'; self.identity_data={'type':'PLA Basic','color':[1,2,3],'brand':'Bambu Lab'}
        self.t=0; self.valid_fn=valid; self.rf=types.SimpleNamespace(active=True)
        self.rf.release=lambda: setattr(self.rf,'active',False)
    def now(self): return self.t
    def ready(self, session): return self.valid_fn()
    def move_and_stop(self, session, amount):
        assert self.rf.active, "RF lease must survive parking"
        self.total+=amount; self.t+=1; return amount

class Scan:
    result=None
    def __init__(self,io,**kw): self.io=io
    def run(self,session):
        self.io.total+=10
        return self.result

class Integrated(unittest.TestCase):
    def setUp(self):
        self.old_io,self.old_scan=m.InsertIO,m.SegmentedPreload
        m.InsertIO,m.SegmentedPreload=IO,Scan
        self.faults=[];self.ace=types.SimpleNamespace(_latch_stop_fault=lambda *x:self.faults.append(x))
    def tearDown(self): m.InsertIO,m.SegmentedPreload=self.old_io,self.old_scan
    def test_real_entry_parks_pending_from_verified_depth(self):
        Scan.result=types.SimpleNamespace(status='pending',uid='55F328E6',circumference_mm=None)
        r=m.run_insert(self.ace,None,0,2,360,lambda:True)
        self.assertTrue(r['park_verified']); self.assertEqual(r['depth_mm'],390)
        self.assertEqual(r['state'],'pending'); self.assertNotIn('remaining_g',r)
    def test_real_entry_measured_uses_geometry_not_nominal_weight(self):
        Scan.result=types.SimpleNamespace(status='interval',uid='55F328E6',circumference_mm=500)
        r=m.run_insert(self.ace,None,0,2,360,lambda:True)
        self.assertEqual(r['state'],'interval'); self.assertGreater(r['remaining_g_max'],0)
        self.assertNotEqual(r['remaining_g_max'],1000)
    def test_unknown_depth_never_moves(self):
        r=m.run_insert(self.ace,None,0,2,None,lambda:True)
        self.assertFalse(r['park_verified'])
    def test_session_invalidated_never_publishes_park(self):
        Scan.result=types.SimpleNamespace(status='pending',uid=None,circumference_mm=None)
        calls=iter([True,False]); r=m.run_insert(self.ace,None,0,2,360,lambda:next(calls,False))
        self.assertFalse(r['park_verified'])

if __name__=='__main__':unittest.main()
