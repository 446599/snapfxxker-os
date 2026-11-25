import unittest
from pathlib import Path
from audit_stock import SITES, audit, check_sites, BASE

class StockAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = (Path(__file__).parent.parent / "input/ACE2_V1.1.31_20260306.bin").read_bytes()

    def test_stock_is_evidence_not_flash_approval(self):
        report = audit(self.data)
        self.assertFalse(report["production_integration"])
        self.assertFalse(report["heap"]["safe_for_state"])
        self.assertEqual(report["evidence_sites_checked"], 24)

    def test_wrong_image_rejected(self):
        for image in (b"", self.data[:-1], self.data + b"\0", b"\0" + self.data[1:]):
            with self.assertRaises(ValueError):
                audit(image)

    def test_each_evidence_site_mutation_is_detected(self):
        for address, _, description in SITES:
            with self.subTest(description=description):
                image = bytearray(self.data)
                image[address-BASE] ^= 1
                with self.assertRaises(ValueError):
                    check_sites(image)

if __name__ == "__main__":
    unittest.main()
