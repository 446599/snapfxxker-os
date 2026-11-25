#!/usr/bin/env python3
"""Freeze the confirmed RF revision and prepare an isolated layout experiment."""
from pathlib import Path
import hashlib,json,shutil
HERE=Path(__file__).resolve().parent
RF=HERE.parent/'integration-rf-spi'
ROOT=HERE/'accessor-experiment'
EXPECTED='1ffba30b853ece41509c79b885180447d52c8d591be9185e2849ef341a202e6b'
def sha(b):return hashlib.sha256(b).hexdigest()
def main():
 m=json.loads((RF/'output/analysis-manifest.json').read_text())
 assert m['image_sha256']==EXPECTED
 assert sha((RF/'output/EXPERIMENTAL-DO-NOT-FLASH.offline-image').read_bytes())==EXPECTED
 files={}
 for dirname in ('firmware','tests'):
  for p in sorted((RF/dirname).iterdir()):
   if p.is_file() and p.suffix in ('.c','.h','.s','.S','.py'):files[f'{dirname}/{p.name}']=(p,p.read_bytes())
 for name,path in [('build_offline.py',RF/'build_offline.py'),
                   ('analysis-manifest.json',RF/'output/analysis-manifest.json'),
                   ('original.elf',RF/'build/EXPERIMENTAL-DO-NOT-FLASH.elf')]:files[name]=(path,path.read_bytes())
 for name in ('preload_hooks.c','preload_hooks.h','preload_hooks.S'):
  p=HERE/'src'/name;files['preload/'+name]=(p,p.read_bytes())
 for p,b in files.values():assert p.read_bytes()==b,'Input changed while freezing'
 frozen=ROOT/'frozen-inputs';frozen.mkdir(parents=True,exist_ok=True)
 for name,(_,b) in files.items():
  p=frozen/name;p.parent.mkdir(parents=True,exist_ok=True)
  if p.exists():assert p.read_bytes()==b,'Frozen revision differs; do not overwrite'
  else:p.write_bytes(b)
 metadata={k:dict(source=str(p),sha256=sha(b),bytes=len(b)) for k,(p,b) in files.items()}
 encoded=json.dumps(metadata,sort_keys=True).encode()
 summary=dict(rf_image_sha256=EXPECTED,rf_elf_sha256=m['elf_sha256'],input_set_sha256=sha(encoded),files=metadata)
 (ROOT/'frozen-inputs.json').write_text(json.dumps(summary,indent=2)+'\n')
 fw=ROOT/'firmware';tests=ROOT/'tests';fw.mkdir(exist_ok=True);tests.mkdir(exist_ok=True)
 for dirname,dest in [('firmware',fw),('tests',tests),('preload',fw)]:
  for p in (frozen/dirname).iterdir():
   q=dest/p.name
   if not q.exists():shutil.copyfile(p,q)
 print('Frozen source/input set SHA256:',summary['input_set_sha256'])
 print(ROOT)
if __name__=='__main__':main()
