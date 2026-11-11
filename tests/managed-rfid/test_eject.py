import importlib.util,pathlib,sys,types,unittest
import atexit,tempfile,tarfile
REPO=pathlib.Path(__file__).resolve().parents[2]
ARCHIVE=REPO/'overlays/233-dev.2-perms.1/root/usr/local/share/233-firmware/multiace-managed-1.27b-233.20261111-single.5.tar.gz'
TMP=tempfile.TemporaryDirectory(prefix='managed-eject-test-');atexit.register(TMP.cleanup)
p=pathlib.Path(TMP.name)
with tarfile.open(ARCHIVE,'r:gz') as archive:
 for name in ('ace_slot_actions.py','ace_location_hold.py'):
  member=archive.getmember('multiace-1.27b-233.20261111-single.5/klipper/extras/'+name)
  (p/name).write_bytes(archive.extractfile(member).read())
pkg=types.ModuleType('ejectpkg');pkg.__path__=[str(p)];sys.modules['ejectpkg']=pkg
from ejectpkg.ace_slot_actions import SlotActions,MAX_MM,SPEED_MM_S
from ejectpkg.ace_location_hold import UID,PORT
class Reactor:
 def __init__(self):self.t=0
 def monotonic(self):return self.t
 def pause(self,t):self.t=t
class TestEject(unittest.TestCase):
 def test_continues_after_first_finite_segment_until_gate_empty(self):
  tr=object();r=Reactor();source={0:None,1:None,2:None,3:None};calls=[]
  a=types.SimpleNamespace(reactor=r,_rfid_route_heads=lambda idx,slot:{slot},_head_source=source,_ace_devices=[PORT],_serials={0:tr},_connected_per_ace={0:True},_serial_failed_per_ace={},_swap_in_progress=True,_location_hold=types.SimpleNamespace(active=False,valid_marker=True,blocks_route=lambda **kw:False,route_token=lambda *a:None),_normal_load_speed=types.SimpleNamespace(park=None),_save_head_source=lambda:None,_latch_stop_fault=lambda *args:self.fail(str(args)))
  x=SlotActions.__new__(SlotActions);x.ace=a;x.cancel=False;x.state={'seq':1,'state':'running','kind':'eject'};x._check=lambda *a,**kw:None;x._eject_lease=None;x._eject_pending=None
  stops=iter([True,True,False]);x._eject_stop_ready=lambda *args:next(stops)
  lanes=iter([{0:'rollback',1:'ready',2:'ready',3:'ready'},{0:'ready',1:'ready',2:'ready',3:'ready'},{0:'rollback',1:'ready',2:'ready',3:'ready'}])
  x._lane_states=lambda *args,**kw:next(lanes);gates=iter([True,True,False]);x._gate=lambda *args,**kw:next(gates)
  def req(idx,method,params,cmd,**kw):
   calls.append((method,dict(params)))
   if method=='discover_device':return {'uid1':1,'uid2':2,'uid3':3}
   return {}
  x._request=req;x._commit_ejected=lambda *a:None
  x._run(0,0,tr)
  self.assertEqual(x.state['state'],'complete',x.state)
  moves=[params for method,params in calls if method=='unwind_filament']
  self.assertEqual(moves,[{'index':0,'length':MAX_MM,'speed':80}]*2)
  self.assertNotIn('get_feed_info',[method for method,_ in calls]);self.assertNotIn('verified_mm',x.state)
 def test_no_cmd76_reply_still_completes_on_empty_and_stopped(self):
  tr=object();r=Reactor();sensors=[types.SimpleNamespace(runout_buttun_state=False) for _ in range(4)];sent=[];source={0:None,1:None,2:None,3:{'ace_index':0,'slot':3}}
  printer=types.SimpleNamespace(lookup_object=lambda n,d=None:types.SimpleNamespace(state='standby') if n=='print_stats' else (sensors[int(n.split(' e')[1][0])] if n.startswith('filament_motion_sensor e') else d))
  a=types.SimpleNamespace(reactor=r,printer=printer,_rfid_route_heads=lambda idx,slot:{slot},_head_source=source,_ace_devices=[PORT],_serials={0:tr},_connected_per_ace={0:True},_serial_failed_per_ace={},_swap_in_progress=True,_location_hold=types.SimpleNamespace(active=False,valid_marker=True,blocks_route=lambda **kw:False,route_token=lambda *a:None),_normal_load_speed=types.SimpleNamespace(park=None),_latch_stop_fault=lambda *x:self.fail('fault'))
  a.send_request_to=lambda idx,req,cb:sent.append(req)
  a._save_head_source=lambda:None
  x=SlotActions.__new__(SlotActions);a._slot_actions=x;x.ace=a;x.cancel=False;x.state={'seq':1,'state':'running','kind':'eject','progress':0};x._eject_lease=None;x._eject_pending=None
  x._check=lambda *args,**kwargs:None;stops=iter([True,False]);x._eject_stop_ready=lambda *args:next(stops);x._eject_stable=lambda *args:False;x._request=lambda idx,method,params,cmd,**kw:{'uid1':1,'uid2':2,'uid3':3} if method=='discover_device' else {};x._lane_states=lambda *args,**kw:{0:'ready',1:'ready',2:'ready',3:'rollback'}
  gates=iter([True]*15+[False]);x._gate=lambda *args,**kw:next(gates)
  x._commit_ejected=lambda *args:None
  x._run(0,3,tr)
  self.assertEqual(x.state['state'],'complete',x.state);self.assertNotIn('verified_mm',x.state);self.assertEqual(sum(q['method']=='get_feed_info' for q in sent),0)
 def test_request_permit_rejects_late_and_cancelled(self):
  tr=object();sensor=types.SimpleNamespace(runout_buttun_state=False);printer=types.SimpleNamespace(lookup_object=lambda n,d=None:types.SimpleNamespace(state='standby') if n=='print_stats' else sensor);source={0:None,1:None,2:None,3:{'ace_index':0,'slot':3}}
  a=types.SimpleNamespace(printer=printer,_serials={0:tr},_connected_per_ace={0:True},_serial_failed_per_ace={},_stop_guard=types.SimpleNamespace(message=lambda:None),_head_source=source)
  x=SlotActions.__new__(SlotActions);x.ace=a;x.state={'state':'running','kind':'eject'};x.cancel=False;x._eject_lease=(0,3,tr);x._eject_pending=object();x._eject_sources=dict(source);req={'method':'unwind_filament','params':{'index':3,'length':MAX_MM,'speed':SPEED_MM_S},'_eject_token':x._eject_pending}
  self.assertTrue(x.eject_allows(0,req));x.cancel=True;self.assertFalse(x.eject_allows(0,req));x.cancel=False;x.eject_consumed(req);self.assertFalse(x.eject_allows(0,req))

