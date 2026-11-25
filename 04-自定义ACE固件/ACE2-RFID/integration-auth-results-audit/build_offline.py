#!/usr/bin/env python3
"""Offline typed-result tests and relocatable ARM objects only. Never an image."""
import argparse, hashlib, json, pathlib, subprocess
R=pathlib.Path(__file__).resolve().parent
def run(args):
    p=subprocess.run(list(map(str,args)),capture_output=True,text=True)
    if p.returncode: raise RuntimeError(p.stdout+p.stderr)
    return p.stdout+p.stderr
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--offline',action='store_true');a=p.parse_args()
    if not a.offline:p.error('REFUSED: offline test objects only, no firmware output')
    b=R/'build';b.mkdir(exist_ok=True)
    f=R/'firmware';v=R/'vendor';t=R/'tests'
    flags=['-std=c11','-Wall','-Wextra','-Werror','-DAUTH_RESULTS_OFFLINE_TEST=1','-I'+str(f),'-I'+str(v)]
    sources=[f/'typed_results.c',t/'test_typed_results.c']
    run(['gcc',*flags,'-O2',*sources,'-o',b/'test-typed-results'])
    normal=run([b/'test-typed-results']);(b/'tests.log').write_text(normal)
    run(['gcc',*flags,'-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer',*sources,'-o',b/'test-typed-results-san'])
    env=__import__('os').environ.copy();env['ASAN_OPTIONS']='detect_leaks=0:abort_on_error=1'
    s=subprocess.run([str(b/'test-typed-results-san')],capture_output=True,text=True,env=env)
    (b/'tests-san.log').write_text(s.stdout+s.stderr)
    if s.returncode:raise RuntimeError(s.stdout+s.stderr)
    tc=R.parent/'toolchain/usr/bin'
    armflags=[*flags,'-mcpu=cortex-m3','-mthumb','-Os','-ffreestanding','-fno-builtin','-fno-common','-fno-unwind-tables','-fno-asynchronous-unwind-tables','-fstack-usage']
    for src,name in [(f/'typed_results.c','typed-results-NOFLASH.o'),(v/'bounded_rc522.c','bounded-core-NOFLASH.o')]:
        run([tc/'arm-none-eabi-gcc',*armflags,'-c',src,'-o',b/name])
    # Relocatable object, not linked at any firmware/device address. Mandatory
    # platform contracts remain unresolved. There is no reset vector or trailer.
    obj=b/'TYPED-RESULTS-NOFLASH.relocatable.o'
    run([tc/'arm-none-eabi-ld','-r',b/'typed-results-NOFLASH.o',b/'bounded-core-NOFLASH.o','-o',obj])
    (b/'arm.dis').write_text(run([tc/'arm-none-eabi-objdump','-dr',obj]))
    unresolved=run([tc/'arm-none-eabi-nm','-u',obj]);(b/'unresolved.txt').write_text(unresolved)
    assert {line.split()[-1] for line in unresolved.splitlines()}=={'br_platform_enter','br_platform_leave','br_platform_commit_valid'}
    # The module cannot compile without explicit offline opt-in.
    denied=subprocess.run([str(tc/'arm-none-eabi-gcc'),'-mcpu=cortex-m3','-mthumb','-I'+str(v),'-I'+str(f),'-fsyntax-only',str(f/'typed_results.c')],capture_output=True,text=True)
    assert denied.returncode and 'no production entry' in denied.stderr
    (b/'production-rejected.log').write_text(denied.stderr)
    unchanged=R.parent/'integration-preload/accessor-experiment/output/ACCESSOR-NOFLASH.offline-image'
    assert sha(unchanged)=='c132f10a9a91f4bf7a5a5917772a61ea123e74d4d7a8e9b7fb8cb65161a89750'
    m={'classification':'OFFLINE TYPED INTERFACE RESEARCH, NO FIRMWARE IMAGE',
       'c132_unchanged_sha256':sha(unchanged),'arm_relocatable_sha256':sha(obj),
       'sources':{str(x.relative_to(R)):sha(x) for folder in [f,v,t] for x in folder.iterdir() if x.is_file()},
       'native_tests':normal.strip(),'sanitized_tests':s.stdout.strip(),
       'auth_negative_emitted':False,'production_entries_changed':False,'slots_connected':False,
       'physical_tests':False,'rc522_terminal_semantics_verified':False,
       'cleanup_proven':False,'unresolved_platform_contracts':unresolved.splitlines()}
    (b/'manifest.json').write_text(json.dumps(m,indent=2)+'\n')
    print(normal.strip());print(s.stdout.strip());print('ARM relocatable SHA256',sha(obj));print('NOFLASH; c132 unchanged; no slot hooks or production entry.')
if __name__=='__main__':main()
