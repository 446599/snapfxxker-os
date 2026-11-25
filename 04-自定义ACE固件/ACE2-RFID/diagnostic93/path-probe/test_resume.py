import unittest
from unittest.mock import patch
import resume
class Tests(unittest.TestCase):
 def test_only_read_commands_retry(self):
  for name in ('A_STATUS','A_FEEDINFO','A_KEYSTATE','A_FEED','A_STOP'):
   calls=[]
   def f(*a,**kw):
    calls.append(a[0])
    if len(calls)==1:raise RuntimeError('transient')
    return {'ok':True}
   with patch.object(resume,'original_macro',f),patch.object(resume.probe,'record',lambda **kw:None),patch.object(resume.time,'sleep',lambda _:None):
    if name in resume.READS:self.assertEqual(resume.retry_reads(name,'label'),{'ok':True})
    else:
     with self.assertRaises(RuntimeError):resume.retry_reads(name,'label')
   self.assertEqual(len(calls),2 if name in resume.READS else 1)
 def test_prior_ledger(self):
  data=resume.prepare();self.assertEqual((data['forward_commanded'],data['forward_encoder']),(330,308))
if __name__=='__main__':unittest.main()
