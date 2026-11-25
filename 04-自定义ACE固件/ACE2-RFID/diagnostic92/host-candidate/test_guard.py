"""Run actual live-derived load failure branches, with mocked STOP and fan."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

source=Path(__file__).with_name('filament_feed_ace.py')
tree=ast.parse(source.read_text())

class TestGuard(unittest.TestCase):
 def exercise(self, branch, fails):
  events=[]
  def stop(idx,slot):
   events.append(('stop',idx,slot))
   if fails: raise RuntimeError('unconfirmed stop')
  obj=SimpleNamespace(_stop_load_or_latch=stop,
      ace=SimpleNamespace(_dwell_fan=lambda enabled:events.append(('fan',enabled))),
      channel_error=['ok'],exception_code=[0])
  scope={'self':obj,'ch':0,'ace_idx':2,'_ace_slot':1,'FEED_ERR_TIMEOUT':'timeout',
         'FEED_ERR_STATE_MISMATCH':'state_mismatch'}
  module=ast.fix_missing_locations(ast.Module(body=branch.body,type_ignores=[]))
  with self.assertRaises(RuntimeError if fails else ValueError):
   exec(compile(module,str(source),'exec'),scope)
  self.assertEqual(events,[('stop',2,1),('fan',False)])
  return obj
 def test_timeout(self):
  branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=='self.reactor.monotonic() > _feed_deadline')
  for fails in (False,True):
   obj=self.exercise(branch,fails)
   if not fails: self.assertEqual((obj.channel_error[0],obj.exception_code[0]),('timeout',34))
 def test_mapping_change(self):
  branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and '_active_device_index != ace_idx' in ast.unparse(n.test) and '_stop_load_or_latch' in ast.unparse(n))
  for fails in (False,True):
   obj=self.exercise(branch,fails)
   if not fails: self.assertEqual(obj.channel_error[0],'state_mismatch')

 def test_retry_mapping_mismatch_aborts_before_retract(self):
  branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If)
              and '_active_device_index != ace_idx' in ast.unparse(n.test)
              and 'before retry' in ast.unparse(n))
  events=[]
  obj=SimpleNamespace(ace=SimpleNamespace(_dwell_fan=lambda enabled:events.append(('fan',enabled))),channel_error=['ok'])
  scope={'self':obj,'ch':0,'FEED_ERR_STATE_MISMATCH':'state_mismatch'}
  module=ast.fix_missing_locations(ast.Module(body=branch.body,type_ignores=[]))
  with self.assertRaises(ValueError): exec(compile(module,str(source),'exec'),scope)
  self.assertEqual(events,[('fan',False)])
  self.assertEqual(obj.channel_error[0],'state_mismatch')

if __name__=='__main__': unittest.main()
