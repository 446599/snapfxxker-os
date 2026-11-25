import unittest, tempfile, pathlib
from unittest.mock import patch
import wupa_probe
from wupa_probe import classify
class Tests(unittest.TestCase):
 def test_atqa(self): self.assertEqual(classify(rpc_ok=True,status=0,irq=0x30,error=0,bits=16,data=b'\x04\x00')['kind'],'atqa')
 def test_timer_is_not_no_card(self): self.assertEqual(classify(rpc_ok=True,status=0xee,irq=1,error=0)['kind'],'no_response')
 def test_collision_and_errors(self):
  self.assertEqual(classify(rpc_ok=True,status=2,irq=2,error=8)['kind'],'collision')
  self.assertEqual(classify(rpc_ok=True,status=1,irq=2,error=1)['kind'],'rf_error')
 def test_never_guess(self):
  self.assertEqual(classify(rpc_ok=False)['kind'],'transport_error')
  for kw in ({'status':1,'irq':0,'error':0},{'status':0,'irq':0x20,'error':0,'bits':7,'data':b'x'}, {'status':0xee,'irq':255,'error':0}):
   self.assertEqual(classify(rpc_ok=True,**kw)['kind'],'ambiguous')
 def test_rf_failure_restores_registers(self):
  writes=[];p=wupa_probe.Probe(None)
  def raw(reader,op,a=0,b=0):
   if op==3:raise RuntimeError('lost transceive reply')
   return 0
  with patch.object(p,'raw',raw),patch.object(p,'read',lambda reader,reg:0),patch.object(p,'write',lambda reader,reg,val:writes.append((reg,val))):
   with self.assertRaises(RuntimeError):p.wupa(1)
  self.assertEqual(writes[-10:],[(1,0)]+[(r,0) for r in (8,13,18,19,42,43,44,45,2)])
 def test_both_readers_restored_after_failure(self):
  events=[]
  class Guard:
   PORT='x'
   def state(self,held=False):events.append(('state',held))
   def request(self,path,payload):events.append(('host',payload['script']))
  class T:
   def __init__(self,port):pass
   def close(self):events.append(('close',))
  class P:
   def __init__(self,t):pass
   def send(self,cmd):return ({1:[(0,b'V1.1.93O')]} if cmd==5 else {1:[(0,808780566)],2:[(0,17445170)],3:[(0,942880330)]})
   def reader_state(self,r):return 0
   def enable(self,r,v):events.append(('enable',r,v))
   def await_state(self,r,disabled):events.append(('await',r,disabled))
   def wupa(self,r):raise RuntimeError('RF failed')
  with tempfile.TemporaryDirectory() as d,patch.object(wupa_probe,'OUT',pathlib.Path(d)),patch.object(wupa_probe,'Probe',P),patch.object(wupa_probe.ota,'get_ace_version',lambda t:('V1.1.93O','')):
   with self.assertRaises(RuntimeError):wupa_probe.run_exclusive(Guard(),T)
   cleanup=pathlib.Path(d,'probe-cleanup.json').read_text();self.assertIn('"failures": []',cleanup)
  self.assertIn(('enable',0,True),events);self.assertIn(('enable',1,True),events)
  self.assertIn(('host','ACE_FW_RESUME ACE=0'),events)

if __name__=='__main__':unittest.main()
