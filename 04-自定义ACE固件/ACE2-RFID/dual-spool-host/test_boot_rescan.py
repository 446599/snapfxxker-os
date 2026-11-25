import ast, pathlib, time, logging, unittest
path=pathlib.Path(__file__).with_name('ace_rescan_candidate.py');tree=ast.parse(path.read_text());cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='MultiAce');names={'_merge_v2_filament_info','_v2_rfid_boot_rescan'}
functions=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in names];mod=ast.Module(body=functions,type_ignores=[]);ast.fix_missing_locations(mod);scope={'time':time,'logging':logging,'RESCAN_BIND_WINDOW':5};exec(compile(mod,str(path),'exec'),scope)
class Reactor:
 NEVER=-1
 def __init__(self):self.timers=[]
 def register_timer(self,cb,when):self.timers.append(cb)
 def monotonic(self):return 0
class Fake:
 _merge_v2_filament_info=scope['_merge_v2_filament_info'];_v2_rfid_boot_rescan=scope['_v2_rfid_boot_rescan'];_V2_FILAMENT_INFO_PENDING_TTL=5;_V2_FILAMENT_INFO_EMPTY_TTL=60
 def __init__(self):
  self._protocols={0:type('P',(),{'NAME':'v2'})()};self._serials={0:object()};self._v2_filament_info_per_ace={};self._v2_filament_info_pending={};self._v2_filament_info_empty={};self._v2_rfid_rescan_pending={0:True};self._rescan_bind_targets={};self._rescan_bind_pending={};self._rc_last_uid={};self.reactor=Reactor();self.callbacks=[];self.stored=[];self.flushed=[]
 def _is_empty_status(self,s):return s=='empty'
 def _raw(self,x):return x
 def _sku_canon(self,x):return (x or '').upper()
 def _persist_tag_reads(self):pass
 def _rescan_flush_binds(self,idx):self.flushed.append(idx)
 def _v2_store_filament_read(self,idx,slot,res,host=False):self.stored.append((idx,slot,res))
 def send_request_to(self,idx,req,cb):self.callbacks.append((req,cb))
 def merge(self,state='ready'):self._merge_v2_filament_info(0,{'slots':[{'status':state,'rfid':0},{'status':'empty','rfid':0}]})
 def reply(self,cb):cb(self=self,response={'result':{'type':'PLA','sku':'SM1'}})
class Tests(unittest.TestCase):
 def test_normal_first_heartbeat(self):
  x=Fake();x.merge();x.reply(x.callbacks[0][1]);self.assertEqual(len(x.stored),1)
 def test_removal_invalidates_response(self):
  x=Fake();x.merge();cb=x.callbacks[0][1];x.merge('empty');x.reply(cb);self.assertFalse(x.stored)
 def test_new_connection_fences_old_timer_and_reply(self):
  x=Fake();x.merge();old=x.callbacks[0][1];timer=x.reactor.timers[0];x._serials[0]=object();x._v2_rfid_rescan_pending[0]=True;x.merge();x.reply(old);timer(0);self.assertFalse(x.stored);self.assertFalse(x.flushed)
 def test_occupied_shared_pair_never_scans(self):
  x=Fake()
  with self.assertLogs(level='INFO') as logs:x._merge_v2_filament_info(0,{'slots':[{'status':'ready','rfid':0},{'status':'ready','rfid':0}]})
  self.assertEqual(x.callbacks,[]);self.assertEqual(sum('boot identify skipped' in row for row in logs.output),2)
 def test_partner_insert_during_scan_invalidates_reply(self):
  x=Fake();x.merge();cb=x.callbacks[0][1]
  x._merge_v2_filament_info(0,{'slots':[{'status':'ready','rfid':0},{'status':'ready','rfid':0}]})
  x.reply(cb);self.assertFalse(x.stored);self.assertNotIn(0,x._rescan_bind_targets)
 def test_missing_partner_is_ambiguous(self):
  x=Fake();x._merge_v2_filament_info(0,{'slots':[{'status':'ready','rfid':0}]});self.assertEqual(x.callbacks,[])
 def test_manual_read_wins(self):
  x=Fake();x.merge();cb=x.callbacks[0][1];x._v2_filament_info_per_ace[0][0]={'host':True};x.reply(cb);self.assertFalse(x.stored)
if __name__=='__main__':unittest.main()
