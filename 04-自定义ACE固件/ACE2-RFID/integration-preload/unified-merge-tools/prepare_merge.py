#!/usr/bin/env python3
"""Prepare a NEW NOFLASH source tree from hash-locked RF/queue snapshots.
No device/network access; refuses overwrite and requires explicit queue hash.
This preparer is not itself a firmware validation result.
"""
import argparse,hashlib,json,shutil
from pathlib import Path
PARENT=Path(__file__).resolve().parents[1]
RF=PARENT/'rf-diagnostics-experiment';QUEUE=PARENT/'queue-experiment'
RF_IMAGE='840bafe77a7fba234fb6e75d3c38e766bf7e7ee05bcce963bbedf53cb72a7403'
RF_ELF='13f59b644a2f6b9706bfe51fbfe2793adc7b2b333e169ec30b981db9808799c5'
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def check_tree(root,image,elf=None):
 m=json.loads((root/'output/manifest.json').read_text())
 assert sha(root/'output/ACCESSOR-NOFLASH.offline-image')==m['image_sha256']==image
 assert sha(root/'build/ACCESSOR-NOFLASH.elf')==m['elf_sha256']
 if elf:assert m['elf_sha256']==elf
 for name,value in m['sources_sha256'].items():assert sha(root/'firmware'/name)==value,(root,name)
 return m
