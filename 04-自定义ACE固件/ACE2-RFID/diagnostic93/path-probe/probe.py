"""Review candidate: bounded path probe; never heats or reads RFID while moving."""
import argparse,json,math,pathlib,time,urllib.request
BASE='http://192.168.2.213'; UID=(808780566,17445170,942880330)
OUT=pathlib.Path(__file__).resolve().parent; MAX_MM=600; STEP=5; SPEED=5; MAX_SECONDS=900
op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def request(path,data=None,timeout=2):
 req=urllib.request.Request(BASE+path,data=None if data is None else json.dumps(data).encode(),headers={'Content-Type':'application/json'})
 with op.open(req,timeout=timeout) as r:return json.load(r)
def record(**data):
 data['monotonic']=time.monotonic()
 with (OUT/'events.jsonl').open('a') as f:f.write(json.dumps(data)+'\n');f.flush()
def macro(name,label,**args):
 rows=request('/server/gcode_store?count=1')['result']['gcode_store'];baseline=max((r['time'] for r in rows),default=0)
 request('/multiace/api/macro',{'name':name,'args':{'ACE':0,**args}},timeout=4)
 end=time.monotonic()+3;prefix='// [A '+label+'] '
 while time.monotonic()<end:
  rows=request('/server/gcode_store?count=30')['result']['gcode_store']
  found=[r for r in rows if r['time']>baseline and r['message'].startswith(prefix)]
  if found:
   if len(found)!=1:raise RuntimeError('ambiguous reply')
   reply=json.loads(found[0]['message'][len(prefix):]);record(command=name,args=args,reply=reply)
   expected={'A_DISCOVER':0,'A_STATUS':6,'A_FEEDINFO':76,'A_FEED':8,'A_STOP':9,'A_KEYSTATE':73}[name]
   if (reply.get('_v2_frame_valid') is not True or reply.get('_v2_payload_valid') is not True or
       reply.get('_v2_command')!=expected or reply.get('code')!=0 or str(reply.get('msg','')).upper()=='FORBIDDEN'):
    raise RuntimeError('unconfirmed '+name)
   return reply.get('result',{})
  time.sleep(.05)
 raise RuntimeError('missing reply '+name)
def heads():
 names='&'.join('filament_motion_sensor%%20e%d_filament'%i for i in range(4))
 status=request('/printer/objects/query?print_stats&virtual_sdcard&webhooks&'+names)['result']['status']
 if status['print_stats']['state']!='standby' or status['virtual_sdcard']['is_active'] or status['webhooks']['state']!='ready':raise RuntimeError('printer not idle')
 values=[status['filament_motion_sensor e%d_filament'%i]['filament_detected'] for i in range(4)]
 if any(type(v) is not bool for v in values):raise RuntimeError('invalid head sensor')
 return values
def status():
 r=macro('A_STATUS','get_status');assert r.get('_status_wire_valid') and len(r.get('slots',[]))==4
 return r
def encoder():
 r=macro('A_FEEDINFO','get_feed_info');assert r.get('_feed_wire_valid');x=next(x for x in r['feed_info'] if x['index']==2)
 if not x.get('_decoder_present') or type(x.get('decoder')) is not int:raise RuntimeError('encoder missing')
 v=x['decoder'];return v-(1<<64) if v>=1<<63 else v
def gate_present():
 r=macro('A_KEYSTATE','get_key_state')
 fields=r.get('fields',{});v=fields.get('1')
 if type(v) is not int or not 0<=v<(1<<17):raise RuntimeError('gate mask missing')
 return bool(v & (1<<8))  # slot 2 INSERT index 8
