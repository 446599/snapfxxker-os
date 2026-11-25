#!/usr/bin/env python3
"""Actual compiled sideband C + stock queue copy/count instruction windows.

Stock scheduler/wait completion is an explicit endpoint, not emulated. The
successful copy helper and receive copy/count window execute actual bytes.
Synthetic ISR context means saved PRIMASK=1; no NVIC priority/timing proof.
"""
import hashlib,json,re,struct,subprocess
import dynamic_instruction_checks as d
for lo,hi in [(0x08008a1e,0x08008aa8),(0x08012a50,0x08012a84),(0x08016b22,0x08016b44)]:
 source=subprocess.check_output([str(d.BIN/'arm-none-eabi-objdump'),'-D','-b','binary','-marm','-Mforce-thumb',
  '--adjust-vma='+hex(d.BASE),'--start-address='+hex(lo),'--stop-address='+hex(hi),str(d.STOCK)],text=True)
 for line in source.splitlines():
  m=re.match(r'\s*([0-9a-f]+):\s+((?:[0-9a-f]{4} ?){1,2})\s+([^\s]+)\s*(.*)',line)
  if not m:continue
  pc=int(m[1],16);raw=b''.join(struct.pack('<H',int(w,16)) for w in m[2].split())
  assert d.STOCK_BYTES[pc-d.BASE:pc-d.BASE+len(raw)]==raw
  d.CODE[pc]=(len(raw),m[3].split('.')[0],m[4].split('@')[0].strip())
d.CODE[0x08016b38]=(4,'bl',hex(d.SYM['pq_receive_hook']))
Q0=0x20003000;Q1=0x20003100;ITEM=0x20002000;OUT=ITEM+32;QS=d.EVENT+32+184
cases=0;peak=0;stack=0
def setup(lane=0,masked=0):
 c=d.CPU(lane,masked)
 c.put(0x2000189c,Q0);c.put(0x200018a0,Q1)
 c.put(0x20001dd0,0x20004000)
 for q in (Q0,Q1):
  for a in range(q-8,q+88):c.mem[a]=0
  for off,val in [(-4,0x80000060),(0,q+80),(4,q+80),(8,q+82),(12,q+81),(60,2),(64,1)]:c.put(q+off,val)
 node=0x200012e8+(lane>>1)*208+lane*32+140
 c.put(node,lane,1);c.put(node+28,1,1);c.put(QS+lane*8,1);c.put(QS+lane*8+4,1)
 c.put(ITEM,1,1);return c,node
def stats(c):
 global peak,stack
 peak=max(peak,c.max_masked);stack=max(stack,d.STACK-c.min_sp)
def call(c,name,args):
 c.r[:len(args)]=args;c.r[14]=d.RETURN|1;c.run(d.SYM[name]);stats(c)
def prepare(c,lane,node,source=1,command=1):
 q=Q0 if lane<2 else Q1
 c.put(ITEM,command,1);c.put(c.r[13],lane);c.put(c.r[13]+4,node)
 c.r[:4]=[q,ITEM,0,source];c.r[14]=d.RETURN|1
 c.run(d.SYM['pq_send'],stops=(d.SYM['pq_stock_send_raw'],))
 assert c.mask==0 # No added interrupt mask across a blocking scheduler call.
 return c.r[:],c.psr,c.mask
def committed(c,saved,receive_first=False):
 q=saved[0][0]
 # Actual task send prologue has 36-byte push + 12 locals. Its saved caller
 # LR is SP+44, checked by the real new enqueue assembly hook.
 c.r[13]-=48;c.put(c.r[13]+44,saved[0][14]);c.r[14]=0x080167fb
 c.r[:2]=[q,ITEM];c.run(d.SYM['pq_enqueue_hook'],stops=(0x080167fa,));stats(c)
 assert c.r[0]==0
 c.r[13]+=48
 if receive_first:receive(c,q)
 c.r,c.psr,c.mask=saved[0][:],saved[1],saved[2]
 resume=c.r[14]&~1;c.r[0]=1;c.run(resume);stats(c)
def receive(c,q):
 c.r[4]=q;c.r[9]=OUT;c.r[10]=c.get(q+56)
 c.run(0x08016b22,stops=(0x08016b42,));stats(c)
def candidate(c,reader):
 call(c,'pq_candidate',[reader,OUT+8]);return c.r[0]
