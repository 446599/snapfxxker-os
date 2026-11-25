"""Exercise new-identity reset using patched-in-memory 97O and emulator-only stub."""
from pathlib import Path
import math,os,struct
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm_const import *
root=Path(__file__).resolve().parent.parent
original=(root/'diagnostic97/ACE2-97O-diagnostic.bin').read_bytes()
stub=Path(os.environ['DUAL_SPOOL_STUB']).read_bytes()
# This address exists only in the emulator; no firmware or free-flash claim.
base,patch_at,stub_at,resume=0x08008000,0x0800fe1a,0x0801e000,0x0800fe1e
def thumb_bw(src,dst):
 off=(dst-(src+4))>>1;s=(off>>23)&1;i1=(off>>22)&1;i2=(off>>21)&1
 return struct.pack('<HH',0xf000|(s<<10)|((off>>11)&0x3ff),0x9000|(((~i1&1)^s)<<13)|(((~i2&1)^s)<<11)|(off&0x7ff))
def execute(ctx,patched,primask):
 image=bytearray(original)
 assert image[patch_at-base:patch_at-base+4]==bytes.fromhex('82ed000a')
 if patched:image[patch_at-base:patch_at-base+4]=thumb_bw(patch_at,stub_at)
 u=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
 u.mem_map(0x08000000,0x20000);u.mem_write(base,bytes(image));u.mem_write(stub_at,stub)
 u.mem_map(0x20000000,0x10000);u.mem_map(0xe000e000,0x1000)
 u.mem_write(0xe000ed88,struct.pack('<I',0x00f00000));u.reg_write(UC_ARM_REG_FPEXC,0x40000000)
 u.mem_write(0x20001880,b'\xa5'*0x200)
 before=struct.pack('<fffII10f',123,456,123456,111111,2,*range(10));u.mem_write(ctx,before)
 for reg,val in ((UC_ARM_REG_R0,0x20004000),(UC_ARM_REG_R1,0x200016b0),(UC_ARM_REG_R2,ctx),
                 (UC_ARM_REG_R3,0x33333333),(UC_ARM_REG_R12,0xcccccccc),
                 (UC_ARM_REG_R7,0x77777777),(UC_ARM_REG_R8,0x88888888),
                 (UC_ARM_REG_SP,0x2000f000),(UC_ARM_REG_LR,0x08007001),(UC_ARM_REG_PRIMASK,primask)):
  u.reg_write(reg,val)
 u.reg_write(UC_ARM_REG_S0,struct.unpack('<I',struct.pack('<f',438.64))[0])
 writes=[]
 def write(uc,access,addr,size,val,user):writes.append((addr,size))
 def stop(uc,pc,size,user):
  if pc==resume:uc.emu_stop()
 u.hook_add(UC_HOOK_MEM_WRITE,write);u.hook_add(UC_HOOK_CODE,stop)
 u.emu_start(0x0800fdfd,0,count=300)
 assert u.reg_read(UC_ARM_REG_PC)==resume
 registers=tuple(u.reg_read(r) for r in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_R4,UC_ARM_REG_R5,UC_ARM_REG_R6,UC_ARM_REG_R7,UC_ARM_REG_R8,UC_ARM_REG_R12,UC_ARM_REG_SP,UC_ARM_REG_LR,UC_ARM_REG_PRIMASK,UC_ARM_REG_APSR,UC_ARM_REG_S0))
 assert all((ctx<=a and a+n<=ctx+60) or (0x2000e000<=a and a+n<=0x2000f000) for a,n in writes),writes
 return bytes(u.mem_read(ctx,60)),registers
for ctx in (0x200018a4,0x200018e0,0x2000191c,0x20001958,0x20001994,0x200019d0):
 for primask in (0,1):
  old,regs0=execute(ctx,False,primask);new,regs1=execute(ctx,True,primask)
  assert regs0==regs1,(hex(ctx),primask,regs0,regs1)
  reference,sample,cap,remaining,count=struct.unpack_from('<fffII',new)
  assert abs(reference-438.64)<.01 and math.isnan(sample) and cap==330000 and remaining==count==0
  assert all(math.isnan(x) for x in struct.unpack_from('<10f',new,20))
  assert struct.unpack_from('<II',old,12)==(111111,2)
print('PASS: 6 native ctx addresses x PRIMASK 0/1: stale values cleared; registers, flags, stack and write boundaries preserved')
