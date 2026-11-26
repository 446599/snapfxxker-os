"""Use the exact pinned field driver loaded by the existing wire tests."""
import struct
import types
import unittest

from test_field_link_wire import FieldLinkWireTest
from cloud_link_evidence import LinkEvidence
from native_eject_contract import Operation


class LinkEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FieldLinkWireTest.setUpClass()
        cls.driver = FieldLinkWireTest.driver

    @classmethod
    def tearDownClass(cls):
        FieldLinkWireTest.tearDownClass()

    def setUp(self):
        self.now = [10.0]
        raw = types.SimpleNamespace(is_open=True)
        self.link = self.driver.Ace2kLink(raw, clock=lambda: self.now[0])
        self.link.uid = struct.pack("<3I", 1, 2, 3)
        self.evidence = LinkEvidence(self.link)
        self.op = Operation(self.evidence.uid, self.evidence.epoch, 2, 17, 10.0)
        self.event = dict(lane=2, seq=17, mode=3, kind=6)

    def report(self, time, lane=2, present=False):
        self.now[0] = time
        self.link._dispatch("ace2k_sensors_state", {"switches": (1 << lane) if present else 0, "age_ms": 0})
        self.link._dispatch("ace2k_feed_state", {"mode": bytes(4), "seq": bytes([0, 0, 17, 0])})
        self.link._dispatch("ace2k_lane_counters", {"encoder_um": struct.pack("<4i", 0, 0, 55, 0),
                                                    "fg": struct.pack("<4I", 0, 0, 3, 0)})
        return self.evidence.sample(lane)

    def test_real_lane_cache_bound_to_operation(self):
        a, b = self.report(12), self.report(13)
        self.assertEqual(self.evidence.verdict(self.op, self.event, a, b, 11), "complete")
        wrong = self.report(14, lane=1)
        self.assertEqual(self.evidence.verdict(self.op, self.event, a, wrong, 11), "unconfirmed")

    def test_reconnect_invalidates_same_uid_and_new_epoch(self):
        a, b = self.report(12), self.report(13)
        self.evidence.invalidate()
        self.assertIsNone(self.evidence.sample(2))
        self.assertEqual(self.evidence.verdict(self.op, self.event, a, b, 11), "unconfirmed")
        other = LinkEvidence(self.link)
        self.assertNotEqual(other.epoch, self.evidence.epoch)
        self.assertEqual(other.verdict(self.op, self.event, a, b, 11), "unconfirmed")

    def test_old_reports_and_empty_without_event_fail(self):
        a, b = self.report(12), self.report(13)
        self.assertEqual(self.evidence.verdict(self.op, {}, a, b, 11), "unconfirmed")
        self.assertEqual(self.evidence.verdict(self.op, self.event, a, b, 12), "unconfirmed")
        self.assertEqual(self.evidence.verdict(self.op, self.event, b, b, 11), "unconfirmed")

    def test_raw_transport_replaced_or_dead(self):
        self.report(12)
        self.link._raw = types.SimpleNamespace(is_open=True)
        self.assertIsNone(self.evidence.sample(2))


if __name__ == "__main__":
    unittest.main()