for lane in range(4):
 for receive_first in (False,True):
  c,node=setup(lane);saved=prepare(c,lane,node);committed(c,saved,receive_first)
  q=Q0 if lane<2 else Q1
  if not receive_first:assert not candidate(c,lane>>1);receive(c,q)
  assert candidate(c,lane>>1)==1 and c.get(OUT+12)==lane
  assert c.get(q+56)==0 and not c.writes and c.mask==0;cases+=1
 # Fixed node reuse after enqueue, before dequeue: same address, new generation.
 c,node=setup(lane);saved=prepare(c,lane,node);committed(c,saved)
 call(c,'pq_job_unlinked',[lane]);call(c,'pq_job_enqueued',[lane])
 receive(c,Q0 if lane<2 else Q1);assert not candidate(c,lane>>1);cases+=1
 # Two successful native commits, followed by stock send's failure endpoint.
 c,node=setup(lane)
 for i in range(2):saved=prepare(c,lane,node);committed(c,saved)
 q=Q0 if lane<2 else Q1;assert c.get(q+56)==2
 ring=bytes(c.mem[a] for a in range(QS+32+(lane>>1)*68,QS+32+(lane>>1)*68+68))
 saved=prepare(c,lane,node);resume=c.r[14]&~1;c.r[0]=0;c.run(resume)
 assert c.get(q+56)==2 and ring==bytes(c.mem[a] for a in range(QS+32+(lane>>1)*68,QS+32+(lane>>1)*68+68))
 receive(c,q);receive(c,q);assert candidate(c,lane>>1);cases+=1
 # Unwrapped task/ISR producer reaches the SAME actual native copy/count helper.
 for masked in (0,1):
  c,node=setup(lane,masked);q=Q0 if lane<2 else Q1
  c.ipsr=16 if masked else 0
  call(c,'pq_enqueue_hook',[q,ITEM]);assert c.get(q+56)==1 and c.mask==masked
  receive(c,q);assert not candidate(c,lane>>1) and c.mask==masked
  assert c.get(QS+32+(lane>>1)*68+4)&2;cases+=1
 # Cleanup/requeue/control byte messages still transfer but cannot own a lane.
 for source,command in [(2,0),(3,6),(4,0),(4,8)]:
  c,node=setup(lane);saved=prepare(c,lane,node,source,command);committed(c,saved)
  receive(c,Q0 if lane<2 else Q1);assert c.get(OUT,1)==command and not candidate(c,lane>>1);cases+=1
# Audit counterexample: another task / ISR / unrelated caller with SAME item.
for lane in range(4):
 for mode in (1,2,3):
  c,node=setup(lane);q=Q0 if lane<2 else Q1;saved=prepare(c,lane,node)
  c.r[13]-=48;c.put(c.r[13]+44,saved[0][14] if mode!=3 else 0x08001235)
  c.r[:2]=[q,ITEM];c.r[14]=0x08017a45 if mode==2 else 0x080167fb
  if mode==1:c.put(0x20001dd0,0x20004100)
  if mode==2:c.ipsr=16;c.mask=1
  c.run(d.SYM['pq_enqueue_hook'],stops=(c.r[14]&~1,));stats(c)
  receive(c,q);assert not candidate(c,lane>>1)
  assert c.get(QS+32+(lane>>1)*68+4)&2
  c.put(0x20001dd0,0x20004000);c.ipsr=0
  c.r,c.psr,c.mask=saved[0][:],saved[1],saved[2]
  committed(c,saved);receive(c,q);assert not candidate(c,lane>>1);cases+=1
# Sibling queueing does not replace the currently consumed candidate. Exercise
# actual C requeue context through the REAL ABEC/ACFA wrapper. Because RF-ready
# is deliberately false, dispatch at rf_requeue_entry is an explicit test
# endpoint: emulate its authorized command6 continuation, never enable RF.
for lane in range(4):
 c,node=setup(lane);q=Q0 if lane<2 else Q1
 saved=prepare(c,lane,node);committed(c,saved);receive(c,q)
 assert candidate(c,lane>>1)
 sibling=lane^1;other=0x200012e8+(sibling>>1)*208+sibling*32+140
 c.put(other,sibling,1);c.put(other+28,1,1);call(c,'pq_job_enqueued',[sibling])
 assert candidate(c,lane>>1) and c.get(OUT+12)==lane
 c.r[0]=lane;c.r[11]=node;c.r[14]=d.RETURN|1
 c.run(d.SYM['pq_preload_requeue_hook'],stops=(d.SYM['rf_requeue_entry'],))
 context=(c.r[:],c.psr,c.mask);assert c.mask==0
 saved=prepare(c,lane,node,3,6);committed(c,saved);receive(c,q)
 assert candidate(c,lane>>1) and c.get(OUT+12)==lane
 c.r,c.psr,c.mask=context;c.run(c.r[14]&~1);stats(c)
 assert all(c.get(QS+168+32*i+24)==0 for i in range(8));cases+=1
result={'image_sha256':json.loads((d.HERE/'output/manifest.json').read_text())['image_sha256'],
 'passed':cases,'actual_ARM_sideband_C_executed':True,'actual_stock_one_byte_copy_and_count_executed':True,
 'actual_stock_receive_window_executed':True,'scheduler_wait_results':'explicit success/failure endpoints, not scheduler execution',
 'requeue_dispatch':'explicit command6 continuation fixture, actual RF-ready remains false',
 'unknown_ISR_case':'synthetic saved PRIMASK=1, not actual NVIC delivery',
 'max_masked_instructions_including_preexisting_mask_observed':peak,'max_stack_below_fixture_entry_observed':stack,
 'stack_measurement_includes_stock_frames_and_dispatch_fixture':True,
 'hardware_time_or_full_interrupt_stack_bound':False,'ownership_or_failure_producer_bound':False}
(d.HERE/'build/queue-commit-results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
