import ast, pathlib, time, logging, unittest
from unittest.mock import patch

path=pathlib.Path(__file__).with_name('ace.py')
tree=ast.parse(path.read_text());cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='MultiAce')
method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_merge_v2_filament_info')
module=ast.Module(body=[method],type_ignores=[]);ast.fix_missing_locations(module);scope={'time':time,'logging':logging};exec(compile(module,str(path),'exec'),scope)
class Fake:
 _merge_v2_filament_info=scope['_merge_v2_filament_info']
 _V2_FILAMENT_INFO_PENDING_TTL=5.;_V2_FILAMENT_INFO_EMPTY_TTL=60.
 def __init__(self):
  self._protocols={0:type('P',(),{'NAME':'v2'})()};self._serials={0:object()};self._gen1_gate_gen={};self._v2_filament_info_per_ace={};self._v2_filament_info_pending={};self._v2_filament_info_empty={};self._v2_rfid_rescan_pending={};self._fa_log=logging.getLogger('fake');self.callbacks=[];self.stored=[]
 def _is_empty_status(self,s):return s=='empty'
 def _v2_rfid_boot_rescan(self,*a):pass
 def _v2_store_filament_read(self,idx,slot,res,host=False):self.stored.append((idx,slot,res))
 def send_request_to(self,idx,req,cb):self.callbacks.append(cb)
 def merge(self,status='ready',rfid=2):self._merge_v2_filament_info(0,{'slots':[{'status':status,'rfid':rfid}]})
 def reply(self,cb,name):cb(self=self,response={'result':{'type':name}})
class Tests(unittest.TestCase):
 def test_expired_old_reply_does_not_clear_or_replace_new(self):
  x=Fake()
  with patch('time.monotonic',side_effect=[10.,16.]):x.merge();old=x.callbacks[-1];x.merge();new=x.callbacks[-1]
  x.reply(old,'old');self.assertEqual(x._v2_filament_info_pending[0][0],16.);self.assertEqual(x.stored,[])
  x.reply(new,'new');self.assertEqual(x.stored[0][2]['type'],'new')
 def test_remove_reinsert_and_reconnect(self):
  x=Fake()
  with patch('time.monotonic',side_effect=[10.,11.,12.]):x.merge();old=x.callbacks[-1];x.merge('empty',2);x._gen1_gate_gen[(0,0)]=2;x.merge();new=x.callbacks[-1]
  x.reply(old,'old');self.assertFalse(x.stored);x._serials[0]=object();x.reply(new,'stale_connection');self.assertFalse(x.stored)
 def test_adjacent_slots_keep_independent_pending(self):
  x=Fake();x._merge_v2_filament_info(0,{'slots':[{'status':'ready','rfid':2},{'status':'ready','rfid':2}]})
  left,right=x.callbacks
  x._merge_v2_filament_info(0,{'slots':[{'status':'empty','rfid':2},{'status':'ready','rfid':2}]})
  x.reply(left,'left-old');x.reply(right,'right-current')
  self.assertEqual([(slot,res['type']) for _,slot,res in x.stored],[(1,'right-current')]);self.assertNotIn(0,x._v2_filament_info_pending[0])
 def test_heartbeat_order_empty_to_present_accepts_new_reply(self):
  x=Fake();x.merge('empty',0);x._gen1_gate_gen[(0,0)]=1
  x.merge('ready',2);new=x.callbacks[-1]
  # Actual heartbeat increments this unrelated counter only after merge.
  x._gen1_gate_gen[(0,0)]=2;x.reply(new,'new')
  self.assertEqual(x.stored[0][2]['type'],'new');self.assertNotIn(0,x._v2_filament_info_pending[0])
 def test_host_manual_read_wins_over_pending_device_reply(self):
  x=Fake();x.merge();old=x.callbacks[-1]
  x._v2_filament_info_per_ace[0][0]={'host':True,'type':'manual'}
  x.reply(old,'old');self.assertFalse(x.stored);self.assertEqual(x._v2_filament_info_per_ace[0][0]['type'],'manual')
 def test_normal_response(self):
  x=Fake();x.merge();x.reply(x.callbacks[-1],'PLA');self.assertEqual(x.stored[0][2]['type'],'PLA')
if __name__=='__main__':unittest.main()
