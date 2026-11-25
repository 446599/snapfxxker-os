import sys,json,time,pathlib
sys.path.insert(0,str(pathlib.Path(__file__).parent));import probe
out=pathlib.Path(__file__).parent
marker=out/'native-calibration-run.once'
with marker.open('x') as f:f.write('single execution\n')
def save(d):
 with (out/'native-calibration-events.jsonl').open('a') as f:f.write(json.dumps(d)+'\n');f.flush()
def stop():
 try: r=probe.stop();save({'stop':r});return True
 except Exception as e:save({'stop_error':str(e)});return False
def cancel():
 try:save({'cancel':probe.request('/printer/gcode/script',{'script':'ACE_CALIBRATION_CANCEL'},timeout=5)})
 except Exception as e:save({'cancel_error':str(e)})
start=time.monotonic();result={'started':start,'state':'unknown'}
try:
 c=probe.request('/multiace/api/state')['calibration']
 if (c['state']!='prepared' or (c['ace'],c['slot'],c['head'])!=(0,2,2) or c['max_feed_mm']>2400 or c['speed_mm_s']>30 or any(probe.heads())):raise RuntimeError('pre-feed verification failed')
 save({'action':'feed_once','at':time.monotonic()})
 probe.request('/printer/gcode/script',{'script':'ACE_CALIBRATION_FEED'},timeout=5)
 print('FEED sent',flush=True)
 last=-1
 while time.monotonic()-start<120:
  heads=probe.heads();s=probe.request('/multiace/api/state',timeout=3);c=s['calibration'];save({'t':round(time.monotonic()-start,2),'heads':heads,'calibration':c})
  if any(heads[i] for i in (0,1,3)):raise RuntimeError('wrong head sensor')
  if s.get('printer_state') not in ('idle','ready') or s.get('swap_in_progress') or s.get('tag_op',{}).get('busy'):raise RuntimeError('printer state changed')
  if c.get('state')=='at_sensor':
   result.update(state='at_sensor',calibration=c,heads_at_sensor=heads);break
  if c.get('state') not in ('feeding','prepared'):raise RuntimeError('calibration '+str(c.get('state'))+' '+str(c.get('error')))
  sec=int(time.monotonic()-start)
  if sec//10 != last:print('elapsed',sec,'state',c.get('state'),'commanded',c.get('commanded_feed_mm'),'heads',heads,flush=True);last=sec//10
  time.sleep(.15)
 else:raise RuntimeError('120s timeout')
 result['stop_ack']=stop()
 time.sleep(.3); st1=probe.status();v1=probe.encoder();time.sleep(.3);st2=probe.status();v2=probe.encoder();result.update(lanes1=[x.get('slot_status') for x in st1['slots']],lanes2=[x.get('slot_status') for x in st2['slots']],decoder1=v1,decoder2=v2,final_heads=probe.heads(),final_state=probe.request('/multiace/api/state')['calibration'])
 result['stable']=result['stop_ack'] and v1==v2 and all(x=='ready' for x in result['lanes1']+result['lanes2'])
 if not result['stable']:result['state']='uncertain';cancel()
except Exception as e:
 result.update(state='error',error=str(e));result['stop_ack']=stop();cancel()
finally:
 result['elapsed_s']=round(time.monotonic()-start,2);(out/'native-calibration-result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
