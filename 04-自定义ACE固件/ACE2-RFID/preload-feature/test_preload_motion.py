import importlib.util, pathlib, sys, types, unittest
p = pathlib.Path(__file__).parent
pkg = types.ModuleType('preload_candidate'); pkg.__path__ = [str(p)]; sys.modules[pkg.__name__] = pkg
spec = importlib.util.spec_from_file_location('preload_candidate.ace_preload_motion', p/'ace_preload_motion.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

class MotionTests(unittest.TestCase):
    def test_lost_move_ack_stops_without_retry_and_still_faults(self):
        motion = object.__new__(m.PreloadMotion)
        motion.idx=0; motion.slot=2; motion._current=lambda: True; motion._transport_current=lambda: True; motion.head_clear=lambda: True
        calls=[]
        def send(method, params, timeout, monitor):
            calls.append(method)
            return None if method == 'feed_or_rollback_raw' else {'code':0,'_v2_payload_valid':True}
        motion._send_monitor=send
        with self.assertRaisesRegex(m.MotionFault, 'position unknown'):
            motion.move_and_stop(5)
        self.assertEqual(calls, ['feed_or_rollback_raw','stop_feed_filament'])
    def test_trigger_during_move_stops_and_confirms_partial_encoder(self):
        motion = object.__new__(m.PreloadMotion); motion.idx=0; motion.slot=2
        motion._current=lambda: True; motion._transport_current=lambda: True
        heads=iter([True,False]); motion.head_clear=lambda: next(heads,False)
        calls=[]
        motion._send_monitor=lambda method,*args: calls.append(method) or {'code':0,'_v2_payload_valid':True}
        reactor=types.SimpleNamespace(monotonic=lambda:0, pause=lambda t:None)
        motion.ace=types.SimpleNamespace(reactor=reactor)
        motion.reader=types.SimpleNamespace(_wait_motion_ready=lambda *a:True, _fresh_encoder=lambda *a:2, _pause=lambda t:None)
        self.assertEqual(motion.move_and_stop(5),2)
        self.assertEqual(calls,['feed_or_rollback_raw','stop_feed_filament'])
    def test_stop_rejection_is_fault(self):
        motion = object.__new__(m.PreloadMotion); motion.slot=2
        motion._send_monitor=lambda *args: {'code':1, '_v2_payload_valid':True}
        with self.assertRaises(m.MotionFault): motion.stop()

if __name__=='__main__': unittest.main()
