#!/usr/bin/env python3
"""Offline hook link and exact-byte patch. Never emits a valid IAP image."""
from pathlib import Path
import hashlib,json,os,struct,subprocess,sys
HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
BIN=Path(os.environ.get('ACE_ARM_BIN',ROOT/'toolchain/usr/bin'))
OUT=HERE/'build'
STOCK=ROOT/'input/ACE2_V1.1.31_20260306.bin'
SHA='a468e366226e7c483d3dd96b444223e89cbf0d1b10573d42b6263843ffd6be3e'
BASE=0x08008000
TEXT=0x08030000
def tool(name,*args,check=True):
 return subprocess.run([str(BIN/('arm-none-eabi-'+name)),*map(str,args)],text=True,capture_output=True,check=check)
def bw(src,dst):
 off=(dst-src-4)>>1
 s=(off>>23)&1;i1=(off>>22)&1;i2=(off>>21)&1
 return struct.pack('<HH',0xf000|(s<<10)|((off>>11)&1023),0x9000|((1^i1^s)<<13)|((1^i2^s)<<11)|(off&2047))
SYMS={'stock_insert_resume':0x0800F72A,'stock_insert_return':0x0800F6C4,
 'stock_start_resume':0x0800ECEC,'stock_start_return':0x0800EED0,
 'stock_after_wait_resume':0x0800ED3C,'stock_gpio_resume':0x0800ED7A,
 'stock_timer_resume':0x0800EE9C,'stock_control_resume':0x0800F018,
 'stock_power_disable':0x0800F088,'stock_encoder_disable':0x08009EB8,
 'stock_active_resume':0x0800ED4C,'stock_encoder_resume':0x08009D6A,
 'stock_encoder_return':0x08009D72,'stock_disable_timer_resume':0x0800EFAA}
SITES=[(0x0800F726,'47f6a460','ph_insert_hook'),
 (0x0800ECE8,'2de9f047','ph_start_hook'),
 (0x0800ED38,'c4e90700','ph_after_wait_hook'),
 (0x0800ED46,'84f83400','ph_active_hook'),
 (0x0800ED76,'11612179','ph_enable_gpio_hook'),
 (0x0800EE98,'08433060','ph_enable_timer_hook'),
 (0x0800EECA,'cff6ff72','ph_start_pwm_hook'),
 (0x0800F012,'cff6ff72','ph_control_pwm_hook'),
 (0x08009D62,'d0600120','ph_encoder_enable_hook'),
 (0x0800EFA4,'22ea0000','ph_disable_timer_hook')]
def main():
 OUT.mkdir(exist_ok=True)
 stock=STOCK.read_bytes()
 assert hashlib.sha256(stock).hexdigest()==SHA
 flags=['-mthumb','-mcpu=cortex-m3','-Os','-ffreestanding','-fno-builtin','-nostdlib',
        '-DPRELOAD_INTEGRATION_EXPERIMENT=1','-Wall','-Wextra','-Werror']
 for ext in ('c','S'):
  tool('gcc',*flags,'-c',HERE/f'src/preload_hooks.{ext}','-o',OUT/f'hooks-{ext}.o')
 ld='SECTIONS { . = 0x%08x; .text : { *(.text*) *(.rodata*) } .binding_required : { *(.binding_required) } }\n'%TEXT
 ld+=''.join(f'{s} = 0x{a:08x};\n' for s,a in SYMS.items())
 (OUT/'production.ld').write_text(ld)
 fail=tool('ld','-T',OUT/'production.ld',OUT/'hooks-c.o',OUT/'hooks-S.o','-o',OUT/'production.elf',check=False)
 (OUT/'production-link-denied.log').write_text(fail.stdout+fail.stderr)
 assert fail.returncode!=0 and 'ph_verified_reserved_ram' in fail.stderr
 # TEST ONLY reservation deliberately OUTSIDE the MCU SRAM range. These are
 # simulation sentinels, not discovered/proven production addresses or proofs.
 test=ld+'ph_storage = 0x21000000;\n'
 for name in ('reserved_ram','exclusive_startup','owned_failure_source','all_motion_writers','controller_quiescence'):
  test+=f'ph_verified_{name} = 0x0badf00d;\n'
 (OUT/'simulation-only.ld').write_text(test)
 tool('ld','-T',OUT/'simulation-only.ld',OUT/'hooks-c.o',OUT/'hooks-S.o','-o',OUT/'simulation-only.elf')
 tool('objcopy','-O','binary',OUT/'simulation-only.elf',OUT/'hook-payload.bin')
 (OUT/'hooks.dis').write_text(tool('objdump','-d',OUT/'simulation-only.elf').stdout)
 symbols={}
 for l in tool('nm',OUT/'simulation-only.elf').stdout.splitlines():
  a,t,n=l.split();symbols[n]=int(a,16)
 (OUT/'symbols.json').write_text(json.dumps(symbols,indent=2))
 body=bytearray(stock[:-8]);body.extend(bytes(TEXT-BASE-len(body)))
 body.extend((OUT/'hook-payload.bin').read_bytes())
 patches=[]
 for address,expect,name in SITES:
  o=address-BASE
  actual=bytes(body[o:o+4]).hex()
  assert actual==expect,(hex(address),actual,expect)
  repl=bw(address,symbols[name]);body[o:o+4]=repl
  patches.append(dict(address=hex(address),expect=expect,replace=repl.hex(),symbol=name))
 # No IAP magic; impossible simulation RAM; explicit nondeployable filename.
 (OUT/'SIMULATION-ONLY-NO-IAP.bin').write_bytes(body)
 # Independently decode installed B.W instructions, rather than trusting the
 # encoder that emitted them. All targets must be exact linked Thumb entries.
 for address,expect,name in SITES:
  dis=tool('objdump','-b','binary','-marm','-Mforce-thumb','-D',
    '--adjust-vma=0x08008000',f'--start-address={address}',
    f'--stop-address={address+4}',OUT/'SIMULATION-ONLY-NO-IAP.bin').stdout
  assert 'b.w' in dis and hex(symbols[name]) in dis,dis
 manifest={'stock_sha256':SHA,'artifact_sha256':hashlib.sha256(body).hexdigest(),
 'deployable':False,'iap_trailer_present':False,'simulation_ram':'0x21000000',
 'normal_insert_entry_unchanged':body[0xF6AE-0x8000:0xF6AE-0x8000+4].hex()=='84f00100',
 'patches':patches}
 (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 print('ARM hooks compile/link: PASS (simulation-only)')
 print('Production link rejection: PASS (all five verification bindings and storage unbound)')
 print('Exact stock hook bytes: PASS (%d sites)'%len(SITES))
 print('IAP trailer: deliberately absent. NO DEVICE CONNECTION; DO NOT FLASH.')
if __name__=='__main__':main()
