#!/usr/bin/env python3
from pathlib import Path
import os,subprocess,sys
HERE=Path(__file__).resolve().parent
OUT=HERE/'build'
OUT.mkdir(exist_ok=True)
def run(name,cmd,env=None,success=True):
 p=subprocess.run(list(map(str,cmd)),text=True,capture_output=True,env=env)
 (OUT/(name+'.log')).write_text(p.stdout+p.stderr)
 assert (p.returncode==0)==success,(name,p.stdout,p.stderr)
 print(name+': PASS')
run('arm-build',[sys.executable,HERE/'build.py'])
run('macro-gate',['gcc','-c',HERE/'src/preload_hooks.c','-o',OUT/'ungated.o'],success=False)
flags=['gcc','-std=c11','-Wall','-Wextra','-Werror','-DPRELOAD_INTEGRATION_EXPERIMENT=1',
       '-I'+str(HERE/'src'),HERE/'src/preload_hooks.c',HERE/'tests/test_core.c']
run('host-compile',flags+['-o',OUT/'test-core'])
run('host-core',[OUT/'test-core'])
run('sanitizer-compile',flags+['-fsanitize=address,undefined','-fno-omit-frame-pointer','-o',OUT/'test-core-san'])
# LeakSanitizer is unavailable under this execution environment's ptrace.
# No allocation is used by the code under test. Address/UB checks remain on.
env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0')
run('host-asan-ubsan',[OUT/'test-core-san'],env)
run('instruction-checks',[sys.executable,HERE/'tests/instruction_checks.py'])
print('Offline checks passed; NOT a board emulation or deployable firmware.')
