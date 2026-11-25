"""Exact finite CPU class extracted from c132; not a full CPU or scheduler."""
class CPU:
 def __init__(self,lane=0,masked=0):
  self.r=[0x12000000+i*0x101 for i in range(16)]
  self.r[13]=STACK;self.r[14]=RETURN|1
  self.psr=0xb8000000;self.mask=masked;self.mem={};self.writes=[];self.count=0
  self.lane=lane;self.fired=False;self.max_masked=0;self.masked_run=0
  self.reads=[];self.min_sp=STACK;self.calls=0
  for a in range(EVENT-8,EVENT+216):self.mem[a]=0
  self.put(EVENT-8,0);self.put(EVENT-4,0x800000e0)
  self.put(0x20001e64,EVENT)
  for off,val in ((32,0x45585432),(36,184),(40,EVENT),(44,0x52445932)):self.put(EVENT+off,val)
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
  if TEXT<=a and a+n<=TEXT+len(PAYLOAD):return int.from_bytes(PAYLOAD[a-TEXT:a-TEXT+n],'little')
  if BASE<=a and a+n<=BASE+len(STOCK_BYTES):return int.from_bytes(STOCK_BYTES[a-BASE:a-BASE+n],'little')
  self.reads.append((a,n))
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
 def cond(self,cc):
  n=bool(self.psr&0x80000000);z=bool(self.psr&0x40000000);c=bool(self.psr&0x20000000);v=bool(self.psr&0x10000000)
  return {'eq':z,'ne':not z,'cs':c,'cc':not c,'hi':c and not z,'ls':not c or z,'ge':n==v,'lt':n!=v,'pl':not n,'mi':n}[cc]
 def run(self,entry,stops=(),inject_at=None):
  self.r[15]=entry;steps=0
  while self.r[15] not in {*stops,RETURN}:
   if steps>1000:raise RuntimeError('instruction limit')
   if inject_at is not None and steps>=inject_at and not self.fired and not self.mask:self.inject()
   pc=self.r[15]
   if pc not in CODE:raise ValueError(f'Unmodeled instruction {pc:#x}')
   sz,op,a=CODE[pc];nxt=pc+sz;p=[x.strip() for x in a.split(',')]
   self.count+=1;steps+=1;self.min_sp=min(self.min_sp,self.r[13])
   if self.mask:self.masked_run+=1
   else:self.max_masked=max(self.max_masked,self.masked_run);self.masked_run=0
   # Objdump spells IT-predicated operations with their condition suffix.
   if op not in ('b','bl','bx','cbz','cbnz','bic','bics') and op not in ('bne','beq','bhi','bcs','bcc','bls','bge') and op[-2:] in ('eq','ne','cs','cc','hi','ls','ge','lt','pl','mi'):
    cc=op[-2:];op=op[:-2]
    if not self.cond(cc):self.r[15]=nxt;continue
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
    elif op=='adds':
     v=self.r[r];self.nz(v)
     self.psr=(self.psr&~0x30000000)|(0x20000000 if left+right>U32 else 0)|(0x10000000 if ((~(left^right))&(left^v)&0x80000000) else 0)
   elif op=='mla':self.r[self.reg(p[0])]=(self.value(p[1])*self.value(p[2])+self.value(p[3]))&U32
   elif op in ('and','ands'):
    v=self.value(p[1])&self.value(p[2]);self.r[self.reg(p[0])]=v
    if op=='ands':self.nz(v)
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
   elif op in ('dmb','nop','it','ite','itte','itt'):pass
   elif op=='bl':
    assert self.r[13]%8==0,('Unaligned call',hex(pc),hex(self.r[13]))
    self.calls+=1;self.r[14]=nxt|1;nxt=self.branch(a)
   elif op=='bx':nxt=self.value(a)&~1
   elif op in ('cbz','cbnz'):
    if (self.value(p[0])==0)==(op=='cbz'):nxt=self.branch(p[1])
   elif op in ('b','bne','beq','bhi','bcs','bcc','bls','bge'):
    if op=='b' or self.cond(op[1:]):nxt=self.branch(a)
   else:raise ValueError((hex(pc),op,a))
   self.r[15]=nxt
  self.max_masked=max(self.max_masked,self.masked_run)
  return steps
