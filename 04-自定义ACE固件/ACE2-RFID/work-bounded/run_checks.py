#!/usr/bin/env python3
"""Offline source checks only. No network, device, firmware link, flash or install."""
import datetime,hashlib,json,os,pathlib,subprocess,sys
ROOT=pathlib.Path(__file__).resolve().parent
OUT=ROOT/'output/current';OUT.mkdir(exist_ok=True)
ARM=ROOT/'arm/current';ARM.mkdir(exist_ok=True)
TC=ROOT.parent/'toolchain/usr/bin'
FW=ROOT/'source/firmware'
results=[]
def run(name,args,expect=0,env=None):
    r=subprocess.run(list(map(str,args)),cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=env)
    (OUT/(name+'.log')).write_text(r.stdout)
    results.append({'check':name,'return_code':r.returncode,'expected':expect})
    if (r.returncode==0)!=(expect==0):
        print(r.stdout);raise RuntimeError(name+' failed its expected result')
    print(name+': '+r.stdout[-600:].strip())
common=['-std=c11','-Wall','-Wextra','-Werror','-DBR_HOST_TEST','-DBR_OFFLINE_TEST','-I'+str(FW)]
for name,test,sources in [('core','test_bounded_rc522.c',['bounded_rc522.c']),('bridge','test_stock_bridge.c',['bounded_rc522.c','stock_bounded_bridge.c'])]:
    files=[ROOT/'tests'/test]+[FW/s for s in sources]
    run('compile-'+name,['cc','-O2',*common,*files,'-o',ROOT/'tests'/('current-'+name)])
    run('host-'+name,[ROOT/'tests'/('current-'+name)])
    run('compile-'+name+'-san',['cc','-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer',*common,*files,'-o',ROOT/'tests'/('current-'+name+'-san')])
    run('host-'+name+'-san',[ROOT/'tests'/('current-'+name+'-san')],env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
flags=['-mcpu=cortex-m3','-mthumb','-Os','-std=c11','-Wall','-Wextra','-Werror','-ffreestanding','-fno-builtin','-fno-common','-fno-unwind-tables','-fno-asynchronous-unwind-tables','-fstack-usage']
run('arm-version',[TC/'arm-none-eabi-gcc','--version'])
for src in ['bounded_rc522','stock_bounded_bridge']:
    run('arm-'+src,[TC/'arm-none-eabi-gcc',*flags,'-DBR_OFFLINE_TEST','-c',FW/(src+'.c'),'-o',ARM/(src+'.o')])
run('production-compile-refused',[TC/'arm-none-eabi-gcc',*flags,'-c',FW/'stock_bounded_bridge.c','-o',ARM/'SHOULD-NOT-EXIST.o'],expect=1)
assert not (ARM/'SHOULD-NOT-EXIST.o').exists()
run('arm-thunks',[TC/'arm-none-eabi-as','-mcpu=cortex-m3','-mthumb',FW/'stock_bounded_thunks.S','-o',ARM/'stock_bounded_thunks.o'])
run('arm-relocatable-only',[TC/'arm-none-eabi-ld','-r',ARM/'bounded_rc522.o',ARM/'stock_bounded_bridge.o',ARM/'stock_bounded_thunks.o','-o',ARM/'OFFLINE-UNLINKED-INTEGRATION.o'])
run('arm-unresolved',[TC/'arm-none-eabi-nm','-u',ARM/'OFFLINE-UNLINKED-INTEGRATION.o'])
run('arm-size',[TC/'arm-none-eabi-size',ARM/'OFFLINE-UNLINKED-INTEGRATION.o'])
run('arm-disassembly',[TC/'arm-none-eabi-objdump','-dr',ARM/'OFFLINE-UNLINKED-INTEGRATION.o'])
expected={'br_platform_commit_valid','br_platform_denied','br_platform_enter','br_platform_leave','br_platform_transaction','memset','stock_select_resume'}
actual={line.split()[-1] for line in (OUT/'arm-unresolved.log').read_text().splitlines()}
assert actual==expected,(actual,expected)
paths=[ROOT/'run_checks.py',FW/'bounded_rc522.c',FW/'bounded_rc522.h',FW/'stock_bounded_bridge.c',FW/'stock_bounded_bridge.h',FW/'stock_bounded_thunks.S',ROOT/'tests/test_bounded_rc522.c',ROOT/'tests/test_stock_bridge.c']
paths+=sorted(ARM.glob('*.o'))+sorted(ARM.glob('*.su'))+sorted(OUT.glob('*.log'))
files={str(p.relative_to(ROOT)):{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in paths}
manifest={'time_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'HOST MOCK TESTS + ARM RELOCATABLE COMPILE ONLY; NOT INSTALLED IN ANY FIRMWARE','sanitizers':'ASan and UBSan; LeakSanitizer disabled because runtime ptrace blocks it; no heap allocations in tested C' ,'base_sha256':hashlib.sha256((ROOT.parent/'input/ACE2_V1.1.31_20260306.bin').read_bytes()).hexdigest(),'checks':results,'files':files}
(OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print('Current source/object/log manifest:',OUT/'manifest.json')
