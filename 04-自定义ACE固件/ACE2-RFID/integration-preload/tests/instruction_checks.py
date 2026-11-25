#!/usr/bin/env python3
"""Finite Thumb instruction model. NOT an ARM/board/RTOS emulator.

Executes decoded, byte-verified ARM core C + ASM + real F088 stock leaf.
Unknown instructions/reads/branches fail. MMIO is a write log only; interrupt
delivery is a single synthetic function event at an instruction boundary.
No CPU timing, hardware/IRQ behavior, FPU, stock queues or scheduler is modeled.
"""
import json,re,struct,subprocess,sys
from pathlib import Path
HERE=Path(__file__).resolve().parents[1]
ROOT=HERE.parent
sys.path.insert(0,str(HERE))
from build import BASE,TEXT,STOCK,BIN
U32=0xffffffff
FLAG=0xf8000000
RETURN=0x0807f000
RAM=0x21000000
STACK=0x21002000
STOCK_BYTES=STOCK.read_bytes()
PAYLOAD=(HERE/'build/hook-payload.bin').read_bytes()
SYM=json.loads((HERE/'build/symbols.json').read_text())

def decode():
 sources=[(HERE/'build/hooks.dis').read_text()]
 sources.append(subprocess.check_output([str(BIN/'arm-none-eabi-objdump'),'-b','binary','-marm','-Mforce-thumb','-D',
 '--adjust-vma=0x08008000','--start-address=0x0800f070','--stop-address=0x0800f0ca',str(STOCK)],text=True))
 sources.append(subprocess.check_output([str(BIN/'arm-none-eabi-objdump'),'-b','binary','-marm','-Mforce-thumb','-D',
 '--adjust-vma=0x08008000','--start-address=0x08009eb8','--stop-address=0x08009ede',str(STOCK)],text=True))
 for lo,hi in ((0x0800ed46,0x0800ed4c),(0x08009d62,0x08009d66)):
  sources.append(subprocess.check_output([str(BIN/'arm-none-eabi-objdump'),'-b','binary','-marm','-Mforce-thumb','-D',
  '--adjust-vma=0x08008000',f'--start-address={lo}',f'--stop-address={hi}',str(STOCK)],text=True))
 code={}
 for source in sources:
  for line in source.splitlines():
   m=re.match(r'\s*([0-9a-f]+):\s+((?:[0-9a-f]{4} ?){1,2})\s+([^\s]+)\s*(.*)',line)
   if not m:continue
   pc=int(m[1],16);words=m[2].split()
   if any(len(w)!=4 for w in words):continue
   raw=b''.join(struct.pack('<H',int(w,16)) for w in words)
   blob,start=(PAYLOAD,TEXT) if pc>=TEXT else (STOCK_BYTES,BASE)
   assert blob[pc-start:pc-start+len(raw)]==raw,(hex(pc),line)
   op=m[3].split('.')[0]
   if op in ('','word'):continue
   code[pc]=(len(raw),op,m[4].split('@')[0].strip())
 return code
CODE=decode()

