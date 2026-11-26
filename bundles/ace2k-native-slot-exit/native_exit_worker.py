"""Private ACE2K writer-queue interface for native exit.

The HOST is responsible for verified physical parking/route, a durable token
before prepare and a durable dispatch marker before start. This transport
worker neither accepts UI requests nor determines physical tip location.
"""
from dataclasses import asdict, dataclass
import uuid
from .ace_protocol import still
from .native_eject_contract import Operation, fresh_sample
from .native_unload_wire import NativeUnloadWire

METHODS = ('native_exit_prepare', 'native_exit_status', 'native_exit_start',
           'native_exit_stop', 'native_exit_abort', 'native_exit_release')


@dataclass
class Job:
    token: str
    wire: object
    hold: object = None
    attempted: bool = False
    deadline: float = 0.
    timed_out: bool = False
    proof: str = ''
    anchor: tuple = None


class NativeExitWorker:
    def __init__(self, link, pack_block):
        self.link = link
        self.guard = link._native_exit
        self.pack = pack_block
        self.jobs = {}
        self.retired = set()  # no replay within this connection

    def _identity(self, job):
        op = job.wire.op
        return dict(token=job.token, uid=op.device_uid, epoch=op.connection_epoch,
                    index=op.lane, seq=op.seq)

    def _result(self, job, state, **more):
        return dict(self._identity(job), state=state, **more)

    def _job(self, p):
        lane = p.get('index')
        if (type(lane) is not int or not 0 <= lane < 4
                or type(p.get('seq')) is not int):
            raise ValueError('bad native task identity')
        job = self.jobs.get(lane)
        if job is None or any(p.get(k) != v for k,v in self._identity(job).items()):
            raise ValueError('stale native exit task')
        return job

    def _prepare(self, p):
        lane, token, uid = p.get('index'), p.get('token'), p.get('uid')
        if (type(lane) is not int or not 0 <= lane < 4 or not isinstance(token,str)
                or not token or len(token) > 128 or not isinstance(uid,str)):
            raise ValueError('invalid native exit identity')
        if token in self.retired:
            raise ValueError('native token already retired')
        if lane in self.jobs:
            job = self.jobs[lane]
            if job.token == token and job.wire.op.device_uid == uid:
                return self._status(job)
            raise ValueError('lane has another exit task')
        if any(j.token == token for j in self.jobs.values()):
            raise ValueError('native token already used on another lane')
        link = self.link
        op = Operation(uid, self.guard.epoch, lane, link._next_seq(lane), link._now())
        wire = NativeUnloadWire(link, op, lambda: self.jobs.get(lane) is job, self.pack)
        anchor = p.get('park_anchor')
        if anchor is not None and (not isinstance(anchor, list) or len(anchor) != 2
                or any(type(n) is not int for n in anchor)):
            raise ValueError('invalid parked counter anchor')
        job = Job(token, wire, anchor=tuple(anchor) if anchor is not None else None)
        self.jobs[lane] = job
        try:
            job.hold = self.guard.reserve(wire)
        except Exception:
            del self.jobs[lane]
            raise
        return self._result(job,'preparing')

    def _preflight(self, job):
        lane = job.wire.op.lane
        pair = self.guard._read_lane(job.hold)
        if pair is None:
            return False
        if job.anchor is not None and (pair[0].encoder, pair[0].motor) != job.anchor:
            raise RuntimeError('verified parking counters changed')
        first = self.guard._samples.get(lane)
        now = self.link._now()
        if first is not None and fresh_sample(first[0], after=job.hold.after, now=now):
            if still(first[0],pair[0]) and pair[0].present is True:
                return True
            if first[0].stamp == pair[0].stamp:
                return False
        self.guard.capture(job.hold)
        return False

    def _postproof(self, job):
        w, g = job.wire,self.guard
        pair = g._read_lane(job.hold)
        if w.event_at is None or pair is None:
            return None
        now = self.link._now()
        if not fresh_sample(pair[0], after=w.event_at, now=now):
            return None
        first = g._post.get(w.op.lane)
        if first is not None and fresh_sample(first[0], after=w.event_at, now=now):
            a,b = first[0],pair[0]
            verdict = w.verdict(a,b,now=now,device_uid=w.op.device_uid,connection_epoch=w.op.connection_epoch)
            if verdict in ('complete','cancelled'):
                return dict(event=dict(w.event), event_at=w.event_at, now=now,
                            first=asdict(a), second=asdict(b), verdict=verdict)
            if a.stamp == b.stamp:
                return None
        g.capture_after_event(job.hold)
        return None

    def _status(self, job):
        w=job.wire
        if not self.guard._same_hold(job.hold):
            return self._result(job,'recovery',reason='connection changed')
        if not job.attempted:
            return self._result(job,'ready' if self._preflight(job) else 'preparing')
        if w.state in ('new','refused'):
            return self._result(job,'not_started')
        if w.event is None:
            feed = self.link._feed
            seqs = feed[1].get('seq', ()) if self.link._fresh(feed) else ()
            if len(seqs) == 4 and seqs[w.op.lane] == w.op.seq:
                self.guard.query_last(job.hold)
        if w.event is None:
            if job.deadline and self.link._now() >= job.deadline and not job.timed_out:
                job.timed_out=True
                self.guard.cancel(job.hold,lane=w.op.lane,seq=w.op.seq,epoch=w.op.connection_epoch)
            return self._result(job,'recovery' if job.timed_out or w.state=='uncertain' else
                                'stopping' if w.state=='stopping' else 'running')
        if w.event.get('kind') not in (1,6):
            return self._result(job,'recovery',event=dict(w.event))
        proof = self._postproof(job)
        if proof is None:
            return self._result(job,'verifying',event=dict(w.event))
        if proof['verdict'] != 'complete':
            return self._result(job,'stopped',evidence=proof)
        if not job.proof:
            job.proof=uuid.uuid4().hex
        return self._result(job,'verified',proof=job.proof,evidence=proof)

    def _start(self, job, p):
        if job.attempted:
            return self._status(job)  # never replay a possible native start
        if not self._preflight(job):
            return self._result(job,'preparing')
        speed=p.get('speed_um_s')
        # Validate speed BEFORE setting attempted, using the original driver bounds.
        d=self.link.dictionary
        low=d.constant('ACE2K_LANE_SPEED_MIN_UM_S',9000)
        high=d.constant('ACE2K_LANE_SPEED_MAX_UM_S',70000)
        if type(speed) is not int or not low <= speed <= high or speed <= 0:
            raise ValueError('native unload speed out of bounds')
        job.attempted=True
        job.deadline=self.link._now()+max(30.,4_000_000./speed+15.)
        try:
            result=self.guard.start(job.hold,speed)
        except Exception:
            # A failed write may have moved the filament. The wire already
            # issues targeted STOP on uncertainty; retain the exact job.
            if job.wire.state == 'new':
                return self._result(job,'not_started')
            return self._result(job,'recovery',reason='native start unconfirmed')
        if result == 'started':
            return self._result(job,'running')
        return self._result(job,'not_started' if result=='refused' else 'recovery')

    def _abort(self,job):
        # No native frame was sent, or the MCU explicitly refused it. The
        # caller must first settle the corresponding durable no-motion record.
        if job.wire.state == 'refused':
            # Refused start may have other native activity; leave for recovery.
            return self._result(job,'recovery',reason='native start refused')
        if job.wire.state != 'new':
            return self._result(job,'recovery',reason='native motion may have started')
        self.guard.release_before_start(job.hold)
        result=self._result(job,'aborted')
        self.retired.add(job.token)
        del self.jobs[job.wire.op.lane]
        return result

    def request(self, method, p):
        """Called only from existing link._execute while link._io is held."""
        with self.link._io:
            if not isinstance(p,dict):
                raise ValueError('bad native exit request')
            if method == 'native_exit_prepare':
                return self._prepare(p)
            job=self._job(p)
            if method == 'native_exit_status':
                return self._status(job)
            if method == 'native_exit_start':
                return self._start(job,p)
            if method == 'native_exit_stop':
                w=job.wire
                ack=self.guard.cancel(job.hold,lane=w.op.lane,seq=w.op.seq,epoch=w.op.connection_epoch)
                return self._result(job,'stopping' if ack else 'recovery',stop_ack=ack)
            if method == 'native_exit_abort':
                return self._abort(job)
            if method == 'native_exit_release':
                # Private coordinator acknowledgement of durable completion,
                # NOT a public proof supplied by the screen. Recheck native
                # evidence synchronously before dropping the link hold.
                if not job.proof or p.get('proof') != job.proof:
                    raise ValueError('invalid completion proof')
                if not self.guard.finish(job.hold,lambda *e: True):
                    return self._result(job,'recovery',reason='completion no longer confirmed')
                result=self._result(job,'released')
                self.retired.add(job.token)
                del self.jobs[job.wire.op.lane]
                return result
            raise ValueError('unknown native exit method')
