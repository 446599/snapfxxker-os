import importlib.util
import json
import unittest
from pathlib import Path

here=Path(__file__).resolve().parent; root=here.parent
sp=importlib.util.spec_from_file_location('verify_image', here/'verify_patch_image.py');m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m)
base=(root/'input/ACE2_V1.1.31_20260306.bin').read_bytes()
spec=json.loads((root/'source/firmware/patch.json').read_text())
image=(root/'output/EXPERIMENTAL-DO-NOT-FLASH-1.1.90O.bin').read_bytes()
class TestIntegrity(unittest.TestCase):
 def test_current(self): self.assertEqual(m.verify(base,spec,image)['size'],78672)
 def test_bad_base(self):
  b=bytearray(base);b[33]^=1
  with self.assertRaises(ValueError):m.verify(bytes(b),spec,image)
 def test_bad_image(self):
  b=bytearray(image);b[33]^=1
  with self.assertRaises(ValueError):m.verify(base,spec,bytes(b))
 def test_bad_crc(self):
  s=dict(spec);s['result_crc16']^=1
  with self.assertRaises(ValueError):m.verify(base,s,image)
 def test_overlap(self):
  s=json.loads(json.dumps(spec));s['hooks'].append(s['hooks'][0])
  with self.assertRaises(ValueError):m.verify(base,s,image)
 def test_unknown_hook(self):
  s=json.loads(json.dumps(spec));s['hooks'][0]['expect_hex']='00000000'
  with self.assertRaises(ValueError):m.verify(base,s,image)
 def test_wrong_version(self):
  s=dict(spec);s['version_string']='V1.1.60O'
  with self.assertRaises(ValueError):m.verify(base,s,image)
if __name__=='__main__': unittest.main()
