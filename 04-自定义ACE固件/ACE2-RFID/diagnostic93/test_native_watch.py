import unittest
from native_watch import Watch, DeviceIO
class IO:
 def __init__(self):self.h=[False]*4;self.s=['ready']*4;self.g=True;self.d=1;self.stops=[];self.events=[];self.ok=True
 def heads(self):return self.h
 def status(self):return self.s
 def gate(self):return self.g
 def decoder(self):return self.d
 def stop(self,x):self.stops.append(x);return self.ok
 def event(self,x):self.events.append(x)
class T(unittest.TestCase):
 def test_trigger_latches_before_status(self):
  io=IO();w=Watch(io);w.phase='watching';w.inserted_at=w.clock();io.h[2]=True;io.status=lambda: (_ for _ in ()).throw(ValueError())
  self.assertEqual(w.tick(),'stopping');self.assertEqual(io.stops,[2]);io.h[2]=False;self.assertEqual(w.triggered,{2})
 def test_timeout_stop(self):
  io=IO();w=Watch(io,lambda:0);w.phase='watching';w.inserted_at=0;w.clock=lambda:91;self.assertEqual(w.tick(),'stopping');self.assertEqual(io.stops,[2])
 def test_stop_rejection(self):
  io=IO();io.ok=False;w=Watch(io);w.phase='watching';w.inserted_at=w.clock();io.h[2]=True;self.assertEqual(w.tick(),'uncertain')
 def test_empty_then_insert(self):
  io=IO();w=Watch(io);io.g=False;self.assertEqual(w.tick(),'waiting_insert');io.g=True;self.assertEqual(w.tick(),'watching')
 def test_initial_head_not_recorded(self):
  io=IO();io.h[2]=True;w=Watch(io);w.tick();self.assertEqual(w.triggered,set())
 def test_stability_requires_15_seconds_and_restop(self):
  io=IO();now=[0];w=Watch(io,lambda:now[0]);w.phase='watching';w.inserted_at=0;io.h[2]=True
  w.tick();io.h[2]=False;w.tick();now[0]=.1;w.tick();now[0]=.4;w.tick();self.assertEqual(w.phase,'stopping')
  io.d=2;now[0]=.5;w.tick();self.assertEqual(io.stops,[2,2])
  for t in (.6,1.,1.5,2.2):now[0]=t;w.tick()
  self.assertEqual(w.phase,'complete')
 def test_stop_retries_at_most_three(self):
  from types import SimpleNamespace
  io=DeviceIO(None,SimpleNamespace(_field_uint32=lambda *args:b'',get_field=lambda fields,k,default:fields.get(k,default)),None)
  calls=[]
  def send(*args,**kwargs):
   calls.append(1)
   if len(calls)<3: raise ValueError('lost ACK')
   return {1:0}
  io.send=send;self.assertTrue(io.stop(2));self.assertEqual(len(calls),3)
 def test_other_lane(self):
  io=IO();w=Watch(io);w.phase='watching';w.inserted_at=w.clock();io.s[1]='feeding';self.assertEqual(w.tick(),'uncertain');self.assertEqual(io.stops,[2,1])
if __name__=='__main__':unittest.main()