def stop():return macro('A_STOP','stop_feed_assist',SLOT=2)
def move(mode,length,allowed_heads):
 if mode not in (0,1) or type(length) is not int or not 1<=length<=STEP:raise ValueError('move outside bounds')
 stop_sent=False;ever_triggered=set()
 try:
  if not gate_present():raise RuntimeError('target gate absent')
  record(action='motion_request',mode=mode,length=length,speed=SPEED)
  macro('A_FEED','feed_or_rollback_raw',SLOT=2,SPEED=SPEED,LENGTH=length,MODE=mode)
  deadline=time.monotonic()+length/SPEED+.15
  while time.monotonic()<deadline:
   values=heads();current={i for i,v in enumerate(values) if v}
   ever_triggered.update(current)
   if current - allowed_heads:break  # STOP before any further query
   if not gate_present():raise RuntimeError('target gate lost during motion')
   time.sleep(.08)
  stop_sent=True;stop()
  values=heads()
  ever_triggered.update(i for i,v in enumerate(values) if v)
  if mode==1 and ever_triggered - allowed_heads:raise RuntimeError('unexpected head triggered during restoration')
  deadline=time.monotonic()+5
  while True:
   lane=status()['slots'][2]['slot_status']
   if lane=='ready':break
   if time.monotonic()>deadline:raise RuntimeError('STOP not followed by ready')
   time.sleep(.15)
  first=encoder();time.sleep(.25);second=encoder();lane=status()['slots'][2]['slot_status']
  if first!=second or lane!='ready' or not 0<abs(first)<=length*1.5+2:raise RuntimeError('invalid or unstable encoder')
  return first,sorted(ever_triggered)
 except BaseException as exc:
  if not stop_sent:
   stop_sent=True
   try:stop()
   except Exception as e:record(stop_error=repr(e))
  record(uncertain=repr(exc),mode=mode,length=length)
  raise

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--execute',action='store_true');args=parser.parse_args()
 if not args.execute:raise SystemExit('Review candidate; no action without --execute')
 marker=OUT/'started.marker'
 with marker.open('x') as f:f.write('single-use path probe\n')
 result={'complete':False,'forward_commanded':0,'reverse_commanded':0,
         'forward_encoder':0,'reverse_encoder':0,'forward_segments':0,'reverse_segments':0,
         'decoder_resolution_mm_per_count':1.2342,'triggered_heads':[]}
 def save(): (OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n')
 try:
  s=request('/multiace/api/state');a=s['aces'][0]
  if (s['printer_state'] not in ('idle','ready') or s['tag_op']['busy'] or s['swap_in_progress']
      or s['unload_all_active'] or a['firmware']!='V1.1.93O' or not a['connected']
      or a['status']!='ready' or a['fw_hold'] or a['dryer']['status']!='stop'
      or a['auto_dry_running'] or a['auto_dry']['enabled']):raise RuntimeError('device not safe/idle')
  if any(heads()):raise RuntimeError('head sensor not initially clear')
  if not gate_present():raise RuntimeError('target gate not present')
  identity=macro('A_DISCOVER','discover_device')
  if tuple(identity.get('uid%d'%i) for i in (1,2,3))!=UID:raise RuntimeError('wrong ACE UID')
  if any(x['slot_status']!='ready' for x in status()['slots']):raise RuntimeError('slot not ready')
  started=time.monotonic();forward_sign=None
  while (result['forward_commanded']+STEP <= MAX_MM
         and result['forward_encoder']+math.ceil(STEP*1.5+2) <= MAX_MM):
   if time.monotonic()-started>MAX_SECONDS:raise RuntimeError('path probe timed out')
   value,triggered=move(0,STEP,set())
   sign=1 if value>0 else -1
   if forward_sign is not None and sign!=forward_sign:raise RuntimeError('forward direction changed')
   forward_sign=sign;result['forward_commanded']+=STEP;result['forward_encoder']+=abs(value);result['forward_segments']+=1
   result['triggered_heads']=triggered;save()
   if result['forward_segments']==1 or result['forward_commanded']%100==0 or triggered:
    print(json.dumps({'forward_commanded':result['forward_commanded'],'forward_encoder':result['forward_encoder'],'triggered_heads':triggered}),flush=True)
   if triggered:break
  result['outcome']='sensor_triggered' if result['triggered_heads'] else 'no_trigger_within_bounded_range'
  # Return only distance confirmed by the filament encoder, using equally
  # bounded commands. Never proceed to this point after uncertain motion.
  remaining=max(0,min(int(result['forward_encoder']),result['forward_commanded'])-3);allowed=set(result['triggered_heads'])
  while remaining>0:
   if time.monotonic()-started>MAX_SECONDS:raise RuntimeError('restore timed out')
   length=min(STEP,remaining);value,triggered=move(1,length,allowed)
   if (1 if value>0 else -1)==forward_sign:raise RuntimeError('reverse direction invalid')
   result['reverse_commanded']+=length;result['reverse_encoder']+=abs(value);result['reverse_segments']+=1
   remaining-=length;save()
  result['encoder_residual']=result['forward_encoder']-result['reverse_encoder']
  final_heads=heads();final_status=status();final_state=request('/multiace/api/state')
  final_identity=macro('A_DISCOVER','discover_device')
  result['final_heads']=final_heads
  result['final_lanes']=[x.get('slot_status') for x in final_status['slots']]
  result['final_identity']=final_identity
  result['final_firmware']=final_state['aces'][0].get('firmware')
  result['final_fw_hold']=final_state['aces'][0].get('fw_hold')
  result['restoration_partial']=(any(final_heads) or abs(result['encoder_residual'])>10
      or any(x!='ready' for x in result['final_lanes'])
      or tuple(final_identity.get('uid%d'%i) for i in (1,2,3))!=UID
      or result['final_firmware']!='V1.1.93O' or result['final_fw_hold'] is not False)
  result['complete']=True;save()
 except BaseException as exc:
  result['error']=repr(exc);save();raise

if __name__=='__main__':main()
