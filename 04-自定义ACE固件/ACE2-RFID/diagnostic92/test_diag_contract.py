import unittest
from diag_contract import request_word, interpret_word

class Contract(unittest.TestCase):
    def test_bounds_and_packing(self):
        self.assertEqual(request_word(3,2),0x800a0302)
        for args in ((4,0),(-1,0),(0,3),(True,0)):
            with self.assertRaises(ValueError): request_word(*args)
    def test_diagnostic_only_and_rejected(self):
        self.assertEqual(interpret_word(0,0x3f800000)['value'],1.0)
        self.assertEqual(interpret_word(2,5)['state'],'diagnostic_unattributed')
        self.assertEqual(interpret_word(2,0xffffffff)['state'],'rejected_or_ambiguous')

if __name__ == '__main__': unittest.main()
