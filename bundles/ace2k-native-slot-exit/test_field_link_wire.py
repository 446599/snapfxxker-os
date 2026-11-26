"""Exercise the staged adapter against the pinned production link class.

This is offline only. It does not claim to test the yet-unwritten event,
lane-ownership, route-lock, G-code, or packaging integration.
"""
import importlib
from pathlib import Path
import struct
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import Mock

from native_eject_contract import Operation
from native_unload_wire import NativeUnloadWire


class FieldLinkWireTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        archive = (Path(__file__).resolve().parents[1] / 'multiace-190' / 'artifacts' /
                   'multiace-1.90.2-233.20261126.tar.gz')
        with tarfile.open(archive, 'r:gz') as bundle:
            for name in ('ace_protocol.py', 'ace_protocol_ace2k.py'):
                path = 'multiace-1.90.2-233.20261126/klipper/extras/' + name
                (root / name).write_bytes(bundle.extractfile(path).read())
        package = types.ModuleType('native_field_link_test')
        package.__path__ = [str(root)]
        sys.modules[package.__name__] = package
        cls.protocol = importlib.import_module(package.__name__ + '.ace_protocol')
        cls.driver = importlib.import_module(package.__name__ + '.ace_protocol_ace2k')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_native_start_uses_production_link_sequence_and_report_shape(self):
        now = [10.0]
        raw = types.SimpleNamespace(is_open=True, write=Mock(side_effect=lambda data: len(data)))
        link = self.driver.Ace2kLink(raw, clock=lambda: now[0])
        link.uid = struct.pack('<3I', 1, 2, 3)
        encode = Mock(return_value=b'payload')
        link.dictionary = types.SimpleNamespace(has=lambda name: True, encode=encode,
            constant=lambda name, default: {'ACE2K_LANE_SPEED_MIN_UM_S': 9000,
                                             'ACE2K_LANE_SPEED_MAX_UM_S': 80000}.get(name, default))
        link._wait = Mock(return_value=({'lane': 2, 'accepted': 1, 'reason': 0}, None))
        link._send = Mock(return_value=True)
        op = Operation('uid:00000001-00000002-00000003', 'boot-1', 2, 17, 10.0)
        wire = NativeUnloadWire(link, op, lambda: True, self.driver.klipper_block)

        def sample(stamp):
            now[0] = stamp
            link._dispatch('ace2k_sensors_state', {'switches': 4, 'age_ms': 0})
            link._dispatch('ace2k_feed_state', {'mode': bytes(4), 'seq': bytes([0, 0, 16, 0])})
            link._dispatch('ace2k_lane_counters', {'encoder_um': struct.pack('<4i', 0, 0, 55, 0),
                                                    'fg': struct.pack('<4I', 0, 0, 3, 0)})
            return self.protocol.native_sample(link._evidence_sensors, link._feed,
                                               link._evidence_enc, link._fg, 2, now[0])

        a, b = sample(11), sample(12)
        self.assertEqual(wire.start(a, b, device_uid=op.device_uid,
                                    connection_epoch=op.connection_epoch,
                                    speed_um_s=60000), 'started')
        self.assertEqual(link._lane_seq[2], 17)
        encode.assert_called_once_with('ace2k_feed_start',
            lane=2, mode=4, length_um=0, speed_um_s=60000, seq=17, free_tip=0)
        raw.write.assert_called_once_with(b'\x7e' + self.driver.klipper_block(0, b'payload'))
        link._wait.assert_called_once_with('ace2k_feed_start_response', 0, 0.5)


if __name__ == '__main__':
    unittest.main()
