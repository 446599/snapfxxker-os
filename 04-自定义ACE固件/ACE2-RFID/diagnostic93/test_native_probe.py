import unittest
from unittest.mock import patch
import native_probe as p
class Monitor(unittest.TestCase):
 def run_scenario(self,states,counts,head=True):
  now=[0];calls=[];samples=[]
  def state(t):return states.pop(0)
  def count(t):return counts.pop(0)
  def pause(dt):now[0]+=dt
  def send(*a):calls.append(a);return {}
  with patch.object(p,'send',send):
   try:r=p.monitor(None,state,count,lambda:head,lambda:now[0],pause,lambda e,**k:samples.append(e),timeout=2)
   except (RuntimeError,IndexError):r=None
  return r,calls,samples
 def test_motion_then_stable(self):
  r,c,_=self.run_scenario([[0,0,5,0],[0,0,0,0],[0,0,0,0]],[12,20,20]);self.assertTrue(r['motion_seen']);self.assertEqual(c,[])
 def test_budget_or_head_stop_once(self):
  for states,counts,head in [([[0,0,5,0]],[1501],True),([],[],False)]:
   r,c,_=self.run_scenario(states,counts,head);self.assertIsNone(r);self.assertEqual(len(c),1);self.assertEqual(c[0][1],9)
 def test_ready_alone_is_not_completion(self):
  r,c,_=self.run_scenario([[0,0,0,0]]*12,[0]*12);self.assertIsNone(r);self.assertEqual(len(c),1)
if __name__=='__main__':unittest.main()
