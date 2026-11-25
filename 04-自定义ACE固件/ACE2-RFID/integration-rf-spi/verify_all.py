#!/usr/bin/env python3
"""Rebuild and verify the gated offline fixture. Never accesses a device/network."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parent
BUILD=ROOT/'build';OUT=ROOT/'output';FW=ROOT/'firmware';TEST=ROOT/'tests'
BUILD.mkdir(exist_ok=True);OUT.mkdir(exist_ok=True)
checks=[]
def run(name,args,expected_success=True,env=None):
    p=subprocess.run(list(map(str,args)),cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
        text=True,env=env)
    (BUILD/(name+'.log')).write_text(p.stdout)
    ok=(p.returncode==0)==expected_success
    checks.append({'name':name,'exit_code':p.returncode,'expected_success':expected_success,'passed':ok})
    print(name+': '+p.stdout[-450:].strip())
    if not ok:raise RuntimeError(name+' failed; see log')

flags=['-std=c11','-Wall','-Wextra','-Werror','-O2','-I'+str(FW),'-DBR_HOST_TEST=1',
    '-DBR_OFFLINE_TEST=1','-DRF_ADAPTER_OFFLINE_TEST=1']
suites={
 'storage':(['event_storage'],'test_event_storage.c'),
 'core':(['bounded_rc522'],'test_bounded_rc522.c'),
 'bridge':(['bounded_rc522','stock_bounded_bridge'],'test_stock_bridge.c'),
 'owner':(['rf_owner'],'test_rf_owner.c'),
 'adapter':(['rf_owner','rf_stock_adapter'],'test_rf_stock_adapter.c'),
 'integration':(['event_storage','experimental_binding','bounded_rc522','stock_bounded_bridge','rf_owner','rf_stock_adapter'],
     'test_integration.c')}
for name,(sources,test) in suites.items():
    for sanitized in (False,True):
        target=name+('-san' if sanitized else '')
        extra=['-DACE_EXPERIMENTAL_OFFLINE=1'] if name in ('integration','storage') else []
        if sanitized:extra+=['-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-fno-pie','-no-pie']
        objects=[]
        if name in ('integration','storage'):
            extra+=['-DEX_STORAGE_HOST_TEST=1'];objects=[TEST/'event_mock.c']
        if name=='integration':
            obj=BUILD/('host-decoder-'+target+'.o')
            # Use libc ONLY for host tests. The actual ARM image links its own
            # freestanding memcpy/memset, not these host symbols.
            run('compile-decoder-'+target,['cc',*[x for x in flags if x!='-Werror'],*extra,
                '-include','stdint.h','-include','string.h','-c',FW/'native_tag_decoder.c','-o',obj])
            objects.append(obj)
        run('compile-'+target,['cc',*flags,*extra,*[FW/(s+'.c') for s in sources],TEST/test,*objects,
            '-o',BUILD/('test-'+target)])
        run('test-'+target,[BUILD/('test-'+target)],env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
run('default-output-refused',[sys.executable,ROOT/'build_offline.py'],False)
for source in ('experimental_binding','stock_bounded_bridge','rf_stock_adapter'):
    run('normal-compile-refused-'+source,['cc','-I'+str(FW),'-c',FW/(source+'.c'),
        '-o',BUILD/('FORBIDDEN-'+source+'.o')],False)
    assert not (BUILD/('FORBIDDEN-'+source+'.o')).exists()
run('arm-link',[sys.executable,ROOT/'build_offline.py','--offline'])
first=json.loads((OUT/'analysis-manifest.json').read_text())
run('arm-link-repeat',[sys.executable,ROOT/'build_offline.py','--offline'])
second=json.loads((OUT/'analysis-manifest.json').read_text())
assert first==second,'Nondeterministic offline link'
run('image-static-and-limited-model',[sys.executable,TEST/'verify_image.py'])
paths=[ROOT/'build_offline.py',ROOT/'verify_all.py']
paths+=sorted(FW.glob('*'))+sorted(TEST.glob('*.c'))+sorted(TEST.glob('*.h'))+sorted(TEST.glob('*.py'))
paths+=sorted(BUILD.glob('*.o'))+sorted(BUILD.glob('*.su'))
# Exclude the caller's still-open aggregate stdout capture; individual logs are complete.
paths+=[p for p in sorted(BUILD.glob('*.log')) if p.name!='final-verification.log']
paths+=[BUILD/'EXPERIMENTAL-DO-NOT-FLASH.elf',OUT/'EXPERIMENTAL-DO-NOT-FLASH.offline-image',
    OUT/'analysis-manifest.json',BUILD/'integration.dis',BUILD/'image.dis',BUILD/'symbols.json']
files={str(p.relative_to(ROOT)):{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size}
       for p in paths if p.is_file()}
(OUT/'verification-manifest.json').write_text(json.dumps({'scope':'Host doubles + ARM compile/link + static byte/limited branch model only',
    'not_run':['ARM CPU emulation','original RTOS execution','device test','physical STOP/thermal test','flash'],
    'sanitizers':'ASan and UBSan; leak detector disabled under ptrace; no heap allocation in candidate C',
    'checks':checks,'files':files},indent=2)+'\n')
print('PASS: reproducible linked OFFLINE fixture. NOT a functional/flashable/safety-verified firmware.')
