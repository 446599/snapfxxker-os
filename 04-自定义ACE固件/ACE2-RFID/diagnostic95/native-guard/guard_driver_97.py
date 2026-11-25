"""Host-side coordinator for local Klipper native guard; run only after review."""
import json, pathlib, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/'diagnostic93/path-probe'))
import probe
ROOT=pathlib.Path(__file__).resolve().parent/'run-20261107-97'

def macro(name,label,**args):
 before=probe.request('/server/gcode_store?count=1')['result']['gcode_store'];stamp=max((x['time'] for x in before),default=0)
 probe.request('/multiace/api/macro',{'name':name,'args':{'ACE':0,**args}},timeout=5)
 end=time.monotonic()+4
 while time.monotonic()<end:
  rows=probe.request('/server/gcode_store?count=30')['result']['gcode_store'];found=[r for r in rows if r['time']>stamp and r['message'].startswith('// [A '+label+'] ')]
  if len(found)==1:
   reply=json.loads(found[0]['message'].split('] ',1)[1])
   if reply.get('_v2_frame_valid') is not True or reply.get('_v2_payload_valid') is not True or reply.get('code')!=0:raise ValueError('invalid reply '+name)
   return reply['result']
  time.sleep(.05)
 raise ValueError('timeout '+name)
def command(s):return probe.request('/printer/gcode/script',{'script':s},timeout=6)
def status():return probe.request('/printer/objects/query?ace_native_guard')['result']['status']['ace_native_guard']
def diag(field):
 word=0x800a0200|field;r=macro('A_FILAMENT_IDENTIFY','filament_identify',SLOT=word)
 if r.get('index')!=word or r.get('code')==0xffffffff:raise ValueError('diagnostic rejected')
 return r['code']
def snapshot():
 ctx={str(i):diag(i) for i in range(6)}
 return {'context':ctx,'cmd13':macro('A_FILAMENT','get_filament_info',SLOT=2)}
def main():
 marker=ROOT/'guard-driver.once'
 with marker.open('x') as f:f.write('one shot\n')
 changed=False;armed=False;confirmed=False;result={};held=[]
 try:
  ident=macro('A_DISCOVER','discover_device');info=macro('A_INFO','get_info');s=probe.request('/multiace/api/state');a=s['aces'][0]
  if tuple(ident.get('uid%d'%i) for i in (1,2,3))!=probe.UID or info.get('firmware')!='V1.1.97O' or a['fw_hold'] or s['calibration']['state']!='idle' or s['printer_state'] not in ('idle','ready') or s['swap_in_progress'] or s['unload_all_active'] or s['tag_op']['busy'] or a['dryer']['status']!='stop' or a['auto_dry_running'] or a['auto_dry']['enabled'] or a['feed_assist']!=-1:raise ValueError('identity or calibration invalid')
  st=macro('A_STATUS','get_status');flags=[st.get('rfid1_enable_raw'),st.get('rfid2_enable_raw')]
  if st.get('_status_wire_valid') is not True or [x.get('slot_status') for x in st['slots']]!=['ready']*4 or flags[0] not in (0,1) or flags[1]!=1:raise ValueError('RF flags or status invalid')
  reader0=macro('A_FILAMENT_IDENTIFY','filament_identify',SLOT=0x800a0003)['code'];reader1=diag(3)
  if reader1==8 or (flags[0]==0 and reader0!=8):raise ValueError('reader state mismatch')
  result['rf_initial']={'flags':flags,'reader0':reader0,'reader1':reader1};result['before']=snapshot()
  if flags[0]==1:
   changed=True;macro('A_RFID','set_rfid_enable',SLOT=0,ENABLE=0)
   end=time.monotonic()+8
   while time.monotonic()<end:
    if macro('A_FILAMENT_IDENTIFY','filament_identify',SLOT=0x800a0003)['code']==8:break
    time.sleep(.15)
   else:raise ValueError('reader0 disable not confirmed')
  for idx in (1,2,3):
   pre=probe.request('/multiace/api/state')['aces'][idx]
   if pre['connected'] is not False or pre['fw_hold'] is not False:raise ValueError('unused port not confirmed idle/unheld')
   command('ACE_FW_RELEASE ACE=%d'%idx)
   if probe.request('/multiace/api/state')['aces'][idx]['fw_hold'] is not True:raise ValueError('unused port hold unconfirmed')
   held.append(idx)
  result['temporary_holds']=list(held)
  command('ACE_NATIVE_GUARD_ARM');armed=True
  last_event=None
  while True:
   state=status();event=(state['state'],state['event_seq'])
   with (ROOT/'guard-states.jsonl').open('a') as f:f.write(json.dumps({'time':time.monotonic(),**state})+'\n')
   if event!=last_event:print(json.dumps(state),flush=True);last_event=event
   if state['state']=='confirmed':confirmed=True;break
   if state['state']=='fault':raise ValueError('guard fault')
   time.sleep(.3)
  result['guard']=state;result['after']=snapshot();result['quality']='pending_attribution'
  if changed:
   macro('A_RFID','set_rfid_enable',SLOT=0,ENABLE=1)
   end=time.monotonic()+8
   while time.monotonic()<end:
    if macro('A_FILAMENT_IDENTIFY','filament_identify',SLOT=0x800a0003)['code']!=8:break
    time.sleep(.15)
   else:raise ValueError('reader0 restore not confirmed')
   changed=False
  if status()['state']!='confirmed':raise ValueError('guard not stable before clear')
  result['guard_left_armed']=True;result['complete']=state.get('reason') in ('head sensor','native completed')
  if not result['complete']:result['outcome']='aborted_before_observation'
 except BaseException as e:
  result['error']=repr(e)
  if armed:
   try:probe.stop()
   except Exception:pass
  result.update(guard_left_armed=armed,reader_restore_pending=changed,confirmed=confirmed)
 finally:
  result['hold_cleanup']={}
  for idx in held:
   try:command('ACE_FW_RESUME ACE=%d'%idx)
   except Exception as exc:result['hold_cleanup'][str(idx)]={'resume_error':repr(exc)}
   try:result['hold_cleanup'].setdefault(str(idx),{})['fw_hold']=probe.request('/multiace/api/state')['aces'][idx]['fw_hold']
   except Exception as exc:result['hold_cleanup'].setdefault(str(idx),{})['state_error']=repr(exc)
  (ROOT/'guard-driver-result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
 if not result.get('complete'):raise SystemExit('incomplete; inspect guard and RF state')
if __name__=='__main__':main()
