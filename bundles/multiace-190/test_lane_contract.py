import unittest
from lane_contract import accepted, uid, native_sample, stock_sample, still


def native(slot=1, mode=0, present=2, enc=1234, fg=8, t=10, after=None):
    return native_sample((t, present), (t, {'mode': [0, mode, 0, 8]}),
                         (t, [0, enc, 0, 999]), (t, [0, fg, 0, 999]),
                         slot, t, after=after)


def stock():
    def r(cmd, result):
        return dict(code=0, msg='success', result=result, _v2_frame_valid=True,
                    _v2_payload_valid=True, _v2_command=cmd)
    return (r(6, dict(_status_wire_valid=True, slots=[dict(index=1, _wire_valid=True, slot_status='ready')])),
            r(73, dict(fields={'1': 16})),
            r(76, dict(_feed_wire_valid=True, feed_info=[dict(index=1, _wire_valid=True,
                 _decoder_present=True, decoder=2**64-2, steps=8)])))


class Tests(unittest.TestCase):
    def test_native_slot2(self):
        s = native()
        self.assertEqual((s.present, s.state, s.encoder, s.motor), (True, 'ready', 1234, 8))
    def test_native_slot4_untouched(self):
        s = native(slot=3)
        self.assertEqual((s.present, s.state, s.encoder, s.motor), (False, 'assisting', 999, 999))
    def test_native_mode_map(self):
        for i, state in enumerate(('ready','feeding','rollback','rollback','preloading','assisting','rollback_assisting','feed_error','assisting')):
            self.assertEqual(native(mode=i).state, state)
    def test_native_unknown_mode(self):
        self.assertIsNone(native(mode=9))
    def test_native_requires_fresh_post_stop_data(self):
        self.assertIsNone(native(after=10))
        self.assertIsNotNone(native(after=9))
    def test_native_missing_counter(self):
        self.assertIsNone(native_sample((10,2),(10,{'mode':[0]*4}),None,(10,[0]*4),1,10))
    def test_native_stale_counter(self):
        self.assertIsNone(native_sample((10,2),(10,{'mode':[0]*4}),(6,[0]*4),(10,[0]*4),1,10))
    def test_native_repeated_cache_not_stop_proof(self):
        self.assertFalse(still(native(), native()))
        self.assertTrue(still(native(), native(t=11)))
    def test_native_encoder_movement(self):
        self.assertFalse(still(native(), native(t=11,enc=1235)))
    def test_native_motor_movement(self):
        self.assertFalse(still(native(), native(t=11,fg=9)))
    def test_native_moving_not_still(self):
        self.assertFalse(still(native(mode=2),native(mode=2,t=11)))
    def test_native_inlet_changes(self):
        self.assertFalse(still(native(),native(t=11,present=0)))
    def test_stock_signed_decoder(self):
        s = stock_sample(*stock(),1)
        self.assertEqual((s.present,s.state,s.encoder,s.motor),(True,'ready',-2,8))
    def test_stock_bad_crc(self):
        s,g,f=stock(); s['_v2_frame_valid']=False
        self.assertIsNone(stock_sample(s,g,f,1))
    def test_stock_missing_decoder_not_zero(self):
        s,g,f=stock(); f['result']['feed_info'][0]['_decoder_present']=False
        self.assertIsNone(stock_sample(s,g,f,1))
    def test_stock_other_slot_not_accepted(self):
        self.assertIsNone(stock_sample(*stock(),2))
    def test_uid_valid(self):
        self.assertEqual(uid(dict(code=0,msg='success',result=dict(uid1=1,uid2=0,uid3=3))),(1,0,3))
    def test_uid_invalid(self):
        for x in (False,-1,2**32,None,0):
            self.assertIsNone(uid(dict(code=0,msg='success',result=dict(uid1=x,uid2=0,uid3=0))))
    def test_error_not_success(self):
        self.assertFalse(accepted(dict(code=0,msg='FORBIDDEN',result={})))
        self.assertFalse(accepted(dict(code=False,msg='success',result={})))
        self.assertFalse(accepted(None))

if __name__ == '__main__':
    unittest.main()
