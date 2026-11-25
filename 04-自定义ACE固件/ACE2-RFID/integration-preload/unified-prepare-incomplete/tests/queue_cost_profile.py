#!/usr/bin/env python3
"""Instruction counts, not CPU cycles/time or a stack/interrupt-safety proof.

Only measure added mask regions entered with thread mode PRIMASK=0. The separate
commit tests include synthetic pre-masked ISR cases; their cumulative number is
not an added-interrupt-latency bound. The longest tested commit region includes
all allocator/queue/TCB validations and the real stock one-byte copy/count code.
"""
import bisect,collections,json,re
import queue_commit_checks as q

d=q.d;records=[]
labels=[]
for line in (d.HERE/'build/combined.dis').read_text().splitlines():
 m=re.match(r'^([0-9a-f]+) <([^>]+)>:',line)
 if m:labels.append((int(m[1],16),m[2]))
labels += [(0x08008a1e,'stock_copy'),(0x08012a54,'stock_enqueue_body'),(0x08016b22,'stock_receive_prefix')]
labels.sort();addresses=[x[0] for x in labels]
def name(pc):return labels[bisect.bisect_right(addresses,pc)-1][1]
def profile(label,c,fn):
 assert c.mask==0 and not getattr(c,'ipsr',0)
 c.trace=[];startsp=c.r[13];result=fn();trace=c.trace;del c.trace
 runs=[];run=[]
 for entry in trace:
  if entry[1]:run.append(entry)
  elif run:runs.append(run);run=[]
 if run:runs.append(run)
 longest=max(runs,key=len,default=[])
 records.append(dict(path=label,total_instructions=len(trace),
   longest_added_mask_instructions=len(longest),
   longest_mask_by_function=dict(collections.Counter(name(x[0]) for x in longest)),
   function_start_stack=startsp,max_stack_below_start=max([startsp-x[2] for x in trace]+[0]),
   longest_mask_first_pc=hex(longest[0][0]) if longest else None,
   longest_mask_last_pc=hex(longest[-1][0]) if longest else None))
 return result
for lane in range(4):
 for occupied in (0,7):
  for source,command in ((1,1),(2,0),(3,6),(4,8)):
   c,node=q.setup(lane);queue=q.Q0 if lane<2 else q.Q1
   for i in range(occupied):
    p=q.QS+168+i*32
    for off,v in ((0,q.ITEM+i+1),(4,queue),(8,1),(12,lane),(16,1),(20,1),(24,1),(28,0x20004000)):c.put(p+off,v)
   label=f'lane{lane}/pending{occupied}/source{source}'
   saved=profile(label+'/send_prepare',c,lambda:q.prepare(c,lane,node,source,command))
   profile(label+'/enqueue_and_send_return',c,lambda:q.committed(c,saved))
   profile(label+'/receive',c,lambda:q.receive(c,queue))
   profile(label+'/candidate',c,lambda:q.candidate(c,lane>>1))
longest=max(records,key=lambda r:r['longest_added_mask_instructions'])
result=dict(image_sha256=json.loads((d.HERE/'output/manifest.json').read_text())['image_sha256'],
 measurements=len(records),entry_context='thread mode IPSR=0, PRIMASK=0',
 max_added_mask_instructions_observed=longest['longest_added_mask_instructions'],longest_observed=longest,
 records=records,not_a_bound_on=['CPU cycles/time','all interleavings','RTOS priorities','interrupt service deadlines','total task/ISR stack'],
 required_hardware_evidence=['actual MCU/HCLK and flash wait states/prefetch','SRAM/bus/arbitration timing',
 'NVIC priorities and worst allowed IRQ latency for motor/timer/communication','maximum task plus exception/MSP stack watermark',
 'NMI/HardFault/DMA writers and permitted nesting'])
(d.HERE/'build/queue-cost-results.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='records'},indent=2))
