#!/usr/bin/env python3
import os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
BUILD=ROOT/'build';BUILD.mkdir(exist_ok=True)
def run(name,args,env=None,want=0):
 p=subprocess.run(list(map(str,args)),text=True,capture_output=True,env=env)
 (BUILD/(name+'.log')).write_text(p.stdout+p.stderr)
 assert (p.returncode==0)==(want==0),(name,p.stdout,p.stderr)
 print(name+': PASS')
run('arm-accessor-build',[sys.executable,ROOT/'build_accessor.py','--offline'])
run('default-build-refused',[sys.executable,ROOT/'build_accessor.py'],want=1)
flags=['gcc','-std=c11','-Wall','-Wextra','-Werror','-DPRELOAD_QUEUE_EXPERIMENT=1','-DACE_EXPERIMENTAL_OFFLINE=1','-DBR_OFFLINE_TEST=1',
 '-DRF_ADAPTER_OFFLINE_TEST=1','-DPRELOAD_INTEGRATION_EXPERIMENT=1','-DEX_STORAGE_HOST_TEST=1',
 '-DPQ_HOST_TEST=1','-I'+str(ROOT/'firmware'),'-I'+str(ROOT/'tests')]
for name,test,more in [('storage','test_event_storage.c',[]),('arm-queue-storage','test_arm_queue_storage.c',[]),('preload-queue','test_preload_queue.c',[ROOT/'firmware/preload_queue.c']),('preload-accessor','test_preload_accessor.c',[ROOT/'firmware/preload_hooks.c'])]:
 sources=[ROOT/'firmware/event_storage.c',ROOT/'tests/event_mock.c',ROOT/'tests'/test,*more]
 run('compile-'+name,[*flags,*sources,'-o',BUILD/('test-'+name)])
 run('test-'+name,[BUILD/('test-'+name)])
 run('compile-'+name+'-san',[*flags,'-fsanitize=address,undefined','-fno-omit-frame-pointer',*sources,'-o',BUILD/('test-'+name+'-san')])
 run('test-'+name+'-san',[BUILD/('test-'+name+'-san')],dict(os.environ,ASAN_OPTIONS='detect_leaks=0'))
run('dynamic-instruction-checks',[sys.executable,ROOT/'tests/dynamic_instruction_checks.py'])
for n in ('queue_instruction_checks','queue_commit_checks','queue_cost_profile'):
 run(n,[sys.executable,ROOT/'tests'/(n+'.py')])
run('image-and-conflict-checks',[sys.executable,ROOT/'tests/verify_image.py'])
print('All accessor experiment checks passed. NOFLASH, no hardware or actual AUTH producer proof.')
