"""PRE-REVIEW DRAFT. Exclusive native insert observation; never starts motion.

Run only after external physical 2m clearance and script review. Not executed.
"""
import argparse, json, pathlib, sys, time, urllib.request
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/'minimal-repair/live'))
import ace2_ota as ota

HOST='http://127.0.0.1'
PORT='/dev/serial/by-path/platform-fed00000.usb-usb-0:1.3:1.0'
UID=(808780566,17445170,942880330)
OUT=pathlib.Path(__file__).resolve().parent/'native-probe.jsonl'
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))

def http(path, payload=None):
    req=urllib.request.Request(HOST+path,data=None if payload is None else json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
    with opener.open(req,timeout=1.0) as r:return json.load(r)
def log(event, **kwargs):
    with OUT.open('a') as f:f.write(json.dumps({'event':event,'monotonic':time.monotonic(),**kwargs})+'\n')
def send(t,cmd,payload=b''):
    packets=t.send_recv(cmd,payload,timeout=.5)
    if len(packets)!=1 or not packets[0]['is_resp']:
        raise RuntimeError('missing or ambiguous response cmd=%s'%cmd)
    return ota.pb_decode(packets[0]['payload'])
def sensor_ok():
    names='&'.join('filament_motion_sensor%%20e%d_filament'%i for i in range(4))
    status=http('/printer/objects/query?print_stats&virtual_sdcard&webhooks&'+names)['result']['status']
    return (status['print_stats']['state']=='standby' and not status['virtual_sdcard']['is_active']
            and status['webhooks']['state']=='ready'
            and all(status['filament_motion_sensor e%d_filament'%i]['filament_detected'] is False for i in range(4)))
def status(t):
    f=send(t,6); slots=f.get(9,[])
    if len(slots)!=4 or any(w!=2 for w,_ in slots):raise RuntimeError('invalid slot status')
    return [ota.get_field(ota.pb_decode(payload),1,0) for _,payload in slots]
def encoder(t):
    f=send(t,76); rows=f.get(1,[])
    if len(rows)!=4 or any(w!=2 for w,_ in rows):raise RuntimeError('invalid encoder rows')
    data=ota.pb_decode(rows[2][1]);v=ota.get_field(data,3,None)
    if not isinstance(v,int):raise RuntimeError('missing encoder')
    return v-(1<<64) if v >= 1<<63 else v

def monitor(t, get_status, get_encoder, head_ok, now, pause, record, timeout=65.0):
    """Observe one native insert; no FEED or heat. Caller owns exclusive port.

    Returns only after observed motion and two ready/stable samples. Any
    uncertainty requests one STOP and raises; caller must not auto-resume.
    """
    deadline=now()+timeout
    seen=False; stable=[]; stop_sent=False
    def stop_once(reason):
        nonlocal stop_sent
        if not stop_sent:
            stop_sent=True
            record('stop_requested', reason=reason)
            send(t,9,ota._field_uint32(1,2))
    try:
        while now()<deadline:
            if head_ok() is not True: raise RuntimeError('head sensor or printer unsafe')
            states=get_status(t); count=get_encoder(t)
            if len(states)!=4 or any(states[i]!=0 for i in (0,1,3)):
                raise RuntimeError('non-target slot active')
            if states[2] not in (0,1,2,5):raise RuntimeError('target abnormal state')
            if type(count) is not int or abs(count)>1500:
                raise RuntimeError('encoder invalid or budget reached')
            if states[2] in (1,2,5): seen=True
            record('sample',states=states,decoder=count)
            if seen and states[2]==0:
                stable.append((now(),count));stable=stable[-2:]
                if len(stable)==2 and stable[1][0]-stable[0][0]>=.2 and stable[0][1]==stable[1][1]:
                    return {'motion_seen':True,'decoder':count,'stop_sent':False}
            else: stable=[]
            pause(.25)
        raise RuntimeError('motion not observed or did not complete')
    except BaseException as exc:
        try: stop_once(str(exc))
        except BaseException as stop_exc: record('stop_unconfirmed',error=str(stop_exc))
        raise

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--execute',action='store_true');ap.add_argument('--confirmed-2m',action='store_true');args=ap.parse_args()
    if not (args.execute and args.confirmed_2m):raise SystemExit('PRE-REVIEW: requires explicit execute and confirmed physical 2m')
    raise SystemExit('PRE-REVIEW INTERLOCK: execution disabled until script and emergency recovery are reviewed')

if __name__=='__main__':main()
