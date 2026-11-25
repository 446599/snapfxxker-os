"""Read-only macro diagnostics; 30 requests maximum, no retry of POST."""
import json,pathlib,sys,time,traceback,urllib.request
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent));import unwind002 as u
out=pathlib.Path(__file__).resolve().parent/'readonly-diagnostic.jsonl'
open_url=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def record(**row):
 row['monotonic']=time.monotonic()
 with out.open('a') as f:f.write(json.dumps(row)+'\n')
def request(path,data=None,timeout=2):
 method='GET' if data is None else 'POST';req=urllib.request.Request('http://192.168.2.213'+path,data=None if data is None else json.dumps(data).encode(),headers={'Content-Type':'application/json'})
 try:
  with open_url.open(req,timeout=timeout) as resp:
   raw=resp.read();metadata={'endpoint':path,'method':method,'status':resp.status,'content_type':resp.headers.get('Content-Type'),'length':len(raw)}
  try:return json.loads(raw)
  except Exception:
   record(stage='http_json',body_excerpt=raw[:300].decode('utf-8','replace'),traceback=traceback.format_exc(),**metadata);raise
 except Exception:
  record(stage='http_request',endpoint=path,method=method,traceback=traceback.format_exc());raise
u.probe.request=request
# Intercept the gcode message parser independently from HTTP JSON parsing.
original_loads=u.json.loads
def loads(value,*a,**kw):
 try:return original_loads(value,*a,**kw)
 except Exception:
  if isinstance(value,str):record(stage='gcode_json',excerpt=value[:300],traceback=traceback.format_exc())
  raise
# Do not patch json.loads globally; function-level macro variant isolates parsing.
def macro(name,label):
 rows=request('/server/gcode_store?count=1')['result']['gcode_store'];base=max((r['time'] for r in rows),default=0)
 request('/multiace/api/macro',{'name':name,'args':{'ACE':0}},timeout=4)
 end=time.monotonic()+3;prefix='// [A '+label+'] '
 while time.monotonic()<end:
  rows=request('/server/gcode_store?count=30')['result']['gcode_store'];found=[r for r in rows if r['time']>base and r['message'].startswith(prefix)]
  if found:
   if len(found)!=1:raise ValueError('ambiguous reply')
   row=found[0];raw=row['message'][len(prefix):]
   try:reply=original_loads(raw)
   except Exception:
    record(stage='gcode_json',command=name,message_excerpt=raw[:300],traceback=traceback.format_exc());raise
   if reply.get('_v2_frame_valid') is not True or reply.get('_v2_payload_valid') is not True or reply.get('_v2_command')!={'A_STATUS':6,'A_KEYSTATE':73,'A_FEEDINFO':76}[name] or reply.get('code')!=0:raise ValueError('invalid frame')
   record(stage='success',command=name,id=reply.get('id'));return
  time.sleep(.05)
 raise TimeoutError('reply missing')
for i in range(30):
 name,label=(('A_STATUS','get_status'),('A_KEYSTATE','get_key_state'),('A_FEEDINFO','get_feed_info'))[i%3]
 try:macro(name,label)
 except Exception:record(stage='macro_error',command=name,traceback=traceback.format_exc())
print('diagnostic complete',out)
