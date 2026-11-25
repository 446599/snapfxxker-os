import json,urllib.request,time,pathlib
p=pathlib.Path(__file__).resolve().parent/'live';op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def req(path,data=None):
 r=urllib.request.Request('http://192.168.2.213'+path,data=None if data is None else json.dumps(data).encode(),headers={'Content-Type':'application/json'});return json.load(op.open(r,timeout=15))
s=req('/multiace/api/state');(p/'post-state.json').write_text(json.dumps(s,indent=2));a=s['aces'][0]
assert a['firmware']=='V1.1.93O' and a['status']=='ready' and not a['fw_hold'] and s['printer_state'] in ('idle','ready')
req('/printer/gcode/script',{'script':'A_DISCOVER ACE=0\nA_INFO ACE=0\nA_STATUS ACE=0\nA_TEMP ACE=0'})
time.sleep(.4); (p/'post-info.json').write_text(json.dumps(req('/server/gcode_store?count=20'),indent=2))
for lane in range(4):
 for field in range(5):
  rows=req('/server/gcode_store?count=1')['result']['gcode_store']; stamp=max((x['time'] for x in rows),default=0)
  word=0x800a0000|(lane<<8)|field
  req('/multiace/api/macro',{'name':'A_FILAMENT_IDENTIFY','args':{'ACE':0,'SLOT':word}})
  rows=req('/server/gcode_store?count=15')['result']['gcode_store']; prefix='// [A filament_identify] '
  found=[x for x in rows if x['time']>stamp and x['message'].startswith(prefix)]
  if len(found)!=1: raise RuntimeError((lane,field,'no unique response'))
  reply=json.loads(found[0]['message'][len(prefix):]); print(lane,field,reply.get('result',{}).get('code'))
  with (p/'diagnostic-words.jsonl').open('a') as f:f.write(json.dumps({'lane':lane,'field':field,'request':word,'reply':reply})+'\n')
