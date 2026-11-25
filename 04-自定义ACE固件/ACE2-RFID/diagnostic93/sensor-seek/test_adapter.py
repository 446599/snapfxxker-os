import unittest
from ace_sensor_seek import SensorSeek

class FakeAce:
    def __init__(self): self.faults=[]; self._serials={0:object()}; self.stops=0
    def _latch_stop_fault(self,*args): self.faults.append(args)
    def _stop_feeding(self,*args,**kwargs): self.stops+=1; return True

class FakeReactor:
    NEVER = -1
    def monotonic(self): return 1

class FakeGcode:
    def respond_info(self, msg): pass

class AdapterTest(unittest.TestCase):
    def setup_seek(self):
        o=SensorSeek.__new__(SensorSeek); o.ace=FakeAce(); o.reactor=FakeReactor(); o.gcode=FakeGcode()
        o.transport=o.ace._serials[0]; o.state='moving'; o.generation=1; o.ack=True
        o.result={'commanded_mm':50,'encoder_mm':0}; o.samples=[]; o.pending=False
        o.booked=False; o.started=0; o.motion_started=0; o.last_poll=0
        o.sensors=lambda:(False,False,False,False); o.check_session=lambda now: None
        return o
    @staticmethod
    def decoder(v):
        return {'_v2_frame_valid':True,'_v2_payload_valid':True,'code':0,'result':{
            '_feed_wire_valid':True,'feed_info':[{'index':i,'_decoder_present':True,'decoder':v} for i in range(4)]}}
    def test_completed_segment_not_counted_twice_after_sensor(self):
        o=self.setup_seek(); o.decoder_reply(self.decoder(40)); o.decoder_reply(self.decoder(40))
        self.assertEqual(o.state,'between'); self.assertEqual(o.result['encoder_mm'],40)
        o.hit=(2,); o.stop('sensor'); o.decoder_reply(self.decoder(40)); o.decoder_reply(self.decoder(40))
        self.assertEqual(o.state,'at_sensor'); self.assertEqual(o.result['encoder_mm'],40)
    def test_ack_timeout_stops_once(self):
        o=self.setup_seek(); o.ack=False; o.tick(2.1); self.assertEqual(o.ace.stops,1)
        o.decoder_reply(self.decoder(5)); o.decoder_reply(self.decoder(5))
        self.assertEqual(o.state,'uncertain'); self.assertTrue(o.ace.faults)
    def test_sensor_trigger_stops_before_other_queries(self):
        o=self.setup_seek(); o.sensors=lambda:(False,False,True,False)
        o.tick(.1); self.assertEqual(o.ace.stops,1); self.assertEqual(o.hit,(2,))
    def test_final_segment_not_stopped_at_budget(self):
        o=self.setup_seek(); o.result['commanded_mm']=2000; o.last_poll=100
        o.tick(.1); self.assertEqual(o.state,'moving'); self.assertEqual(o.ace.stops,0)
    def test_sensor_error_stops(self):
        o=self.setup_seek(); o.sensors=lambda: (_ for _ in ()).throw(ValueError())
        o.tick(.1); self.assertEqual(o.ace.stops,1)
    def test_wrong_sensor_does_not_succeed(self):
        o=self.setup_seek(); o.sensors=lambda:(True,False,False,False)
        o.tick(.1); o.decoder_reply(self.decoder(10)); o.decoder_reply(self.decoder(10))
        self.assertEqual(o.state,'failed'); self.assertEqual(o.hit,(0,))
    def test_rejected_ack_stops_and_latches_after_verification(self):
        o=self.setup_seek(); o.ack=False; o.move_reply({'code': 1})
        o.decoder_reply(self.decoder(10)); o.decoder_reply(self.decoder(10))
        self.assertEqual(o.state,'uncertain'); self.assertTrue(o.ace.faults)
    def test_late_decoder_ignored_after_finish(self):
        o=self.setup_seek(); o.finish('failed'); prior=dict(o.result)
        o.decoder_reply(self.decoder(40)); o.decoder_reply(self.decoder(40))
        self.assertEqual(o.result, prior)
    def test_state_change_stops(self):
        o=self.setup_seek(); o.check_session=lambda now: (_ for _ in ()).throw(ValueError())
        o.tick(.1); self.assertEqual(o.ace.stops,1)
    def test_stop_exception_latches(self):
        o=self.setup_seek(); o.ace._stop_feeding=lambda *a,**k: (_ for _ in ()).throw(ValueError())
        o.stop('sensor'); self.assertEqual(o.state,'uncertain'); self.assertTrue(o.ace.faults)
if __name__=='__main__': unittest.main()
