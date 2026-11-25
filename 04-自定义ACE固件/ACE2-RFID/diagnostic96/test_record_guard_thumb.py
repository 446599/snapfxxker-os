"""Execute the linked 96O decoder address guard; no device I/O."""
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import *
image=(Path(__file__).parent/'ACE2-96O-diagnostic.bin').read_bytes()
entry,admitted,ret=0x0801ab5c,0x0801ab92,0x08007000
valid=(0x2000160c,0x200016b0,0x20001754,0x200017f8)
invalid=(0,1,0x20001608,0x2000160d,0x200016af,0x20001748,0x2000189c,0x200018a4,0x20001900,0xffffffff)
for address in valid+invalid:
 u=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
 u.mem_map(0x08000000,0x20000);u.mem_write(0x08008000,image)
 u.mem_map(0x20000000,0x10000);u.mem_write(0x20001000,b'\xa5'*4096)
 u.reg_write(UC_ARM_REG_R0,address);u.reg_write(UC_ARM_REG_R1,0x20004000)
 u.reg_write(UC_ARM_REG_R2,0);u.reg_write(UC_ARM_REG_R3,0)
 u.reg_write(UC_ARM_REG_SP,0x2000f000);u.reg_write(UC_ARM_REG_LR,ret|1)
 reached=[]
 def end(uc,pc,size,user):
  if pc in (admitted,ret): reached.append(pc);uc.emu_stop()
 u.hook_add(UC_HOOK_CODE,end);u.emu_start(entry|1,0,count=100)
 assert reached==[admitted if address in valid else ret],(hex(address),reached)
 assert bytes(u.mem_read(0x20001000,4096))==b'\xa5'*4096
 if address not in valid:
  assert u.reg_read(UC_ARM_REG_R0)==0xffffffff
  assert u.reg_read(UC_ARM_REG_SP)==0x2000f000
print('PASS: linked 96O Thumb guard admits only four exact records; invalid pointers return -1 with stack restored')
