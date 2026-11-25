import unittest
from ace_native_remainder import estimate_native
class TestNative(unittest.TestCase):
 def run_case(self,**changes):
  p=dict(session=7,captured_session=7,slot=2,captured_slot=2,uid='E5DDA105',captured_uid='E5DDA105',cycle_count=1,raw_mm=100000,material='PLA',observation_time=100, trusted_attribution=True);p.update(changes);return estimate_native(**p)
 def test_valid_and_nominal_not_accepted(self):
  self.assertEqual(self.run_case()['remaining_g'],298)
  self.assertIsNone(self.run_case(raw_mm=1000)['remaining_g'])
  self.assertIsNone(self.run_case(cycle_count=0)['remaining_g'])
 def test_no_cross_session_or_uid(self):
  for kw in ({'captured_session':6},{'captured_slot':1},{'captured_uid':'55F328E6'},{'trusted_attribution':False}):
   self.assertEqual(self.run_case(**kw)['state'],'ambiguous')
 def test_unknown_material(self): self.assertEqual(self.run_case(material='PETG')['state'],'pending')
if __name__=='__main__':unittest.main()
