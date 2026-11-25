#!/usr/bin/env python3
"""Link actual ACE2-Open/checked transport hooks, for OFFLINE analysis only.

Default invocation refuses. --offline emits a deliberately non-bootable image
with an immutable reset loop and INVALID IAP trailer, never a .bin/patch.json or
updater allowlist. No network, device, deploy or force option exists here.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

ROOT=Path(__file__).resolve().parent
FW=ROOT/'firmware'
BUILD=ROOT/'build'
OUT=ROOT/'output'
TC=ROOT.parent/'toolchain/usr/bin'
BASE=0x08008000
STOCK_SHA='a468e366226e7c483d3dd96b444223e89cbf0d1b10573d42b6263843ffd6be3e'
STOCK_SIZE=71592
MAGIC=bytes.fromhex('61a5635a65a5325a')
INVALID_TRAILER=b'NOFLASH!'


def tool(name): return str(TC/('arm-none-eabi-'+name))
def run(args):
    return subprocess.check_output(list(map(str,args)),stderr=subprocess.STDOUT,text=True)
def branch(src,dst):
    assert not ((src|dst)&1)
    delta=dst-src-4
    assert -(1<<24)<=delta<(1<<24)
    x=delta>>1
    s=(x>>23)&1; i1=(x>>22)&1; i2=(x>>21)&1
    return struct.pack('<HH',0xf000|(s<<10)|((x>>11)&0x3ff),
        0x9000|((~i1&1)^s)<<13|((~i2&1)^s)<<11|(x&0x7ff))

# Whole instructions only. F574/F5D0/EA14 require six bytes, not four.
HOOKS=[
    (0x080116a8,'04f049fd','ex_event_factory_entry','extended event-group allocation factory BL'),
    (0x08008244,'09488047','experiment_reset_gate','original reset barrier'),
    (0x0800f574,'81b0d0e90123','br_stock_read','stock register read'),
    (0x0800f5d0,'81b0d0e901c3','br_stock_write','stock register write'),
    (0x0800f32c,'2de9f04f','br_stock_transceive_entry','six-argument transceive + LR'),
    (0x0800deb6,'f0b585b0','ex_reject_select','unsafe stock SELECT rejected'),
    (0x0800e108,'b0b584b0','ex_reject_halt','unimplemented physical HALT rejected'),
    (0x0800e244,'2de9f04f','rf_background_entry','background RF operation'),
    (0x0800e7a8,'2de9f047','rf_cmd68_entry','CMD68'),
    (0x0800e910,'2de9f043','rf_cmd13_entry','CMD13 cache response'),
    (0x0800e74c,'10b582b0','rf_cmd14_entry','CMD14 queue path blocked'),
    (0x0800e70c,'80b582b0','rf_requeue_entry','requeue path blocked'),
    (0x0800ea14,'82b041f2f056','ex_reject_worker','outer worker GPIO/cache writes blocked'),
    (0x0800e7da,'012092e0','ex_raw_reject_stub','legacy unticketed raw commands rejected'),
    (0x0800e836,'062064e0','uid_stub','UID success and fallback publication rejected'),
    (0x0800e842,'0df18c0e','rawtag_stub','live decoder publication rejected'),
    (0x0800fe3c,'05f1880e','rawtag_cache_stub','cache copy and parse publication rejected'),
    (0x0800fe36,'06468c28','rawtag_extract_stub','background decoder publication rejected'),
    (0x0800e228,'40f20471','pageread_gate_stub','page read publication rejected'),
]
STOCK_SYMBOLS={
    'epilogue':0x0800e904,'rc522_read_reg':0x0800f574,
    'rc522_write_reg':0x0800f5d0,'rc522_transceive':0x0800f32c,
    'rc522_timer':0x0800f50c,'rc522_flush':0x0800f30a,
    'rfid_select':0x0800deb6,'rfid_pageread':0x0800e18c,
    'resume':0x0800e846,'cache_resume':0x0800fe40,
    'pageread_resume':0x0800e22c,'pageread_fail':0x0800e23c,
    'extract_resume':0x0800fe3a,'scan_exit':0x0800fe98,'scan_fail':0x0800fe94,
    'ex_stock_malloc':0x080132c5,'ex_stock_event_factory':0x0801613f,
    'stock_select_resume':0x0800deba,'stock_background_resume':0x0800e248,
    'stock_cmd68_resume':0x0800e7ac,'stock_cmd13_resume':0x0800e914,
    'stock_cmd14_resume':0x0800e750,'stock_requeue_resume':0x0800e710,
}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--offline',action='store_true',help='build gated analysis fixture only')
    a=p.parse_args()
    if not a.offline: p.error('REFUSED: no production image or flash output is supported')
    stock=(ROOT.parent/'input/ACE2_V1.1.31_20260306.bin').read_bytes()
    assert len(stock)==STOCK_SIZE and hashlib.sha256(stock).hexdigest()==STOCK_SHA
    assert stock[-8:]==MAGIC
    BUILD.mkdir(exist_ok=True); OUT.mkdir(exist_ok=True)
    flags=['-mcpu=cortex-m3','-mthumb','-Os','-ffreestanding','-fno-builtin',
        '-fno-tree-loop-distribute-patterns','-fno-common','-fno-unwind-tables',
        '-fno-asynchronous-unwind-tables','-fstack-usage',
        '-DACE_EXPERIMENTAL_OFFLINE=1','-DBR_OFFLINE_TEST=1',
        '-DRF_ADAPTER_OFFLINE_TEST=1','-I'+str(FW)]
    sources=['experimental_gate.S','event_storage.c','experimental_binding.c','bounded_rc522.c',
        'stock_bounded_bridge.c','stock_bounded_thunks.S','rf_owner.c',
        'rf_stock_adapter.c','rf_stock_trampolines.S','native_tag_decoder.c',
        'uid_stub.s','rawtag_stub.s','rawtag_cache_stub.s','rawtag_extract_stub.s',
        'pageread_gate_stub.s']
    objects=[]
    for source in sources:
        obj=BUILD/(Path(source).stem+'.o'); objects.append(obj)
        warnings=[] if source=='native_tag_decoder.c' else ['-Wall','-Wextra','-Werror']
        log=run([tool('gcc'),*flags,*warnings,'-c',FW/source,'-o',obj])
        (BUILD/(source+'.log')).write_text(log)
    ld=BUILD/'integration.ld'
    origin=BASE+STOCK_SIZE-8
    ld.write_text('ENTRY(experiment_reset_gate)\nSECTIONS {\n'+
        f' . = 0x{origin:x};\n .text : {{ *(.text*) *(.rodata*) }}\n'+
        ' .bss (NOLOAD) : { *(.bss*) *(COMMON) }\n'+
        ' .data : { *(.data*) }\n ASSERT(SIZEOF(.data)==0,"No initialized RAM in offline fixture")\n'+
        ' ASSERT(SIZEOF(.bss)==0,"No unreserved state is permitted")\n'+
        '\n'.join(f' {k} = 0x{v:x};' for k,v in STOCK_SYMBOLS.items())+'\n}\n')
    elf=BUILD/'EXPERIMENTAL-DO-NOT-FLASH.elf'
    (BUILD/'link.log').write_text(run([tool('ld'),'-T',ld,*objects,'-Map='+str(BUILD/'integration.map'),'-o',elf]))
    unresolved=run([tool('nm'),'-u',elf]); assert not unresolved.strip(),unresolved
    (BUILD/'unresolved.txt').write_text(unresolved)
    syms={}
    for line in run([tool('nm'),elf]).splitlines():
        fields=line.split()
        if len(fields)==3: syms[fields[2]]=int(fields[0],16)
    (BUILD/'symbols.json').write_text(json.dumps(syms,indent=2)+'\n')
    payload=BUILD/'EXPERIMENTAL.offline-code'
    run([tool('objcopy'),'-O','binary','-j','.text',elf,payload])
    body=bytearray(stock[:-8])+payload.read_bytes()
    applied=[]
    for address,expected,symbol,reason in HOOKS:
        off=address-BASE; before=bytes.fromhex(expected)
        assert body[off:off+len(before)]==before,(hex(address),reason)
        replacement=branch(address,syms[symbol])
        if address==0x080116a8:
            lo,hi=struct.unpack('<HH',replacement);replacement=struct.pack('<HH',lo,hi|0x4000)
        replacement+=b'\x00\xbf'*((len(before)-4)//2)
        body[off:off+len(before)]=replacement
        applied.append(dict(address=hex(address),target=hex(syms[symbol]),symbol=symbol,
            expected_bytes=expected,replacement_bytes=replacement.hex(),reason=reason,
            branch_kind='BL' if address==0x080116a8 else 'B.W'))
    # Preserve stock busy safeguards: DO NOT apply F6F8/F724 dual-grab NOPs.
    # Preserve stock page count/failure branches; extension cannot be certified.
    vi=body.index(b'V1.1.31\0'); body[vi:vi+8]=b'EXPRF!!\0'
    struct.pack_into('<I',body,4,syms['experiment_reset_gate']|1)
    prefix_changes=[dict(address=h['address'], expected_bytes=h['expected_bytes'],
        replacement_bytes=h['replacement_bytes'], reason=h['reason']) for h in applied]
    for off,n,reason in [(4,4,'reset vector'),(vi,8,'invalid experimental version label')]:
        prefix_changes.append(dict(address=hex(BASE+off),expected_bytes=stock[off:off+n].hex(),
            replacement_bytes=body[off:off+n].hex(),reason=reason))
    reconstructed=bytearray(stock[:-8])
    for change in prefix_changes:
        off=int(change['address'],16)-BASE; before=bytes.fromhex(change['expected_bytes'])
        assert reconstructed[off:off+len(before)]==before
        reconstructed[off:off+len(before)]=bytes.fromhex(change['replacement_bytes'])
    assert reconstructed==body[:STOCK_SIZE-8], 'Unlisted change in stock prefix'
    image=OUT/'EXPERIMENTAL-DO-NOT-FLASH.offline-image'
    image.write_bytes(bytes(body)+INVALID_TRAILER)
    assert image.read_bytes()[-8:]!=MAGIC
    (BUILD/'integration.dis').write_text(run([tool('objdump'),'-dr',elf]))
    (BUILD/'image.dis').write_text(run([tool('objdump'),'-D','-b','binary','-marm','-Mforce-thumb',
        '--adjust-vma='+hex(BASE),image]))
    (BUILD/'size.txt').write_text(run([tool('size'),elf]))
    manifest={'classification':'EXPERIMENTAL DO NOT FLASH; NORMAL RESET BLOCKED OFFLINE FIXTURE',
        'stock_sha256':STOCK_SHA,'image_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
        'image_bytes':image.stat().st_size,'elf_sha256':hashlib.sha256(elf.read_bytes()).hexdigest(),
        'payload_address':hex(origin),'state_source':'checked event-group handle at 0x20001E64, tail at object+32',
        'arm_extended_payload_bytes':176,'arm_tail_bytes':144,'arm_allocator_block_min':184,
        'ram_verified_for_device':False,'valid_iap_trailer':False,'reset_normal_control_flow_reaches_stock':False,
        'exception_paths_quarantined':False,
        'production_output_supported':False,'device_tests_run':False,
        'physical_stop_implemented':False,'physical_cleanup_proven':False,
        'unresolved_symbols':[],'hooks':applied,'prefix_changes':prefix_changes,
        'stock_busy_checks_preserved':['0x0800F6F8','0x0800F724']}
    (OUT/'analysis-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print((BUILD/'size.txt').read_text().strip())
    print(f'Linked {len(HOOKS)} real image hooks, zero unresolved symbols.')
    print('EXPERIMENTAL: normal reset self-loop + invalid trailer. Exception paths NOT proven. No flash output.')
    print(image)

if __name__=='__main__': main()
