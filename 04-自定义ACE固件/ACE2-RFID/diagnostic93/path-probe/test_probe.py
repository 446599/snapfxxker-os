import unittest, tempfile, pathlib, json
from unittest.mock import patch
import probe
class ProbeTests(unittest.TestCase):
 def run_move(self,heads,gate=True,encoder=(5,5),stop_failure=False):
  clock=[0.0];stops=[]
  def move_cmd(*a,**kw):return {}
  def stop():
   stops.append(1)
   if stop_failure: raise RuntimeError('stop failed')
  def now():return clock[0]
  def sleep(t):clock[0]+=t
  values=iter(heads); values_last=heads[-1]
  def sensor():return next(values,values_last)
  enc=iter(encoder)
  with patch.object(probe,'macro',move_cmd),patch.object(probe,'heads',sensor),patch.object(probe,'gate_present',lambda:gate),patch.object(probe,'stop',stop),patch.object(probe,'status',lambda:{'slots':[{}, {}, {'slot_status':'ready'}]}),patch.object(probe,'encoder',lambda:next(enc)),patch.object(probe.time,'monotonic',now),patch.object(probe.time,'sleep',sleep),patch.object(probe,'record',lambda **kw:None):
   try:r=probe.move(0,5,set())
   except (RuntimeError,AssertionError):r=None
  return r,len(stops)
 def test_trigger_stops_once(self):
  r,n=self.run_move([[False]*4,[False,False,True,False]])
  self.assertEqual(r,(5,[2]));self.assertEqual(n,1)
 def test_transient_trigger_latched(self):
  r,n=self.run_move([[True,False,False,False],[False]*4])
  self.assertEqual(r,(5,[0]));self.assertEqual(n,1)
 def test_gate_loss_stops_once(self):
  r,n=self.run_move([[False]*4],gate=False);self.assertIsNone(r);self.assertEqual(n,1)
 def test_stop_or_encoder_uncertain_never_retries_stop(self):
  for kwargs in ({'stop_failure':True},{'encoder':(5,6)}):
   r,n=self.run_move([[False]*4],**kwargs);self.assertIsNone(r);self.assertEqual(n,1)
 def test_refuses_long_segment(self):
  with self.assertRaises(ValueError):probe.move(0,6,set())
 def test_main_uncertain_forward_never_reverses(self):
  calls=[]
  state={'printer_state':'idle','tag_op':{'busy':False},'swap_in_progress':False,'unload_all_active':False,
         'aces':[{'firmware':'V1.1.93O','connected':True,'status':'ready','fw_hold':False,
                  'dryer':{'status':'stop'},'auto_dry_running':False,'auto_dry':{'enabled':False}}]}
  def mac(name,label,**kwargs):
   return {'uid1':808780566,'uid2':17445170,'uid3':942880330} if name=='A_DISCOVER' else {}
  def move(mode,length,allowed):calls.append(mode);raise RuntimeError('unconfirmed motion')
  with tempfile.TemporaryDirectory() as d, patch.object(probe,'OUT',pathlib.Path(d)),patch.object(probe,'request',lambda *a,**k:state),patch.object(probe,'heads',lambda:[False]*4),patch.object(probe,'gate_present',lambda:True),patch.object(probe,'macro',mac),patch.object(probe,'status',lambda:{'slots':[{'slot_status':'ready'}]*4}),patch.object(probe,'move',move),patch('sys.argv',['probe.py','--execute']):
   with self.assertRaises(RuntimeError):probe.main()
   result=json.loads((pathlib.Path(d)/'result.json').read_text())
   self.assertFalse(result['complete']);self.assertEqual(calls,[0])

if __name__=='__main__':unittest.main()
