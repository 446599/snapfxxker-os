"""Actual 97O INSERT callback enqueue, with RTOS notifications mocked."""
from pathlib import Path
import struct
from unicorn import Uc,UC_ARCH_ARM,UC_MODE_THUMB,UC_MODE_MCLASS,UC_HOOK_CODE
from unicorn.arm_const import *
image=(Path(__file__).resolve().parent.parent/'diagnostic97/ACE2-97O-diagnostic.bin').read_bytes()
u=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
u.mem_map(0x08000000,0x20000);u.mem_write(0x08008000,image);u.mem_map(0x20000000,0x10000)
manager=0x200012e8;reader=1;seg=manager+reader*208;lst=seg+60;sentinel=lst+8
w32=lambda a,v:u.mem_write(a,struct.pack('<I',v))
r32=lambda a:struct.unpack('<I',u.mem_read(a,4))[0]
w32(0x20003000,manager);w32(lst,0);w32(lst+4,sentinel);w32(sentinel+4,sentinel);w32(sentinel+8,sentinel)
u.mem_write(manager+52+reader,b'\xff');w32(manager+56,0x20005000);w32(manager+4,0x20006000)
for slot in (2,3):
 rec=seg+slot*32;u.mem_write(rec+140,bytes([slot]));w32(rec+160,rec+140)
notify=[];finished=[];motor=[]
def hook(uc,pc,size,user):
 if pc==0x0800f7f8:finished.append(pc);uc.emu_stop()
 if pc==0x0800ece8:motor.append(pc);uc.emu_stop()
 if pc in (0x080177a4,0x0800b474,0x08016c38,0x08016630,0x080172c4):
  if pc==0x080172c4:notify.append(uc.reg_read(UC_ARM_REG_R0))
  uc.reg_write(UC_ARM_REG_R0,1);uc.reg_write(UC_ARM_REG_PC,uc.reg_read(UC_ARM_REG_LR))
u.hook_add(UC_HOOK_CODE,hook)
for key in (8,12):
 for reg,val in ((UC_ARM_REG_R0,0x20003000),(UC_ARM_REG_R1,key),(UC_ARM_REG_R2,0),(UC_ARM_REG_R3,1),(UC_ARM_REG_SP,0x2000f000)):
  u.reg_write(reg,val)
 try: u.emu_start(0x0800f699,0,count=400)
 except Exception:
  print("pc",hex(u.reg_read(UC_ARM_REG_PC)),"r0",hex(u.reg_read(UC_ARM_REG_R0)),"r1",hex(u.reg_read(UC_ARM_REG_R1)),"r2",hex(u.reg_read(UC_ARM_REG_R2)));raise
assert len(finished)==2 and not motor and len(notify)==1,(finished,notify,motor)
assert r32(lst)==2
node=r32(sentinel+4);slots=[]
for _ in range(2):
 owner=r32(node+12);slots.append(u.mem_read(owner,1)[0]);node=r32(node+4)
assert slots==[2,3] and node==sentinel,slots
assert u.mem_read(manager+52+reader,1)==b'\x01'
assert u.mem_read(0x20001603,1)==b'\x00' # event1 mapping not yet touched
print('PASS: two same-reader INSERT callbacks queue slot2->slot3 on one list, issue one worker notification, and do not start motors')
