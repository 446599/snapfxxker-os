"""Separate one-shot restoration candidate, disabled until internal review."""
import argparse,json,pathlib,time
import probe
OUT=pathlib.Path(__file__).resolve().parent

def prepare():
 prior=json.loads((OUT/'result.json').read_text());check=json.loads((OUT/'recheck.json').read_text())
 if (prior.get('complete') is not False or prior.get('forward_commanded')!=325
      or prior.get('forward_encoder')!=304 or prior.get('reverse_commanded')!=0
      or check.get('telemetry_still') is not True or check.get('encoder1')!=4
      or check.get('encoder2')!=4 or check.get('heads')!=[False]*4
      or check.get('gate_present') is not True):
  raise RuntimeError('prior stopped position evidence does not match')
 return 305

def execute():
 # The immutable reviewed bound uses 330mm sent and 308mm accepted encoder,
 # with 3mm reserve. Cumulative quantization means residual is approximate.
 budget=prepare()
 s=probe.request('/multiace/api/state');a=s['aces'][0]
 if (s['printer_state'] not in ('idle','ready') or s['tag_op']['busy'] or s['swap_in_progress']
     or s['unload_all_active'] or a['firmware']!='V1.1.93O' or not a['connected']
     or a['status']!='ready' or a['fw_hold'] or a['dryer']['status']!='stop'
     or a['auto_dry_running'] or a['auto_dry']['enabled']):raise RuntimeError('device not idle')
 if any(probe.heads()) or not probe.gate_present():raise RuntimeError('head or gate unsafe')
 uid=probe.macro('A_DISCOVER','discover_device')
 if tuple(uid.get('uid%d'%i) for i in (1,2,3))!=probe.UID:raise RuntimeError('wrong ACE')
 if any(x['slot_status']!='ready' for x in probe.status()['slots']):raise RuntimeError('slot not ready')
 first=probe.encoder();time.sleep(.25);second=probe.encoder()
 if first!=4 or second!=4:raise RuntimeError('position evidence changed')
 with (OUT/'restore-started.marker').open('x') as f:f.write('single use\n')
 result={'complete':False,'commanded_mm':0,'encoder_mm':0,'segments':0,'start_encoder':308,'limit_mm':budget}
 def save():(OUT/'restore-result.json').write_text(json.dumps(result,indent=2)+'\n')
 try:
  while result['commanded_mm']<budget:
   length=min(5,budget-result['commanded_mm'])
   value,heads=probe.move(1,length,set())
   if value>=0 or heads:raise RuntimeError('wrong direction or head triggered')
   result['commanded_mm']+=length;result['encoder_mm']+=abs(value);result['segments']+=1;save()
   if result['commanded_mm']%100==0 or result['commanded_mm']==budget:
    print(json.dumps({'reverse_commanded':result['commanded_mm'],'reverse_encoder':result['encoder_mm']}),flush=True)
  result['encoder_residual']=308-result['encoder_mm'];result['final_heads']=probe.heads();result['final_status']=probe.status()
  state=probe.request('/multiace/api/state');result['final_device']={k:state['aces'][0].get(k) for k in ('firmware','status','connected','fw_hold','dryer')}
  result['restoration_partial']=(any(result['final_heads']) or abs(result['encoder_residual'])>10 or
      any(x.get('slot_status')!='ready' for x in result['final_status']['slots']))
  result['complete']=True;save()
 except BaseException as e:
  result['error']=repr(e);save();raise

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--execute',action='store_true');args=ap.parse_args()
 if not args.execute:raise SystemExit('review candidate; no motion without --execute')
 raise SystemExit('REVIEW INTERLOCK: restore remains disabled pending internal review')

if __name__=='__main__':main()
