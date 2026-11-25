import ast, logging, pathlib, sys, types, unittest
p=pathlib.Path(__file__).parent
pkg=types.ModuleType('entry_candidate');pkg.__path__=[str(p)];sys.modules[pkg.__name__]=pkg
rc=types.ModuleType('entry_candidate.ace_rc522');rc.AceTagReader=lambda ace:object();sys.modules[rc.__name__]=rc
module=types.ModuleType('entry_candidate.ace_preload_integrated');sys.modules[module.__name__]=module
source=ast.parse((p/'ace.py').read_text())
node=next(n for n in ast.walk(source) if isinstance(n,ast.FunctionDef) and n.name=='_insert_uid_read')
mod=ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[]))
ns={'logging':logging,'V2_ACTIVE_MOTION_STATES':('feeding','rollback','preloading'),'__package__':'entry_candidate'}
exec(compile(mod,str(p/'ace.py'),'exec'),ns)
entry=ns['_insert_uid_read']
class EntryTest(unittest.TestCase):
 def setup_ace(self):
  self.ser=object();self.drains=[]
  return types.SimpleNamespace(printer=types.SimpleNamespace(lookup_object=lambda *a:types.SimpleNamespace(state='standby')),
   _tag_read_busy=False,_v2_filament_info_per_ace={0:{}},_insert_verified_abort=lambda *a:25,
   _v2_get_slot_status=lambda *a:'ready',_serials={0:self.ser},_stop_guard=types.SimpleNamespace(snapshot=lambda:None),
   _insert_slot_generation={(0,2):1,(0,3):1},_insert_drain_queue=lambda:self.drains.append(True))
 def test_pending_park_updates_volatile_metadata_and_returns_true(self):
  a=self.setup_ace()
  module.run_insert=lambda *args:{'state':'pending','park_verified':True,'uid':'55F328E6','identity':{'type':'PLA Basic','color':[1,2,3],'brand':'Bambu Lab'}}
  self.assertTrue(entry(a,0,2));self.assertEqual(a._insert_scan_results[(0,2)]['state'],'pending')
  self.assertEqual(a._v2_filament_info_per_ace[0][2]['uid'],'55F328E6')
  self.assertFalse(a._tag_read_busy);self.assertEqual(self.drains,[True])
 def test_sibling_change_requeues_verified_depth(self):
  a=self.setup_ace(); queued=[];a._insert_queue_push=lambda *args:queued.append(args)
  def run(*args):
   a._insert_slot_generation[(0,3)]=2
   return {'state':'cancelled','park_verified':False,'depth_mm':35}
  module.run_insert=run
  self.assertFalse(entry(a,0,2));self.assertEqual(queued,[(0,2,35)])
  self.assertFalse(hasattr(a,'_insert_scan_results'))
 def test_target_removed_does_not_requeue(self):
  a=self.setup_ace();queued=[];a._insert_queue_push=lambda *args:queued.append(args)
  def run(*args):
   a._insert_slot_generation[(0,2)]=2
   return {'state':'cancelled','park_verified':False,'depth_mm':35}
  module.run_insert=run
  self.assertFalse(entry(a,0,2));self.assertEqual(queued,[])
if __name__=='__main__':unittest.main()
