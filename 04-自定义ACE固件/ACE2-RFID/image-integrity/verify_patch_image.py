#!/usr/bin/env python3
"""Independent byte-level integrity check; NOT a firmware safety verifier."""
import hashlib
import json
from pathlib import Path

BASE_SIZE = 71592
BASE_SHA = 'a468e366226e7c483d3dd96b444223e89cbf0d1b10573d42b6263843ffd6be3e'
MAGIC = bytes.fromhex('61a5635a65a5325a')

def crc16(data, initial):
    x=initial
    for b in data:
        x ^= b
        for _ in range(8): x = (x >> 1) ^ (0x8408 if x & 1 else 0)
    return x & 0xffff

def verify(base, spec, out):
    if len(base)!=BASE_SIZE or hashlib.sha256(base).hexdigest()!=BASE_SHA or base[-8:]!=MAGIC:
        raise ValueError('unrecognised stock base')
    if spec['base_size']!=BASE_SIZE or spec['base_md5']!=hashlib.md5(base).hexdigest():
        raise ValueError('spec does not target verified base')
    if not isinstance(spec['version_string'],str) or not spec['version_string'].endswith('O'):
        raise ValueError('invalid version marker')
    app=bytearray(base[:-8]); app.extend(bytes.fromhex(spec['appended_hex']))
    used=set()
    for category in ('hooks','pokes'):
        for item in spec[category]:
            offset=item['file_offset']
            if type(offset) is not int or offset < 0 or offset >= BASE_SIZE-8:
                raise ValueError('modification outside stock body')
            new=bytes.fromhex(item['replace_hex'] if category=='hooks' else item['bytes_hex'])
            if not new or offset+len(new)>BASE_SIZE-8:
                raise ValueError('invalid modification length')
            cells=set(range(offset,offset+len(new)))
            if cells & used: raise ValueError('overlapping modifications')
            used |= cells
            if category=='hooks':
                old=bytes.fromhex(item['expect_hex'])
                if len(old)!=len(new) or base[offset:offset+len(old)]!=old:
                    raise ValueError('stock hook bytes mismatch')
            app[offset:offset+len(new)]=new
    reconstructed=bytes(app)+MAGIC
    if reconstructed!=out: raise ValueError('binary and patch differ')
    if len(out)!=spec['result_size'] or crc16(out,0)!=spec['result_crc16']:
        raise ValueError('size or patch CRC mismatch')
    if out.count(spec['version_string'].encode()+b'\0')!=1:
        raise ValueError('version marker absent or ambiguous')
    return {'size':len(out),'sha256':hashlib.sha256(out).hexdigest(),
            'crc_kermit_init0':f'{crc16(out,0):04X}',
            'crc_kermit_initffff':f'{crc16(out,0xffff):04X}',
            'modified_stock_bytes':len(used)}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--base',required=True);p.add_argument('--spec',required=True);p.add_argument('--image',required=True)
    a=p.parse_args(); print(json.dumps(verify(Path(a.base).read_bytes(),json.loads(Path(a.spec).read_text()),Path(a.image).read_bytes()),indent=2));print('Integrity only; NOT a safety or hardware verification.')
