#!/usr/bin/env python3
"""Offline C/ARM integration checks, never hardware or CPU/RTOS emulation."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent;FW=ROOT/'firmware';TEST=ROOT/'tests';BUILD=ROOT/'build';OUT=ROOT/'output'
checks=[]
def run(name,args,success=True):
 p=subprocess.run(list(map(str,args)),cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0:abort_on_error=1'})
 (BUILD/(name+'.log')).write_text(p.stdout)
 ok=(p.returncode==0)==success;checks.append(dict(name=name,exit_code=p.returncode,passed=ok,output_tail=p.stdout[-1200:]))
 print(name+': '+p.stdout[-360:].strip(),flush=True)
 if not ok:raise RuntimeError(name)
run('accessor-full-regression',[sys.executable,ROOT/'verify_accessor.py'])
flags=['-std=c11','-O2','-Wall','-Wextra','-Werror','-DAUTH_RESULTS_OFFLINE_TEST=1','-DBR_HOST_TEST=1','-DBR_OFFLINE_TEST=1','-DRF_ADAPTER_OFFLINE_TEST=1','-I'+str(FW),'-I'+str(TEST)]
suites={
 'typed-results':(['typed_results'],'test_typed_results.c'),
 'core':(['bounded_rc522'],'test_bounded_rc522.c'),
 'bridge':(['bounded_rc522','bounded_halt','stock_bounded_bridge'],'test_stock_bridge.c'),
 'owner':(['rf_owner'],'test_rf_owner.c'),
 'adapter':(['rf_owner','rf_stock_adapter'],'test_rf_stock_adapter.c'),
 'integration':(['experimental_binding','event_storage','bounded_rc522','bounded_halt','stock_bounded_bridge','rf_owner','rf_stock_adapter','typed_results','decoder_typed_bridge'],'test_integration.c'),
 'typed-runtime':(['experimental_binding','event_storage','bounded_rc522','bounded_halt','stock_bounded_bridge','rf_owner','rf_stock_adapter','typed_results','decoder_typed_bridge'],'test_typed_runtime.c'),
 'halt-runtime':(['experimental_binding','event_storage','bounded_rc522','bounded_halt','stock_bounded_bridge','rf_owner','rf_stock_adapter','typed_results','decoder_typed_bridge'],'test_halt_runtime.c')}
for name,(sources,test) in suites.items():
 for sanitized in (False,True):
  target=name+('-san' if sanitized else '');extra=[];objects=[]
  if sanitized:extra+=['-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-fno-pie','-no-pie']
  if name in ('integration','halt-runtime','typed-runtime'):
   extra+=['-DACE_EXPERIMENTAL_OFFLINE=1','-DPRELOAD_INTEGRATION_EXPERIMENT=1','-DEX_STORAGE_HOST_TEST=1']
   obj=BUILD/('runtime-decoder-'+target+'.o')
   run('runtime-compile-decoder-'+target,['gcc',*flags,*extra,'-Wno-unused-function','-Wno-unused-const-variable','-include','stdint.h','-include','string.h','-c',FW/'native_tag_decoder.c','-o',obj])
   objects=[TEST/'event_mock.c',obj]
  run('runtime-compile-'+target,['gcc',*flags,*extra,*[FW/(s+'.c') for s in sources],TEST/test,*objects,'-o',BUILD/('runtime-'+target)])
  run('runtime-test-'+target,[BUILD/('runtime-'+target)])
first=(OUT/'manifest.json').read_bytes()
run('runtime-repeat-arm-link',[sys.executable,ROOT/'build_accessor.py','--offline'])
assert first==(OUT/'manifest.json').read_bytes(),'Nonreproducible ARM link'
run('runtime-image-checks',[sys.executable,TEST/'verify_runtime_image.py'])
paths=[ROOT/'README.md',ROOT/'runtime-base.json',OUT/'merge-contract.json',ROOT/'build_accessor.py',ROOT/'verify_runtime.py',ROOT/'verify_accessor.py',*sorted(FW.glob('*')),*sorted(TEST.glob('*')),
 BUILD/'ACCESSOR-NOFLASH.elf',OUT/'ACCESSOR-NOFLASH.offline-image',OUT/'manifest.json',BUILD/'symbols.json',BUILD/'combined.dis']
assert hashlib.sha256((ROOT.parent/'accessor-experiment/output/ACCESSOR-NOFLASH.offline-image').read_bytes()).hexdigest()=='c132f10a9a91f4bf7a5a5917772a61ea123e74d4d7a8e9b7fb8cb65161a89750'
assert hashlib.sha256((ROOT.parent.parent/'integration-rf-spi/output/EXPERIMENTAL-DO-NOT-FLASH.offline-image').read_bytes()).hexdigest()=='1ffba30b853ece41509c79b885180447d52c8d591be9185e2849ef341a202e6b'
assert hashlib.sha256((ROOT.parent/'rf-runtime-experiment/output/ACCESSOR-NOFLASH.offline-image').read_bytes()).hexdigest()=='6b67835bff4cbc3c52bab477929206fe79e48807176c91393246021633921c93'
files={str(p.relative_to(ROOT)):{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in paths if p.is_file()}
(OUT/'runtime-verification.json').write_text(json.dumps(dict(scope='Host C doubles, ASan/UBSan, GNU ARM static link and limited Thumb branch model only',checks=checks,files=files,physical_proof=False,device_or_network_access=False),indent=2)+'\n')
print('PASS: OFFLINE runtime experiment only. NOFLASH, RF-ready false, no physical proof.')