class TestAdmission(unittest.TestCase):
 def make(self):
  tr=object();pins=[types.SimpleNamespace(runout_buttun_state=False) for _ in range(4)];sources={0:None,1:{'ace_index':0,'slot':1},2:None,3:{'ace_index':0,'slot':3}}
  printer=types.SimpleNamespace(lookup_object=lambda n,d=None:types.SimpleNamespace(state='standby') if n=='print_stats' else (pins[int(n.split(' e')[1][0])] if n.startswith('filament_motion_sensor e') else d))
  a=types.SimpleNamespace(printer=printer,_head_source=sources,_ace_devices=[PORT],_serials={0:tr},_connected_per_ace={0:True},_fw_update_hold=set(),_swap_in_progress=False,_unload_all_active=False,_tag_read_busy=False,_gen1_manual_busy=False,_calibration={'state':'idle'},_calibration_unload={},_get_heads_for_ace_slot=lambda *x:[3],_is_v2_idx=lambda i:True,_require_no_stop_fault=lambda:None)
  x=SlotActions.__new__(SlotActions);x.ace=a;return x,pins,sources
 def test_historical_source_allowed_only_with_raw_clear(self):
  x,pins,sources=self.make();x._check(0,3,for_eject=True);pins[3].runout_buttun_state=True
  with self.assertRaisesRegex(RuntimeError,'喷头'):x._check(0,3,for_eject=True)
  pins[3].runout_buttun_state=None
  with self.assertRaisesRegex(RuntimeError,'未知'):x._check(0,3,for_eject=True)
 def test_loaded_rejected_at_public_start_before_stop_or_motor(self):
  x,pins,sources=self.make();pins[3].runout_buttun_state=True;x.state={'state':'idle'};x.cancel=False
  class G:
   def get_int(self,name,**kwargs):return 0 if name=='ACE' else 3
   def error(self,message):return RuntimeError(message)
  with self.assertRaisesRegex(RuntimeError,'喷头'):x.start(G())
  self.assertFalse(x.ace._swap_in_progress)
 def test_unrelated_known_loaded_head_allowed_unknown_refused(self):
  x,pins,sources=self.make();pins[1].runout_buttun_state=True;x._check(0,3,for_eject=True);sources[1]=None
  with self.assertRaisesRegex(RuntimeError,'未知'):x._check(0,3,for_eject=True)

