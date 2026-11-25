#!/usr/bin/env python3
"""Current combined accessor image checks, not the old RF-only test entry."""
import hashlib,json,struct,subprocess,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent))
import build as ph
from merge_offline import decode_branch,check_changes
class Image(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.m=json.loads((ROOT/'output/manifest.json').read_text())
  cls.img=(ROOT/'output/ACCESSOR-NOFLASH.offline-image').read_bytes()
  cls.elf=ROOT/'build/ACCESSOR-NOFLASH.elf'
  cls.stock=ph.STOCK.read_bytes()
  cls.sym=json.loads((ROOT/'build/symbols.json').read_text())
 def test_01_exact_hashes(self):
  self.assertEqual(hashlib.sha256(self.img).hexdigest(),self.m['image_sha256'])
  self.assertEqual(hashlib.sha256(self.elf.read_bytes()).hexdigest(),self.m['elf_sha256'])
  for f,sha in self.m['sources_sha256'].items():self.assertEqual(hashlib.sha256((ROOT/'firmware'/f).read_bytes()).hexdigest(),sha)
 def test_02_full_prefix_reconstruction(self):
  b=bytearray(self.stock[:-8]);changes=[]
  for h in self.m['hooks']:
   addr=int(h['address'],16);before=bytes.fromhex(h['expected_bytes']);after=bytes.fromhex(h['replacement_bytes'])
   changes.append(dict(address=addr,before=before,after=after,reason=h['symbol'],component=h['component']))
  check_changes(self.stock,changes)
  for c in changes:
   off=c['address']-ph.BASE;b[off:off+len(c['after'])]=c['after']
  struct.pack_into('<I',b,4,self.sym['experiment_reset_gate']|1)
  vi=b.index(b'V1.1.31\0');b[vi:vi+8]=b'EXUNI!!\0'
  self.assertEqual(b,self.img[:len(self.stock)-8])
 def test_03_branches(self):
  self.assertEqual(len(self.m['hooks']),39)
  for h in self.m['hooks']:
   addr=int(h['address'],16);target,kind=decode_branch(addr,self.img[addr-ph.BASE:addr-ph.BASE+4])
   self.assertEqual(target,self.sym[h['symbol']]);self.assertEqual(kind.upper(),h['branch_kind'])
  startup=[h for h in self.m['hooks'] if h['address']=='0x80116a8']
  self.assertEqual(len(startup),1);self.assertEqual(startup[0]['branch_kind'],'BL')
 def test_04_in_application_region(self):
  self.assertEqual(int(self.m['payload_origin'],16),ph.BASE+len(self.stock)-8)
  self.assertEqual(ph.BASE+len(self.img),int(self.m['image_end_exclusive'],16))
  self.assertLess(ph.BASE+len(self.img),0x08024000)
  self.assertEqual(len(self.img),len(self.stock)+self.m['payload_bytes'])
 def test_05_barriers(self):
  gate=self.sym['experiment_reset_gate'];self.assertEqual(self.img[gate-ph.BASE:gate-ph.BASE+2],b'\xfe\xe7')
  self.assertEqual(struct.unpack_from('<I',self.img,4)[0],gate|1)
  self.assertEqual(decode_branch(0x08008244,self.img[0x244:0x248])[0],gate)
  self.assertEqual(self.img[-8:],b'NOFLASH!')
  for opts in ([],['--force'],['--out','wrong.bin']):
   p=subprocess.run([sys.executable,str(ROOT/'build_accessor.py'),*opts],capture_output=True)
   self.assertNotEqual(p.returncode,0)
 def test_06_no_static_state_or_fake_binding(self):
  self.assertNotIn('ph_storage',self.sym);self.assertNotIn('ph_init_exclusive',self.sym)
  self.assertIn('ph_platform_state',self.sym);self.assertIn('ph_raw_lane_disable',self.sym)
  self.assertEqual(self.m['arm_tail_bytes'],608);self.assertEqual(self.m['arm_payload_bytes'],640)
  self.assertEqual(self.m['arm_minimum_allocator_block'],648)
  log=(ROOT/'build/production-link-rejected.log').read_text()
  for s in self.m['unresolved_production_proofs']:self.assertIn("undefined reference to `"+s+"'",log)
  self.assertFalse(self.m['auth_failure_source_connected']);self.assertFalse(self.m['physical_stop_proven'])
 def test_07_normal_insert_not_blanket_disabled(self):
  for a,n in ((0x0800f6ae,4),(0x0800f6f8,2),(0x0800f724,2)):
   o=a-ph.BASE;self.assertEqual(self.img[o:o+n],self.stock[o:o+n])
 def test_08_conflicts_refused(self):
  address=0x080116a8;before=self.stock[address-ph.BASE:address-ph.BASE+4]
  c=dict(address=address,before=before,after=before,reason='startup',component='RF')
  with self.assertRaisesRegex(ValueError,'overlap'):check_changes(self.stock,[c,dict(c,component='other fragment')])
  partial=dict(address=address+2,before=before[2:],after=before[2:],reason='partial',component='other')
  with self.assertRaisesRegex(ValueError,'overlap'):check_changes(self.stock,[c,partial])
  with self.assertRaisesRegex(ValueError,'mismatch'):check_changes(self.stock,[dict(c,before=b'\0'*4)])
 def test_09_result_version_matches(self):
  r=json.loads((ROOT/'build/dynamic-instruction-results.json').read_text())
  self.assertEqual(r['image_sha256'],self.m['image_sha256'])
  self.assertTrue(r['missing_storage_known_lane_stop_effects'])
  self.assertTrue(r['missing_storage_publisher_ownership_unresolved'])
  for f in ('queue-instruction-results.json','queue-commit-results.json','queue-cost-results.json'):
   q=json.loads((ROOT/'build'/f).read_text());self.assertEqual(q['image_sha256'],self.m['image_sha256'])
if __name__=='__main__':unittest.main(verbosity=2)
