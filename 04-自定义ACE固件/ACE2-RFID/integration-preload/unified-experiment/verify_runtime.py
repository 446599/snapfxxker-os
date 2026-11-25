#!/usr/bin/env python3
"""Offline C/ARM integration checks, never hardware or CPU/RTOS emulation."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent;FW=ROOT/'firmware';TEST=ROOT/'tests';BUILD=ROOT/'build';OUT=ROOT/'output'
from verification_report import VerificationRun
sys.path.insert(0,str(ROOT.parent))
import build as ph
def run(name,args,success=True):
 p=subprocess.run(list(map(str,args)),cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0:abort_on_error=1'})
 (BUILD/(name+'.log')).write_text(p.stdout)
 ok=(p.returncode==0)==success;report.checks.append(dict(name=name,exit_code=p.returncode,passed=ok,output_tail=p.stdout[-1200:]))
 print(name+': '+p.stdout[-360:].strip(),flush=True)
 if not ok:raise RuntimeError(name)
inputs=[ROOT/'README.md',ROOT/'runtime-base.json',ROOT/'build_accessor.py',ROOT/'verify_runtime.py',
 ROOT/'verify_accessor.py',ROOT/'verification_report.py',ROOT.parent/'build.py',ROOT.parent/'merge_offline.py',
 ph.STOCK,*sorted(FW.glob('*')),*sorted(TEST.glob('*')),*sorted((ROOT/'frozen-inputs').rglob('*')),
 ROOT/'frozen-inputs.json']
inputs=[p for p in inputs if not p.is_dir()]
with VerificationRun(ROOT,inputs) as report:
 report.tools={name:subprocess.check_output([str(path),'--version'],text=True).splitlines()[0]
  for name,path in [('arm-gcc',ph.BIN/'arm-none-eabi-gcc'),('arm-ld',ph.BIN/'arm-none-eabi-ld'),('host-gcc','gcc')]}
 run('verification-report-regression',[sys.executable,TEST/'test_verification_report.py'])
 run('accessor-full-regression',[sys.executable,ROOT/'verify_accessor.py'])
 flags=['-std=c11','-O2','-Wall','-Wextra','-Werror','-DPRELOAD_QUEUE_EXPERIMENT=1','-DAUTH_RESULTS_OFFLINE_TEST=1','-DBR_HOST_TEST=1','-DBR_OFFLINE_TEST=1','-DRF_ADAPTER_OFFLINE_TEST=1','-I'+str(FW),'-I'+str(TEST)]
 suites={
  'typed-results':(['typed_results'],'test_typed_results.c'),
  'core':(['bounded_rc522'],'test_bounded_rc522.c'),
  'bridge':(['bounded_rc522','bounded_halt','stock_bounded_bridge'],'test_stock_bridge.c'),
  'owner':(['rf_owner'],'test_rf_owner.c'),
  'adapter':(['rf_owner','rf_stock_adapter'],'test_rf_stock_adapter.c'),
  'integration':(['experimental_binding','event_storage','bounded_rc522','bounded_halt','stock_bounded_bridge','rf_owner','rf_stock_adapter','typed_results','decoder_typed_bridge'],'test_integration.c'),
  'typed-runtime':(['experimental_binding','event_storage','bounded_rc522','bounded_halt','stock_bounded_bridge','rf_owner','rf_stock_adapter','typed_results','decoder_typed_bridge'],'test_typed_runtime.c'),
  'lease-runtime':(['experimental_binding','event_storage','bounded_rc522','bounded_halt','stock_bounded_bridge','rf_owner','rf_stock_adapter','typed_results','decoder_typed_bridge'],'test_lease_runtime.c'),
  'unified-isolation':(['experimental_binding','event_storage','bounded_rc522','bounded_halt','stock_bounded_bridge','rf_owner','rf_stock_adapter','typed_results','decoder_typed_bridge','preload_queue'],'test_unified_isolation.c'),
  'halt-runtime':(['experimental_binding','event_storage','bounded_rc522','bounded_halt','stock_bounded_bridge','rf_owner','rf_stock_adapter','typed_results','decoder_typed_bridge'],'test_halt_runtime.c')}
 for name,(sources,test) in suites.items():
  for sanitized in (False,True):
   target=name+('-san' if sanitized else '');extra=[];objects=[]
   if sanitized:extra+=['-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-fno-pie','-no-pie']
   if name=='unified-isolation':extra+=['-DPQ_HOST_TEST=1','-Wl,--wrap=rf_requeue_entry']
   if name in ('integration','halt-runtime','typed-runtime','unified-isolation','lease-runtime'):
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
 assert hashlib.sha256((ROOT.parent/'accessor-experiment/output/ACCESSOR-NOFLASH.offline-image').read_bytes()).hexdigest()=='c132f10a9a91f4bf7a5a5917772a61ea123e74d4d7a8e9b7fb8cb65161a89750'
 assert hashlib.sha256((ROOT.parent.parent/'integration-rf-spi/output/EXPERIMENTAL-DO-NOT-FLASH.offline-image').read_bytes()).hexdigest()=='1ffba30b853ece41509c79b885180447d52c8d591be9185e2849ef341a202e6b'
 assert hashlib.sha256((ROOT.parent/'rf-runtime-experiment/output/ACCESSOR-NOFLASH.offline-image').read_bytes()).hexdigest()=='6b67835bff4cbc3c52bab477929206fe79e48807176c91393246021633921c93'
 for name,image_sha in [('rf-diagnostics-experiment','840bafe77a7fba234fb6e75d3c38e766bf7e7ee05bcce963bbedf53cb72a7403'),('queue-experiment','e568a39514a405c185995bdd8cf5cbcb90ad3fdc9bafa01f71e41333254d8114')]:
  assert hashlib.sha256((ROOT.parent/name/'output/ACCESSOR-NOFLASH.offline-image').read_bytes()).hexdigest()==image_sha
 report.files=inputs+[BUILD/'ACCESSOR-NOFLASH.elf',OUT/'ACCESSOR-NOFLASH.offline-image',
  OUT/'manifest.json',BUILD/'symbols.json',BUILD/'combined.dis']
 report.files += [BUILD/(c['name']+'.log') for c in report.checks]
print('PASS: OFFLINE runtime experiment only. NOFLASH, RF-ready false, no physical proof.')