class TestEjectedCommit(unittest.TestCase):
 def test_commit_exact_target_and_preserves_other_source(self):
  import tempfile,json,os,hashlib
  from unittest.mock import patch
  import ejectpkg.ace_slot_actions as m
  from ejectpkg.ace_location_hold import LocationHold
  with tempfile.TemporaryDirectory() as td:
   path=pathlib.Path(td)/'marker';doc={'reason':'position_unconfirmed_after_seek','ace_uid':list(UID),'slot':3,'head':3,'port':PORT};path.write_text(json.dumps(doc));os.chmod(path,0o600);old=path.read_bytes();sources={0:None,1:{'ace_index':0,'slot':1},2:None,3:{'ace_index':0,'slot':3}};speed=types.SimpleNamespace(park='old',record_park=lambda *a:self.fail('ejected cannot record park'))
   a=types.SimpleNamespace(_head_source=sources,_ace_devices=[PORT],_location_hold=LocationHold(str(path),str(path)+'.routes'),_normal_load_speed=speed,_save_head_source=lambda:None);x=SlotActions.__new__(SlotActions);x.ace=a
   with patch.object(m,'PATH',str(path)),patch.object(m,'LocationHold',lambda p=None,**kw:LocationHold(p or str(path),kw.get('routes_path') or str(path)+'.routes')):x._commit_ejected(0,3,object(),UID,hashlib.sha256(old).hexdigest(),sources)
   self.assertIsNone(a._head_source[3]);self.assertEqual(a._head_source[1]['slot'],1);self.assertIsNone(speed.park);self.assertFalse(a._location_hold.active);self.assertEqual(json.loads(path.read_text())['state'],'verified_ejected')
 def test_other_lane_cannot_change_target_marker(self):
  import tempfile,json,os
  from unittest.mock import patch
  import ejectpkg.ace_slot_actions as m
  with tempfile.TemporaryDirectory() as td:
   path=pathlib.Path(td)/'marker';path.write_text('original');old=path.read_bytes();sources={0:None,1:{'ace_index':0,'slot':1},2:None,3:{'ace_index':0,'slot':3}};a=types.SimpleNamespace(_head_source=sources,_ace_devices=[PORT],_location_hold=types.SimpleNamespace(active=True),_normal_load_speed=types.SimpleNamespace(park=None),_save_head_source=lambda:None);x=SlotActions.__new__(SlotActions);x.ace=a
   with patch.object(m,'PATH',str(path)):x._commit_ejected(0,1,object(),UID,None,sources)
   self.assertEqual(path.read_bytes(),old);self.assertIsNone(a._head_source[1]);self.assertEqual(a._head_source[3]['slot'],3)

class TestHeldFlow(unittest.TestCase):
 def test_pending_eject_no_decoder_commits_once_only_after_empty(self):
  import tempfile,json,os
  from unittest.mock import patch
  import ejectpkg.ace_slot_actions as m
  from ejectpkg.ace_location_hold import LocationHold
  with tempfile.TemporaryDirectory() as td:
   path=pathlib.Path(td)/'marker';doc={'reason':'position_unconfirmed_after_seek','ace_uid':list(UID),'slot':3,'head':3,'port':PORT};path.write_text(json.dumps(doc));os.chmod(path,0o600)
   tr=object();r=Reactor();sensors=[types.SimpleNamespace(runout_buttun_state=False) for _ in range(4)];source={0:None,1:{'ace_index':0,'slot':1},2:None,3:{'ace_index':0,'slot':3}};moves=[]
   printer=types.SimpleNamespace(lookup_object=lambda n,d=None:types.SimpleNamespace(state='standby') if n=='print_stats' else (sensors[int(n.split(' e')[1][0])] if n.startswith('filament_motion_sensor e') else d))
   a=types.SimpleNamespace(reactor=r,printer=printer,_rfid_route_heads=lambda idx,slot:{slot},_head_source=source,_ace_devices=[PORT],_serials={0:tr},_connected_per_ace={0:True},_serial_failed_per_ace={},_swap_in_progress=True,_location_hold=LocationHold(str(path),str(path)+'.routes'),_normal_load_speed=types.SimpleNamespace(park='reference'),_save_head_source=lambda:None,_latch_stop_fault=lambda *x:self.fail('fault'));a.send_request_to=lambda idx,req,cb:None
   x=SlotActions.__new__(SlotActions);a._slot_actions=x;x.ace=a;x.cancel=False;x.state={'seq':1,'state':'running','kind':'eject','progress':0};x._eject_lease=None;x._eject_pending=None;x._check=lambda *args,**kwargs:None;stops=iter([True,False]);x._eject_stop_ready=lambda *args:next(stops);x._eject_stable=lambda *args:False
   def req(idx,method,params,cmd,**kw):
    if method=='discover_device':return {'uid1':UID[0],'uid2':UID[1],'uid3':UID[2]}
    moves.append((method,dict(params)));return {}
   x._request=req;x._lane_states=lambda *args,**kw:{0:'ready',1:'ready',2:'ready',3:'rollback'};gates=iter([True]*15+[False]);x._gate=lambda *args,**kw:next(gates)
   with patch.object(m,'PATH',str(path)),patch.object(m,'LocationHold',lambda p=None,**kw:LocationHold(p or str(path),kw.get('routes_path') or str(path)+'.routes')):x._run(0,3,tr)
   self.assertEqual(x.state['state'],'complete',x.state);self.assertEqual(len(moves),1);self.assertNotIn('verified_mm',x.state);self.assertEqual(json.loads(path.read_text())['state'],'verified_ejected');self.assertIsNone(a._head_source[3]);self.assertIsNone(a._normal_load_speed.park)

