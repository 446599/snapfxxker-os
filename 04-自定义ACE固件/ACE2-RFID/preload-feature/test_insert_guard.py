"""Exercise live-derived insert failure paths without importing Klipper."""
import ast,logging,types,unittest
from pathlib import Path
p=Path(__file__).with_name('ace.py'); tree=ast.parse(p.read_text())
def load(name):
 node=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==name)
 mod=ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[]))
 ns={'logging':logging,'INSERT_GRAB_WAIT_S':5,'INSERT_GRAB_MM':20,'INSERT_ABORT_VERIFY_S':10,'INSERT_DEPTH_MAX':900}
 exec(compile(mod,str(p),'exec'),ns);return ns[name]
class Guard(unittest.TestCase):
 def test_unaccepted_stop_latches(self):
  events=[]
  def latch(i,s,r):events.append((i,s,r))
  def require(): raise RuntimeError('latched')
  o=types.SimpleNamespace(_v2_slot_rfid=lambda i,s:0, reactor=types.SimpleNamespace(monotonic=lambda:0),
    _read_decoder=lambda i,s:20,_stop_feeding=lambda s,idx=None:False,
    _latch_stop_fault=latch,_require_no_stop_fault=require)
  with self.assertRaises(RuntimeError):load('_insert_verified_abort')(o,0,2)
  self.assertEqual(events,[(0,2,'insert STOP unconfirmed')])
 def test_insert_failure_never_preloads(self):
  events=[]
  def fail(*args):raise RuntimeError('fail')
  o=types.SimpleNamespace(_insert_uid_read=fail,_pre_load=lambda *a,**k:events.append('preload'),_serials={0:object()})
  load('_insert_read_then_preload')(o,0,2)
  self.assertEqual(events,[]);self.assertEqual(o._insert_read_running,set())
 def test_latched_fault_discards_queued_inserts(self):
  q=[(0,2,0),(0,3,0)]
  o=types.SimpleNamespace(_insert_read_queue=q,_stop_guard=types.SimpleNamespace(snapshot=lambda:{'fault':True}))
  load('_insert_drain_queue')(o)
  self.assertEqual(q,[])

 def test_deferred_insert_never_preloads(self):
  events=[]
  o=types.SimpleNamespace(_insert_uid_read=lambda *args:False,_pre_load=lambda *a,**k:events.append('preload'),_serials={0:object()})
  load('_insert_read_then_preload')(o,0,2)
  self.assertEqual(events,[]);self.assertEqual(o._insert_read_running,set())

 def test_scheduled_callback_drops_after_fault_remove_or_reconnect(self):
  for change in ('fault','slot','connection','none'):
   calls=[]; scheduled=[]; conn=object()
   o=types.SimpleNamespace(_insert_fault_epoch=0,_insert_slot_generation={(0,2):1},
      _serials={0:conn},_stop_guard=types.SimpleNamespace(snapshot=lambda:None),
      reactor=types.SimpleNamespace(register_async_callback=lambda f:scheduled.append(f)))
   load('_queue_insert_callback')(o,lambda *args:calls.append(args),0,2,3)
   if change=='fault':o._insert_fault_epoch+=1
   if change=='slot':o._insert_slot_generation[(0,2)]+=1
   if change=='connection':o._serials[0]=object()
   scheduled[0](0)
   self.assertEqual(calls,[(0,2,3)] if change=='none' else [])

 def test_queued_insert_removed_and_reinserted_is_dropped(self):
  conn=object(); scheduled=[]
  o=types.SimpleNamespace(_insert_read_queue=[],_insert_fault_epoch=0,_insert_slot_generation={(0,2):1},
      _serials={0:conn},_stop_guard=types.SimpleNamespace(snapshot=lambda:None),
      printer=types.SimpleNamespace(lookup_object=lambda *args:None),
      reactor=types.SimpleNamespace(register_async_callback=lambda f:scheduled.append(f)),
      _insert_queue_stale_reason=lambda *args:None)
  load('_insert_queue_push')(o,0,2,10)
  o._insert_slot_generation[(0,2)]=3
  load('_insert_drain_queue')(o)
  self.assertEqual(o._insert_read_queue,[]);self.assertEqual(scheduled,[])

 def test_cached_rfid_does_not_prove_park(self):
  events=[]
  def require(): raise RuntimeError('latched')
  o=types.SimpleNamespace(_v2_slot_rfid=lambda i,s:2,_latch_stop_fault=lambda *a:events.append(a),
                          _require_no_stop_fault=require,_disp=lambda x:x)
  with self.assertRaises(RuntimeError):load('_insert_verified_abort')(o,0,2)
  self.assertEqual(events[0][2],'insert position or stop unconfirmed')
 def test_implausible_depth_never_guessed(self):
  events=[];counter=[0];clock=[0.0]
  def decoder(i,s):
   counter[0]+=1
   return 20 if counter[0]==1 else 1200
  def pause(when):clock[0]=when
  o=types.SimpleNamespace(_v2_slot_rfid=lambda i,s:0,reactor=types.SimpleNamespace(monotonic=lambda:clock[0],pause=pause),
     _read_decoder=decoder,_stop_feeding=lambda s,idx=None:True,_disp=lambda x:x,
     _latch_stop_fault=lambda *a:events.append(a),_require_no_stop_fault=lambda:(_ for _ in ()).throw(RuntimeError('latched')))
  with self.assertRaises(RuntimeError):load('_insert_verified_abort')(o,0,2)
  self.assertEqual(events[0][2],'insert position or stop unconfirmed')

if __name__=='__main__':unittest.main()
