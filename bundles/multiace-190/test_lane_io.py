import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import lane_contract as c
from test_lane_contract import stock
from pathlib import Path

exec(Path(__file__).with_name('lane_io.py').read_text(), c.__dict__)

class IOTests(unittest.TestCase):
    def host(self, protocol, replies):
        return SimpleNamespace(_serials={3: object()}, _protocols={3: SimpleNamespace(NAME=protocol)},
             _connected_per_ace={3: True}, _tipform_send=Mock(side_effect=replies))
    def test_native(self):
        h = self.host('ace2k',[dict(code=0,msg='success',result=dict(index=1,present=True,state='ready',encoder=1,motor=2,stamp=[1,1,1,1]))])
        self.assertEqual(c.read_lane_sample(h,3,1).motor,2)
        h._tipform_send.assert_called_once_with(3,{'method':'get_lane_state','params':{'index':1}},timeout=2.0)
    def test_v2(self):
        h = self.host('v2',stock())
        self.assertEqual(c.read_lane_sample(h,3,1).encoder,-2)
        self.assertEqual([x.args[1]['method'] for x in h._tipform_send.call_args_list],['get_status','get_key_state','get_feed_info'])
    def test_disconnected(self):
        h = self.host('ace2k',[]); h._connected_per_ace[3] = False
        self.assertIsNone(c.read_lane_sample(h,3,1)); h._tipform_send.assert_not_called()
    def test_reconnected_during_reply(self):
        h = self.host('ace2k',[])
        def change(*a,**kw):
            h._serials[3] = object()
            return dict(code=0,msg='success',result=dict(index=1,present=True,state='ready',encoder=1,motor=2,stamp=[1,1,1,1]))
        h._tipform_send.side_effect = change
        self.assertIsNone(c.read_lane_sample(h,3,1))
    def test_unsupported_driver(self):
        h = self.host('v1',[])
        self.assertIsNone(c.read_lane_sample(h,3,1)); h._tipform_send.assert_not_called()
if __name__=='__main__': unittest.main()