class CPU:
 def __init__(self,lane=0,masked=0):
  self.r=[0x12000000+i*0x101 for i in range(16)]
  self.r[13]=STACK;self.r[14]=RETURN|1
  self.psr=0xb8000000;self.mask=masked;self.mem={};self.writes=[];self.count=0
  self.lane=lane;self.fired=False;self.max_masked=0;self.masked_run=0
  for a in range(RAM,RAM+40):self.mem[a]=0
  for i in range(4):
   motor=0x20001014+i*64;encoder=0x20001ba0+i*64
   self.put(motor+4,i,1);self.put(motor+56,encoder)
   self.put(encoder,0x08018c34);self.put(encoder+8,i,1);self.put(encoder+4,0,1)
   timer=self.get(0x08019580+i*40);self.put(timer+12,0xf)
  self.put(0x4000080c,0x1e);self.writes=[]
  # Explicit simulated ownership, never represented as a firmware binding.
  self.put(RAM+16+12*(lane>>1),1)
  self.put(RAM+20+12*(lane>>1),lane)
  self.put(RAM+24+12*(lane>>1),1)
 def reg(self,s):
  return {'sp':13,'lr':14,'pc':15,'ip':12,'sl':10,'fp':11}.get(s,int(s[1:]) if re.fullmatch(r'r\d+',s) else -1)
 def value(self,s):
  s=s.strip()
  if s.startswith('#'):return int(s[1:],0)
  return self.r[self.reg(s)]
 def get(self,a,n=4):
  if BASE<=a and a+n<=BASE+len(STOCK_BYTES):return int.from_bytes(STOCK_BYTES[a-BASE:a-BASE+n],'little')
  if TEXT<=a and a+n<=TEXT+len(PAYLOAD):return int.from_bytes(PAYLOAD[a-TEXT:a-TEXT+n],'little')
  if not all(a+i in self.mem for i in range(n)):raise ValueError(f'Unmodeled/uninitialized read {a:#x}/{n}')
  return sum(self.mem[a+i]<<(8*i) for i in range(n))
 def put(self,a,v,n=4):
  if not (0x20000000<=a<0x20010000 or RAM<=a<STACK+4 or 0x40000000<=a<0x40020000):
   raise ValueError(f'Unmodeled write {a:#x}')
  for i in range(n):self.mem[a+i]=(v>>(8*i))&255
  if a>=0x40000000:self.writes.append((self.count,a,v&((1<<(8*n))-1),self.fired,self.mask))
 def nz(self,v):
  self.psr=(self.psr&0x38000000)|(v&0x80000000)|(0x40000000 if v==0 else 0)
 def subflags(self,a,b):
  v=(a-b)&U32;self.nz(v)
  self.psr=(self.psr&~0x30000000)|(0x20000000 if a>=b else 0)|(0x10000000 if ((a^b)&(a^v)&0x80000000) else 0)
  return v
 def shiftval(self,s):
  p=[x.strip() for x in s.split(',')]
  v=self.value(p[0])
  if len(p)>1:
   kind,amt=p[1].split();amt=int(amt[1:],0)
   v=v>>amt if kind=='lsr' else (v<<amt)&U32
  return v
 def addr(self,s,pc):
  p=[x.strip() for x in s.split(',')]
  a=((pc+4)&~3) if p[0]=='pc' else self.value(p[0])
  if len(p)>1:a+=self.shiftval(','.join(p[1:]))
  return a&U32
 def branch(self,s):return int(s.split()[0],16)&~1
 def regs(self,s):return [self.reg(x.strip()) for x in s.strip('{}').split(',')]
 def inject(self):
  saved=(self.r[:],self.psr,self.mask)
  self.fired=True
  self.r[0:4]=[self.lane>>1,self.lane,1,1];self.r[14]=RETURN|1
  self.run(SYM['ph_publish_failure'])
  assert self.r[0]==4
  self.r,self.psr,self.mask=saved
 def run(self,entry,stops=(),inject_at=None):
  self.r[15]=entry;steps=0
  while self.r[15] not in {*stops,RETURN}:
   if steps>1000:raise RuntimeError('instruction limit')
   if inject_at is not None and steps>=inject_at and not self.fired and not self.mask:self.inject()
   pc=self.r[15]
   if pc not in CODE:raise ValueError(f'Unmodeled instruction {pc:#x}')
   sz,op,a=CODE[pc];nxt=pc+sz;p=[x.strip() for x in a.split(',')]
   self.count+=1;steps+=1
   if self.mask:self.masked_run+=1
   else:self.max_masked=max(self.max_masked,self.masked_run);self.masked_run=0
   if op in ('push','pop','stmdb','ldmia'):
    rr=self.regs(a.split(', ',1)[1] if op in ('stmdb','ldmia') else a)
    if op in ('push','stmdb'):
     self.r[13]-=4*len(rr)
     for i,r in enumerate(rr):self.put(self.r[13]+4*i,self.r[r])
    else:
     vals=[self.get(self.r[13]+4*i) for i in range(len(rr))];self.r[13]+=4*len(rr)
     for r,v in zip(rr,vals):
      if r==15:nxt=v&~1
      else:self.r[r]=v
   elif op in ('ldr','ldrb','ldrh','str','strb','strd'):
    mm=re.fullmatch(r'(.*), \[(.*)\]',a)
    if not mm:raise ValueError((op,a))
    rr=[self.reg(r.strip()) for r in mm[1].split(',')];ad=self.addr(mm[2],pc)
    n={'ldrb':1,'strb':1,'ldrh':2}.get(op,4)
    for i,r in enumerate(rr):
     if op.startswith('str'):self.put(ad+i*n,self.r[r],n)
     else:self.r[r]=self.get(ad+i*n,n)
   elif op in ('mov','movs','movw','movt'):
    v=self.shiftval(','.join(p[1:]));r=self.reg(p[0])
    if op=='movt':v=(self.r[r]&65535)|((v&65535)<<16)
    self.r[r]=v&U32
    if op=='movs':self.nz(v)
   elif op=='cmp':self.subflags(self.value(p[0]),self.shiftval(','.join(p[1:])))
   elif op in ('add','adds','sub','subs'):
    r=self.reg(p[0]);left=self.r[r] if len(p)==2 else self.value(p[1]);right=self.shiftval(','.join(p[1:] if len(p)==2 else p[2:]))
    self.r[r]=(left+right if op.startswith('add') else left-right)&U32
    if op=='subs':self.subflags(left,right)
    elif op=='adds':raise ValueError('add flag semantics not required/implemented')
   elif op=='mla':self.r[self.reg(p[0])]=(self.value(p[1])*self.value(p[2])+self.value(p[3]))&U32
   elif op in ('orr','bic'):
    self.r[self.reg(p[0])]=(self.value(p[1]) | self.value(p[2])) if op=='orr' else self.value(p[1]) & ~self.value(p[2]) & U32
   elif op=='mvn':self.r[self.reg(p[0])]=~self.value(p[1])&U32
   elif op=='orrs':
    v=self.value(p[0])|self.value(p[1]);self.r[self.reg(p[0])]=v;self.nz(v)
   elif op=='mrs':self.r[self.reg(p[0])]=self.mask if p[1]=='PRIMASK' else self.psr
   elif op=='msr':
    if p[0]=='PRIMASK':self.mask=self.value(p[1])&1
    elif p[0]=='CPSR_f':self.psr=self.value(p[1])&FLAG
    else:raise ValueError((op,a))
   elif op=='cpsid':self.mask=1
   elif op in ('dmb','nop'):pass
   elif op=='bl':self.r[14]=nxt|1;nxt=self.branch(a)
   elif op=='bx':nxt=self.value(a)&~1
   elif op in ('cbz','cbnz'):
    if (self.value(p[0])==0)==(op=='cbz'):nxt=self.branch(p[1])
   elif op in ('b','bne','beq','bhi','bcs','bcc'):
    z=bool(self.psr&0x40000000);c=bool(self.psr&0x20000000)
    if {'b':True,'bne':not z,'beq':z,'bhi':c and not z,'bcs':c,'bcc':not c}[op]:nxt=self.branch(a)
   else:raise ValueError((hex(pc),op,a))
   self.r[15]=nxt
  self.max_masked=max(self.max_masked,self.masked_run)
  return steps

