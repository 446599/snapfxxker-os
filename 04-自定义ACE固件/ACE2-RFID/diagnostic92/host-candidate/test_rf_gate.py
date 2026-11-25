import unittest
from ace_load_rf import RfGate

class GateTests(unittest.TestCase):
 def test_ace_wide_exclusive_and_stop_never_blocked(self):
  g=RfGate(); c=object(); t=g.acquire(0,0,c)
  self.assertIsNone(g.acquire(0,1,c))
  self.assertTrue(g.allows(0,{'method':'stop_feed_filament'}))
  for method in ('raw','rfid_test','set_rfid_enable','filament_identify','filament_recognition'):
   self.assertFalse(g.allows(0,{'method':method},connection=c))
   self.assertTrue(g.allows(0,{'method':method},t,c))
  self.assertTrue(g.allows(1,{'method':'filament_identify'}))
 def test_disconnect_and_late_owner(self):
  g=RfGate(); c=object(); old=g.acquire(0,1,c);g.invalidate(0)
  self.assertFalse(g.allows(0,{'method':'filament_identify'},old,c))
  new=g.acquire(0,2,object());self.assertNotEqual(old,new)
  self.assertFalse(g.allows(0,{'method':'filament_identify'},old,c))
  self.assertFalse(g.release(old));self.assertTrue(g.release(new))
 def test_same_uid_cannot_change_lane_token(self):
  g=RfGate();c=object();t=g.acquire(0,0,c)
  wrong=(0,1,t[2],c)
  self.assertFalse(g.allows(0,{'method':'filament_identify'},wrong,c))

if __name__=='__main__': unittest.main()
