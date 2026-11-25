#!/usr/bin/env python3
"""Static real-byte checks and a LIMITED branch/thunk model, not CPU emulation.
No interrupts, exception entry, scheduler, peripherals or original C are run.
"""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import build_offline as build

def decode_wide_branch(pc,raw,link=False):
    a,b=struct.unpack('<HH',raw)
    assert a&0xf800==0xf000
    assert b&0xd000==(0xd000 if link else 0x9000)
    s=(a>>10)&1; j1=(b>>13)&1; j2=(b>>11)&1
    i1=(~(j1^s))&1; i2=(~(j2^s))&1
    imm=(s<<24)|(i1<<23)|(i2<<22)|((a&0x3ff)<<12)|((b&0x7ff)<<1)
    if s: imm-=1<<25
    return pc+4+imm

class ImageChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stock=(ROOT.parent/'input/ACE2_V1.1.31_20260306.bin').read_bytes()
        cls.image=(ROOT/'output/EXPERIMENTAL-DO-NOT-FLASH.offline-image').read_bytes()
        cls.m=json.loads((ROOT/'output/analysis-manifest.json').read_text())
        cls.syms=json.loads((ROOT/'build/symbols.json').read_text())
        cls.dis=(ROOT/'build/integration.dis').read_text()
    def raw(self,address,n): return self.image[address-build.BASE:address-build.BASE+n]
    def test_01_complete_prefix_manifest(self):
        self.assertEqual(hashlib.sha256(self.stock).hexdigest(),build.STOCK_SHA)
        self.assertEqual(hashlib.sha256(self.image).hexdigest(),self.m['image_sha256'])
        reconstructed=bytearray(self.stock[:-8]);seen=set()
        for c in self.m['prefix_changes']:
            address=int(c['address'],16);before=bytes.fromhex(c['expected_bytes'])
            after=bytes.fromhex(c['replacement_bytes']);off=address-build.BASE
            self.assertEqual(len(before),len(after))
            self.assertEqual(reconstructed[off:off+len(before)],before)
            for i in range(off,off+len(before)):
                self.assertNotIn(i,seen);seen.add(i)
            reconstructed[off:off+len(before)]=after
        self.assertEqual(reconstructed,self.image[:build.STOCK_SIZE-8])
        self.assertEqual(len(self.m['prefix_changes']),21)
        for addr,n in [(0x0800f6f8,2),(0x0800f724,2),(0x0800e216,1),(0x0800e21c,1),(0x0800e220,1)]:
            off=addr-build.BASE;self.assertEqual(self.raw(addr,n),self.stock[off:off+n])
    def test_02_all_actual_branches(self):
        for h in self.m['hooks']:
            src=int(h['address'],16);dst=int(h['target'],16)
            self.assertEqual(decode_wide_branch(src,self.raw(src,4),h['branch_kind']=='BL'),dst)
            self.assertEqual(dst,self.syms[h['symbol']]);self.assertEqual(dst&1,0)
            if len(bytes.fromhex(h['expected_bytes']))==6:
                self.assertEqual(self.raw(src+4,2),b'\x00\xbf')
        # The overwritten stock boundaries are complete instructions, not half
        # of the original LDRD/MOVW. Assertions use independent stock lengths.
        for address in (0x0800f574,0x0800f5d0,0x0800ea14):
            entries=[h for h in self.m['hooks'] if int(h['address'],16)==address]
            self.assertEqual(len(bytes.fromhex(entries[0]['expected_bytes'])),6)
    def test_03_normal_reset_limited_branch_model(self):
        gate=self.syms['experiment_reset_gate']
        self.assertEqual(self.raw(gate,2),b'\xfe\xe7')
        vector=struct.unpack_from('<I',self.image,4)[0]
        self.assertEqual(vector,gate|1)
        for start in (vector&~1,0x08008244):
            pc=start
            for _ in range(1024):
                half=struct.unpack('<H',self.raw(pc,2))[0]
                if half&0xf800==0xe000:
                    imm=half&0x7ff
                    if imm&0x400: imm-=0x800
                    pc=pc+4+imm*2
                else: pc=decode_wide_branch(pc,self.raw(pc,4))
                self.assertEqual(pc,gate)
        # This deliberately leaves exception vectors as stock. No claim about
        # arbitrary PC entry or interrupt/NMI/HardFault behavior is permitted.
        self.assertEqual(self.image[8:64],self.stock[8:64])
        self.assertFalse(self.m['exception_paths_quarantined'])
    def test_04_not_an_updater_image(self):
        self.assertEqual(self.image[-8:],b'NOFLASH!')
        self.assertNotEqual(self.image[-8:],build.MAGIC)
        self.assertNotIn(b'V1.1.90O',self.image)
        self.assertIn(b'EXPRF!!\0',self.image)
        self.assertFalse((ROOT/'output/patch.json').exists())
        for args in ([],['--force'],['--out','FORBIDDEN.bin']):
            result=subprocess.run([sys.executable,str(ROOT/'build_offline.py'),*args],capture_output=True)
            self.assertNotEqual(result.returncode,0)
        self.assertFalse((ROOT/'FORBIDDEN.bin').exists())
    def test_05_real_thunk_abi_limited_model(self):
        pc=self.syms['br_stock_transceive_entry'];raw=self.raw(pc,24)
        self.assertEqual(raw[:12],bytes.fromhex('10b584b0069c0094079c0194'))
        self.assertEqual(raw[12:16],bytes.fromhex('cdf808e0'))
        self.assertEqual(decode_wide_branch(pc+16,raw[16:20],link=True),self.syms['br_stock_transceive_checked'])
        self.assertEqual(raw[20:],bytes.fromhex('04b010bd'))
        for caller_lr in (0x0800dc41,0x0800dd05,0x0800e00d,0x0800e217,0x0800e187):
            initial_sp=0x20009000;sp=initial_sp;old_r4=0x44444444
            memory={sp:0x20001020,sp+4:0x20001060}
            # Model only the above checked bytes, stopping at the C BL call.
            sp-=8;memory[sp]=old_r4;memory[sp+4]=caller_lr
            sp-=16;memory[sp]=memory[sp+24];memory[sp+4]=memory[sp+28];memory[sp+8]=caller_lr
            self.assertEqual(sp%8,0)
            self.assertEqual([memory[sp+i] for i in (0,4,8)],[0x20001020,0x20001060,caller_lr])
            sp+=16;self.assertEqual(memory[sp],old_r4);self.assertEqual(memory[sp+4],caller_lr)
            sp+=8;self.assertEqual(sp,initial_sp)
    def test_06_publication_stubs_cannot_fallback(self):
        for name in ('rawtag_stub','uid_stub'):
            pc=self.syms[name]
            self.assertEqual(decode_wide_branch(pc,self.raw(pc,4)),self.syms['ex_raw_reject_stub'])
        pc=self.syms['ex_raw_reject_stub']
        self.assertEqual(decode_wide_branch(pc,self.raw(pc,4),True),self.syms['ex_stop_request'])
        self.assertEqual(self.raw(pc+4,2),bytes.fromhex('0120')) # status 1, never SUCCESS=0
        self.assertEqual(decode_wide_branch(pc+6,self.raw(pc+6,4)),0x0800e904)
        for name in ('rawtag_cache_stub','rawtag_extract_stub'):
            pc=self.syms[name]
            self.assertEqual(decode_wide_branch(pc,self.raw(pc,4),True),self.syms['ex_stop_request'])
            self.assertEqual(self.raw(pc+4,2),bytes.fromhex('0026')) # byte count 0
            self.assertEqual(decode_wide_branch(pc+6,self.raw(pc+6,4)),0x0800fe94)
        pc=self.syms['pageread_gate_stub']
        self.assertEqual(decode_wide_branch(pc,self.raw(pc,4),True),self.syms['ex_stop_request'])
        self.assertEqual(self.raw(pc+4,4),bytes.fromhex('4ff0000a')) # r10 = 0
        self.assertEqual(decode_wide_branch(pc+8,self.raw(pc+8,4)),0x0800e23c)
    def test_07_zero_unresolved_symbols(self):
        self.assertEqual((ROOT/'build/unresolved.txt').read_text().strip(),'')
        self.assertEqual(self.m['unresolved_symbols'],[])
        self.assertFalse(self.m['ram_verified_for_device'])
        self.assertFalse(self.m['physical_stop_implemented'])
        self.assertFalse(self.m['physical_cleanup_proven'])
    def test_08_real_factory_link_and_failure_barrier(self):
        pc=self.syms['ex_event_factory_entry'];raw=self.raw(pc,18)
        self.assertEqual(raw[:2],bytes.fromhex('10b5'))
        self.assertEqual(decode_wide_branch(pc+2,raw[2:6],True),self.syms['ex_event_factory'])
        self.assertEqual(raw[6:8],bytes.fromhex('00b1')) # CBZ r0 to failure pop
        self.assertEqual(raw[8:10],bytes.fromhex('10bd')) # nonzero returns to 116AC
        self.assertEqual(raw[10:14],bytes.fromhex('bde81040'))
        self.assertEqual(decode_wide_branch(pc+14,raw[14:18]),self.syms['experiment_reset_gate'])
        self.assertEqual(decode_wide_branch(0x080116a8,self.raw(0x080116a8,4),True),pc)
        self.assertEqual(self.syms['ex_stock_malloc']&~1,0x080132c4)
        self.assertEqual(self.syms['ex_stock_event_factory']&~1,0x0801613e)
        self.assertEqual(self.m['arm_extended_payload_bytes'],176)
        self.assertEqual(self.m['arm_tail_bytes'],144)
        self.assertEqual(self.m['arm_allocator_block_min'],184)
        # Actual linked ELF has no guessed data/BSS region for RF state.
        self.assertNotIn('rf_device_state',self.syms)
        self.assertNotIn('ex_state',self.syms)
        self.assertIn('0\t      0',(ROOT/'build/size.txt').read_text())

if __name__=='__main__':unittest.main(verbosity=2)
