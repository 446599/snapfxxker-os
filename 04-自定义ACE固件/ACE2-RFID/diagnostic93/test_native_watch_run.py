import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'minimal-repair/live'))
import native_watch_run as run
class Transport:
 def __init__(self,*a):pass
 def close(self):pass
class IO:
 def __init__(self,*a):pass
 def send(self,cmd,*a,**k):
  if cmd==0:return {1:[(0,808780566)],2:[(0,17445170)],3:[(0,942880330)]}
  if cmd==7:return {1:[(2,b'V1.1.95O')]}
  return {5:[(0,0)],6:[(0,1)]}
 def status(self):return ['ready']*4
 def stop(self,slot):return True
class W:
 phase='complete';reason='head sensor';triggered={2};forward_peak=20;stop_sent={2}
 def __init__(self,io):pass
class Test(unittest.TestCase):
 def test_failure_after_complete_never_resumes(self):
  actions=[]
  def http(path,*a,**k):return {'aces':[{'fw_hold':True}]}
  with tempfile.TemporaryDirectory() as d, patch.object(run,'ROOT',Path(d)),patch.object(run,'check_preflight'),patch.object(run,'gcode',side_effect=lambda x:actions.append(x)),patch.object(run,'http',side_effect=http),patch.object(run.ota,'ACE2Transport',Transport),patch.object(run,'DeviceIO',IO),patch.object(run,'Watch',W),patch.object(run,'diag',side_effect=[8,0]),patch.object(run,'snapshot',side_effect=[{},ValueError('diagnostic failure')]),patch.object(run,'confirm_still',return_value=3),patch.object(sys,'argv',['x','--execute']):
   with self.assertRaises(SystemExit):run.main()
  self.assertEqual(actions,['ACE_FW_RELEASE ACE=0'])
if __name__=='__main__':unittest.main()
