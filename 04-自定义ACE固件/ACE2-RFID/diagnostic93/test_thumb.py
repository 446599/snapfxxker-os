"""Run the linked 92O RC522 diagnostic path in Unicorn; no device I/O."""
from pathlib import Path
import struct
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm_const import *

image=(Path(__file__).parent/'ACE2-93O-diagnostic.bin').read_bytes()
RC=0x0801b1a2
END=0x0800e904
for primask in (0,1):
 for lane in range(5):
  for field in range(6):
   for busy in (-1,0,1,2,3):
    u=Uc(UC_ARCH_ARM,UC_MODE_THUMB | UC_MODE_MCLASS)
    u.mem_map(0x08000000,0x20000);u.mem_write(0x08008000,image)
    u.mem_map(0x20000000,0x10000)
    status=[0]*4
    if busy>=0: status[busy]=2
    u.mem_write(0x20001004,struct.pack('<4I',*status))
    u.mem_write(0x20001600,bytes([7,8]));u.mem_write(0x20000098,bytes([1]))
    for i in range(4):
     addr=0x200018a4+60*i
     for off in (0,4,12,16): u.mem_write(addr+off,struct.pack('<I',0x11000000+i*0x100+off))
    request=0x80000000 | (10<<16) | (lane<<8) | field
    u.mem_write(0x20003000,struct.pack('<I',request))
    u.reg_write(UC_ARM_REG_R7,0x20003000);u.reg_write(UC_ARM_REG_R4,0x20004000)
    u.reg_write(UC_ARM_REG_SP,0x2000f000);u.reg_write(UC_ARM_REG_PRIMASK,primask)
    writes=[]
    u.hook_add(UC_HOOK_MEM_WRITE,lambda uc,a,address,size,value,user: writes.append((address,size)))
    reached=[]
    def stop_at_end(uc,address,size,user):
     if address==END: reached.append(address); uc.emu_stop()
    u.hook_add(UC_HOOK_CODE,stop_at_end)
    u.emu_start(RC|1,0,count=100)
    assert reached == [END], 'did not reach epilogue'
    val=u.reg_read(UC_ARM_REG_R0)
    exp=0xffffffff if lane>=4 or field>=5 or busy>=0 else ((7,8)[lane//2] if field==3 else 0x11000000+lane*0x100+(0,4,16,0,12)[field])
    assert val==exp,(lane,field,busy,hex(val),hex(exp))
    assert u.reg_read(UC_ARM_REG_PRIMASK)==primask
    assert u.reg_read(UC_ARM_REG_R4)==0x20004000 and u.reg_read(UC_ARM_REG_R7)==0x20003000
    assert not writes,writes
print('PASS: 300 linked ARM Thumb diagnostic executions; lane/field bounds, all-lane busy, PRIMASK 0/1, r4/r7, no writes')
# Simulate caller's saved register frame and execute patched dispatch through the
# original epilogue; only expected response write at r4+140 is permitted.
u=Uc(UC_ARCH_ARM,UC_MODE_THUMB | UC_MODE_MCLASS)
u.mem_map(0x08000000,0x20000); u.mem_write(0x08008000,image)
u.mem_map(0x20000000,0x10000)
u.mem_write(0x20001004,b'\0'*16); u.mem_write(0x200018a4,struct.pack('<I',0x12345678))
u.mem_write(0x20003000,struct.pack('<I',0x800a0000))
saved=[0x20004000,0x55555555,0x66666666,0x20003000,0x88888888,0x99999999,0xaaaaaaaa,0x08007001]
u.mem_write(0x2000f000+168,struct.pack('<8I',*saved))
u.reg_write(UC_ARM_REG_SP,0x2000f000);u.reg_write(UC_ARM_REG_R4,saved[0]);u.reg_write(UC_ARM_REG_R7,saved[3]);u.reg_write(UC_ARM_REG_R0,0x800a0000)
writes=[]
u.hook_add(UC_HOOK_MEM_WRITE,lambda uc,a,address,size,value,user:writes.append((address,size,value)))
reached=[]
def stop_at_return(uc,address,size,user):
 if address==0x08007000: reached.append(address); uc.emu_stop()
u.hook_add(UC_HOOK_CODE,stop_at_return)
u.emu_start(0x0800e7da|1,0,count=150)
assert reached == [0x08007000], 'did not return to caller'
assert struct.unpack('<I',u.mem_read(0x2000408c,4))[0]==0x12345678
for reg,value in zip((UC_ARM_REG_R4,UC_ARM_REG_R5,UC_ARM_REG_R6,UC_ARM_REG_R7,UC_ARM_REG_R8,UC_ARM_REG_R9,UC_ARM_REG_R10),saved): assert u.reg_read(reg)==value
assert writes==[(0x2000408c,4,0x12345678)],writes
assert u.reg_read(UC_ARM_REG_SP)==0x2000f000+168+32
print('PASS: patched dispatch and native epilogue restore r4-r10/SP; only normal response word written')
