import json,time,pathlib,sys,struct
sys.path.insert(0,'diagnostic93/path-probe');import probe
p=pathlib.Path('diagnostic95/read-words.json')
def call(name,label,**kw):
 rows=probe.request('/server/gcode_store?count=1')['result']['gcode_store'];stamp=max((x['time'] for x in rows),default=0)
 probe.request('/multiace/api/macro',{'name':name,'args':{'ACE':0,**kw}})
 end=time.monotonic()+3
 while time.monotonic()<end:
  rows=probe.request('/server/gcode_store?count=20')['result']['gcode_store'];f=[x for x in rows if x['time']>stamp and x['message'].startswith('// [A '+label+'] ')]
  if len(f)==1:
   r=json.loads(f[0]['message'].split('] ',1)[1]);
   if r.get('_v2_frame_valid') is not True or r.get('_v2_payload_valid') is not True or r.get('code')!=0:raise ValueError('invalid '+name)
   return r['result']
  time.sleep(.05)
 raise ValueError('timeout '+name)
s=probe.request('/multiace/api/state');a=s['aces'][0];ident=call('A_DISCOVER','discover_device');info=call('A_INFO','get_info');status=call('A_STATUS','get_status')
if tuple(ident.get('uid%d'%i) for i in (1,2,3))!=probe.UID or info.get('firmware')!='V1.1.95O' or any(x['slot_status']!='ready' for x in status['slots']):raise ValueError('identity or readiness mismatch')
r={'identity':ident,'firmware':info,'heads':probe.heads(),'lanes':status['slots'],'calibration':s['calibration'],'context':{},'cmd13':{}}
for lane in range(4):
 r['context'][lane]={}
 for field in range(6):
  result=call('A_FILAMENT_IDENTIFY','filament_identify',SLOT=0x800a0000|(lane<<8)|field);v=result.get('code');r['context'][lane][field]={'raw':v,'float':struct.unpack('<f',struct.pack('<I',v))[0] if type(v)==int and 0<=v<=0xffffffff else None}
 r['cmd13'][lane]=call('A_FILAMENT','get_filament_info',SLOT=lane)
p.write_text(json.dumps(r,indent=2,allow_nan=True)+'\n');print(json.dumps({'heads':r['heads'],'context':r['context'],'cmd13_unparsed':{k:v.get('_unparsed') for k,v in r['cmd13'].items()}}))
