"""Execute 97 Thumb worker branch; mock memcmp only, not RF/RC522 hardware."""
from pathlib import Path
from unicorn import Uc,UC_ARCH_ARM,UC_MODE_THUMB,UC_MODE_MCLASS,UC_HOOK_CODE
from unicorn.arm_const import *
img=(Path(__file__).resolve().parents[2]/'diagnostic97/ACE2-97O-diagnostic.bin').read_bytes()
def run(neighbor_match):
 u=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS);u.mem_map(0x08000000,0x20000);u.mem_write(0x08008000,img);u.mem_map(0x20000000,0x10000)
 slotbase=0x20001600; reader=1; neighbor=2; selection=0x20000ec4; sp=0x2000e000
 u.mem_write(0x20000054+neighbor*164+282,b'\x02')
 u.mem_write(slotbase+reader*164+34,b'\x02')
 u.reg_write(UC_ARM_REG_R5,reader);u.reg_write(UC_ARM_REG_R8,neighbor);u.reg_write(UC_ARM_REG_R10,slotbase)
 u.reg_write(UC_ARM_REG_R6,selection);u.reg_write(UC_ARM_REG_R3,slotbase+reader*164+12);u.reg_write(UC_ARM_REG_R1,0x20003000);u.reg_write(UC_ARM_REG_R4,0x20004000);u.reg_write(UC_ARM_REG_SP,sp)
 calls=[];ends=[]
 def hook(uc,pc,size,user):
  if pc==0x08008498:
   calls.append((uc.reg_read(UC_ARM_REG_R0),uc.reg_read(UC_ARM_REG_R1)))
   uc.reg_write(UC_ARM_REG_R0,0 if len(calls)>1 or neighbor_match else 1);uc.reg_write(UC_ARM_REG_PC,uc.reg_read(UC_ARM_REG_LR))
  elif pc in (0x0800e51a,0x0800e108):ends.append(pc);uc.emu_stop()
 u.hook_add(UC_HOOK_CODE,hook);u.emu_start(0x0800e347,0,count=300)
 return calls,ends
calls,end=run(True);assert len(calls)==1 and end==[0x0800e51a],(calls,end)
calls,end=run(False);assert len(calls)==2 and end==[0x0800e108],(calls,end)
print('PASS: known-neighbor UID branch returns without HALT; candidate UID branch reaches HALT')
