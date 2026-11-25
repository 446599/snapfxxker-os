"""Only CMD 0, 5, 6, 76, under existing FW release/resume; no motion."""
import json,time,pathlib
import bench_flash as guard
import ace2_ota as ota

out=pathlib.Path(__file__).with_name('direct-read-results.jsonl')
def save(row):
 with out.open('a') as f:f.write(json.dumps(row)+'\n');f.flush()
def main():
 guard.state();released=False;t=None
 try:
  released=True;guard.request('/printer/gcode/script',{'script':'ACE_FW_RELEASE ACE=0'});guard.state(held=True)
  t=ota.ACE2Transport(guard.PORT)
  p=t.send_recv(0,b'',timeout=2);assert len(p)==1 and p[0]['is_resp'] and p[0]['seq']==t._seq
  fields=ota.pb_decode(p[0]['payload']);uid=tuple(ota.get_field(fields,i) for i in (1,2,3));assert uid==guard.UID
  version=ota.get_ace_version(t);assert version and version[0]=='V1.1.93O'
  save({'event':'identity','uid':uid,'version':version})
  for i in range(6):
   for cmd in (6,76):
    start=time.monotonic(); packets=t.send_recv(cmd,b'',timeout=1.0);seq=t._seq
    row={'iteration':i,'cmd':cmd,'request_seq':seq,'elapsed_ms':round((time.monotonic()-start)*1000,1),
         'response_count':len(packets),'response_seqs':[p['seq'] for p in packets],
         'valid':len(packets)==1 and packets[0]['is_resp'] and packets[0]['seq']==seq}
    save(row);print(json.dumps(row),flush=True)
 finally:
  if t is not None:t.close()
  if released:guard.request('/printer/gcode/script',{'script':'ACE_FW_RESUME ACE=0'})
if __name__=='__main__':main()