def edit(path,old,new):
 s=path.read_text();assert old in s,(path,old);path.write_text(s.replace(old,new))
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--queue-image-sha',required=True);p.add_argument('--output-name',default='unified-experiment');a=p.parse_args()
 assert a.output_name and '/' not in a.output_name and a.output_name not in ('.','..')
 out=PARENT/a.output_name;assert not out.exists(),'Refusing to overwrite an existing experiment'
 r=check_tree(RF,RF_IMAGE,RF_ELF);q=check_tree(QUEUE,a.queue_image_sha)
 assert q['arm_tail_bytes']==608 and q['arm_payload_bytes']==640 and q['arm_queue_offset']==184 and q['arm_queue_bytes']==424
 assert q['total_hooks']==39 and not q['queue_candidate_is_ownership']
 shutil.copytree(RF,out,ignore=shutil.ignore_patterns('build','output','__pycache__'))
 (out/'build').mkdir();(out/'output').mkdir();(out/'input-contracts').mkdir()
 for root,name in ((RF,'rf'),(QUEUE,'queue')):
  shutil.copy2(root/'output/manifest.json',out/'input-contracts'/(name+'-manifest.json'))
  listing={str(f.relative_to(root)):sha(f) for f in sorted(root.rglob('*')) if f.is_file() and '__pycache__' not in str(f) and ('firmware' in f.parts or 'tests' in f.parts or f.name=='build_accessor.py')}
  (out/'input-contracts'/(name+'-source-files.json')).write_text(json.dumps(listing,indent=2)+'\n')
 for f in ['preload_queue.c','preload_queue.h','preload_queue.S','experimental_binding.h','event_storage.c']:
  shutil.copy2(QUEUE/'firmware'/f,out/'firmware'/f)
 edit(out/'firmware/experimental_binding.h','uint32_t initialized, stop_epoch','uint32_t owner_task, stop_epoch')
 edit(out/'firmware/experimental_binding.h','0x45585434','0x45585531');edit(out/'firmware/experimental_binding.h','0x52445934','0x52445531')
 for f in ['dynamic_instruction_checks.py','queue_instruction_checks.py','queue_commit_checks.py','test_preload_queue.c','test_arm_queue_storage.c','queue_cost_profile.py','verify_image.py']:
  shutil.copy2(QUEUE/'tests'/f,out/'tests'/f)
 edit(out/'tests/dynamic_instruction_checks.py','0x45585434','0x45585531');edit(out/'tests/dynamic_instruction_checks.py','0x52445934','0x52445531')
 edit(out/'tests/verify_image.py','EXQ4!!!','EXUNI!!')
 edit(out/'tests/test_arm_queue_storage.c','s->ex.initialized','s->ex.owner_task')
 edit(out/'tests/test_arm_queue_storage.c','0x45585231};','0x45585231,0x45585434};')
 edit(out/'tests/test_arm_queue_storage.c','0x52445231};','0x52445231,0x52445934};')
 edit(out/'tests/test_arm_queue_storage.c','i<4;i++','i<5;i++')
 shutil.copy2(QUEUE/'build_accessor.py',out/'build_accessor.py')
 b=out/'build_accessor.py'
 edit(b,"'preload_queue.c','preload_queue.S']","'preload_queue.c','preload_queue.S','bounded_halt.c','typed_results.c','decoder_typed_bridge.c']")
 edit(b,"'-DBR_OFFLINE_TEST=1'","'-DBR_OFFLINE_TEST=1','-DAUTH_RESULTS_OFFLINE_TEST=1'")
 edit(b,"for address,expected,symbol,reason in rf.HOOKS+", "hookspec=[(a,e,'br_stock_halt' if a==0x0800e108 else s,r) for a,e,s,r in rf.HOOKS]\n for address,expected,symbol,reason in hookspec+")
 edit(b,'EXQ4!!!','EXUNI!!')
 edit(b,'QUEUE ASSOCIATION EXPERIMENT NOFLASH; normal reset absolutely blocked','UNIFIED RF/QUEUE EXPERIMENT NOFLASH; sequential normal reset self-loop')
 edit(b,'arm_tail_bytes=608,',"storage_version='UNIFIED-EXU1/RDU1',storage_magic='0x45585531',storage_ready='0x52445531',owner_task_offset=76,owner_task_busy_bit=1,\n  arm_tail_bytes=608,")
 edit(b,'rf_ready=False,auth_failure_source_connected=False',"bounded_halt_hook='0x0800e108 -> br_stock_halt',per_register_owner_check=True,\n  background_lane_provenance=False,caller_task_identity_binding='IPSR==0 and current TCB at 0x20001dd0',caller_task_lifetime_proven=False,typed_decoder_probe_connected=True,auth_observation_is_identity=False,whole_operation_cleanup_proven=False,\n  rf_ready=False,auth_failure_source_connected=False")
 for name in ['verify_accessor.py','verify_runtime.py']:
  edit(out/name,"'-DACE_EXPERIMENTAL_OFFLINE=1'","'-DPRELOAD_QUEUE_EXPERIMENT=1','-DACE_EXPERIMENTAL_OFFLINE=1'") if name=='verify_accessor.py' else edit(out/name,"'-DAUTH_RESULTS_OFFLINE_TEST=1'","'-DPRELOAD_QUEUE_EXPERIMENT=1','-DAUTH_RESULTS_OFFLINE_TEST=1'")
 edit(out/'verify_accessor.py',"run('image-and-conflict-checks'", "for n in ('queue_instruction_checks','queue_commit_checks','queue_cost_profile'):\n run(n,[sys.executable,ROOT/'tests'/(n+'.py')])\nrun('image-and-conflict-checks'")
 edit(out/'tests/verify_runtime_image.py',"len(m['hooks'])==29","len(m['hooks'])==39")
 # Source snapshots remain unchanged after materialization, before any build.
 check_tree(RF,RF_IMAGE,RF_ELF);check_tree(QUEUE,a.queue_image_sha)
 contract=dict(rf_image=RF_IMAGE,rf_elf=RF_ELF,queue_image=q['image_sha256'],queue_elf=q['elf_sha256'],
  output=str(out),status='prepared-not-built-or-verified',tail=608,payload=640,minimum_block=648,
  fields={'rf':16,'ex':76,'preload':144,'queue':184},rf_owner_task_field='EX+0, bit0 exchange busy, aligned current TCB',
  version='EXU1/RDU1',queue_candidate_bound_to_rf_owner=False,production_proofs=7,ready=False,physical_proof=False)
 (out/'output/unified-input-contract.json').write_text(json.dumps(contract,indent=2)+'\n')
 print(out)
if __name__=='__main__':main()
