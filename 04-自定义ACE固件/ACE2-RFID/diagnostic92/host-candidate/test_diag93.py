import unittest
from ace_diag93 import request_word,UID
class Guard(unittest.TestCase):
 def test_exact_target(self):
  self.assertEqual(request_word('V1.1.93O',UID,2,3),0x800a0203)
  for version,uid in [('V1.1.60O',UID),('V1.1.92O',UID),('V1.1.93O',(1,2,3))]:
   self.assertIsNone(request_word(version,uid,2,3))
if __name__=='__main__':unittest.main()
