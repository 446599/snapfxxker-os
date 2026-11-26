import itertools
import unittest
from batch_material import BatchScheduler, Bay, Plan, parse_batch


class Backend:
    def __init__(self, parked=True):
        self.parked=parked
        self.started=[]
        self.stopped=[]
        self.held={}
        self.results={}
        self.unknown=set()
        self.fail=set()
    def plan(self, action, bay):
        if bay in self.unknown:
            raise ValueError('route unknown')
        return Plan(bay, self.parked)
    def claim(self, ident, action, plan, resources):
        if any(resources & old for old in self.held.values()): return None
        self.held[ident]=resources
        self.results[ident]='running'
        return ident
    def begin(self, handle):
        self.started.append(handle)
        if handle in self.fail: raise IOError('lost ACK')
    def poll(self, handle):
        result=self.results[handle]
        if result in ('complete','failed','cancelled'): self.held.pop(handle,None)
        return result
    def stop(self, handle):
        self.stopped.append(handle)
        return True


def request(action, slots):
    return dict(action=action,slots=[dict(ace=a,slot=s) for a,s in slots])


class BatchTests(unittest.TestCase):
    def test_sixteen_parked_exits_start_before_any_completes(self):
        backend=Backend(); q=BatchScheduler(backend)
        batch=q.submit(request('eject', itertools.product(range(4), repeat=2)))
        q.advance()
        self.assertEqual(len(backend.started),16)
        self.assertTrue(all(t['state']=='running' for t in q.status(batch)))
    def test_shared_tool_motion_stays_serial(self):
        b=Backend(False); q=BatchScheduler(b)
        batch=q.submit(request('unload',[(0,0),(0,1),(1,2)])); q.advance()
        self.assertEqual(len(b.started),1)
        b.results[b.started[0]]='complete'; q.advance()
        self.assertEqual(len(b.started),2)
    def test_unknown_one_does_not_prevent_independent_start(self):
        b=Backend(); b.unknown.add(Bay(0,0)); q=BatchScheduler(b)
        batch=q.submit(request('eject',[(0,0),(0,1)])); q.advance()
        self.assertEqual([t['state'] for t in q.status(batch)],['rejected','running'])
    def test_stop_targets_exact_task_and_ack_not_complete(self):
        b=Backend(); q=BatchScheduler(b)
        batch=q.submit(request('eject',[(0,0),(0,1)])); q.advance()
        first,second=q.status(batch)
        self.assertFalse(q.cancel('stale'))
        self.assertTrue(q.cancel(first['id']))
        self.assertEqual(b.stopped,[first['id']])
        self.assertEqual([t['state'] for t in q.status(batch)],['stopping','running'])
        b.results[first['id']]='cancelled'; q.advance()
        self.assertEqual([t['state'] for t in q.status(batch)],['cancelled','running'])
    def test_uncertain_start_keeps_only_its_claim(self):
        b=Backend(); q=BatchScheduler(b)
        batch=q.submit(request('eject',[(0,0),(0,1)]))
        first=q.status(batch)[0]['id']; b.fail.add(first); q.advance()
        self.assertEqual([t['state'] for t in q.status(batch)],['recovery','running'])
        self.assertIn(first,b.held)
        with self.assertRaises(ValueError): q.submit(request('eject',[(0,0)]))
    def test_invalid_selection_and_untrusted_park_flag(self):
        for body in [request('load',[(0,0),(1,0)]),request('eject',[(0,0),(0,0)]),
                     request('unload',[(True,0)]),request('eject',[]),
                     {'action':'eject','slots':[{'ace':0,'slot':0,'parked':True}]}]:
            with self.assertRaises(ValueError): parse_batch(body)
    def test_queued_cancel_never_moves(self):
        b=Backend(); q=BatchScheduler(b)
        batch=q.submit(request('eject',[(0,0)])); task=q.status(batch)[0]['id']
        self.assertTrue(q.cancel(task)); q.advance(); self.assertEqual(b.started,[])
    def test_conflicts_only_shared_resources(self):
        a=Plan(Bay(0,0),True); b=Plan(Bay(1,0),True)
        self.assertFalse(a.resources('eject') & b.resources('eject'))
        self.assertTrue(a.resources('load') & b.resources('load'))
        self.assertTrue(a.resources('eject') & a.resources('load'))


if __name__=='__main__': unittest.main()
