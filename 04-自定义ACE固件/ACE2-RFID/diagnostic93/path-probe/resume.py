"""One-shot continuation from independently confirmed 330-command/308-encoder state."""
import argparse,json,math,pathlib,time
import probe
OUT=pathlib.Path(__file__).resolve().parent
READS=frozenset(('A_STATUS','A_FEEDINFO','A_KEYSTATE'))
original_macro=probe.macro

def retry_reads(name,label,**kwargs):
    try:return original_macro(name,label,**kwargs)
    except Exception:
        if name not in READS:raise
        probe.record(read_retry=name)
        time.sleep(.25)
        return original_macro(name,label,**kwargs)

def prepare():
    prior=json.loads((OUT/'result.json').read_text());check=json.loads((OUT/'recheck.json').read_text())
    if (prior.get('complete') is not False or prior.get('forward_commanded')!=325
        or prior.get('forward_encoder')!=304 or prior.get('reverse_commanded')!=0
        or check.get('telemetry_still') is not True or check.get('encoder1')!=4
        or check.get('encoder2')!=4 or check.get('heads')!=[False]*4
        or check.get('gate_present') is not True):raise RuntimeError('prior position evidence mismatch')
    return {'forward_commanded':330,'forward_encoder':308,'forward_segments':66,
            'reverse_commanded':0,'reverse_encoder':0,'reverse_segments':0,
            'triggered_heads':[],'complete':False,'prior_evidence':'result.json + recheck.json'}

def execute():
    result=prepare();probe.macro=retry_reads
    s=probe.request('/multiace/api/state');a=s['aces'][0]
    if (s['printer_state'] not in ('idle','ready') or s['tag_op']['busy'] or s['swap_in_progress']
        or s['unload_all_active'] or a['firmware']!='V1.1.93O' or not a['connected']
        or a['status']!='ready' or a['fw_hold'] or a['dryer']['status']!='stop'
        or a['auto_dry_running'] or a['auto_dry']['enabled']):raise RuntimeError('device not idle')
    if any(probe.heads()) or not probe.gate_present():raise RuntimeError('head or gate unsafe')
    uid=probe.macro('A_DISCOVER','discover_device')
    if tuple(uid.get('uid%d'%i) for i in (1,2,3))!=probe.UID:raise RuntimeError('wrong ACE')
    if any(x['slot_status']!='ready' for x in probe.status()['slots']):raise RuntimeError('slot not ready')
    a=probe.encoder();time.sleep(.25);b=probe.encoder()
    if a!=4 or b!=4:raise RuntimeError('prior stopped counter changed')
    with (OUT/'resume-started.marker').open('x') as f:f.write('single-use continuation\n')
    def save():(OUT/'resume-result.json').write_text(json.dumps(result,indent=2)+'\n')
    started=time.monotonic()
    try:
        while (result['forward_commanded']+5 <= 600 and
               result['forward_encoder']+math.ceil(5*1.5+2) <= 600):
            if time.monotonic()-started>probe.MAX_SECONDS:raise RuntimeError('probe timeout')
            before=probe.heads()
            if any(before):
                result['triggered_heads']=[i for i,v in enumerate(before) if v]
                save()
                print(json.dumps({'forward_commanded':result['forward_commanded'],'forward_encoder':result['forward_encoder'],'triggered_heads':result['triggered_heads']}),flush=True)
                break
            value,triggered=probe.move(0,5,set())
            if value<=0:raise RuntimeError('forward direction changed')
            result['forward_commanded']+=5;result['forward_encoder']+=value
            result['forward_segments']+=1;result['triggered_heads']=triggered;save()
            if result['forward_commanded']%100==0 or triggered:
                print(json.dumps({'forward_commanded':result['forward_commanded'],'forward_encoder':result['forward_encoder'],'triggered_heads':triggered}),flush=True)
            if triggered:break
        result['outcome']='sensor_triggered' if result['triggered_heads'] else 'no_trigger_within_bounded_range'
        remaining=max(0,min(result['forward_commanded'],result['forward_encoder'])-3)
        allowed=set(result['triggered_heads'])
        while remaining>0:
            if time.monotonic()-started>probe.MAX_SECONDS:raise RuntimeError('restore timeout')
            length=min(5,remaining);value,triggered=probe.move(1,length,allowed)
            if value>=0:raise RuntimeError('reverse direction changed')
            result['reverse_commanded']+=length;result['reverse_encoder']+=abs(value)
            result['reverse_segments']+=1;remaining-=length;save()
            if result['reverse_commanded']%100==0 or not remaining:
                print(json.dumps({'reverse_commanded':result['reverse_commanded'],'reverse_encoder':result['reverse_encoder']}),flush=True)
        result['encoder_residual']=result['forward_encoder']-result['reverse_encoder']
        result['final_heads']=probe.heads();result['final_lanes']=[x.get('slot_status') for x in probe.status()['slots']]
        final=probe.request('/multiace/api/state')['aces'][0];result['final_firmware']=final.get('firmware');result['final_fw_hold']=final.get('fw_hold')
        uid=probe.macro('A_DISCOVER','discover_device');result['final_uid']=[uid.get('uid%d'%i) for i in (1,2,3)]
        result['restoration_partial']=(any(result['final_heads']) or abs(result['encoder_residual'])>10 or
             any(x!='ready' for x in result['final_lanes']) or result['final_firmware']!='V1.1.93O' or
             result['final_fw_hold'] is not False or tuple(result['final_uid'])!=probe.UID)
        result['complete']=True;save()
    except BaseException as exc:
        result['error']=repr(exc);save();raise

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--execute',action='store_true');args=ap.parse_args()
    if not args.execute:raise SystemExit('review candidate; no motion without --execute')
    execute()
if __name__=='__main__':main()
