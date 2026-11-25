"""One authorized 20mm feed and bounded reverse; no RF transactions while moving."""
import json,pathlib,sys,time,urllib.request
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/'tooling'))
from ace_spool_measure import Printer
from ace_spool_geometry import signed_encoder
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))
out=pathlib.Path(__file__).resolve().parent/'live'; log=(out/'motion20-rpc.jsonl').open('w'); p=Printer('http://192.168.2.213',0,2,log)
sensors='&'.join('filament_motion_sensor%%20e%d_filament'%i for i in range(4))
result={'ace':0,'slot':2,'length_limit':20,'speed':5,'stops':0,'commands':[]}
def save(): (out/'motion20-result.json').write_text(json.dumps(result,indent=2)+'\n')
def call(name,label,**kw):
 r=p.macro(name,label,**kw)
 if not r.get('_v2_frame_valid',True): raise RuntimeError('invalid frame')
 return r
def checks():
 s=p.state();a=s['aces'][0]
 assert a['firmware']=='V1.1.92O' and a['connected'] and a['status']=='ready' and not a['fw_hold']
 assert a['dryer']['status']=='stop' and not a['auto_dry_running'] and not a['auto_dry']['enabled'] and not s['unload_all_active']
 q=p.request('/printer/objects/query?print_stats&virtual_sdcard&webhooks&'+sensors)['result']['status']
 assert q['print_stats']['state']=='standby' and not q['virtual_sdcard']['is_active'] and q['webhooks']['state']=='ready'
 assert all(q['filament_motion_sensor e%d_filament'%i]['filament_detected'] is False for i in range(4))
 result['initial_checks']=True;save()
def head_clear():
 q=p.request('/printer/objects/query?'+sensors)['result']['status']
 if not all(q['filament_motion_sensor e%d_filament'%i]['filament_detected'] is False for i in range(4)): raise RuntimeError('head sensor changed')
def status():
 r=call('A_STATUS','get_status');assert r.get('_status_wire_valid') and r.get('status')=='ready'; return r
def feedback():
 r=call('A_FEEDINFO','get_feed_info');assert r.get('_feed_wire_valid'); x=next(x for x in r['feed_info'] if x['index']==2)
 assert x.get('_decoder_present') and type(x.get('decoder')) is int
 return signed_encoder(x['decoder'])
def stop():
 result['stops']+=1;save();return call('A_STOP','stop_feed_assist',SLOT=2)
def diag():
 vals=[]
 for field in range(3):
  word=0x800a0200|field;r=call('A_FILAMENT_IDENTIFY','filament_identify',SLOT=word)
  assert r.get('index')==word and type(r.get('code')) is int;vals.append(r['code'])
 return vals
def move(mode):
 uncertain=True
 try:
  head_clear();result['commands'].append({'mode':mode,'mm':20,'speed':5});save()
  call('A_FEED','feed_or_rollback_raw',SLOT=2,SPEED=5,LENGTH=20,MODE=mode)
  end=time.monotonic()+4.5
  while time.monotonic()<end: head_clear();time.sleep(.15)
  stop();uncertain=False
  end=time.monotonic()+6
  while True:
   head_clear();r=status();lane=next(x for x in r['slots'] if x['index']==2)
   if lane['slot_status']=='ready': break
   if time.monotonic()>end: raise RuntimeError('not ready after STOP')
   time.sleep(.2)
  v1=feedback();time.sleep(.25);v2=feedback()
  assert v1==v2 and 8<=abs(v1)<=32,('invalid or changing encoder',v1,v2)
  return v1
 except BaseException:
  if uncertain:
   try:stop()
   except Exception as e:result['stop_error']=repr(e)
  raise
try:
 checks()
 d=call('A_DISCOVER','discover_device');result['discover']=d
 ids=[d.get(k) for k in ('uid1','uid2','uid3')]
 # Fresh identity response names vary; require exact values somewhere in response.
 assert all(str(v) in json.dumps(d) for v in (808780566,17445170,942880330)),d
 status();result['before']=diag();save()
 forward=move(0);result['forward_decoder']=forward;result['after_forward']=diag();save()
 checks();reverse=move(1);result['reverse_decoder']=reverse
 assert (forward>0)!=(reverse>0),('reverse sign',forward,reverse)
 result['after_reverse']=diag();result['done']=True;save()
except BaseException as e:
 result['error']=repr(e);result['done']=False;save();raise
