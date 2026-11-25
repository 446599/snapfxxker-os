import json,pathlib,sys,time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'path-probe'))
import probe
out=pathlib.Path(__file__).with_name('http-read-results.jsonl')
for i in range(6):
 for name,label in [('A_STATUS','get_status'),('A_FEEDINFO','get_feed_info')]:
  start=time.monotonic()
  try:r=probe.macro(name,label);row={'ok':True,'value':r}
  except Exception as e:row={'ok':False,'error':repr(e)}
  row.update(i=i,name=name,elapsed_ms=round((time.monotonic()-start)*1000,1))
  with out.open('a') as f:f.write(json.dumps(row)+'\n')
  print(i,name,row['ok'],row['elapsed_ms'],flush=True)
