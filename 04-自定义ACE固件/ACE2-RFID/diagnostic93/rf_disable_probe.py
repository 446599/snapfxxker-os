"""Read-only state comparison plus reversible CMD14 disable/restore."""
import json,pathlib,sys,time,urllib.request
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/'tooling'))
from ace_spool_measure import Printer
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))
out=pathlib.Path(__file__).resolve().parent/'live'; log=(out/'rf-disable-rpc.jsonl').open('w');p=Printer('http://192.168.2.213',0,2,log)
r={'operation':'disable-reader1-restore','changed':False,'restored':False}
def save(): (out/'rf-disable-result.json').write_text(json.dumps(r,indent=2)+'\n')
def call(n,l,**kw):return p.macro(n,l,**kw)
def state():
 s=p.state();a=s['aces'][0]
 assert a['firmware']=='V1.1.93O' and a['connected'] and a['status']=='ready' and not a['fw_hold'] and a['dryer']['status']=='stop'
 assert not s['unload_all_active'] and not a['auto_dry_running'] and not a['auto_dry']['enabled']
 q=p.request('/printer/objects/query?print_stats&virtual_sdcard')['result']['status'];assert q['print_stats']['state']=='standby' and not q['virtual_sdcard']['is_active']
def stat():
 x=call('A_STATUS','get_status'); assert x.get('_status_wire_valid');return x
def word(slot):
 v=0x800a0000|(slot<<8)|3;x=call('A_FILAMENT_IDENTIFY','filament_identify',SLOT=v);assert x.get('index')==v and type(x.get('code')) is int;return x['code']
def snapshot():return {'status':stat(),'reader0':word(0),'reader1':word(2)}
state(); uid=call('A_DISCOVER','discover_device');assert [uid.get(x) for x in ('uid1','uid2','uid3')]==[808780566,17445170,942880330]
r['before']=snapshot();save()
# Only disable an enabled target; this ensures restore command reflects true prior state.
if r['before']['reader1']==8: raise SystemExit('target already disabled; no change')
try:
 r['changed']=True;save();call('A_RFID','set_rfid_enable',SLOT=2,ENABLE=0)
 deadline=time.monotonic()+8
 while time.monotonic()<deadline:
  r['disabled']=snapshot();save()
  if r['disabled']['reader1']==8:break
  time.sleep(.15)
 else: raise RuntimeError('reader did not acknowledge disabled state')
finally:
 if r['changed']:
  try:
   call('A_RFID','set_rfid_enable',SLOT=2,ENABLE=1)
   deadline=time.monotonic()+8
   while time.monotonic()<deadline:
    r['after']=snapshot();save()
    if r['after']['reader1']!=8:
     r['restored']=True;break
    time.sleep(.15)
   if not r['restored']:raise RuntimeError('reader enable not observed')
  except Exception as e:
   r['restore_error']=repr(e);save();raise
 save()
print(json.dumps({'before':[r['before']['reader0'],r['before']['reader1']], 'disabled':[r['disabled']['reader0'],r['disabled']['reader1']], 'after':[r['after']['reader0'],r['after']['reader1']], 'restored':r['restored']}))
