"""97O reader assignment fragment: queued same-reader jobs share one worker."""
from pathlib import Path
import struct
from unicorn import Uc,UC_ARCH_ARM,UC_MODE_THUMB,UC_MODE_MCLASS,UC_HOOK_CODE
from unicorn.arm_const import *
image=(Path(__file__).resolve().parent.parent/'diagnostic97/ACE2-97O-diagnostic.bin').read_bytes()
u=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
u.mem_map(0x08000000,0x20000);u.mem_write(0x08008000,image);u.mem_map(0x20000000,0x10000)
base=0x200012e8;reader=1
u.mem_write(base+52+reader,b'\xff');u.mem_write(base+56,struct.pack('<I',0x20005000))
u.mem_write(base+4,struct.pack('<I',0x20006000));u.mem_write(base+8,struct.pack('<I',0x20006004))
notify=[];done=[]
def hook(uc,pc,size,user):
 if pc in (0x08016c38,0x08016630,0x080172c4):
  if pc==0x080172c4:notify.append((uc.reg_read(UC_ARM_REG_R0),uc.reg_read(UC_ARM_REG_R2)))
  uc.reg_write(UC_ARM_REG_R0,1);uc.reg_write(UC_ARM_REG_PC,uc.reg_read(UC_ARM_REG_LR))
 if pc==0x0800f7f8:done.append(1);uc.emu_stop()
u.hook_add(UC_HOOK_CODE,hook)
for worker in (1,2):
 u.reg_write(UC_ARM_REG_R5,base);u.reg_write(UC_ARM_REG_R6,reader)
 u.reg_write(UC_ARM_REG_R0,worker);u.reg_write(UC_ARM_REG_SP,0x2000f000)
 u.emu_start(0x0800f79f,0,count=100)
assert len(done)==2 and len(notify)==1,notify
assert u.mem_read(base+52+reader,1)==b'\x01'
print('PASS: sequential same-reader enqueue fragments retain first assigned worker and emit one task notification')
