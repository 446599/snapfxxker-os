"""Synthetic AA6C fragment interleaving; full-program reachability is not established."""
from pathlib import Path
import struct
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import *
image=(Path(__file__).resolve().parent.parent/'diagnostic97/ACE2-97O-diagnostic.bin').read_bytes()
u=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
u.mem_map(0x08000000,0x20000);u.mem_write(0x08008000,image)
u.mem_map(0x20000000,0x10000)
sp=0x2000f000; queueptr=0x20004000; target_slot_byte=0x20001603
u.mem_write(sp+60,struct.pack('<I',0x20003000));u.mem_write(0x20003000,struct.pack('<I',queueptr))
u.mem_write(sp+64,struct.pack('<I',target_slot_byte))
events=[];finished=[]
def hook(uc,pc,size,user):
 if pc==0x08016630:
  assert uc.reg_read(UC_ARM_REG_R0)==queueptr
  events.append(bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R1),1))[0])
  uc.reg_write(UC_ARM_REG_R0,1);uc.reg_write(UC_ARM_REG_PC,uc.reg_read(UC_ARM_REG_LR))
 if pc==0x0800aa86:
  finished.append(pc);uc.emu_stop()
u.hook_add(UC_HOOK_CODE,hook)
for slot in (2,3):
 u.reg_write(UC_ARM_REG_SP,sp);u.reg_write(UC_ARM_REG_R7,slot)
 u.emu_start(0x0800aa6d,0,count=100)
assert len(finished)==2 and events==[1,1]
assert bytes(u.mem_read(target_slot_byte,1))==bytes([3])
print('REPRODUCED: two queued event1 bytes carry no slot; shared reader target is overwritten 2 -> 3 before consumption')
