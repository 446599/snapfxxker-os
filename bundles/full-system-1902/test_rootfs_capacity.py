import unittest

from rootfs_capacity import check_capacity

MIB = 1024 * 1024
BASE = 263831552
NEW = 263970816


def unpack_log(size=BASE, start_a=172032, count_a=614400,
               start_b=786432, count_b=614400, file_b='rootfs.img'):
    blocks = (size + 2047) // 2048
    return (f'Unpacking: name=system_a filename=rootfs.img\tnand_addr={start_a}/{count_a}\tpos=22183936/{size}/{blocks}\n'
            f'Unpacking: name=system_b filename={file_b}\tnand_addr={start_b}/{count_b}\tpos=22183936/{size}/{blocks}\n')


class RootfsCapacityTest(unittest.TestCase):
    def test_actual_base_geometry_growth_allowed(self):
        r = check_capacity(unpack_log(), BASE, NEW)
        self.assertEqual(r['limit_bytes'], 300 * MIB)
        self.assertEqual(r['free_bytes'], 300 * MIB - NEW)
        self.assertGreater(NEW, BASE)

    def test_exact_fit(self):
        self.assertEqual(check_capacity(unpack_log(), BASE, 300 * MIB)['free_bytes'], 0)

    def test_oversize_by_one_byte(self):
        with self.assertRaisesRegex(ValueError, 'does not fit'):
            check_capacity(unpack_log(), BASE, 300 * MIB + 1)

    def test_smaller_slot_sets_limit(self):
        r = check_capacity(unpack_log(count_b=550000), BASE, NEW)
        self.assertEqual(r['limit_bytes'], 550000 * 512)

    def test_missing_or_wrong_mapping(self):
        for log in (unpack_log().splitlines()[0], unpack_log(file_b='other.img')):
            with self.subTest(log=log), self.assertRaises(ValueError):
                check_capacity(log, BASE, NEW)

    def test_overlap_or_unknown_capacity(self):
        for log in (unpack_log(start_b=700000), unpack_log(count_b=0xffffffff)):
            with self.subTest(log=log), self.assertRaises(ValueError):
                check_capacity(log, BASE, NEW)

    def test_two_different_payloads_rejected(self):
        log = unpack_log().replace('pos=22183936/', 'pos=22183937/', 1)
        with self.assertRaisesRegex(ValueError, 'same rootfs payload'):
            check_capacity(log, BASE, NEW)

    def test_metadata_size_mismatch(self):
        with self.assertRaisesRegex(ValueError, 'metadata'):
            check_capacity(unpack_log(), BASE+1, NEW)

    def test_final_readback_geometry(self):
        r = check_capacity(unpack_log(), BASE, NEW, unpack_log(size=NEW))
        self.assertTrue(r['readback_checked'])
        with self.assertRaisesRegex(ValueError, 'geometry changed'):
            check_capacity(unpack_log(), BASE, NEW, unpack_log(size=NEW, count_b=614401))

    def test_final_readback_size_mismatch(self):
        with self.assertRaisesRegex(ValueError, 'metadata'):
            check_capacity(unpack_log(), BASE, NEW, unpack_log())


if __name__ == '__main__':
    unittest.main()