def gate(cpu,name,blocked=False,inject_at=None,mutate=None):
 motor=0x20001014+cpu.lane*64
 cpu.r[4]=cpu.lane if name=='ph_insert_hook' else motor
 if name=='ph_start_hook':cpu.r[0]=motor
 if name=='ph_after_wait_hook':cpu.r[0]=0
 if name=='ph_active_hook':cpu.r[0]=1
 if name=='ph_encoder_enable_hook':
  cpu.r[4]=0x20001ba0+64*cpu.lane
  cpu.r[0]=0xf;cpu.r[2]=cpu.get(0x08019580+40*cpu.lane)
 if name=='ph_enable_gpio_hook':cpu.r[1]=4;cpu.r[2]=0x40011800
 if name=='ph_enable_timer_hook':
  cpu.r[0]=2<<cpu.lane;cpu.r[1]=cpu.get(0x4000080c);cpu.r[6]=0x4000080c
 if name in ('ph_start_pwm_hook','ph_control_pwm_hook'):
  cpu.r[0]=0x4000080c+4*cpu.lane;cpu.r[1]=517;cpu.r[2]=0x0000f828
 if blocked:cpu.put(RAM+4*cpu.lane,1)
 if mutate:mutate(cpu)
 live_timer=cpu.get(0x4000080c)
 before=cpu.r[:];psr=cpu.psr;mask=cpu.mask
 stops=[v for k,v in SYM.items() if k.startswith('stock_') and k not in ('stock_power_disable','stock_encoder_disable')]
 steps=cpu.run(SYM[name],stops,inject_at)
 # Hooks preserve all registers except documented displaced instructions.
 expected=before[:]
 if name=='ph_insert_hook' and cpu.r[15]==SYM['stock_insert_resume']:expected[0]=0x7ea4
 if name=='ph_start_hook' and cpu.r[15]==SYM['stock_start_resume']:expected[13]-=32
 if name=='ph_enable_gpio_hook':expected[1]=cpu.lane
 if name=='ph_enable_timer_hook':
  expected[0]=before[0]|(live_timer if mutate else before[1]);psr=(psr&0x38000000)
 if name=='ph_encoder_enable_hook' and cpu.r[15]==SYM['stock_encoder_resume']:
  expected[0]=1;expected[2]=0x08018c34;psr&=0x38000000
 if name in ('ph_start_pwm_hook','ph_control_pwm_hook'):expected[2]=0xfffff828
 assert cpu.r[:15]==expected[:15],(name,cpu.r,expected)
 assert cpu.psr==psr,(name,hex(cpu.psr),hex(psr))
 assert cpu.mask==mask,(name,'PRIMASK')
 return steps

