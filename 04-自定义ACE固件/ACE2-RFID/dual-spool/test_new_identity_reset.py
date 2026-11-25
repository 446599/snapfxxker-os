"""Execute 97O FDFC until its new-identity reference initialization.

No device I/O. Default records the pre-fix defect; --expect-fresh demands
that all measurement state belongs to the new identity.
"""
from pathlib import Path
import math,struct,sys
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import *
root=Path(__file__).resolve().parent.parent
image=(root/'diagnostic97/ACE2-97O-diagnostic.bin').read_bytes()
u=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
u.mem_map(0x08000000,0x20000);u.mem_write(0x08008000,image)
u.mem_map(0x20000000,0x10000);u.mem_map(0xe000e000,0x1000)
u.mem_write(0xe000ed88,struct.pack('<I',0x00f00000));u.reg_write(UC_ARM_REG_FPEXC,0x40000000)
ctx=0x200018a4+2*60
before=struct.pack('<fffII10f',123,456,123456,111111,2,*range(10))
u.mem_write(ctx,before)
u.reg_write(UC_ARM_REG_R0,0x20004000);u.reg_write(UC_ARM_REG_R1,0x200016b0);u.reg_write(UC_ARM_REG_R2,ctx)
u.reg_write(UC_ARM_REG_S0,struct.unpack('<I',struct.pack('<f',438.64))[0])
u.reg_write(UC_ARM_REG_SP,0x2000f000);u.reg_write(UC_ARM_REG_LR,0x08007001)
reached=[]
def stop(uc,pc,size,user):
 if pc==0x0800fe1e:reached.append(pc);uc.emu_stop()
u.hook_add(UC_HOOK_CODE,stop);u.emu_start(0x0800fdfd,0,count=100)
assert reached
old_ref,sample,cap,remaining,count=struct.unpack('<fffII',u.mem_read(ctx,20))
arr=struct.unpack('<10f',u.mem_read(ctx+20,40))
assert abs(old_ref-438.64)<.01 and math.isnan(sample)
fresh=(cap==330000 and remaining==0 and count==0 and all(math.isnan(x) for x in arr))
if '--expect-fresh' in sys.argv:
 assert fresh, '97O FDFC leaves prior cap=%s remaining=%s count=%s array=%s' % (cap,remaining,count,arr)
else:
 assert not fresh and (cap,remaining,count)==(123456,111111,2)
 print('REPRODUCED: new identity reference updated while old cap/length/count/samples remain')
