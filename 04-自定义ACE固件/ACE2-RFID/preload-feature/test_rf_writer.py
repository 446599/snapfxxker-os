import unittest
from ace_load_rf import RfGate, RfDenied

class WriterTest(unittest.TestCase):
    def test_owner_blocks_queued_rf_not_stop(self):
        gate = RfGate(); serial = object(); token = gate.acquire(0, 2, serial)
        writes = []
        def send(req): return gate.write(0, req, serial, lambda: writes.append(req['method']) or True)
        with self.assertRaises(RfDenied): send({'method':'filament_identify'})
        self.assertTrue(send({'method':'stop_feed_filament'}))
        self.assertTrue(send({'method':'filament_identify','_rf_token':token}))
        self.assertEqual(writes, ['stop_feed_filament','filament_identify'])
    def test_reconnected_serial_cannot_use_old_token(self):
        gate = RfGate(); token = gate.acquire(0, 0, object())
        with self.assertRaises(RfDenied): gate.write(0, {'method':'raw','_rf_token':token}, object(), lambda: True)
    def test_stale_token_cannot_send_after_release(self):
        gate = RfGate(); serial = object(); token = gate.acquire(0, 0, serial)
        self.assertTrue(gate.release(token))
        with self.assertRaises(RfDenied): gate.write(0, {'method':'raw','_rf_token':token}, serial, lambda: True)

if __name__ == '__main__': unittest.main()
