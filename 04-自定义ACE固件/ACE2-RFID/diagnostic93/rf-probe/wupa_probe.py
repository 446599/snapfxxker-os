"""PRE-REVIEW 93O WUPA diagnostic; no motor, heater or tag-memory writes.

Execution stays hard-disabled until RF buffer/cleanup review. `classify` is a
conservative provisional contract, not proof of a physically absent tag.
"""
import argparse, json, pathlib, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/'minimal-repair/live'))
import ace2_ota as ota

UID=(808780566,17445170,942880330)
PORT='/dev/serial/by-path/platform-fed00000.usb-usb-0:1.3:1.0'
OUT=pathlib.Path(__file__).resolve().parent

def classify(*, rpc_ok, status=None, irq=None, error=None, bits=None, data=None):
    if rpc_ok is not True:return {'kind':'transport_error'}
    if any(type(x) is not int or not 0<=x<=255 for x in (status,irq,error)):
        return {'kind':'ambiguous','reason':'missing register evidence'}
    if irq==255 or error==255:return {'kind':'ambiguous','reason':'invalid register'}
    if status==2 or error&0x08:return {'kind':'collision'}
    if error:return {'kind':'rf_error','error':error}
    if status==0:
        if bits==16 and isinstance(data,bytes) and len(data)==2 and (irq&0x20):
            return {'kind':'atqa','atqa_hex':data.hex().upper()}
        return {'kind':'ambiguous','reason':'short or inconsistent frame'}
    if status==0xee and (irq&1) and not (irq&0x20):
        return {'kind':'no_response','reason':'reader timer expired without RX; not proof of no physical tag'}
    return {'kind':'ambiguous','reason':'unclassified transceive outcome'}

class Probe:
    def __init__(self,t):self.t=t
    def send(self,cmd,payload=b'',timeout=2):
        if cmd not in (0,5,6,14,68):raise ValueError('command outside read-only RF scope')
        packets=self.t.send_recv(cmd,payload,timeout=timeout);seq=self.t._seq
        if len(packets)!=1 or not packets[0]['is_resp'] or packets[0]['seq']!=seq:
            raise RuntimeError('RPC response missing or mismatched')
        return ota.pb_decode(packets[0]['payload'])
    def raw(self,reader,op,a=0,b=0):
        if reader not in (0,1) or op not in (0,1,2,3,4,5,6,10):raise ValueError('invalid op')
        if not 0<=a<=255 or not 0<=b<=255:raise ValueError('invalid argument')
        word=0x80000000|(reader<<24)|(op<<16)|(a<<8)|b
        fields=self.send(68,ota._field_uint32(1,word))
        if ota.get_field(fields,1,None)!=word:raise RuntimeError('tunnel index mismatch')
        return ota.get_field(fields,12,0)
    def read(self,reader,reg):return self.raw(reader,0,reg)
    def write(self,reader,reg,value):
        # Register operations only. No tag WRITE command, only WUPA in FIFO.
        if reg not in (1,2,4,8,10,13,18,19,42,43,44,45):raise ValueError('register not permitted')
        if self.raw(reader,1,reg,value)!=0:raise RuntimeError('register write refused')
    def reader_state(self,reader):return self.raw(0,10,reader*2,3)
    def enable(self,reader,enabled):
        f=self.send(14,ota._field_uint32(1,reader*2)+ota._field_uint32(2,int(enabled)))
        if ota.get_field(f,1,0)!=0:raise RuntimeError('RFID enable rejected')
    def await_state(self,reader,disabled):
        end=time.monotonic()+8
        while time.monotonic()<end:
            v=self.reader_state(reader)
            if (v==8)==disabled:return v
            time.sleep(.1)
        raise RuntimeError('reader state transition not confirmed')
    def wupa(self,reader):
        # op6 performs existing bounded initialization/select and powers antenna.
        # It is not interpreted as no-card evidence.
        select=self.raw(reader,6)
        snapshot={reg:self.read(reader,reg) for reg in (1,2,8,13,18,19,42,43,44,45)}
        if any(type(v) is not int or v==255 for v in snapshot.values()) or snapshot[8]&8:
            raise RuntimeError('register snapshot invalid or crypto active')
        try:
            self.write(reader,1,0); self.write(reader,8,snapshot[8]&~8)
            self.write(reader,18,snapshot[18]&~0x80);self.write(reader,19,snapshot[19]&~0x80)
            self.write(reader,13,(snapshot[13]&~0x87)|7)
            # Reuse op6's native timer configuration; do not synthesize timer constants.
            if self.raw(reader,2,0,0x52)!=0:raise RuntimeError('staging failed')
            status=self.raw(reader,3,1,0x0c)
            irq=self.read(reader,4);error=self.read(reader,6)
            bits=self.raw(reader,5) if status==0 else None
            data=bytes((self.raw(reader,4,0),self.raw(reader,4,1))) if status==0 and bits==16 else None
            return {'select_for_power':select,'status':status,'irq':irq,'error':error,'bits':bits,
                    'data_hex':None if data is None else data.hex(),
                    'classification':classify(rpc_ok=True,status=status,irq=irq,error=error,bits=bits,data=data)}
        finally:
            # Snapshot/restore modes; command forced idle rather than reviving an old transaction.
            cleanup_errors=[]
            for reg,value in [(1,0)]+[(r,snapshot[r]) for r in (8,13,18,19,42,43,44,45,2)]:
                try:self.write(reader,reg,value)
                except Exception as exc:cleanup_errors.append((reg,repr(exc)))
            if cleanup_errors:raise RuntimeError('RF register restore unconfirmed: %r' % cleanup_errors)

