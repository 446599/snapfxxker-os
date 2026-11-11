import atexit, importlib, pathlib, sys, tarfile, tempfile, types, unittest
from unittest.mock import patch
REPO=pathlib.Path(__file__).resolve().parents[2]
ARCHIVE=REPO/'overlays/233-dev.2-perms.1/root/usr/local/share/233-firmware/multiace-managed-1.27b-233.20261111-single.3.tar.gz'
TMP=tempfile.TemporaryDirectory(prefix='queue-package-');atexit.register(TMP.cleanup);root=pathlib.Path(TMP.name)
with tarfile.open(ARCHIVE) as archive:
 for member in archive:
  if member.isfile() and '/klipper/extras/' in member.name and member.name.endswith('.py'):
   (root/member.name.rsplit('/',1)[-1]).write_bytes(archive.extractfile(member).read())
pkg=types.ModuleType('queuecandidate');pkg.__path__=[str(root)];sys.modules[pkg.__name__]=pkg
serial=types.ModuleType('serial');serial.SerialException=Exception;sys.modules.setdefault('serial',serial)
with patch('os.path.isdir',return_value=True),patch('os.path.isfile',return_value=True),patch('os.path.lexists',return_value=True):
 mod=importlib.import_module('queuecandidate.ace')
class Fake:
 _insert_read_then_preload=mod.MultiAce._insert_read_then_preload
 _insert_tag_read_safe=mod.MultiAce._insert_tag_read_safe
 _insert_uid_read=mod.MultiAce._insert_uid_read
 def __init__(self):
  self._serials={0:object()};self._insert_epochs={(0,1):1};self._gate_status_per_ace={0:[1,1,1,1]};self._location_hold=types.SimpleNamespace(blocks_route=lambda **kw:False);self.state='printing';self.printer=types.SimpleNamespace(lookup_object=lambda *args:types.SimpleNamespace(state=self.state));self.fault=[];self.route=False;self.identity=('port','uid');self._tag_read_busy=True
 def _rfid_route_heads(self,idx,slot):return {slot}
 def _location_identity(self,i):return self.identity
 def _insert_guard(self,*a,**k):return True
 def _insert_queue_stale_reason(self,*a):return 'slot feeds a head' if self.route else None
 def _insert_fresh_lane(self,*a):return ('ready',-10,True,100,10)
 def _latch_stop_fault(self,*a):self.fault.append(a)
 def _insert_drain_queue(self):pass
 def _disp(self,x):return x+1
 def log_always(self,*a):pass
 def depth(self):return {'kind':'relative_unknown','transport':self._serials[0],'identity':self.identity,'epoch':1,'baseline':(-10,True,100,10)}
class Test(unittest.TestCase):
 def test_multiple_print_races_then_busy_queue(self):
  for entry in ('_insert_read_then_preload','_insert_tag_read_safe'):
   f=Fake();d=f.depth()
   for epoch in (2,3,4):
    self.assertEqual(getattr(f,entry)(0,1,d)['kind'],'queued');d=f._insert_read_queue.pop()[2];self.assertEqual(d['epoch'],epoch)
   f.state='standby';self.assertEqual(getattr(f,entry)(0,1,d)['kind'],'queued');self.assertEqual(f._insert_read_queue[0][2]['epoch'],5);self.assertEqual(f.fault,[])
 def test_print_race_then_real_reader_entry(self):
  f=Fake();d=f.depth();self.assertEqual(f._insert_tag_read_safe(0,1,d)['kind'],'queued');d=f._insert_read_queue.pop()[2]
  f.state='standby';f._tag_read_busy=False;f._v2_filament_info_per_ace={0:{}}
  old=sys.modules.get('queuecandidate.ace_rc522');stub=types.ModuleType('queuecandidate.ace_rc522')
  class Reader:
   def __init__(self,ace):self._encoder_direction={}
   def read_slot_single_checked(self,*args,**kwargs):return {'kind':'unread','parked':True}
  stub.AceTagReader=Reader;sys.modules[stub.__name__]=stub
  try:
   r=f._insert_tag_read_safe(0,1,d);self.assertEqual(r,{'kind':'unread','parked':True,'relative_unknown':True});self.assertFalse(f.fault)
  finally:
   if old is None:sys.modules.pop(stub.__name__,None)
   else:sys.modules[stub.__name__]=old
 def test_changed_owner_or_epoch_never_requeued(self):
  for change in ('uid','transport','gate','epoch','route'):
   f=Fake();d=f.depth()
   if change=='uid':f.identity=('port','new')
   if change=='transport':f._serials[0]=object()
   if change=='gate':f._gate_status_per_ace[0][1]=0
   if change=='route':f.route=True
   if change=='epoch':f._insert_epochs[(0,1)]=3
   self.assertEqual(f._insert_read_then_preload(0,1,d)['kind'],'transport_changed');self.assertFalse(getattr(f,'_insert_read_queue',[]));self.assertFalse(f.fault)
if __name__=='__main__':unittest.main(verbosity=2)
