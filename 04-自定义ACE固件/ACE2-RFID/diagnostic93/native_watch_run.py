"""One-shot local native preload observation. Do not run before review.

Requires ace2_ota.py and native_watch.py beside this file. It issues no FEED,
rollback, tag writes or CMD20. Uncertain STOP or reader restore retains FW hold.
"""
import argparse, json, os, pathlib, time, urllib.request
import ace2_ota as ota
from native_watch import Watch, DeviceIO
ROOT=pathlib.Path(__file__).resolve().parent
PORT='/dev/serial/by-path/platform-fed00000.usb-usb-0:1.3:1.0'
UID=(808780566,17445170,942880330)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def http(path, data=None, timeout=.2):
 req=urllib.request.Request('http://127.0.0.1'+path,data=None if data is None else json.dumps(data).encode(),headers={'Content-Type':'application/json'})
 with opener.open(req,timeout=timeout) as r:return json.load(r)
def gcode(command):return http('/printer/gcode/script',{'script':command},timeout=10)
def log(kind, **fields):
 row={'t':time.monotonic(),'kind':kind,**fields}
 with (ROOT/'native-watch-events.jsonl').open('a') as f:f.write(json.dumps(row)+'\n');f.flush()
 print(json.dumps(row),flush=True)
def diag(io,lane,field):
 word=0x800a0000|(lane<<8)|field
 fields=io.send(68,ota._field_uint32(1,word))
 if ota.get_field(fields,1,None)!=word:raise ValueError('diagnostic response index')
 v=ota.get_field(fields,12,0)
 if type(v) is not int or v==0xffffffff:raise ValueError('diagnostic refused')
 return v
def set_reader(io,enabled):
 fields=io.send(14,ota._field_uint32(1,0)+ota._field_uint32(2,int(enabled)))
 if ota.get_field(fields,1,0)!=0:raise ValueError('RF enable rejected')
def await_reader(io,disabled):
 end=time.monotonic()+8
 while time.monotonic()<end:
  state=diag(io,0,3)
  if (state==8)==disabled:return state
  time.sleep(.1)
 raise ValueError('reader state timeout')
def confirm_still(io):
 if not io.stop(2): raise ValueError('final STOP unconfirmed')
 start=time.monotonic();last=None;samples=0
 while time.monotonic()-start < 6:
  states=io.status();value=io.decoder()
  if states != ['ready']*4 or (last is not None and value!=last):
   moving=[i for i,v in enumerate(states) if v!='ready']
   for slot in set([2]+moving): io.stop(slot)
   raise ValueError('activity during stillness verification')
  last=value;samples+=1
  if samples>=3 and time.monotonic()-start>=1.5: return value
  time.sleep(.55)
 raise ValueError('stillness timeout')
def snapshot(io):
 context={str(i):diag(io,2,i) for i in range(6)}
 fields=io.send(13,ota._field_uint32(1,2))
 sku=ota.get_field(fields,3,b'')
 return {'context':context,'cmd13_version_field2':ota.get_field(fields,2,None),
         'cmd13_sku':sku.decode('ascii',errors='replace') if isinstance(sku,bytes) else None,
         'cmd13_fields':{str(k):[(w,v.hex() if isinstance(v,bytes) else v) for w,v in vals] for k,vals in fields.items()}}
def check_preflight():
 s=http('/multiace/api/state',timeout=2);a=s['aces'][0]
 if (s['printer_state'] not in ('idle','ready') or s['mode']!='multi' or s['active_device']!=0
  or s['swap_in_progress'] or s['unload_all_active'] or s['tag_op']['busy'] or s['calibration']['state']!='idle'
  or a['firmware']!='V1.1.95O' or not a['connected'] or a['status']!='ready' or a['fw_hold']
  or a['dryer']['status']!='stop' or a['auto_dry_running'] or a['auto_dry']['enabled']
  or a['feed_assist']!=-1):raise ValueError('preflight state rejected')
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--execute',action='store_true');ap.add_argument('--preflight',action='store_true');args=ap.parse_args()
 if args.preflight:
  check_preflight(); heads=DeviceIO(None,ota,http).heads(); print(json.dumps({'preflight':'ok','heads':heads,'serial_released':False})); return
 if not args.execute:raise SystemExit('review candidate; pass --execute only after approval')
 marker=ROOT/'native-watch.once'
 with marker.open('x') as f:f.write('one-shot\n')
 released=False;t=None;io=None;changed=False;safe=False;reader_restored=False;resumed=False;result={}
 try:
  check_preflight();gcode('ACE_FW_RELEASE ACE=0');released=True
  s=http('/multiace/api/state',timeout=2)
  if s['aces'][0]['fw_hold'] is not True:raise ValueError('release unconfirmed')
  t=ota.ACE2Transport(PORT);io=DeviceIO(t,ota,http)
  ident=io.send(0);uid=tuple(ota.get_field(ident,i,None) for i in (1,2,3))
  info=io.send(7);version=ota.get_field(info,1,b'')
  if uid!=UID or version!=b'V1.1.95O':raise ValueError('wrong identity')
  if io.status()!=['ready']*4:raise ValueError('lanes not ready')
  raw=io.send(6);flag0=ota.get_field(raw,5,0);flag1=ota.get_field(raw,6,0)
  if flag0 not in (0,1) or flag1!=1:raise ValueError('RFID flags invalid or target disabled')
  reader0=diag(io,0,3);reader1=diag(io,2,3)
  if reader1==8:raise ValueError('target reader disabled')
  if flag0==0 and reader0!=8:raise ValueError('disabled reader0 state mismatch')
  log('reader_initial',reader0=reader0,reader1=reader1,flag0=flag0,flag1=flag1)
  if flag0==1:
   changed=True;set_reader(io,False);await_reader(io,True)
  result['baseline']=snapshot(io)
  watch=Watch(io);log('waiting_empty')
  while watch.phase not in ('complete','uncertain'):
   watch.tick();time.sleep(.05)
  result.update(phase=watch.phase,reason=watch.reason,triggered=sorted(watch.triggered),forward_peak=watch.forward_peak,stops=sorted(watch.stop_sent))
  log('watch_finished',**result)
  if watch.phase!='complete':raise ValueError('motion/STOP uncertain')
  result['decoder_before_diagnostic']=confirm_still(io)
  result['after']=snapshot(io)
  result['decoder_after_diagnostic']=confirm_still(io)
  safe=True
  sku=result['after']['cmd13_sku']
  result['uid_hint']=sku[2:] if isinstance(sku,str) and len(sku)==10 and sku.startswith('SM') else None
  result['quality']='pending_attribution'
 except BaseException as exc:
  safe=False
  if io is not None:
   try: io.stop(2)
   except Exception: pass
  result['error']=repr(exc);log('error',error=repr(exc))
 finally:
  if t is not None and safe:
   try:
    if changed:set_reader(io,True);await_reader(io,False)
    result['decoder_after_restore']=confirm_still(io)
    reader_restored=True
   except Exception as exc:
    safe=False;result['reader_restore_error']=repr(exc)
  if t is not None:t.close()
  if released and safe and reader_restored:
   try:gcode('ACE_FW_RESUME ACE=0');resumed=True
   except Exception as exc:result['resume_error']=repr(exc)
  result.update(safe_still=safe,reader_restored=reader_restored,resumed=resumed,fw_hold_expected=released and not resumed)
  (ROOT/'native-watch-result.json').write_text(json.dumps(result,indent=2)+'\n');log('result',**result)
 if not resumed:raise SystemExit('incomplete: device remains held, inspect log')
if __name__=='__main__':main()
