#!/usr/bin/env python3
"""Offline-only regression; no device, network, installation or flash operations."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BUILD=ROOT/'build';BUILD.mkdir(exist_ok=True)
FW=ROOT/'firmware';TEST=ROOT/'tests';checks=[]
def run(name,args,want=0):
 p=subprocess.run(list(map(str,args)),text=True,capture_output=True,
     env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
 (BUILD/(name+'.log')).write_text(p.stdout+p.stderr)
 ok=(p.returncode==0)==(want==0);checks.append(dict(name=name,exit_code=p.returncode,passed=ok))
 print(name+': '+('PASS' if ok else 'FAIL'),flush=True)
 if not ok:raise RuntimeError(p.stdout+p.stderr)
run('arm-queue-build',[sys.executable,ROOT/'build_accessor.py','--offline'])
first=json.loads((ROOT/'output/manifest.json').read_text())
run('arm-queue-repeat',[sys.executable,ROOT/'build_accessor.py','--offline'])
assert first==json.loads((ROOT/'output/manifest.json').read_text())
run('default-build-refused',[sys.executable,ROOT/'build_accessor.py'],want=1)
flags=['gcc','-std=c11','-Wall','-Wextra','-Werror','-DACE_EXPERIMENTAL_OFFLINE=1','-DBR_OFFLINE_TEST=1',
 '-DRF_ADAPTER_OFFLINE_TEST=1','-DPRELOAD_INTEGRATION_EXPERIMENT=1','-DPRELOAD_QUEUE_EXPERIMENT=1',
 '-DEX_STORAGE_HOST_TEST=1','-DBR_HOST_TEST=1','-DPQ_HOST_TEST=1',
 '-I'+str(FW),'-I'+str(TEST)]
suites={
 'storage':(['event_storage'],'test_event_storage.c',True),
 'arm-queue-storage':(['event_storage'],'test_arm_queue_storage.c',True),
 'preload-accessor':(['event_storage','preload_hooks'],'test_preload_accessor.c',True),
 'preload-queue':(['event_storage','preload_queue'],'test_preload_queue.c',True),
 'core':(['bounded_rc522'],'test_bounded_rc522.c',False),
 'bridge':(['bounded_rc522','stock_bounded_bridge'],'test_stock_bridge.c',False),
 'owner':(['rf_owner'],'test_rf_owner.c',False),
 'adapter':(['rf_owner','rf_stock_adapter'],'test_rf_stock_adapter.c',False),
 'integration':(['event_storage','experimental_binding','bounded_rc522','stock_bounded_bridge','rf_owner','rf_stock_adapter'],
                'test_integration.c',True)}
for name,(sources,test,mock) in suites.items():
 for san in (False,True):
  tag=name+('-san' if san else '')
  extra=['-fsanitize=address,undefined','-fno-omit-frame-pointer','-fno-pie','-no-pie'] if san else []
  objects=[TEST/'event_mock.c'] if mock else []
  if name=='integration':
   obj=BUILD/('host-decoder-'+tag+'.o')
   run('compile-decoder-'+tag,[*[x for x in flags if x!='-Werror'],*extra,'-include','stdint.h',
       '-include','string.h','-c',FW/'native_tag_decoder.c','-o',obj]);objects.append(obj)
  suiteflags=flags if mock else [x for x in flags if x!='-DACE_EXPERIMENTAL_OFFLINE=1']
  run('compile-'+tag,[*suiteflags,*extra,*[FW/(s+'.c') for s in sources],TEST/test,*objects,'-o',BUILD/('test-'+tag)])
  run('test-'+tag,[BUILD/('test-'+tag)])
for source in ('preload_queue.c','preload_queue.S'):
 run('normal-compile-refused-'+source,[str(ROOT.parent.parent/'toolchain/usr/bin/arm-none-eabi-gcc'),
     '-mcpu=cortex-m3','-mthumb','-I'+str(FW),'-c',FW/source,'-o',BUILD/('FORBIDDEN-'+source+'.o')],want=1)
for name in ('dynamic_instruction_checks','queue_instruction_checks','queue_commit_checks','queue_cost_profile','verify_image'):
 run(name,[sys.executable,TEST/(name+'.py')])
paths=[ROOT/'build_accessor.py',ROOT/'verify_queue.py',ROOT/'README.md',ROOT/'output/merge-contract.json']
paths+=sorted(FW.glob('*'))+sorted(TEST.glob('*.c'))+sorted(TEST.glob('*.h'))+sorted(TEST.glob('*.py'))
paths+=sorted(BUILD.glob('*.su'))+[BUILD/'ACCESSOR-NOFLASH.elf',ROOT/'output/ACCESSOR-NOFLASH.offline-image',ROOT/'output/manifest.json']
paths+=[p for p in BUILD.glob('*results.json')]
files={str(p.relative_to(ROOT)):{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in paths if p.is_file()}
(ROOT/'output/verification-manifest.json').write_text(json.dumps(dict(checks=checks,files=files,
 scope='Host doubles + ASan/UBSan + compiled ARM/Thumb finite instruction windows, not full RTOS',
 not_proven=['hardware/IRQ timing','heap/stack margin','queue/TCB/boot lifetime ABA','physical tag identity',
 'actual terminal AUTH failure/normal retry classification','safe motor stop','deployability']),indent=2)+'\n')
print('PASS offline queue experiment; NOT functional/flashable/safety-verified firmware.')
