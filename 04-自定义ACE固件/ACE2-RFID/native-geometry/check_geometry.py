"""Offline 96O BB00 floating-literal experiment; no image artifact or device I/O."""
from pathlib import Path
import math, struct, sys
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm_const import *
root=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(root/'preload-feature'))
from ace_filament_approximation import estimate_remaining
image=bytearray((root/'diagnostic96/ACE2-96O-diagnostic.bin').read_bytes())
constants={0x0800bcd8:1/(math.pi*3.5), 0x0800bce0:-87/3.5,
           0x0800bce8:.9*2*math.pi*33*1.75,
           0x0800bcf0:.9*2*math.pi*33*(43.5-.875),
           0x0800bcf8:.9*2*math.pi*33*1.75,
           0x0800bd00:.9*2*math.pi*33*(43.5-.875)}
for addr,v in constants.items(): struct.pack_into('<d',image,addr-0x08008000,v)

def run(c,cap=330000, modified=True):
 u=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
 u.mem_map(0x08000000,0x20000);u.mem_write(0x08008000,bytes(image) if modified else (root/'diagnostic96/ACE2-96O-diagnostic.bin').read_bytes())
 u.mem_map(0x20000000,0x10000);u.mem_map(0xe000e000,0x1000)
 u.mem_write(0xe000ed88,struct.pack('<I',0x00f00000))
 u.reg_write(UC_ARM_REG_FPEXC,0x40000000)
 ctx=0x200018a4
 u.mem_write(ctx,struct.pack('<ff f II',0.0,0.0,cap,0,0)+struct.pack('<10I',*([0x7fc00000]*10)))
 u.reg_write(UC_ARM_REG_R0,ctx);u.reg_write(UC_ARM_REG_S0,struct.unpack('<I',struct.pack('<f',c))[0]);u.reg_write(UC_ARM_REG_SP,0x2000f000);u.reg_write(UC_ARM_REG_LR,0x08007001)
 preserved={UC_ARM_REG_R4:0x44444444, UC_ARM_REG_R5:0x55555555, UC_ARM_REG_R6:0x66666666, UC_ARM_REG_R7:0x77777777, UC_ARM_REG_R8:0x88888888, UC_ARM_REG_R9:0x99999999, UC_ARM_REG_R10:0xaaaaaaaa, UC_ARM_REG_R11:0xbbbbbbbb, UC_ARM_REG_S16:0x3f800000, UC_ARM_REG_S17:0x40000000, UC_ARM_REG_S18:0x40400000, UC_ARM_REG_S19:0x40800000}
 for reg,value in preserved.items():u.reg_write(reg,value)
 writes=[]
 def write_hook(uc,access,address,size,value,user):writes.append((address,size))
 u.hook_add(UC_HOOK_MEM_WRITE,write_hook)
 reached=[]
 def hook(uc,addr,size,user):
  if addr==0x08007000: reached.append(addr);uc.emu_stop()
 u.hook_add(UC_HOOK_CODE,hook);u.emu_start(0x0800bb01,0,count=200000)
 assert reached,hex(u.reg_read(UC_ARM_REG_PC))
 assert u.reg_read(UC_ARM_REG_SP)==0x2000f000
 assert all(u.reg_read(reg)==value for reg,value in preserved.items())
 assert all((ctx<=addr and addr+size<=ctx+60) or (0x2000e000<=addr and addr+size<=0x2000f000) for addr,size in writes),writes
 return struct.unpack('<ff f II',u.mem_read(ctx,20))
for c in (280,285,300,400,500,550,599):
 result=run(c)
 expected=estimate_remaining(c,87,60)['remaining_m']*1000
 expected=min(330000,max(10000,expected))
 assert abs(result[3]-expected)<2,(c,result,expected)
 print('C=%.0f native=%d expected=%.2f count=%d'%(c,result[3],expected,result[4]))

# Strict original threshold paths are byte-for-byte semantically unchanged.
for c in (0, 179, 180, 600, 700, float('nan')):
 a,b=run(c,modified=False),run(c,modified=True)
 assert struct.pack('<ff f II',*a)==struct.pack('<ff f II',*b),(c,a,b)
# Sweep geometry values; float32 rounding and integer truncation can differ by
# less than two mm except at a whole-layer transition, tested separately.
for step in range(2750,5999,5):
 c=step/10
 result=run(c)
 expected=min(330000,max(10000,estimate_remaining(c,87,60)['remaining_m']*1000))
 assert abs(result[3]-expected)<2,(c,result,expected)
for c in (285,300,400,500,550):
 result=run(c,cap=120000)
 expected=min(120000,max(10000,estimate_remaining(c,87,60)['remaining_m']*1000))
 assert abs(result[3]-expected)<2,(c,result,expected)
print('PASS: 650+ actual Thumb calls; callee-saved registers/stack, context write bounds, threshold and cap preserved')

# Verify the two adjacent float32 values around each mathematical layer edge.
for n in range(1,30):
 boundary=2*math.pi*(43.5+1.75*n)
 bits=struct.unpack('<I',struct.pack('<f',boundary))[0]
 for word in (bits-1,bits,bits+1):
  c=struct.unpack('<f',struct.pack('<I',word))[0]
  if not 180<c<600: continue
  result=run(c)
  expected=min(330000,max(10000,estimate_remaining(c,87,60)['remaining_m']*1000))
  assert abs(result[3]-expected)<2,(n,c,result,expected)
print('PASS: float32 values bracketing every 1.75 mm layer transition')
# Run three matching visits in one actual context, with deliberately unsorted
# geometry, to exercise the native insertion sort and initial +12 semantics.
u=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
u.mem_map(0x08000000,0x20000);u.mem_write(0x08008000,bytes(image))
u.mem_map(0x20000000,0x10000);u.mem_map(0xe000e000,0x1000)
u.mem_write(0xe000ed88,struct.pack('<I',0x00f00000));u.reg_write(UC_ARM_REG_FPEXC,0x40000000)
ctx=0x200018a4
u.mem_write(ctx,struct.pack('<ff f II',0,0,330000,0,0)+struct.pack('<10I',*([0x7fc00000]*10)))
def halt(uc,addr,size,user):
 if addr==0x08007000:uc.emu_stop()
u.hook_add(UC_HOOK_CODE,halt)
for position in (500.0,900.0,1450.0):
 u.reg_write(UC_ARM_REG_R0,ctx);u.reg_write(UC_ARM_REG_S0,struct.unpack('<I',struct.pack('<f',position))[0])
 u.reg_write(UC_ARM_REG_SP,0x2000f000);u.reg_write(UC_ARM_REG_LR,0x08007001)
 u.emu_start(0x0800bb01,0,count=200000)
raw=bytes(u.mem_read(ctx,60));reference,sample,cap,first,count=struct.unpack_from('<fffII',raw)
values=struct.unpack_from('<10f',raw,20)
assert reference==sample==1450 and count==3 and first==227664
assert all(abs(a-b)<2 for a,b in zip(values[:3],(109050.57,227664.94,304991.71)))
assert all(math.isnan(v) for v in values[3:])
print('PASS: three native visits preserve insertion sort, count, first result and reference update')
