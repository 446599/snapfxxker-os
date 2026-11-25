"""Candidate: explicit device timeout handling for permitted read-only macros."""
import json,time
READ_ONLY={'A_DISCOVER','A_INFO','A_STATUS','A_KEYSTATE','A_FEEDINFO','A_FILAMENT'}
COMMANDS={'A_DISCOVER':0,'A_INFO':7,'A_STATUS':6,'A_KEYSTATE':73,'A_FEEDINFO':76,'A_FILAMENT':13,'A_FILAMENT_IDENTIFY':68,'A_RFID':14,'A_STOP':9,'A_FEED':8}
class DeviceTimeout(RuntimeError):pass

def macro_once(request,name,label,args,record=lambda **kw:None):
 before=request('/server/gcode_store?count=1')['result']['gcode_store'];stamp=max((r['time'] for r in before),default=0)
 request('/multiace/api/macro',{'name':name,'args':{'ACE':0,**args}},timeout=5)
 end=time.monotonic()+4;prefix='// [A '+label+'] '
 while time.monotonic()<end:
  rows=request('/server/gcode_store?count=30')['result']['gcode_store'];found=[r for r in rows if r['time']>stamp and r['message'].startswith(prefix)]
  if len(found)>1:raise ValueError('ambiguous reply '+name)
  if found:
   raw=found[0]['message'][len(prefix):]
   try:reply=json.loads(raw)
   except json.JSONDecodeError:
    record(command=name,non_json_reply=raw[:300])
    if raw.startswith(('超时 (','timeout (')):raise DeviceTimeout(raw)
    raise
   if reply.get('_v2_frame_valid') is not True or reply.get('_v2_payload_valid') is not True or reply.get('_v2_command')!=COMMANDS[name] or reply.get('code')!=0:raise ValueError('invalid reply '+name)
   record(command=name,args=args,reply=reply)
   return reply['result']
  time.sleep(.05)
 raise TimeoutError('missing reply '+name)

def macro(request,name,label,record=lambda **kw:None,**args):
 try:return macro_once(request,name,label,args,record)
 except DeviceTimeout:
  if name not in READ_ONLY:raise
  record(command=name,action='retry_read_once')
  return macro_once(request,name,label,args,record)
