#!/usr/bin/env python3
"""Single-image accessor experiment. Reset blocked, NOFLASH, no device IO."""
import argparse,ast,hashlib,importlib.util,json,struct,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
PARENT=ROOT.parent
sys.path.insert(0,str(PARENT))
import build as ph
from merge_offline import check_changes,decode_branch
FW=ROOT/'firmware';BUILD=ROOT/'build';OUT=ROOT/'output'
APP_END=0x08024000
PROOFS=('reserved_ram','exclusive_startup','owned_failure_source','all_motion_writers','controller_quiescence')
def sha(b):return hashlib.sha256(b).hexdigest()
def tool(name,*args,check=True):
 p=subprocess.run([str(ph.BIN/('arm-none-eabi-'+name)),*map(str,args)],text=True,capture_output=True)
 if check and p.returncode:raise ValueError(p.stdout+p.stderr)
 return p
def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--offline',action='store_true');a=ap.parse_args()
 if not a.offline:ap.error('REFUSED: no production/flash image supported')
 frozen=json.loads((ROOT/'frozen-inputs.json').read_text())
 for name,meta in frozen['files'].items():
  assert sha((ROOT/'frozen-inputs'/name).read_bytes())==meta['sha256'],'Frozen input altered'
 spec=importlib.util.spec_from_file_location('frozen_rf_build',ROOT/'frozen-inputs/build_offline.py')
 rf=importlib.util.module_from_spec(spec);spec.loader.exec_module(rf)
 tree=ast.parse((ROOT/'frozen-inputs/build_offline.py').read_text())
 assignments=[n for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='sources' for t in n.targets)]
 assert len(assignments)==1
 sources=ast.literal_eval(assignments[0].value)+['preload_hooks.c','preload_hooks.S']
 stock=ph.STOCK.read_bytes();assert sha(stock)==ph.SHA
 BUILD.mkdir(exist_ok=True);OUT.mkdir(exist_ok=True)
 flags=['-mcpu=cortex-m3','-mthumb','-Os','-ffreestanding','-fno-builtin',
 '-fno-tree-loop-distribute-patterns','-fno-common','-fno-unwind-tables',
 '-fno-asynchronous-unwind-tables','-fstack-usage','-DACE_EXPERIMENTAL_OFFLINE=1',
 '-DBR_OFFLINE_TEST=1','-DRF_ADAPTER_OFFLINE_TEST=1','-DPRELOAD_INTEGRATION_EXPERIMENT=1','-I'+str(FW)]
 before={p.name:sha(p.read_bytes()) for p in FW.iterdir() if p.is_file()}
 objects=[]
 for i,source in enumerate(sources):
  obj=BUILD/(f'{i:02d}-'+source.replace('.','-')+'.o');objects.append(obj)
  warnings=[] if source=='native_tag_decoder.c' else ['-Wall','-Wextra','-Werror']
  r=tool('gcc',*flags,*warnings,'-c',FW/source,'-o',obj)
  (BUILD/(source+'.log')).write_text(r.stdout+r.stderr)
 assert before=={p.name:sha(p.read_bytes()) for p in FW.iterdir() if p.is_file()},'Source changed during build'
 aliases=dict(rf.STOCK_SYMBOLS)
 for k,v in ph.SYMS.items():assert k not in aliases or aliases[k]==v;aliases[k]=v
 origin=ph.BASE+len(stock)-8
 ld='ENTRY(experiment_reset_gate)\nSECTIONS {\n . = 0x%x;\n'%origin
 ld+=' .text : { *(.text*) *(.rodata*) KEEP(*(.binding_required)) }\n'
 ld+=' .bss (NOLOAD) : { *(.bss*) *(COMMON) }\n .data : { *(.data*) }\n'
 ld+=' ASSERT(SIZEOF(.bss)==0,"No unreserved global state")\n ASSERT(SIZEOF(.data)==0,"No initialized RAM")\n'
 ld+=' ASSERT(ADDR(.text)+SIZEOF(.text)+8<0x08024000,"Outside stock application region")\n'
 ld+=''.join(f' {k} = 0x{v:x};\n' for k,v in aliases.items())+'}\n'
 (BUILD/'production-unbound.ld').write_text(ld)
 denied=tool('ld','-T',BUILD/'production-unbound.ld',*objects,'-o',BUILD/'FORBIDDEN.elf',check=False)
 (BUILD/'production-link-rejected.log').write_text(denied.stdout+denied.stderr)
 assert denied.returncode and all("undefined reference to `ph_verified_"+p+"'" in denied.stderr for p in PROOFS)
 assert "undefined reference to `ph_storage'" not in denied.stderr,'Dynamic storage migration incomplete'
 test=ld+''.join(f'ph_verified_{p} = 0x0badf00d;\n' for p in PROOFS)
 (BUILD/'accessor-NOFLASH.ld').write_text(test)
 elf=BUILD/'ACCESSOR-NOFLASH.elf'
 result=tool('ld','-T',BUILD/'accessor-NOFLASH.ld',*objects,'-Map='+str(BUILD/'accessor.map'),'-o',elf)
 (BUILD/'link.log').write_text(result.stdout+result.stderr)
 assert not tool('nm','-u',elf).stdout.strip()
 syms={}
 for l in tool('nm',elf).stdout.splitlines():
  p=l.split()
  if len(p)==3:syms[p[2]]=int(p[0],16)
 assert 'ph_storage' not in syms and 'ph_platform_state' in syms and 'ph_init_exclusive' not in syms
 payload=BUILD/'combined.text';tool('objcopy','-O','binary','-j','.text',elf,payload)
 code=payload.read_bytes();assert origin+len(code)+8<APP_END
 hooks=[];changes=[]
 for address,expected,symbol,reason in rf.HOOKS+[(a,e,s,s) for a,e,s in ph.SITES]:
  after=ph.bw(address,syms[symbol]);kind='B.W'
  if address==0x080116a8:
   x,y=struct.unpack('<HH',after);after=struct.pack('<HH',x,y|0x4000);kind='BL'
  after+=b'\x00\xbf'*((len(bytes.fromhex(expected))-4)//2)
  c=dict(address=address,before=bytes.fromhex(expected),after=after,reason=reason,
         component='preload' if symbol.startswith('ph_') else 'RF')
  changes.append(c);hooks.append(dict(address=hex(address),expected_bytes=expected,replacement_bytes=after.hex(),
      symbol=symbol,target=hex(syms[symbol]),branch_kind=kind,component=c['component']))
 vi=stock.index(b'V1.1.31\0')
 changes.extend([dict(address=ph.BASE+4,before=stock[4:8],after=struct.pack('<I',syms['experiment_reset_gate']|1),reason='reset vector',component='barrier'),
  dict(address=ph.BASE+vi,before=stock[vi:vi+8],after=b'EXPH2!!\0',reason='NOFLASH label',component='barrier')])
 check_changes(stock,changes)
 body=bytearray(stock[:-8])+code
 for c in changes:
  o=c['address']-ph.BASE;body[o:o+len(c['before'])]=c['after']
 body.extend(b'NOFLASH!')
 image=OUT/'ACCESSOR-NOFLASH.offline-image';image.write_bytes(body)
 for h in hooks:
  address=int(h['address'],16);raw=body[address-ph.BASE:address-ph.BASE+4]
  target,kind=decode_branch(address,raw);assert target==int(h['target'],16) and kind.upper()==h['branch_kind']
  dis=tool('objdump','-D','-b','binary','-marm','-Mforce-thumb','--adjust-vma='+hex(ph.BASE),
   '--start-address='+hex(address),'--stop-address='+hex(address+len(bytes.fromhex(h['replacement_bytes']))),image).stdout
  assert hex(target) in dis
 gate=syms['experiment_reset_gate'];assert body[gate-ph.BASE:gate-ph.BASE+2]==b'\xfe\xe7'
 assert struct.unpack_from('<I',body,4)[0]==gate|1 and body[-8:]==b'NOFLASH!'
 for a,n in [(0x0800f6ae,4),(0x0800f6f8,2),(0x0800f724,2)]:assert body[a-ph.BASE:a-ph.BASE+n]==stock[a-ph.BASE:a-ph.BASE+n]
 (BUILD/'symbols.json').write_text(json.dumps(syms,indent=2)+'\n')
 (BUILD/'combined.dis').write_text(tool('objdump','-d',elf).stdout)
 manifest=dict(classification='ACCESSOR EXPERIMENT NOFLASH; normal reset absolutely blocked',
  frozen_input_set=frozen['input_set_sha256'],rf_baseline_image=frozen['rf_image_sha256'],
  sources_sha256=before,stock_sha256=ph.SHA,image_sha256=sha(body),elf_sha256=sha(elf.read_bytes()),
  hooks=hooks,total_hooks=len(hooks),payload_origin=hex(origin),payload_bytes=len(code),image_end_exclusive=hex(ph.BASE+len(body)),
  application_end_exclusive=hex(APP_END),flash_capacity_proven=False,within_documented_application_range=True,
  ph_storage_static_address=None,ph_accessor='ph_platform_state -> ex_storage_current',
  arm_tail_bytes=184,arm_payload_bytes=216,arm_minimum_allocator_block=224,arm_ph_offset=144,
  normal_insert_preserved=True,reset_blocked=True,valid_iap_trailer=False,
  unresolved_production_proofs=['ph_verified_'+p for p in PROOFS],
  rf_ready=False,auth_failure_source_connected=False,rf_stop_request_connected_to_preload=False,
  missing_state_known_lane_stop_effects=True,missing_state_publisher_ownership_unresolved=True,
  physical_stop_proven=False,boot_aba_lifetime_proven=False,heap_margin_proven=False,device_tests_run=False)
 (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 print(f'ARM accessor link PASS: {len(hooks)} hooks; ph_storage absolute symbol absent; BSS=0')
 print(f'ARM tail=184 payload=216 block>=224; image end {ph.BASE+len(body):#010x} < {APP_END:#010x}')
 print('Production proof link rejected; NOFLASH/reset barriers intact; AUTH source unbound.')
if __name__=='__main__':main()
