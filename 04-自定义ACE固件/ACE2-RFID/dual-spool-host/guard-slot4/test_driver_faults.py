import json,pathlib,tempfile,unittest
from unittest.mock import patch
import paired_driver as driver
class Tests(unittest.TestCase):
 def run_case(self,fail):
  flags={1:False,2:False,3:False};commands=[];stop=[]
  def request(path,*a,**kw):
   aces=[{'fw_hold':False,'dryer':{'status':'stop'},'auto_dry_running':False,'auto_dry':{'enabled':False},'feed_assist':-1}]+[{'fw_hold':flags[i],'connected':False} for i in (1,2,3)]
   return {'aces':aces,'calibration':{'state':'idle'},'printer_state':'idle','swap_in_progress':False,'unload_all_active':False,'tag_op':{'busy':False}}
  def macro(name,label,**kw):
   if name=='A_DISCOVER':return {'uid1':808780566,'uid2':17445170,'uid3':942880330}
   if name=='A_INFO':return {'firmware':'V1.1.97O'}
   if name=='A_STATUS':return {'_status_wire_valid':True,'slots':[{'slot_status':'ready'}]*4,'rfid1_enable_raw':0,'rfid2_enable_raw':1}
   if name=='A_FILAMENT_IDENTIFY':return {'index':kw['SLOT'],'code':8 if kw['SLOT']==0x800a0003 else 0}
   if name=='A_KEYSTATE':return {'fields':{'1':1<<8}}
   if name=='A_STOP':stop.append(kw['SLOT']);return {}
   raise ValueError(name)
  def command(text):
   commands.append(text)
   if text.startswith('ACE_FW_RELEASE'):
    n=int(text[-1]);flags[n]=True
    if fail=='release':raise TimeoutError('release reply lost')
   elif text.startswith('ACE_FW_RESUME'):
    n=int(text[-1]);flags[n]=False
   elif text.startswith('ACE_NATIVE_GUARD_ARM') and fail=='arm':raise TimeoutError('arm reply lost')
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp)/'run'
   with patch.object(driver,'ROOT',root),patch.object(driver.probe,'request',side_effect=request),patch.object(driver.probe,'heads',return_value=[False]*4),patch.object(driver,'macro',side_effect=macro),patch.object(driver,'snapshot',side_effect=lambda lane:{'cmd13':{'sku':'SME5DDA105' if lane==2 else ''}}),patch.object(driver,'status',return_value={'state':'waiting_empty'}),patch.object(driver,'command',side_effect=command):
    with self.assertRaises(SystemExit):driver.main()
   result=json.loads((root/'guard-driver-result.json').read_text())
  return result,commands,stop,flags
 def test_arm_executed_response_timeout_stops_and_records_uncertainty(self):
  r,c,s,f=self.run_case('arm');self.assertTrue(r['arm_attempted']);self.assertEqual(r['guard_left_armed'],'unknown');self.assertEqual(s,[3]);self.assertTrue(r['error_stop_ack']);self.assertEqual(r['error_stillness'],'unconfirmed')
 def test_release_executed_response_timeout_restores_hold(self):
  r,c,s,f=self.run_case('release');self.assertIn('ACE_FW_RESUME ACE=1',c);self.assertFalse(f[1]);self.assertFalse(r['hold_cleanup']['1']['fw_hold']);self.assertFalse(r['arm_attempted']);self.assertEqual(s,[])
if __name__=='__main__':unittest.main()
