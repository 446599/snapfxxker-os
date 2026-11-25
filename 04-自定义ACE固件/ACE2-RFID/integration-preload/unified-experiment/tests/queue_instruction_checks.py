#!/usr/bin/env python3
"""Actual queue Thumb entry/inline-hook ABI checks. Not a scheduler proof."""
import json
import dynamic_instruction_checks as d

cases=0;peak_mask=0;peak_stack=0
QSTATE=d.EVENT+32+184
for lane in range(4):
 for masked in (0,1):
  for flags in range(32):
   for name in ('pq_job_link_hook','pq_job_unlink_hook'):
    c=d.CPU(lane,masked);c.psr=flags<<27;c.r[4]=lane
    flag=0x200012e8+(lane>>1)*208+lane*32+168
    c.put(flag,0 if name=='pq_job_link_hook' else 1,1)
    if name=='pq_job_link_hook':c.r[0]=flag;c.r[12]=1
    else:c.r[1]=0;c.r[3]=flag
    c.put(QSTATE+lane*8,3);c.put(QSTATE+lane*8+4,1)
    before=c.r[:];psr=c.psr
    stop='pq_stock_job_link_resume' if name=='pq_job_link_hook' else 'pq_stock_job_unlink_resume'
    c.run(d.SYM[name],stops=(d.SYM[stop],))
    assert c.r[:15]==before[:15] and c.mask==masked
    assert c.get(flag,1)==(name=='pq_job_link_hook')
    if name=='pq_job_link_hook':
     assert c.get(QSTATE+lane*8)==4 and c.get(QSTATE+lane*8+4)==1
     assert c.psr==psr
    else:
     assert c.get(QSTATE+lane*8)==3 and c.get(QSTATE+lane*8+4)==0
     observed=c.psr;c.psr=psr;c.subflags(before[14],5);assert observed==c.psr
    assert not c.writes # No MMIO or motor stop in observation hooks.
    peak_mask=max(peak_mask,c.max_masked);peak_stack=max(peak_stack,d.STACK-c.min_sp)
    cases+=1
for name,source in [('pq_send_preload',1),('pq_send_cleanup',2),('pq_send_requeue',3),('pq_send_control',4)]:
 for lane in range(4):
  c=d.CPU(lane);before=c.r[:];c.run(d.SYM[name],stops=(d.SYM['pq_send'],))
  assert c.r[:3]==before[:3] and c.r[3]==source and c.r[13]%8==0
  assert c.get(c.r[13])==(before[7] if source==1 else d.U32)
  assert c.get(c.r[13]+4)==(before[11] if source==1 else d.U32)
  # C callee is covered by host tests; simulate only its AAPCS return here.
  resume=c.r[14]&~1;c.r[0:4]=[1,0xdead,0xbeef,0xabcd]
  c.run(resume)
  assert c.r[0]==1 and c.r[4:12]==before[4:12] and c.r[13]==before[13]
  cases+=1
c=d.CPU();before=c.r[:];c.run(d.SYM['pq_receive_hook'],stops=(d.SYM['pq_receive_copy'],))
assert c.r[:3]==before[:3] and c.r[3]==before[4] and c.get(c.r[13])==before[10] and c.r[13]%8==0
resume=c.r[14]&~1;c.r[0:4]=[1,0xdead,0xbeef,0xabcd]
c.run(resume,stops=(d.SYM['pq_stock_receive_resume'],))
assert c.r[0]==1 and c.r[4:15]==before[4:15];cases+=1
c=d.CPU();before=c.r[:];c.run(d.SYM['pq_enqueue_original'],stops=(d.SYM['pq_stock_enqueue_resume'],))
assert c.r[13]==before[13]-24
assert [c.get(c.r[13]+4*i) for i in range(6)]==[before[i] for i in (4,5,6,7,8,14)];cases+=1
# Enqueue hook reads SP+44 ONLY for the proven task-send call site.
for origin in (0x080167fb,0x08017a45,d.RETURN|1):
 c=d.CPU();before=c.r[:];c.r[14]=origin
 if origin==0x080167fb:c.put(c.r[13]+44,0x08012345)
 c.run(d.SYM['pq_enqueue_hook'],stops=(d.SYM['pq_enqueue_entry'],))
 assert c.r[:2]==before[:2] and c.r[2]==origin
 assert c.r[3]==(0x08012345 if origin==0x080167fb else 0)
 assert c.r[13]==before[13] and c.r[4:12]==before[4:12];cases+=1
# Both real preload call sites use this same wrapper: preserve slot and pass fp.
c=d.CPU();before=c.r[:]
c.run(d.SYM['pq_preload_requeue_hook'],stops=(d.SYM['pq_preload_requeue'],))
assert c.r[0]==before[0] and c.r[1]==before[11] and c.r[4:15]==before[4:15];cases+=1
result={'image_sha256':json.loads((d.HERE/'output/manifest.json').read_text())['image_sha256'],
 'passed':cases,'job_inline_actual_C_executed':True,'queue_wrappers_ABI_checked':True,
 'send_receive_C_tested_separately_with_host_queue_doubles':True,
 'job_hook_masked_instructions_observed':peak_mask,'job_hook_added_stack_observed':peak_stack,
 'queue_commit_masked_timing_proven':False,'queue_commit_full_CPU_execution':False,
 'full_RTOS_or_lifecycle_proof':False}
(d.HERE/'build/queue-instruction-results.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
