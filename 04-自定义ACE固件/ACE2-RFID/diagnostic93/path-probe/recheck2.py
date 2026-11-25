import json,pathlib,time,probe
result={}; errors=[]
def retry(fn):
 for i in range(3):
  try:return fn()
  except Exception as e: errors.append(repr(e));time.sleep(.4)
 raise RuntimeError('read-only retries exhausted')
try:
 result['heads']=retry(probe.heads);result['status1']=retry(probe.status);result['encoder1']=retry(probe.encoder);time.sleep(.5);result['encoder2']=retry(probe.encoder);result['status2']=retry(probe.status);result['gate_present']=retry(probe.gate_present)
 s=retry(lambda:probe.request('/multiace/api/state'));a=s['aces'][0];result['device']={k:a.get(k) for k in ('firmware','status','connected','fw_hold','dryer')};result['printer_state']=s['printer_state']
 result['telemetry_still']=result['encoder1']==result['encoder2'] and all(x['slot_status']=='ready' for x in result['status1']['slots']) and all(x['slot_status']=='ready' for x in result['status2']['slots'])
except Exception as e:result['error']=repr(e)
result['read_errors']=errors
pathlib.Path(__file__).with_name('recheck2.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:result.get(k) for k in ('heads','encoder1','encoder2','gate_present','telemetry_still','device','error')}))