def main():
 names=['ph_insert_hook','ph_start_hook','ph_after_wait_hook','ph_enable_gpio_hook',
        'ph_enable_timer_hook','ph_start_pwm_hook','ph_control_pwm_hook','ph_active_hook','ph_encoder_enable_hook']
 cases=0;max_masked=0
 for lane in range(4):
  for masked in (0,1):
   for blocked in (False,True):
    for name in names:
     c=CPU(lane,masked);gate(c,name,blocked)
     if blocked:assert not c.writes,(name,c.writes)
     elif name in names[3:] and name!='ph_active_hook':assert len(c.writes)==1 and c.writes[0][4]==1
     max_masked=max(max_masked,c.max_masked);cases+=1
  # Vary every combination of NZCVQ on each inline gate.
  for flags in range(32):
   for name in names:
    c=CPU(lane);c.psr=flags<<27;gate(c,name);cases+=1
  # Run actual ARM C publisher, actual ARM lock helpers, and exact stock F088.
  c=CPU(lane);c.r[0:4]=[lane>>1,lane,1,1];c.run(SYM['ph_publish_failure'])
  assert c.r[0]==4 and c.get(RAM+4*lane)==1 and c.mask==0
  assert len(c.writes)==5 and c.writes[1][2]==1000
  assert c.get(0x4000080c)&(2<<lane)==0
  assert c.get(c.get(0x08019580+40*lane)+12)&1==0
  assert c.get(0x20001014+lane*64+6,1)==0
  cases+=1
  # Actual TIM4 handler leaf F070, with active=0 and arbitrary count.
  for count in (0,1,7,0xffffffff):
   c=CPU(lane);motor=0x20001014+64*lane;c.put(motor+6,0,1);c.put(motor+12,count)
   c.r[0]=motor;c.run(0x0800f070)
   assert len(c.writes)==2 and c.writes[-1][2]==1000
   cases+=1
  # Synthetic fault at each possible instruction boundary of each effect gate.
  for name in names[3:]:
   reference=CPU(lane);steps=gate(reference,name)
   for at in range(steps):
    c=CPU(lane);gate(c,name,inject_at=at)
    if not c.fired:c.inject()
    # Any post-failure MMIO write must be one of F088's stop effects.
    post=[w for w in c.writes if w[3]]
    assert len(post)==5 and post[1][2]==1000,(name,at,c.writes)
    if name=='ph_active_hook':assert c.get(0x20001014+64*lane+6,1)==0
    if name=='ph_encoder_enable_hook':
     assert c.get(0x20001ba0+64*lane+4,1)==0
     assert c.get(c.get(0x08019580+40*lane)+12)&1==0
    cases+=1
  # Regression: a sibling clears DIER after the stock pre-read, before gate.
  c=CPU(lane);siblingbit=2<<(lane^1)
  def clear_sibling(cpu):cpu.put(0x4000080c,cpu.get(0x4000080c)&~siblingbit);cpu.writes=[]
  gate(c,'ph_enable_timer_hook',mutate=clear_sibling)
  assert not c.get(0x4000080c)&siblingbit;cases+=1
  c=CPU(lane);timer=c.get(0x08019580+40*lane)
  def change_encoder_dier(cpu):cpu.put(timer+12,4);cpu.writes=[]
  gate(c,'ph_encoder_enable_hook',mutate=change_encoder_dier)
  assert c.get(timer+12)==5;cases+=1
  # Reproduce old unguarded instruction windows, intentionally unsafe outcomes.
  c=CPU(lane);c.inject();c.r[0]=1;c.r[4]=0x20001014+64*lane
  c.run(0x0800ed4a,stops=(0x0800ed4c,))
  assert c.get(c.r[4]+6,1)==1;cases+=1
  c=CPU(lane);c.inject();c.r[0]=0xf;c.r[2]=timer
  c.run(0x08009d62,stops=(0x08009d66,))
  assert c.get(timer+12)&1;cases+=1
 # EFA2/EFA4 stale disable-RMW race, including failure from higher priority
 # context. No assumption that failure publishing is thread-only is needed.
 for lane in range(4):
  for flags in range(32):
   for masked in (0,1):
    c=CPU(lane,masked);c.psr=flags<<27
    c.r[0]=2<<(lane^1);c.r[1]=0x4000080c;c.r[2]=0x1e
    before=c.r[:]
    c.put(0x4000080c,0x1e&~(2<<lane));c.writes=[]
    live=c.get(0x4000080c)
    c.run(SYM['ph_disable_timer_hook'],stops=(SYM['stock_disable_timer_resume'],))
    expected=before[:];expected[0]=live&~before[0];expected[2]=live
    assert c.r[:15]==expected[:15] and c.psr==flags<<27 and c.mask==masked
    assert c.get(0x4000080c)&(2<<lane)==0
    assert c.get(0x4000080c)&(2<<(lane^1))==0
    assert len(c.writes)==1 and c.writes[0][4]==1
    max_masked=max(max_masked,c.max_masked);cases+=1
  reference=CPU(lane);reference.r[0]=2<<(lane^1);reference.r[1]=0x4000080c
  steps=reference.run(SYM['ph_disable_timer_hook'],stops=(SYM['stock_disable_timer_resume'],))
  for at in range(steps):
   c=CPU(lane);c.r[0]=2<<(lane^1);c.r[1]=0x4000080c;c.r[2]=0x1e
   c.run(SYM['ph_disable_timer_hook'],stops=(SYM['stock_disable_timer_resume'],),inject_at=at)
   if not c.fired:c.inject()
   assert not c.get(0x4000080c)&((2<<lane)|(2<<(lane^1)))
   cases+=1
 result={'passed':cases,'hooks':10,'all_lanes':4,'max_gate_masked_instructions_observed':max_masked,
 'hardware_timing_measured':False,'full_cpu_emulator':False,
 'failure_event_source':'synthetic owned call; production unbound',
 'not_covered':['full RTOS','mechanical rest','ISR/MMIO semantics','other firmware writers','actual AUTH result classification','INSERT check-to-queue race']}
 (HERE/'build/instruction-results.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps(result,indent=2))
if __name__=='__main__':main()
