import importlib, pathlib, sys, types, unittest
p=pathlib.Path(__file__).parent
pkg=types.ModuleType('preload_rf_candidate');pkg.__path__=[str(p)];sys.modules[pkg.__name__]=pkg
m=importlib.import_module('preload_rf_candidate.ace_preload_rf')
from ace_load_rf import RfGate
class Reactor:
    def __init__(self): self.t=0
    def monotonic(self): return self.t
class Reader:
    def __init__(self): self.state={0:0,2:0};self._rf_token=None;self.calls=[];self.selected=1;self.bits=1<<8
    def _pause(self,t): pass
    def _req(self,idx,method,params,timeout):
        self.calls.append((method,dict(params)))
        result={}
        if method=='get_info': result={'firmware':'V1.1.93O'}
        if method=='discover_device': result=dict(zip(('uid1','uid2','uid3'),m.EXPECTED_UID))
        if method=='get_status': result={'_status_wire_valid':True,'slots':[{'index':i,'slot_status':'ready'} for i in range(4)],'rfid1_enable_raw':1,'rfid2_enable_raw':1}
        if method=='get_key_state': result={'fields':{'1':self.bits}}
        if method=='set_rfid_enable': self.state[params['index']]=0 if params['enable'] else 8
        if method=='filament_identify':
            word=params['index'];result={'index':word,'code':self.state[(word>>8)&3]}
        return {'code':0,'_v2_payload_valid':True,'result':result}
    def _rc(self,*args): return self.selected
    def _selected_single_uid(self,*args): return '55F328E6'
class RFTest(unittest.TestCase):
    def build(self,version='V1.1.93O'):
        ser=object();reader=Reader();ace=types.SimpleNamespace(_serials={0:ser},_ace_models={0:('ACE',version)},_rf_gate=RfGate(),reactor=Reactor())
        return m.PreloadRf(ace,reader,0,2,lambda:True),reader
    def test_two_readers_disabled_and_restored(self):
        rf,r=self.build();self.assertTrue(rf.acquire());self.assertEqual(r.state,{0:8,2:8})
        rf.release();self.assertEqual(r.state,{0:0,2:0})
        self.assertIsNone(r._rf_token)
    def test_unknown_select_is_not_absence(self):
        rf,r=self.build();rf.acquire();self.assertEqual(rf.observe(),('ambiguous',None));rf.release()
    def test_typed_verified_timer_is_absence_unknown_is_not(self):
        rf,r=self.build();rf.acquire();r._preload_rf_bounded_v1=True
        r._preload_probe_presence=lambda *a:{'source':'raw_reqa_v1','state':'not_detected','verified':True}
        self.assertEqual(rf.observe(),('absent',None))
        r._preload_probe_presence=lambda *a:{'source':'raw_reqa_v1','state':'not_detected','verified':False}
        self.assertEqual(rf.observe(),('ambiguous',None));rf.release()
    def test_sibling_presence_conflicts(self):
        rf,r=self.build();rf.acquire();r.bits|=1<<12
        self.assertEqual(rf.observe(),('conflict',None));rf.release()
    def test_fresh_firmware_mismatch_never_sends_private_opcode(self):
        rf,r=self.build(); original=r._req
        def request(idx,method,params,timeout):
            if method == 'get_info':
                r.calls.append((method,dict(params)))
                return {'code':0,'_v2_payload_valid':True,'result':{'firmware':'V1.1.94O'}}
            return original(idx,method,params,timeout)
        r._req=request
        with self.assertRaises(m.RfUnavailable): rf.acquire()
        self.assertFalse(any(method=='filament_identify' for method,_ in r.calls))
    def test_unbounded_legacy_select_is_not_called(self):
        rf,r=self.build();rf.acquire()
        r._rc=lambda *a: self.fail('unbounded SELECT called')
        self.assertEqual(rf.observe(),('ambiguous',None));rf.release()
    def test_old_firmware_never_sends_private_opcode(self):
        rf,r=self.build('V1.1.60O')
        with self.assertRaises(m.RfUnavailable):rf.acquire()
        self.assertEqual(r.calls,[])

if __name__=='__main__':unittest.main()
