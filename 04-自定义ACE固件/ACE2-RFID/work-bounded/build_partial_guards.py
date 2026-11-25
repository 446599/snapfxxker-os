#!/usr/bin/env python3
"""Build diagnostic-only guard image; never claims complete SPI integration.
No force option, exact base image mandatory. Isolated copied source only.
"""
import hashlib,importlib.util,json,pathlib,struct,subprocess,sys
HERE=pathlib.Path(__file__).resolve().parent
FW=HERE/'source/firmware'
spec=importlib.util.spec_from_file_location('build',FW/'build_patch.py')
build=importlib.util.module_from_spec(spec);spec.loader.exec_module(build)
BASE=HERE.parent/'input/ACE2_V1.1.31_20260306.bin'
OUT=HERE/'output';TMP=HERE/'arm';OUT.mkdir(exist_ok=True);TMP.mkdir(exist_ok=True)
original=BASE.read_bytes()
assert len(original)==build.BASE_SIZE and hashlib.md5(original).hexdigest()==build.BASE_MD5
# First produce the copied existing experimental build, without altering input source.
candidate=OUT/'existing-experimental.bin'
subprocess.run([sys.executable,str(FW/'build_patch.py'),'--base',str(BASE),'--out',str(candidate),'--tmp',str(TMP/'baseline')],check=True)
body=bytearray(candidate.read_bytes()[:-8])
while len(body)%4:body.append(0)
addr=build.BASE_ADDR+len(body)
ld=TMP/'guards.ld';obj=TMP/'guards.o';elf=TMP/'guards.elf';raw=TMP/'guards.bin'
ld.write_text('ENTRY(guarded_transceive_entry)\nSECTIONS { . = 0x%08X; .text : { *(.text*) }\n stock_after_frame = 0x0800F332; stock_irq_read = 0x0800F3E8; stock_result_epilogue = 0x0800F4A2; }\n'%addr)
subprocess.run(['arm-none-eabi-as','-mthumb','-mcpu=cortex-m3',str(FW/'transceive_guards.s'),'-o',str(obj)],check=True)
subprocess.run(['arm-none-eabi-ld','-T',str(ld),str(obj),'-o',str(elf)],check=True)
subprocess.run(['arm-none-eabi-objcopy','-O','binary',str(elf),str(raw)],check=True)
symbols={}
for line in subprocess.check_output(['arm-none-eabi-nm',str(elf)],text=True).splitlines():
    pieces=line.split()
    if len(pieces)==3:symbols[pieces[2]]=int(pieces[0],16)
body+=raw.read_bytes()
patch=json.loads((FW/'patch.json').read_text())
for site,symbol,expected in [(0x0800F32C,'guarded_transceive_entry','2de9f04f'),(0x0800F3E4,'bounded_irq_poll','20460421')]:
    off=site-build.BASE_ADDR
    assert body[off:off+4]==bytes.fromhex(expected)
    replaced=build.thumb_bw(site,symbols[symbol]);body[off:off+4]=replaced
    patch['hooks'].append(dict(file_offset=off,addr=f'0x{site:08X}',expect_hex=expected,replace_hex=replaced.hex()))
result=bytes(body)+build.MAGIC
path=OUT/'PARTIAL-GUARDS-DO-NOT-FLASH.bin';path.write_bytes(result)
patch.update(name='ACE2-Open PARTIAL guards - DO NOT FLASH',appended_hex=body[build.BASE_SIZE-8:].hex(),result_size=len(result),result_crc16=build.crc16_kermit(result),note='Offline diagnostic partial patch. Original SPI still unbounded; no fault latch, owner integration or hardware safety verification. DO NOT FLASH.')
(OUT/'partial-guards.patch.json').write_text(json.dumps(patch,indent=2)+'\n')
print('PARTIAL IMAGE',path,'sha256',hashlib.sha256(result).hexdigest())
print('GUARD SYMBOLS',json.dumps(symbols,sort_keys=True))
