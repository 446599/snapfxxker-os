#!/usr/bin/env python3
"""Offline checks for ACE2 manual RFID motion and attribution. No hardware."""
import importlib.util
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'multiace/klipper/extras/ace_rc522.py'
spec=importlib.util.spec_from_file_location('rc_safety',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
U='35E665E5'; V='55F328E6'
class Fake(m.AceTagReader):
    def __init__(self, replies=()):
        super().__init__(None); self.replies=list(replies);self.calls=[];self.last=None
        self.observations=[];self.notes=[];self.ready=True
    def _pause(self, seconds): pass
    def _wait_motion_ready(self, idx, slot, timeout=15): return self.ready
    def _target_motion_allowed(self, idx, slot): return True
    def _req(self, idx, method, params, timeout=3):
        self.calls.append((method,dict(params)))
        if method == 'feed_or_rollback_raw':
            self.last=dict(params)
            r=self.replies.pop(0) if self.replies else {'code':0,'msg':'success'}
            if isinstance(r,dict):r=dict(r,_v2_payload_valid=True)
            return r
        if method == 'get_feed_info':
            signed=self.last['length'] * (-1 if self.last['mode'] else 1)
            return {'code':0,'_v2_payload_valid':True,'result':{'_feed_wire_valid':True,'feed_info':[{'index':self.last['index'],'decoder':signed,'_decoder_present':True}]}}
        raise AssertionError(method)
    def _observed_uid(self, idx, slot): return self.observations.pop(0)
    def _neighbour_signature(self, idx, slot): return ('stationary',)
    def say(self, msg): self.notes.append(msg)

def expect_unsafe(fn):
    try:fn()
    except m.MotionUncertain:return
    raise AssertionError('unsafe motion was not stopped')

f=Fake([None]);expect_unsafe(lambda:f._move(0,1,20,0,f.say))
assert len(f.calls)==1 and not f._motion_ledger
expect_unsafe(lambda:f._move(0,1,20,1,f.say));assert len(f.calls)==1
f=Fake([{'code':2,'msg':'busy'},{'code':0,'msg':'success'}]);assert f._move(0,1,20,0,f.say);assert len(f.calls)==2
f=Fake([{'code':7,'msg':'error'}]);assert not f._move(0,1,20,0,f.say);assert len(f.calls)==1
f=Fake([{'msg':'success'}]);expect_unsafe(lambda:f._move(0,1,20,0,f.say));assert len(f.calls)==1
f=Fake();assert not f._move(0,1,-40,1,f.say);assert not f.calls
f=Fake();assert f._move_and_confirm(0,1,20,0,f.say);assert f._motion_ledger[(0,1)]==20
f._restore_recorded_motion(f.say);assert f._motion_ledger[(0,1)]==0 and f._encoder_ledger[(0,1)]==0
f=Fake();assert f._move_and_confirm(0,1,40,1,f.say);f._restore_recorded_motion(f.say)
assert f._motion_ledger[(0,1)]==0 and [c[1]['mode'] for c in f.calls if c[0]=='feed_or_rollback_raw']==[1,0]
f=Fake();f._move_and_confirm(0,1,20,0,f.say);f._motion_uncertain='ack missing';n=len(f.calls);f._restore_recorded_motion(f.say);assert len(f.calls)==n
f=Fake();f.observations=[U,U,'','','',U,U]*2;assert f._rotation_test(0,1,U,f.say)=='ours';assert f._motion_ledger[(0,1)]==0
f=Fake();f.observations=[U,U,None,'',''];assert f._rotation_test(0,1,U,f.say) is None;assert f._motion_ledger[(0,1)]==0
f=Fake();f.observations=[U,U,U,U,U];assert f._rotation_test(0,1,U,f.say) is None;assert f._motion_ledger[(0,1)]==0
f=Fake([{'code':0},{'code':7}]);f.observations=[U,U,'','',''];expect_unsafe(lambda:f._rotation_test(0,1,U,f.say));assert f._motion_ledger[(0,1)]==160
f=Fake();assert f._clear_neighbour(0,1,f.say) is False;assert not f.calls
class Changed(Fake):
    def _rc_stable_uid(self,*a):return '',None
    def _selected_single_uid(self,*a):return V
    def _identify(self,*a):raise AssertionError('wrong UID advanced to identify')
    def _read_bambu_classic(self,*a):raise AssertionError('wrong UID authenticated')
f=Changed();assert f._read_card(0,1,f.say,expected_uid=U,allow_nudges=False)==0
assert any('UID changed' in x for x in f.notes)
class Manual(Fake):
    def __init__(self, attribution):super().__init__();self.selects=[1,0];self.attribution=attribution;self.ingested=[]
    def _rc(self, idx, slot, op, a1=0, a2=0):assert op==6;return self.selects.pop(0)
    def _observed_uid(self, idx, slot):return U
    def _neighbour_empty(self, idx, slot):return False
    def _rotation_test(self, idx, slot, uid, respond):self.calls.append(('attribution',uid));return self.attribution
    def _read_card(self, idx, slot, respond, **kw):self.ingested.append(kw);return 0
f=Manual(None);f.read_slot(0,1,f.say,max_mm=200);assert not f.ingested and f._motion_ledger[(0,1)]==0
assert ('attribution',U) in f.calls
f=Manual('ours');f.read_slot(0,1,f.say,max_mm=200);assert f.ingested==[{'expected_uid':U,'allow_nudges':False}]
assert f._motion_ledger[(0,1)]==0
print('PASS: uncertain ACK, explicit busy, signed ledger, feedback, attribution, rollback, neighbour isolation, UID change')

class BadEncoder(Fake):
    def _req(self, idx, method, params, timeout=3):
        if method=='get_feed_info':
            self.calls.append((method,dict(params)))
            return {'code':0,'_v2_payload_valid':True,'result':{'_feed_wire_valid':True,'feed_info':[{'index':slot,'decoder':0,'_decoder_present':True} for slot in range(4)]}}
        return super()._req(idx,method,params,timeout)
f=BadEncoder();expect_unsafe(lambda:f._move_and_confirm(0,1,20,0,f.say))
n=len(f.calls);f._restore_recorded_motion(f.say);assert len(f.calls)==n
class AbortRead(Manual):
    def _read_card(self,*a,**kw):raise RuntimeError('simulated decoder crash')
f=AbortRead('ours')
try:f.read_slot(0,1,f.say,max_mm=200)
except RuntimeError as e:assert str(e)=='simulated decoder crash'
else:raise AssertionError('read failure not propagated')
assert f._motion_ledger[(0,1)]==0
class StopFake(Fake):
    def __init__(self, stop, values):
        super().__init__();self.stop=stop;self.values=list(values);self.ace=self
    def _req(self,idx,method,params,timeout=3):
        if method=='stop_feed_filament':
            self.calls.append((method,dict(params)))
            return dict(self.stop,_v2_payload_valid=True) if isinstance(self.stop,dict) else self.stop
        return super()._req(idx,method,params,timeout)
    def _fresh_encoder(self,idx,slot):return self.values.pop(0)
f=StopFake({'code':0,'msg':'success'},[110,110]);assert f._stop_and_measure(0,1,600,f.say)==110
for stop,vals in [(None,[]),({'code':2},[]),({'code':0},[None,None]),({'code':0},[110,111]),({'code':0},[601,601]),({'code':0},[0,0])]:
    f=StopFake(stop,vals);expect_unsafe(lambda:f._stop_and_measure(0,1,600,f.say))
    assert len([c for c in f.calls if c[0]=='stop_feed_filament'])==1
print('PASS: stop acknowledgement, stable encoder, nested read failure, feedback mismatch')

class Residual(Fake):
    def _fresh_encoder(self, idx, slot):return 80 if self.last['mode']==0 else -240
f=Residual();f.observations=[U,U,'','',''];expect_unsafe(lambda:f._rotation_test(0,1,U,f.say))
assert f._encoder_ledger[(0,1)]==-160 and f._motion_uncertain
class NeighbourMoves(Fake):
    def __init__(self):super().__init__();self.n=0
    def _neighbour_signature(self,idx,slot):
        self.n+=1;return ('stable',) if self.n<=3 else ('changed',)
f=NeighbourMoves();f.observations=[U,U];expect_unsafe(lambda:f._rotation_test(0,1,U,f.say))
assert f._motion_ledger[(0,1)]==160 and len([x for x in f.calls if x[0]=='feed_or_rollback_raw'])==1
# Real protocol decoder, not only hand-built callback dictionaries.
import sys,types,importlib
pkg=types.ModuleType('rc_safety_extras');pkg.__path__=[str(p.parent)];sys.modules[pkg.__name__]=pkg
v=importlib.import_module(pkg.__name__+'.ace_protocol_v2');codec=object.__new__(v.AceProtocolV2)
def field(n,payload):return v.pb_varint((n<<3)|2)+v.pb_varint(len(payload))+payload
assert not codec._v2_response_to_v1(v.Cmd.GET_STATUS,1,b'\x08\x80')['_v2_payload_valid']
valid=field(9,b'')*4
z=codec._v2_response_to_v1(v.Cmd.GET_STATUS,1,valid)
assert z['_v2_payload_valid'] and z['result']['_status_wire_valid']
z=codec._v2_response_to_v1(v.Cmd.GET_STATUS,1,field(9,b'')*3)
assert not z['result']['_status_wire_valid']
z=codec._v2_response_to_v1(v.Cmd.GET_STATUS,1,field(9,b'\x08\x80')+field(9,b'')*3)
assert not z['result']['_status_wire_valid']
z=codec._v2_response_to_v1(v.Cmd.GET_FEED_INFO,1,field(1,v.pb_uint32(2,20))*4)
assert z['result']['_feed_wire_valid'] and not z['result']['feed_info'][0]['_decoder_present']
print('PASS: neighbour movement, round-trip encoder residual, malformed and missing protobuf fields')

class GenericMismatch(Fake):
    def __init__(self):super().__init__();self.ace=object()
    def _rc_stable_uid(self,*a):return '04225251C82A81',None
    def _identify(self,*a):return {'type':'PETG','sku':'unrelated','code':0}
    def _finish_present(self,*a):raise AssertionError('unverified identity stored')
f=GenericMismatch();f._read_card(0,1,f.say,expected_uid='04225251C82A81',allow_nudges=False)
assert any('generic tag identity is not verified' in x for x in f.notes)
class Snapshot(Fake):
    _neighbour_signature=m.AceTagReader._neighbour_signature
    def __init__(self,valid=True,moving=False):super().__init__();self.valid=valid;self.moving=moving
    def _req(self,idx,method,params,timeout=3):
        if method=='get_status':
            lanes=[{'index':i,'status':'unknown','slot_status':'ready'} for i in range(4)]
            if self.moving:lanes[0]['slot_status']='feeding'
            return {'code':0,'_v2_payload_valid':True,'result':{'_status_wire_valid':self.valid,'slots':lanes}}
        if method=='get_feed_info':
            lanes=[{'index':i,'steps':0,'length':0,'decoder':0,'_decoder_present':False} for i in range(4)]
            return {'code':0,'_v2_payload_valid':True,'result':{'_feed_wire_valid':True,'feed_info':lanes}}
        raise AssertionError(method)
assert Snapshot()._neighbour_signature(0,1) is not None
assert Snapshot(valid=False)._neighbour_signature(0,1) is None
assert Snapshot(moving=True)._neighbour_signature(0,1) is None
print('PASS: generic identity source rejected; fresh neighbour snapshot fails closed')

class Loaded(Fake):
    def _target_motion_allowed(self,idx,slot):return False
f=Loaded();assert not f._move_and_confirm(0,1,160,0,f.say);assert not f.calls
class GenericSentinel(GenericMismatch):
    def _identify(self,*a):return {'type':'PLA Basic','sku':'SM04225251C82A81','tag':{'field2':0x0102},'code':0}
    def _read_own_anycubic(self,*a):raise AssertionError('unverified NTAG raw read')
f=GenericSentinel();f._read_card(0,1,f.say,expected_uid='04225251C82A81',allow_nudges=False)
assert any('generic NTAG identity cannot' in x for x in f.notes)
print('PASS: loaded target refused; generic NTAG sentinel not ingested')

class PreflightFails(Manual):
    def _wait_motion_ready(self,idx,slot,timeout=15):return False
f=PreflightFails('ours');f.read_slot(0,1,f.say,max_mm=200)
assert not f.calls and not f.ingested and not f._motion_ledger
print('PASS: mixed-codec or unsafe-motion preflight prevents any move')

class AggregateDrift(Fake):
    def _fresh_encoder(self,idx,slot):return 160 if self.last['mode']==0 else -143
f=AggregateDrift();f.observations=[U,U,'','','',U,U]*2
expect_unsafe(lambda:f._rotation_test(0,1,U,f.say))
assert f._motion_ledger[(0,1)]==0 and f._encoder_ledger[(0,1)]==34
f=Fake();f._motion_ledger[(0,1)]=0;f._encoder_ledger[(0,1)]=34
expect_unsafe(lambda:f._restore_recorded_motion(f.say));assert not f.calls
print('PASS: aggregate encoder drift rejected even with zero command balance')
