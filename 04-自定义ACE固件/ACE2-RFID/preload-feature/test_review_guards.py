import ast, pathlib, types, unittest
from ace_load_rf import RfGate, RfDenied
p=pathlib.Path(__file__).parent
source=ast.parse((p/'ace.py').read_text())
def load(name):
 node=next(n for n in ast.walk(source) if isinstance(n,ast.FunctionDef) and n.name==name)
 mod=ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[]))
 ns={'RfDenied':RfDenied};exec(compile(mod,str(p/'ace.py'),'exec'),ns);return ns[name]
class Guards(unittest.TestCase):
 def test_rf_rejection_callback_not_stop_fault(self):
  callbacks=[];conn=object();gate=RfGate();gate.acquire(0,2,conn)
  def cb(**kw): callbacks.append(kw['response'])
  selfobj=types.SimpleNamespace(_rf_gate=gate,_stop_guard=types.SimpleNamespace(write=lambda *a:True),
   _cb_locks={},_callback_maps={0:{7:cb}},reactor=types.SimpleNamespace(register_async_callback=lambda f:f(0)),
   _drop_queued_stop_fault=lambda *a: self.fail('RF denial treated as STOP fault'))
  self.assertFalse(load('_stop_guarded_write')(selfobj,0,{'id':7,'method':'filament_identify'},conn,b''))
  self.assertTrue(callbacks[0]['_host_rf_busy']);self.assertNotIn('_host_stop_fault',callbacks[0])
  self.assertEqual(selfobj._callback_maps[0],{})
 def test_queued_stale_move_gets_session_rejection(self):
  callbacks=[];conn=object();gate=RfGate()
  def cb(**kw): callbacks.append(kw['response'])
  obj=types.SimpleNamespace(_rf_gate=gate,_cb_locks={},_callback_maps={0:{9:cb}},
   reactor=types.SimpleNamespace(register_async_callback=lambda f:f(0)))
  self.assertFalse(load('_stop_guarded_write')(obj,0,{'id':9,'method':'feed_filament','_session_precondition':lambda:False},conn,b''))
  self.assertTrue(callbacks[0]['_host_stale_session'])
 def test_feed_checks_frozen_target_after_ready_wait(self):
  sent=[];a=types.SimpleNamespace(_require_no_stop_fault=lambda:None,_active_device_index=3,
   wait_ace_ready_on=lambda idx:None,send_request_to=lambda *a,**k:sent.append((a,k)))
  result=load('_feed')(a,2,100,10,0,ace=0,precondition=lambda:False)
  self.assertFalse(result);self.assertEqual(sent,[])
if __name__=='__main__':unittest.main()
