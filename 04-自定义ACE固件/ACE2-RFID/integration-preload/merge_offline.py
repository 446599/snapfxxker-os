#!/usr/bin/env python3
"""Relink stable RF + preload object snapshots into ONE explicitly NOFLASH ELF.

Reads integration-rf-spi but never builds, edits or deletes anything there.
The RF manifest, all prefix edits, reset barrier and invalid trailer are verified.
Any changed input during snapshot, object/payload mismatch, overlapping patches,
unexpected section layout, missing symbol or production-proof binding fails.
No device, updater, network, production flag, force flag or .bin output exists.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import build as ph
OUT=HERE/'merged'
MAGIC=bytes.fromhex('61a5635a65a5325a')
INVALID=b'NOFLASH!'
TEST_RAM=0x21000000
PROOFS=('reserved_ram','exclusive_startup','owned_failure_source',
        'all_motion_writers','controller_quiescence')

def sha(data):return hashlib.sha256(data).hexdigest()
def run(args,cwd=None,success=True):
 p=subprocess.run(list(map(str,args)),cwd=cwd,text=True,capture_output=True)
 if success and p.returncode:raise ValueError('Command failed: '+str(args)+'\n'+p.stdout+p.stderr)
 return p
def tool(name,*args,cwd=None,success=True):
 return run([ph.BIN/('arm-none-eabi-'+name),*args],cwd,success)
def symbols(elf):
 result={}
 for line in tool('nm',elf).stdout.splitlines():
  p=line.split()
  if len(p)==3:result[p[2]]=int(p[0],16)
 return result
def decode_branch(address,data):
 a,b=struct.unpack('<HH',data[:4])
 if a&0xf800!=0xf000 or b&0xd000 not in (0x9000,0xd000):
  raise ValueError(f'Not B.W/BL at {address:#x}')
 s=(a>>10)&1;j1=(b>>13)&1;j2=(b>>11)&1
 n=(s<<24)|(((~(j1^s))&1)<<23)|(((~(j2^s))&1)<<22)|((a&1023)<<12)|((b&2047)<<1)
 if s:n-=1<<25
 return address+4+n,('bl' if b&0xd000==0xd000 else 'b.w')
def patch_change(c,component):
 return dict(address=int(c['address'],16) if isinstance(c['address'],str) else c['address'],
  before=bytes.fromhex(c.get('expected_bytes',c.get('expect',''))),
  after=bytes.fromhex(c.get('replacement_bytes',c.get('replace',''))),
  reason=c.get('reason',c.get('symbol','')),component=component)
def check_changes(stock,changes):
 """No duplicate or partial overlap is permitted, even if bytes agree."""
 occupied={}
 for c in changes:
  address=c['address'];before=c['before'];after=c['after'];off=address-ph.BASE
  if not before or len(before)!=len(after) or off<0 or off+len(before)>len(stock)-8:
   raise ValueError('Patch outside stock prefix or length mismatch: '+str(c))
  if stock[off:off+len(before)]!=before:raise ValueError(f'Original bytes mismatch at {address:#x}')
  for a in range(address,address+len(before)):
   if a in occupied:
    raise ValueError(f'Patch overlap at {a:#x}: {occupied[a]} vs {c["component"]}/{c["reason"]}')
   occupied[a]=c['component']+'/'+c['reason']
 return occupied
def apply_changes(stock,changes):
 check_changes(stock,changes)
 result=bytearray(stock[:-8])
 for c in changes:
  off=c['address']-ph.BASE;result[off:off+len(c['after'])]=c['after']
 return result
def snapshot(rf):
 manifest_path=rf/'output/analysis-manifest.json'
 first=manifest_path.read_bytes();m=json.loads(first)
 map_path=rf/'build/integration.map'
 map_bytes=map_path.read_bytes()
 loads=re.findall(r'^LOAD (.+\.o)$',map_bytes.decode(),re.M)
 if not loads:raise ValueError('RF linker map has no input objects')
 files={'rf-manifest.json':(manifest_path,first),'rf-map.txt':(map_path,map_bytes),
  'rf-original.ld':(rf/'build/integration.ld',(rf/'build/integration.ld').read_bytes()),
  'rf-original.elf':(rf/'build/EXPERIMENTAL-DO-NOT-FLASH.elf',(rf/'build/EXPERIMENTAL-DO-NOT-FLASH.elf').read_bytes()),
  'rf-original.offline-image':(rf/'output/EXPERIMENTAL-DO-NOT-FLASH.offline-image',
      (rf/'output/EXPERIMENTAL-DO-NOT-FLASH.offline-image').read_bytes())}
 names=[]
 for i,rawpath in enumerate(loads):
  path=Path(rawpath)
  if not path.is_absolute():path=rf/'build'/path
  if path.resolve().parent!=(rf/'build').resolve():raise ValueError('RF object not in RF build directory')
  name=f'rf-{i:02d}-{path.name}';names.append(name);files[name]=(path,path.read_bytes())
 # Fail rather than consume a half-written build. Reproduction below also
 # proves the captured objects reproduce the checked ELF text/BSS symbols.
 for name,(path,data) in files.items():
  if path.read_bytes()!=data:raise ValueError('RF build changed during snapshot: '+str(path))
 if sha(files['rf-original.elf'][1])!=m['elf_sha256']:raise ValueError('RF ELF/manifest mismatch; build not stable')
 if sha(files['rf-original.offline-image'][1])!=m['image_sha256']:raise ValueError('RF image/manifest mismatch; build not stable')
 snapid=sha(b''.join(name.encode()+b'\0'+bytes.fromhex(sha(data)) for name,(_,data) in sorted(files.items())))
 dest=OUT/'snapshots'/snapid
 dest.mkdir(parents=True,exist_ok=True)
 for name,(_,data) in files.items():(dest/name).write_bytes(data)
 summary={name:dict(source=str(path),sha256=sha(data),bytes=len(data)) for name,(path,data) in files.items()}
 (dest/'snapshot.json').write_text(json.dumps(summary,indent=2)+'\n')
 return m,dest,names,snapid
def main():
 ap=argparse.ArgumentParser(description=__doc__)
 ap.add_argument('--offline',action='store_true')
 ap.add_argument('--rf-root',type=Path,default=HERE.parent/'integration-rf-spi')
 a=ap.parse_args()
 if not a.offline:ap.error('REFUSED: only a reset-blocked NOFLASH offline image can be built')
 stock=ph.STOCK.read_bytes()
 if sha(stock)!=ph.SHA or stock[-8:]!=MAGIC:raise ValueError('Stock identity mismatch')
 OUT.mkdir(exist_ok=True)
 m,snap,names,snapid=snapshot(a.rf_root.resolve())
 if m['stock_sha256']!=ph.SHA:raise ValueError('Different stock image in RF manifest')
 if m.get('valid_iap_trailer') or m.get('production_output_supported'):
  raise ValueError('RF input is not a gated offline fixture')
 rf_image=(snap/'rf-original.offline-image').read_bytes()
 if rf_image[-8:]!=INVALID:raise ValueError('RF NOFLASH trailer missing')
 rf_syms=symbols(snap/'rf-original.elf')
 rf_changes=[patch_change(c,'RF') for c in m['prefix_changes']]
 rf_prefix=apply_changes(stock,rf_changes)
 if bytes(rf_prefix)!=rf_image[:len(stock)-8]:raise ValueError('Unlisted RF prefix change')
 origin=int(m['payload_address'],16)
 if origin!=ph.BASE+len(stock)-8:raise ValueError('Unexpected RF text origin')
 for h in m['hooks']:
  address=int(h['address'],16);raw=rf_image[address-ph.BASE:address-ph.BASE+4]
  target,_=decode_branch(address,raw)
  if target!=rf_syms[h['symbol']] or target!=int(h['target'],16):raise ValueError('RF hook target mismatch')
  if not any(c['address']==address and c['after']==bytes.fromhex(h['replacement_bytes']) for c in rf_changes):
   raise ValueError('RF hook missing from complete prefix manifest')
 # Reproduce object snapshot before merge, catching objects from another build.
 solo=tool('ld','-T','rf-original.ld',*names,'-o','rf-reproduced.elf',cwd=snap)
 (snap/'rf-reproduce.log').write_text(solo.stdout+solo.stderr)
 tool('objcopy','-O','binary','-j','.text','rf-reproduced.elf','rf-reproduced.text',cwd=snap)
 rftext=(snap/'rf-reproduced.text').read_bytes()
 if rftext!=rf_image[len(stock)-8:-8]:raise ValueError('RF objects do not reproduce manifest image payload')
 if symbols(snap/'rf-reproduced.elf')!=rf_syms:raise ValueError('RF object snapshot symbol mismatch')
 gate=rf_syms['experiment_reset_gate'];off=gate-ph.BASE
 if rf_image[off:off+2]!=b'\xfe\xe7':raise ValueError('RF immutable reset loop missing')
 if struct.unpack_from('<I',rf_image,4)[0]!=(gate|1):raise ValueError('RF reset vector gate missing')
 if decode_branch(0x08008244,rf_image[0x244:0x248])[0]!=gate:raise ValueError('Original startup gate missing')
 # Build local preload objects only. Never invoke another directory's builder.
 local=run([sys.executable,HERE/'build.py'])
 (OUT/'preload-build.log').write_text(local.stdout+local.stderr)
 for src,dst in [('hooks-c.o','ph-c.o'),('hooks-S.o','ph-S.o')]:
  (snap/dst).write_bytes((HERE/'build'/src).read_bytes())
 ld=(snap/'rf-original.ld').read_text()
 text_pattern=r'\.text\s*:\s*\{\s*\*\(\.text\*\)\s*\*\(\.rodata\*\)\s*\}'
 explicit='.text : { '+' '.join(n+'(.text*)' for n in names)+' '+' '.join(n+'(.rodata*)' for n in names)+' }'
 ld,count=re.subn(text_pattern,lambda _:explicit,ld)
 if count!=1:raise ValueError('Unknown RF text layout; review required')
 # Keep RF .text/BSS exactly where the upstream fixture linked them, and
 # preload .text exactly where its finite-instruction tests linked it.
 ld+='\nSECTIONS {\n . = 0x%08x;\n'%ph.TEXT
 ld+=' .preload_text : { ph-c.o(.text*) ph-S.o(.text*) ph-c.o(.rodata*) ph-S.o(.rodata*) }\n'
 ld+=' .binding_required : { KEEP(ph-S.o(.binding_required)) }\n'
 ld+=' ASSERT(ADDR(.text)+SIZEOF(.text)<=ADDR(.preload_text),"RF/preload text overlap")\n'
 ld+=' ASSERT(ADDR(.bss)+SIZEOF(.bss)<=0x21000000,"RF/preload RAM overlap")\n}\n'
 # Exact absolute stock aliases from preload; conflicting names are refused.
 for key,val in ph.SYMS.items():
  if key in rf_syms and rf_syms[key]!=val:raise ValueError('Stock symbol conflict: '+key)
  ld+=f'{key} = 0x{val:x};\n'
 (snap/'combined-production-rejected.ld').write_text(ld)
 objects=[*names,'ph-c.o','ph-S.o']
 prod=tool('ld','-T','combined-production-rejected.ld',*objects,'-o','production-forbidden.elf',cwd=snap,success=False)
 (OUT/'production-link-rejected.log').write_text(prod.stdout+prod.stderr)
 required=('ph_storage',)+tuple('ph_verified_'+p for p in PROOFS)
 if prod.returncode==0 or any("undefined reference to `"+p+"'" not in prod.stderr for p in required):
  raise ValueError('Production link did not reject every unbound preload interface')
 # Simulation sentinels explicitly supply no real allocator/startup/auth proof.
 testld=ld+f'ph_storage = 0x{TEST_RAM:x};\n'
 testld+=''.join(f'ph_verified_{p} = 0x0badf00d;\n' for p in PROOFS)
 (snap/'combined-NOFLASH.ld').write_text(testld)
 merged=OUT/'COMBINED-NOFLASH.elf'
 linked=tool('ld','-T','combined-NOFLASH.ld',*objects,'-Map='+str(OUT/'combined.map'),'-o',merged,cwd=snap)
 (OUT/'combined-link.log').write_text(linked.stdout+linked.stderr)
 if tool('nm','-u',merged).stdout.strip():raise ValueError('Unresolved symbol in simulation ELF')
 syms=symbols(merged)
 for name,value in rf_syms.items():
  if syms.get(name)!=value:raise ValueError('Relink moved an RF symbol: '+name)
 pre_syms=json.loads((HERE/'build/symbols.json').read_text())
 for name,value in pre_syms.items():
  if syms.get(name)!=value:raise ValueError('Relink moved a preload symbol: '+name)
 tool('objcopy','-O','binary','-j','.text',merged,OUT/'combined-rf.text')
 tool('objcopy','-O','binary','-j','.preload_text','-j','.binding_required',merged,OUT/'combined-preload.text')
 if (OUT/'combined-rf.text').read_bytes()!=rftext:raise ValueError('RF text changed on combined link')
 pretext=(OUT/'combined-preload.text').read_bytes()
 if pretext!=(HERE/'build/hook-payload.bin').read_bytes():raise ValueError('Preload text changed on combined link')
 patches=[]
 for address,expected,symbol in ph.SITES:
  patches.append(dict(address=address,before=bytes.fromhex(expected),after=ph.bw(address,syms[symbol]),
                      reason=symbol,component='preload'))
 changes=rf_changes+patches
 prefix=apply_changes(stock,changes) # detects new startup/fragment conflicts
 body=prefix+rftext
 if ph.BASE+len(body)>ph.TEXT:raise ValueError('Payload overlap')
 body.extend(bytes(ph.TEXT-ph.BASE-len(body)));body.extend(pretext);body.extend(INVALID)
 image=OUT/'COMBINED-NOFLASH.offline-image';image.write_bytes(body)
 hooks=[dict(h,component='RF') for h in m['hooks']]
 hooks += [dict(address=hex(c['address']),target=hex(syms[c['reason']]),symbol=c['reason'],component='preload',
                expected_bytes=c['before'].hex(),replacement_bytes=c['after'].hex()) for c in patches]
 for h in hooks:
  address=int(h['address'],16);expected=int(h['target'],16);size=len(bytes.fromhex(h['replacement_bytes']))
  raw=body[address-ph.BASE:address-ph.BASE+size]
  if raw.hex()!=h['replacement_bytes'] or decode_branch(address,raw)[0]!=expected:raise ValueError('Installed branch mismatch')
  dis=tool('objdump','-D','-b','binary','-marm','-Mforce-thumb','--adjust-vma='+hex(ph.BASE),
    '--start-address='+hex(address),'--stop-address='+hex(address+size),image).stdout
  if hex(expected) not in dis:raise ValueError('Independent objdump target mismatch')
 # No blanket INSERT gate; the narrow gate occurs later at F726.
 for address,n in [(0x0800f6ae,4),(0x0800f6f8,2),(0x0800f724,2)]:
  o=address-ph.BASE
  if body[o:o+n]!=stock[o:o+n]:raise ValueError('Normal INSERT guard/entry changed')
 if body[-8:]!=INVALID or body[-8:]==MAGIC:raise ValueError('Unsafe trailer')
 if body[off:off+2]!=b'\xfe\xe7' or struct.unpack_from('<I',body,4)[0]!=gate|1:raise ValueError('Reset barrier lost')
 tool('objdump','-d',merged)
 (OUT/'combined.dis').write_text(tool('objdump','-d',merged).stdout)
 (OUT/'symbols.json').write_text(json.dumps(syms,indent=2)+'\n')
 result=dict(classification='COMBINED NOFLASH offline fixture; normal reset intentionally blocked',
  stock_sha256=ph.SHA,rf_snapshot_id=snapid,rf_snapshot_path=str(snap),
  image_sha256=sha(body),elf_sha256=sha(merged.read_bytes()),image_bytes=len(body),
  rf_hook_count=len(m['hooks']),preload_hook_count=len(patches),total_hooks=len(hooks),hooks=hooks,
  prefix_changes=[dict(address=hex(c['address']),expected_bytes=c['before'].hex(),replacement_bytes=c['after'].hex(),
                       reason=c['reason'],component=c['component']) for c in changes],
  rf_payload=dict(address=hex(origin),bytes=len(rftext),sha256=sha(rftext)),
  preload_payload=dict(address=hex(ph.TEXT),bytes=len(pretext),sha256=sha(pretext)),
  startup_hooks=[h for h in hooks if int(h['address'],16) in (0x08008244,0x080116a8)],
  preload_storage=dict(address=hex(TEST_RAM),simulation_only=True,reservation_verified=False),
  normal_insert_entry_and_guards_preserved=True,normal_reset_blocked=True,exception_paths_quarantined=False,
  valid_iap_trailer=False,production_output_supported=False,production_link_rejected=True,
  unbound_production_interfaces=list(required),auth_failure_producer_connected=False,
  rf_stop_request_connected_to_preload=False,physical_stop_proven=False,device_tests_run=False,
  rf_input_directory_modified=False)
 (OUT/'combined-manifest.json').write_text(json.dumps(result,indent=2)+'\n')
 print(f'Combined ARM ELF linked: {len(m["hooks"])} RF + {len(patches)} preload = {len(hooks)} hooks')
 print('All prefix bytes, nonoverlap, branch targets, text identity and reset/trailer barriers: PASS')
 print('Production link: deliberately rejected. AUTH->preload and actual hardware STOP proof remain UNBOUND.')
 print(image)

if __name__=='__main__':
 try:main()
 except (ValueError,KeyError,AssertionError) as e:
  print('REFUSED: '+str(e),file=sys.stderr);sys.exit(1)