def run_exclusive(guard, transport_factory=ota.ACE2Transport):
    """Release/resume host and restore original reader settings even on failure."""
    guard.state(); released=False; t=None; original={}; changed=[]; observations=[]
    failures=[]
    try:
        released=True
        guard.request('/printer/gcode/script',{'script':'ACE_FW_RELEASE ACE=0'})
        guard.state(held=True)
        t=transport_factory(PORT);p=Probe(t)
        d=p.send(0);identity=tuple(ota.get_field(d,i,None) for i in (1,2,3))
        if identity!=UID:raise RuntimeError('ACE UID mismatch')
        vf=p.send(5); raw_version=ota.get_field(vf,1,b'')
        if not isinstance(raw_version,bytes) or raw_version.decode(errors='replace')!='V1.1.93O':
            raise RuntimeError('firmware mismatch')
        version='V1.1.93O'
        for reader in (0,1):
            state=p.reader_state(reader)
            if type(state) is not int or not 0<=state<=8:raise RuntimeError('invalid initial reader state')
            original[reader]=(state!=8)
        for reader in (0,1):
            if original[reader]:
                changed.append(reader) # record before send: ACK can be lost
                p.enable(reader,False)
            p.await_state(reader,True)
        for reader in (0,1):
            observations.append({'reader':reader,'result':p.wupa(reader)})
            (OUT/'probe-observations.json').write_text(json.dumps(observations,indent=2))
    finally:
        if t is not None:
            for reader in reversed(changed):
                try:
                    p.enable(reader,True);p.await_state(reader,False)
                except Exception as e:failures.append({'reader':reader,'error':repr(e)})
            t.close()
        if released and not failures:
            try:guard.request('/printer/gcode/script',{'script':'ACE_FW_RESUME ACE=0'})
            except Exception as e:failures.append({'resume_error':repr(e)})
        elif released:
            failures.append({'resume_skipped':'reader restoration unconfirmed; device held'})
        (OUT/'probe-cleanup.json').write_text(json.dumps({'original':original,'changed':changed,'failures':failures},indent=2))
    if failures:raise RuntimeError('reader or host restoration unconfirmed')
    return {'uid':identity,'version':version,'reader_initial_enabled':original,'observations':observations}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--execute',action='store_true');args=parser.parse_args()
    if not args.execute:raise SystemExit('offline review only')
    raise SystemExit('REVIEW INTERLOCK: RF action disabled pending owner approval')
if __name__=='__main__':main()
