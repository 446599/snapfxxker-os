import tempfile
import unittest
from pathlib import Path
from park_return import ParkReturnStore,encoder_distance_mm

class ParkReturnTests(unittest.TestCase):
    def test_native_units_and_wrap(self):
        self.assertAlmostEqual(encoder_distance_mm(0,1634567),1634.567)
        self.assertAlmostEqual(encoder_distance_mm(2147000000,-2146627296),1340.)
        self.assertAlmostEqual(encoder_distance_mm(1000000,-500000),1500.)

    def test_invalid_measurement(self):
        for a,b in [(0,0),(0,1000),(0,6000000),(False,1000000)]:
            with self.assertRaises(ValueError):
                encoder_distance_mm(a,b)

    def test_persist_and_scope(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'park.json'
            store=ParkReturnStore(path)
            with self.assertRaises(ValueError):
                store.length('uid:A',0)
            store.record('uid:A',0,1630)
            self.assertEqual(ParkReturnStore(path).length('uid:A',0),1630)
            for uid,slot in [('uid:A',1),('uid:B',0)]:
                with self.assertRaises(ValueError):
                    store.length(uid,slot)
            store.record('uid:B',0,1700)
            store.forget('uid:A',0)
            with self.assertRaises(ValueError):
                store.length('uid:A',0)
            self.assertEqual(store.length('uid:B',0),1700)

    def test_corrupt_store_not_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'park.json'
            path.write_text('broken')
            with self.assertRaises(ValueError):
                ParkReturnStore(path).record('uid:A',0,1600)
            self.assertEqual(path.read_text(),'broken')
