#!/usr/bin/env python3
import json,re,sys
from pathlib import Path
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R.parent))
from merge_offline import decode_branch
m=json.loads((R/'output/manifest.json').read_text());s=json.loads((R/'build/symbols.json').read_text());img=(R/'output/ACCESSOR-NOFLASH.offline-image').read_bytes();dis=(R/'build/combined.dis').read_text()
assert len(m['hooks'])==39
assert decode_branch(0x0800e108,img[0x6108:0x610c])[0]==s['br_stock_halt']
assert s['br_halt_checked'] and s['rf_platform_bind_operation'] and s['br_decoder_probe']
assert '<br_decoder_probe>' in dis and '<ar_auth_checked>' in dis and '<ar_read_checked>' in dis
functions=dict(re.findall(r'^[0-9a-f]+ <([^>]+)>:\n(.*?)(?=^[0-9a-f]+ <|\Z)',dis,re.M|re.S))
def reaches(start,target,seen=None):
 seen=set() if seen is None else seen
 if start==target:return True
 if start in seen:return False
 seen.add(start)
 return any(reaches(x.split('+')[0],target,seen) for x in re.findall(r'\b(?:bl|b\.w)\s+[^<]*<([^>]+)>',functions.get(start,'')))
for fn in ('decode_native_tag','decode_cmd68_uid_tag'):
 assert reaches(fn,'br_decoder_probe'),fn
assert reaches('br_decoder_probe','ar_auth_checked') and reaches('br_decoder_probe','ar_read_checked')
assert 'mrs' in dis and 'IPSR' in dis
assert not m['auth_observation_is_identity']
assert not m['rf_ready'] and not m['physical_stop_proven'] and img[-8:]==b'NOFLASH!'
for fn in ('br_halt_checked',): # restore_registers is inlined by -Os
 body=re.search(r'^[0-9a-f]+ <'+fn+r'>:\n(.*?)(?=^[0-9a-f]+ <|\Z)',dis,re.M|re.S)
 assert body,fn
 assert not any(x in body.group(1) for x in ['13c70','<delay_ms>','<rc522_timer>'])
body=re.search(r'^[0-9a-f]+ <rf_platform_background_lane>:\n(.*?)(?=^[0-9a-f]+ <|\Z)',dis,re.M|re.S).group(1)
assert re.search(r'movs\s+r0, #0',body)
print('PASS: HALT E108 branch, bound owner symbol, no HALT RTOS-delay calls, background provenance denied, NOFLASH unchanged.')
