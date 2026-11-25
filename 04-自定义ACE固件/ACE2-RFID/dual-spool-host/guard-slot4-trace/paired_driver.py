"""Host-side coordinator for local Klipper native guard; run only after review."""
import json, pathlib, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/'diagnostic93/path-probe'))
import probe
ROOT=pathlib.Path(__file__).resolve().parent/'run-pair-slot4-trace'

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
def diag(lane,field):
 word=0x800a0000|(lane<<8)|field;r=macro('A_FILAMENT_IDENTIFY','filament_identify',SLOT=word)
 if r.get('index')!=word or r.get('code')==0xffffffff:raise ValueError('diagnostic rejected')
 return r['code']
def snapshot(lane):
 ctx={str(i):diag(lane,i) for i in range(6)}
 row=macro('A_FILAMENT','get_filament_info',SLOT=lane)
 if row.get('_index_present') is not True or row.get('index')!=lane:raise ValueError('CMD13 slot mismatch')
 sku=row.get('sku') or '';uid_hint=sku[2:] if len(sku)==10 and sku.startswith('SM') and all(c in '0123456789ABCDEF' for c in sku[2:]) else None
 return {'context':ctx,'cmd13':row,'native_uid_hint':uid_hint,'uid_proven_physical':False}
def main():
 ROOT.mkdir(exist_ok=False)
 marker=ROOT/'guard-driver.once'
 with marker.open('x') as f:f.write('one shot\n')
 changed=False;armed=False;arm_attempted=False;confirmed=False;result={};held=[];hold_attempted=[]
 try:
  ident=macro('A_DISCOVER','discover_device');info=macro('A_INFO','get_info');s=probe.request('/multiace/api/state');a=s['aces'][0]
  if tuple(ident.get('uid%d'%i) for i in (1,2,3))!=probe.UID or info.get('firmware')!='V1.1.97O' or a['fw_hold'] or s['calibration']['state']!='idle' or s['printer_state'] not in ('idle','ready') or s['swap_in_progress'] or s['unload_all_active'] or s['tag_op']['busy'] or a['dryer']['status']!='stop' or a['auto_dry_running'] or a['auto_dry']['enabled'] or a['feed_assist']!=-1:raise ValueError('identity or calibration invalid')
  st=macro('A_STATUS','get_status');flags=[st.get('rfid1_enable_raw'),st.get('rfid2_enable_raw')]
  if st.get('_status_wire_valid') is not True or [x.get('slot_status') for x in st['slots']]!=['ready']*4 or flags[0] not in (0,1) or flags[1]!=1:raise ValueError('RF flags or status invalid')
  reader0=macro('A_FILAMENT_IDENTIFY','filament_identify',SLOT=0x800a0003)['code'];reader1=diag(3,3)
  if reader1==8 or (flags[0]==0 and reader0!=8):raise ValueError('reader state mismatch')
  result['rf_initial']={'flags':flags,'reader0':reader0,'reader1':reader1};result['before']={str(i):snapshot(i) for i in (2,3)}
  (ROOT/'preflight.json').write_text(json.dumps({'uid':ident,'firmware':info['firmware'],
      'lanes':st['slots'],'rf_initial':result['rf_initial'],'before':result['before']},indent=2)+'\n')
  if result['before']['2']['cmd13'].get('sku')!='SME5DDA105':
   raise ValueError('static pink slot record differs or missing')
  key=macro('A_KEYSTATE','get_key_state')['fields'].get('1')
  if type(key) is not int or not key & (1<<8) or key & (1<<12) or any(probe.heads()):raise ValueError('target must be empty; neighbour present; heads clear')
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
   hold_attempted.append(idx)
   command('ACE_FW_RELEASE ACE=%d'%idx)
   if probe.request('/multiace/api/state')['aces'][idx]['fw_hold'] is not True:raise ValueError('unused port hold unconfirmed')
   held.append(idx)
  result['temporary_holds']=list(held)
  arm_attempted=True
  command('ACE_NATIVE_GUARD_ARM TARGET=3');armed=True
  last_event=None;last_trace=0
  while True:
   state=status();event=(state['state'],state['event_seq'])
   with (ROOT/'telemetry.jsonl').open('a') as trace:
    for row in state.get('trace_events',[]):
     if row['seq']>last_trace+1:
      trace.write(json.dumps({'kind':'trace_gap','after':last_trace,'before':row['seq']})+'\n')
     if row['seq']>last_trace:
      trace.write(json.dumps(row)+'\n');last_trace=row['seq']
   state={k:v for k,v in state.items() if k!='trace_events'};state['trace_seq']=last_trace
   with (ROOT/'guard-states.jsonl').open('a') as f:f.write(json.dumps({'time':time.monotonic(),**state})+'\n')
   if event!=last_event:print(json.dumps(state),flush=True);last_event=event
   if state['state']=='confirmed':confirmed=True;break
   if state['state']=='fault':raise ValueError('guard fault')
   time.sleep(.3)
  result['guard']=state;result['after']={str(i):snapshot(i) for i in (2,3)};result['quality']='pending_attribution'
  if changed:
   macro('A_RFID','set_rfid_enable',SLOT=0,ENABLE=1)
   end=time.monotonic()+8
   while time.monotonic()<end:
    if macro('A_FILAMENT_IDENTIFY','filament_identify',SLOT=0x800a0003)['code']!=8:break
    time.sleep(.15)
   else:raise ValueError('reader0 restore not confirmed')
   changed=False
  if status()['state']!='confirmed':raise ValueError('guard not stable before clear')
  result['guard_left_armed']=True;result['complete']=state.get('reason') in ('head sensor','ready and encoder stable; supervised stop')
  if not result['complete']:result['outcome']='aborted_before_observation'
 except BaseException as e:
  result['error']=repr(e)
  if arm_attempted:
   try:result['guard_after_error']={k:v for k,v in status().items() if k!='trace_events'}
   except Exception as exc:result['guard_query_error']=repr(exc)
   try:
    result['error_stop_reply']=macro('A_STOP','stop_feed_assist',SLOT=3)
    result['error_stop_ack']=True
   except Exception as exc:
    result['error_stop_ack']=False;result['error_stop_error']=repr(exc)
   result['error_stillness']='unconfirmed'
  result.update(guard_left_armed=(True if armed else 'unknown' if arm_attempted else False),arm_attempted=arm_attempted,reader_restore_pending=changed,confirmed=confirmed)
 finally:
  result['hold_cleanup']={}
  for idx in hold_attempted:
   result['hold_cleanup'][str(idx)]={}
   try:
    current=probe.request('/multiace/api/state')['aces'][idx]['fw_hold']
    result['hold_cleanup'][str(idx)]['observed_before']=current
    if current is True:
     try:command('ACE_FW_RESUME ACE=%d'%idx)
     except Exception as exc:result['hold_cleanup'][str(idx)]['resume_error']=repr(exc)
    result['hold_cleanup'][str(idx)]['fw_hold']=probe.request('/multiace/api/state')['aces'][idx]['fw_hold']
   except Exception as exc:result['hold_cleanup'][str(idx)]['state_error']=repr(exc)
  (ROOT/'guard-driver-result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
 if not result.get('complete'):raise SystemExit('incomplete; inspect guard and RF state')
if __name__=='__main__':main()
