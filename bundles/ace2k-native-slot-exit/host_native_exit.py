"""Host-side native exit controller on Klipper's reactor, using its writer queue.

Only independently verified parked tips may enter begin_parked(). The trusted
route verifier must issue ParkedProof from real physical parking evidence and
fresh counters; UI-supplied flags, inlet present, and empty head alone do not
prove it. There is deliberately no public endpoint in this staging step.
"""
from dataclasses import dataclass
from math import isfinite
from cloud_route_owner import Route, RouteOwner
from native_eject_contract import Operation, outcome
from lane_contract import LaneSample

MOTION = frozenset(('feed_filament','unwind_filament','start_feed_assist',
                    'update_feeding_speed','update_unwinding_speed',
                    'tag_read_motion','tag_read_load','relax_buffer'))


@dataclass(frozen=True)
class ParkedProof:
    ace: int
    slot: int
    uid: str
    port: str
    transport: object
    encoder: int
    motor: int
    observed_at: float


@dataclass
class HostJob:
    claim: object
    proof: ParkedProof
    speed: int
    state: str = 'preparing'
    identity: dict = None
    pending: int = 0
    deadline: float = 0.
    prepare_deadline: float = 0.
    issued_at: float = 0.
    reason: str = ''


class HostNativeExit:
    def __init__(self, ace, path):
        self.ace=ace
        self.owner=RouteOwner(path)
        self.jobs={}
        self.serial=0
        self.timer=None

    def now(self):
        return self.ace.reactor.monotonic()

    def install_timer(self):
        if self.timer is None:
            self.timer=self.ace.reactor.register_timer(self.tick,self.now()+.1)

    def close(self):
        if self.timer is not None:
            self.ace.reactor.unregister_timer(self.timer)
            self.timer=None
        self.owner.close()  # on restart every retained claim is recovery

    def _same(self,p):
        a=self.ace
        return (a._connected_per_ace.get(p.ace) and a._serials.get(p.ace) is p.transport
                and p.ace < len(a._ace_devices) and str(a._ace_devices[p.ace]) == p.port
                and a._unit_key(p.ace)==p.uid
                and getattr(a._protocols.get(p.ace),'NAME','')=='ace2k')

    def _parked_route(self,p):
        a=self.ace
        if not self._same(p) or a._ace_mode!='multi' or not a.head_uses_ace(p.slot):
            return False
        ps=a.printer.lookup_object('print_stats',None)
        if ps is None or getattr(ps,'state','') not in ('standby','complete','cancelled','error'):
            return False
        if a._get_heads_for_ace_slot(p.ace,p.slot) or p.slot in a._bg_lanes_on(p.ace):
            return False
        if any(tuple(s)==(p.ace,p.slot) for s in a._bg_staged.values() if s):
            return False
        if p.slot in a._loading_heads:
            return False
        c=a._calibration_route_occupied()
        return not c or (c.get('ace'),c.get('slot'))!=(p.ace,p.slot)

    def begin_parked(self,p,speed):
        # This is an internal call, not a web API that may assert parking.
        if (not isinstance(p,ParkedProof) or type(p.ace) is not int or not 0<=p.ace<4
                or type(p.slot) is not int or not 0<=p.slot<4 or not p.uid.startswith('uid:')
                or type(p.encoder) is not int or type(p.motor) is not int
                or not -(2**31)<=p.encoder<2**31 or not 0<=p.motor<2**32
                or not isfinite(p.observed_at) or not 0<=self.now()-p.observed_at<=3.5
                or type(speed) is not int or not 9000<=speed<=80000 or not self._parked_route(p)):
            raise ValueError('live verified parked route required')
        claim=self.owner.reserve(Route(p.uid,p.port,p.slot,'',()))
        j=HostJob(claim,p,speed,prepare_deadline=self.now()+15,issued_at=self.now())
        self.jobs[claim.token]=j
        self._send(j,'prepare',dict(token=claim.token,uid=p.uid,index=p.slot,
                                    park_anchor=[p.encoder,p.motor]))
        return claim.token

    def _matches(self, j, data):
        return (isinstance(data,dict) and all(data.get(k)==v for k,v in
                dict(token=j.claim.token,uid=j.proof.uid,index=j.proof.slot).items())
                and type(data.get('seq')) is int and 1<=data['seq']<=255
                and isinstance(data.get('epoch'),str) and bool(data['epoch'])
                and (j.identity is None or all(data.get(k)==v for k,v in j.identity.items())))

    def _send(self,j,verb,params=None):
        if not self._same(j.proof):
            self._recover(j,'connection changed',stop=False)
            return
        self.serial+=1
        number=self.serial
        j.pending=number
        j.deadline=self.now()+5
        request={'method':'native_exit_'+verb,'params':dict(params if params is not None else j.identity)}
        controller=self
        def reply(self,response):
            if j.pending!=number:
                return  # stale callback must not complete a newer request
            j.pending=0
            try:
                controller._reply(j,verb,response)
            except Exception as exc:
                controller._recover(j,str(exc))
        try:
            self.ace.send_request_to(j.proof.ace,request,reply)
        except Exception as exc:
            self._recover(j,str(exc))

    def _recover(self,j,reason,stop=True):
        j.pending=0
        j.state='recovery'
        j.reason=str(reason)[:200]
        if j.claim.state!='recovery':
            j.claim=self.owner.hold_recovery(j.claim)
        sent=getattr(self.ace,'_lane_report_sent',None)
        if sent is not None: sent.pop(j.proof.ace,None)
        if stop and j.identity and self._same(j.proof):
            # Exact task STOP only. No automatic retries of START or release.
            self.ace.send_request_to(j.proof.ace,{'method':'native_exit_stop',
                                    'params':dict(j.identity)},None)

    def _complete_evidence(self,j,d):
        e=d.get('evidence')
        if not isinstance(e,dict) or e.get('verdict')!='complete':
            return False
        try:
            a,b=(LaneSample(**e[name]) for name in ('first','second'))
            op=Operation(j.proof.uid,j.identity['epoch'],j.proof.slot,j.identity['seq'],j.issued_at)
            return outcome(op,e['event'],a,b,event_received_at=e['event_at'],now=self.now(),
                           device_uid=j.proof.uid,connection_epoch=j.identity['epoch'])=='complete'
        except (KeyError,TypeError,ValueError):
            return False

    def _reply(self,j,verb,response):
        if not self._same(j.proof):
            self._recover(j,'connection changed',stop=False)
            return
        if (not isinstance(response,dict) or response.get('code')!=0
                or not self._matches(j,response.get('result'))):
            self._recover(j,'native reply unconfirmed')
            return
        d=response['result']; state=d.get('state')
        if j.identity is None:
            if verb!='prepare' or state!='preparing':
                self._recover(j,'prepare not confirmed'); return
            j.identity={k:d[k] for k in ('token','uid','epoch','index','seq')}
            return
        if verb=='abort' and state=='aborted' and j.claim.state=='reserved':
            self.owner.abandon_before_dispatch(j.claim)
            j.state='cancelled'; return
        if verb=='release' and state=='released' and j.claim.state=='verified':
            self.owner.release_verified(j.claim,lambda c:c.state=='verified')
            j.state='complete'; return
        if state=='ready' and j.claim.state=='reserved':
            if not self._parked_route(j.proof):
                self._send(j,'abort'); return
            j.claim=self.owner.mark_dispatched(j.claim)
            j.issued_at=self.now()
            j.state='starting'
            self._send(j,'start',dict(j.identity,speed_um_s=j.speed)); return
        if state=='verified' and j.claim.state=='dispatched':
            if not self._complete_evidence(j,d) or not isinstance(d.get('proof'),str) or not d['proof']:
                self._recover(j,'invalid terminal evidence'); return
            j.claim=self.owner.mark_verified(j.claim)
            j.state='releasing'
            self._send(j,'release',dict(j.identity,proof=d['proof'])); return
        if state in ('preparing','running','stopping','verifying') and not (
                state=='preparing' and j.claim.state!='reserved'):
            j.state=state
            return
        self._recover(j,'native exit '+str(state),stop=state not in ('stopped','recovery'))

    def tick(self,eventtime):
        for j in tuple(self.jobs.values()):
            if j.state in ('complete','cancelled','recovery'): continue
            if not self._same(j.proof):
                self._recover(j,'connection changed',stop=False)
            elif j.pending and eventtime>=j.deadline:
                self._recover(j,'native response timeout')
            elif not j.pending:
                if j.claim.state=='reserved' and eventtime>=j.prepare_deadline:
                    if j.identity: self._send(j,'abort')
                    else: self._recover(j,'prepare timed out')
                elif j.identity:
                    self._send(j,'status')
        return eventtime+.25

    def cancel(self,token):
        j=self.jobs.get(token)
        if j is None or j.state in ('complete','cancelled','releasing','recovery'):
            return False
        if j.identity is None:
            self._recover(j,'prepare cancelled')
            return True
        j.state='stopping'
        self._send(j,'abort' if j.claim.state=='reserved' else 'stop')
        return True

    def mask(self,idx,recovery_only=False):
        if not 0<=idx<len(self.ace._ace_devices): return 0
        uid=self.ace._unit_key(idx); port=str(self.ace._ace_devices[idx])
        out=0
        for c in self.owner.snapshot():
            if c.route.uid!=uid and c.route.port!=port: continue
            j=self.jobs.get(c.token)
            if recovery_only and j is not None and j.state!='recovery' and self._same(j.proof):
                continue
            out|=1<<c.route.lane
        return out

    def blocks(self,idx,request):
        if request.get('method') not in MOTION:
            return False
        mask=self.mask(idx)
        if not mask: return False
        p=request.get('params') or {}
        lane=p.get('index',p.get('slot'))
        return bool(mask & (1<<lane)) if type(lane) is int and 0<=lane<4 else True

    def status(self):
        out=[dict(token=j.claim.token,ace=j.proof.ace,slot=j.proof.slot,
                  state=j.state,reason=j.reason) for j in self.jobs.values()]
        for c in self.owner.snapshot():
            if c.token not in self.jobs:
                out.append(dict(token=c.token,uid=c.route.uid,slot=c.route.lane,state='recovery'))
        return out
