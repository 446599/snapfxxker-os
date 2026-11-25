"""Review-only bounded ACE0 slot3 unwind; never runs without --execute."""
import argparse,json,pathlib,sys,time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/'diagnostic93/path-probe'))
import probe
def macro(name,label,**args):
 rows=probe.request('/server/gcode_store?count=1')['result']['gcode_store'];baseline=max((r['time'] for r in rows),default=0)
 probe.request('/multiace/api/macro',{'name':name,'args':{'ACE':0,**args}},timeout=4)
 end=time.monotonic()+3;prefix='// [A '+label+'] '
 while time.monotonic()<end:
  rows=probe.request('/server/gcode_store?count=30')['result']['gcode_store'];found=[r for r in rows if r['time']>baseline and r['message'].startswith(prefix)]
  if found:
   if len(found)!=1:raise RuntimeError('ambiguous reply')
   reply=json.loads(found[0]['message'][len(prefix):]);probe.record(command=name,args=args,reply=reply)
   expected={'A_DISCOVER':0,'A_INFO':7,'A_STATUS':6,'A_FEEDINFO':76,'A_FEED':8,'A_STOP':9,'A_KEYSTATE':73}[name]
   if reply.get('_v2_frame_valid') is not True or reply.get('_v2_payload_valid') is not True or reply.get('_v2_command')!=expected or reply.get('code')!=0:raise RuntimeError('unconfirmed '+name)
   return reply.get('result',{})
  time.sleep(.05)
 raise RuntimeError('missing reply '+name)
probe.macro=macro
OUT=pathlib.Path(__file__).resolve().parent/'run-002'
SLOT=3;STEP=20;SPEED=5;LIMIT=380;TIME_LIMIT=180

def stop(slot=SLOT):return probe.macro('A_STOP','stop_feed_assist',SLOT=slot)
def check():
 if any(probe.heads()):raise RuntimeError('head triggered')
 st=probe.status();lanes=[x.get('slot_status') for x in st['slots']]
 other=[i for i,x in enumerate(lanes) if i!=SLOT and x!='ready']
 if other:
  for i in other:stop(i)
  raise RuntimeError('other slot active: '+repr(other))
 key=probe.macro('A_KEYSTATE','get_key_state')['fields'].get('1')
 if type(key) is not int:raise RuntimeError('gate invalid')
 return lanes,bool(key & (1<<(4*SLOT)))
def verify():
 samples=[]
 for _ in range(3):
  lanes,gate=check()
  if lanes!=['ready']*4:raise RuntimeError('not ready after stop')
  feed=probe.macro('A_FEEDINFO','get_feed_info')
  if feed.get('_feed_wire_valid') is not True:raise RuntimeError('feed invalid')
  rows=[x for x in feed['feed_info'] if x.get('index')==SLOT]
  if len(rows)!=1 or rows[0].get('_wire_valid') is not True or rows[0].get('_decoder_present') is not True or type(rows[0].get('decoder')) is not int:raise RuntimeError('decoder missing')
  samples.append({'decoder':rows[0]['decoder'],'gate':gate,'lanes':lanes,'feed':feed})
  time.sleep(.25)
 if len({x['decoder'] for x in samples})!=1:raise RuntimeError('decoder unstable')
 return samples

def run():
 result={'commanded':0,'segments':[],'outcome':'uncertain'};start=time.monotonic();motion=False
 try:
  state=probe.request('/multiace/api/state');info=probe.macro('A_INFO','get_info');uid=probe.macro('A_DISCOVER','discover_device')
  if tuple(uid.get('uid%d'%i) for i in (1,2,3))!=probe.UID or info.get('firmware')!='V1.1.97O' or state['printer_state'] not in ('idle','ready') or state['aces'][0]['fw_hold'] or state['tag_op']['busy'] or state['swap_in_progress'] or state['calibration']['state']!='idle':raise RuntimeError('preflight failed')
  lanes,gate=check()
  if lanes!=['ready']*4:raise RuntimeError('preflight lanes not ready')
  stop();result['initial']=verify();gate=result['initial'][-1]['gate']
  while gate and result['commanded']<LIMIT:
   if time.monotonic()-start+STEP/SPEED+4>TIME_LIMIT:break
   motion=True;result['commanded']+=STEP
   probe.macro('A_FEED','feed_or_rollback_raw',SLOT=SLOT,SPEED=SPEED,LENGTH=STEP,MODE=1)
   deadline=time.monotonic()+STEP/SPEED+.5
   while time.monotonic()<deadline:
    lanes,gate=check()
    if not gate:break
    time.sleep(.08)
   stop();motion=False
   samples=verify();result['segments'].append(samples);gate=samples[-1]['gate']
   raw=samples[-1]['decoder'];signed=raw-(1<<64) if raw>=1<<63 else raw
   if not -30<=signed<0:raise RuntimeError('unexpected per-segment decoder '+str(signed))
   print(json.dumps({'segment':len(result['segments']),'commanded':result['commanded'],'gate':gate,'decoder':samples[-1]['decoder']}),flush=True)
  result['outcome']='gate_empty' if not gate else 'bounded_limit'
 except BaseException as exc:
  result['error']=repr(exc);result['motion_unconfirmed']=motion
  try:stop();result['exception_stop_ack']=True
  except Exception as stop_error:result['exception_stop_ack']=False;result['stop_error']=repr(stop_error)
 finally:
  (OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n')
 return result
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');a=p.parse_args()
 if not a.execute:raise SystemExit('review only')
 OUT.mkdir(exist_ok=False);(OUT/'once').write_text('single use\n');probe.OUT=OUT
 print(json.dumps(run()),flush=True)