class TestFailures(unittest.TestCase):
 def setup_run(self):
  tr=object();r=Reactor();source={0:None,1:None,2:None,3:None};a=types.SimpleNamespace(reactor=r,_rfid_route_heads=lambda idx,slot:{slot},_head_source=source,_ace_devices=[PORT],_serials={0:tr},_connected_per_ace={0:True},_serial_failed_per_ace={},_swap_in_progress=True,_location_hold=types.SimpleNamespace(active=False,valid_marker=True,blocks_route=lambda **kw:False,route_token=lambda *a:None),_normal_load_speed=types.SimpleNamespace(park=None));a._save_head_source=lambda:None;a.send_request_to=lambda *args:None;a.fault=[];a._latch_stop_fault=lambda *args:a.fault.append(args)
  x=SlotActions.__new__(SlotActions);x.ace=a;x.cancel=False;x.state={'seq':1,'state':'running','kind':'eject','progress':0};x._check=lambda *a,**kw:None;x._eject_lease=None;x._eject_pending=None;return x,a,tr
 def test_motor_ack_timeout_never_reissues_and_stop_failure_latches(self):
  x,a,tr=self.setup_run();calls=[];stops=[0]
  def stop(*args):
   stops[0]+=1
   if stops[0]==1:return True
   raise RuntimeError('stop unknown')
  x._eject_stop_ready=stop
  def req(idx,method,params,cmd,**kw):
   if method=='discover_device':return {'uid1':1,'uid2':2,'uid3':3}
   calls.append(method);raise RuntimeError('ACK timeout')
  x._request=req;x._run(0,3,tr)
  self.assertEqual(calls,['unwind_filament']);self.assertEqual(stops[0],2);self.assertTrue(a.fault);self.assertIn('停止未确认',x.state['message'])
 def test_cancelled_operation_does_not_report_success(self):
  x,a,tr=self.setup_run();x.cancel=True;x._run(0,3,tr);self.assertEqual(x.state['state'],'error')

class TestPendingBeforeMotion(unittest.TestCase):
 def test_verified_park_is_persistently_rearmed(self):
  import tempfile,json,os,hashlib
  from unittest.mock import patch
  import ejectpkg.ace_slot_actions as m
  from ejectpkg.ace_location_hold import LocationHold
  with tempfile.TemporaryDirectory() as td:
   path=pathlib.Path(td)/'marker';doc={'reason':'position_unconfirmed_after_seek','state':'verified_recovered','ace_uid':list(UID),'slot':3,'head':3,'port':PORT,'head_triggered':True,'head_clear':True,'gate_present':True,'stopped':True,'encoder_target_reached':True,'target_mm':1393};path.write_text(json.dumps(doc));os.chmod(path,0o600);a=types.SimpleNamespace(_location_hold=LocationHold(str(path),str(path)+'.routes'));x=SlotActions.__new__(SlotActions);x.ace=a
   with patch.object(m,'PATH',str(path)),patch.object(m,'LocationHold',lambda p=None,**kw:LocationHold(p or str(path),kw.get('routes_path') or str(path)+'.routes')):digest=x._arm_eject_pending(hashlib.sha256(path.read_bytes()).hexdigest())
   self.assertTrue(a._location_hold.active);self.assertEqual(json.loads(path.read_text())['state'],'eject_in_progress');self.assertEqual(digest,hashlib.sha256(path.read_bytes()).hexdigest())
