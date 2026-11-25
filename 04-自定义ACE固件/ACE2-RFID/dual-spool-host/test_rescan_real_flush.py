import ast,pathlib,time,logging,unittest
p=pathlib.Path(__file__).with_name('ace_rescan_candidate.py');tree=ast.parse(p.read_text());cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='MultiAce');names={'_v2_store_filament_read','_rescan_flush_binds'}
f=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in names];m=ast.Module(body=f,type_ignores=[]);ast.fix_missing_locations(m);scope={'time':time,'logging':logging,'UID_SENTINEL_VERSIONS':(0x0201,0x0102)};exec(compile(m,str(p),'exec'),scope)
class Fake:
 _v2_store_filament_read=scope['_v2_store_filament_read'];_rescan_flush_binds=scope['_rescan_flush_binds']
 def __init__(self):
  t=object();token=object();self._serials={0:t};self._v2_rescan_tokens={0:token};self._v2_cmd13_slot_epochs={(0,0):(True,1),(0,1):(False,1),(0,2):(True,1),(0,3):(False,1)};self._v2_rescan_contexts={0:{'transport':t,'token':token,'epochs':{0:(True,1),2:(True,1)},'partners':{0:(False,1),2:(False,1)}}};self._rescan_bind_targets={0:[0,2]};self._rescan_bind_pending={0:{2}};self._v2_filament_info_per_ace={0:{0:{'sku':'SM1'}}};self._v2_filament_info_empty={};self.bound=[]
 def _raw(self,n):return n
 def _resolve_tag_binds(self,idx,items):self.bound.extend(items)
 def complete_store(self):self._v2_store_filament_read(0,2,{'tag':{'field2':0x0102}},host=False)
class T(unittest.TestCase):
 def test_real_store_flush_binds_current_batch(self):
  x=Fake();x.complete_store();self.assertEqual([a[0] for a in x.bound],[0])
 def test_real_store_flush_rejects_reconnect(self):
  x=Fake();x._serials[0]=object();x.complete_store();self.assertEqual(x.bound,[])
 def test_real_store_flush_rejects_partner_epoch_change(self):
  x=Fake();x._v2_cmd13_slot_epochs[(0,1)]=(True,2);x.complete_store();self.assertEqual(x.bound,[])
if __name__=='__main__':unittest.main()
