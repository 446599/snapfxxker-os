"""Material batch service on the real Klipper reactor.

Preview is read-only, with a single-use ticket bound to live unit, connection,
slot, source and native counter evidence. The operator must visually confirm
that an otherwise untracked tip is in its own branch, before the shared tube.
The confirmation is not a sensor verdict: later movement/connection change
invalidates it. Actual ejection completion comes only from HostNativeExit.

Toolhead motion goes through the existing Klipper G-code mutex. Independently
parked native exits use the existing writer queue and do not wait for one
another or for toolhead G-code to finish.
"""
from dataclasses import dataclass, replace
from functools import wraps
from math import isfinite
from uuid import uuid4
import json
import logging
from host_native_exit import ParkedProof
from park_return import ParkReturnStore, encoder_delta_mm, encoder_distance_mm

ACTIVE = frozenset(('queued','sampling','head_running','native','stopping','awaiting_parking','recovery'))
ACTIONS = frozenset(('load','unload','eject'))
TERMINAL = frozenset(('complete','cancelled','rejected','failed'))
PROBE_MM = 150
RETURN_WINDOW_MM = 5


def park_command(fn):
    """Expected user, telemetry and storage failures are G-code errors."""
    @wraps(fn)
    def command(self,gcmd):
        try:
            return fn(self,gcmd)
        except (ValueError, RuntimeError, OSError) as exc:
            raise gcmd.error(str(exc)) from exc
    return command


def index(n):
    return type(n) is int and 0 <= n < 4


@dataclass(frozen=True)
class Selection:
    ace: int
    slot: int
    uid: str
    port: str
    link: object
    route: str
    anchor: tuple = None


@dataclass
class Task:
    id: str
    batch: str
    action: str
    selection: Selection
    state: str = 'queued'
    detail: str = ''
    token: str = ''
    deadline: float = 0.
    sample_request: object = None
    cancel_requested: bool = False

    def public(self):
        return dict(id=self.id,batch=self.batch,action=self.action,
                    ace=self.selection.ace,slot=self.selection.slot,
                    uid=self.selection.uid,state=self.state,detail=self.detail,
                    cancel_mode=('after_head' if self.state=='head_running' else
                                 'none' if self.state=='recovery' or self.state in TERMINAL else 'targeted'))


@dataclass
class ThermalReturn:
    selection: Selection
    direction: int
    result: tuple = None


class MaterialBatchRuntime:
    TICKET_TTL = 120.
    MAX_PREVIEWS = 32

    def __init__(self, ace, native):
        self.ace, self.native = ace,native
        self.tickets={}
        self.tasks={}
        self.timer=None
        self.head_running=None
        self._owns_head_motion=False
        self._park_reference=None
        self._loaded_park={}
        self._stopped_park={}
        self.thermal_return=None

    def now(self):
        return self.ace.reactor.monotonic()

    def install(self):
        if self.timer is None:
            self.timer=self.ace.reactor.register_timer(self.tick,self.now()+.25)

    def close(self):
        if self.timer is not None:
            self.ace.reactor.unregister_timer(self.timer)
            self.timer=None
        self.tickets.clear()
        self._park_reference=None
        self._loaded_park.clear()
        self._stopped_park.clear()
        self.thermal_return=None

    def _print_idle(self):
        a=self.ace
        ps=a.printer.lookup_object('print_stats',None)
        return (ps is not None and ps.get_status(self.now()).get('state') in
                ('standby','complete','cancelled','error')
                and a._ace_mode=='multi')

    def _idle(self):
        return self._print_idle() and not getattr(self.ace,'_homing_active',False)

    def _own_homing(self):
        # The batch's own blocking G-code yields to reactor timers during
        # homing. That is not an external print or a new batch admission.
        return (self._owns_head_motion and self._print_idle()
                and getattr(self.ace,'_homing_active',False))

    def _identity(self,ace,slot,uid):
        a=self.ace
        if (not index(ace) or not index(slot) or not isinstance(uid,str)
                or not uid.startswith('uid:') or len(uid)>128
                or ace>=len(a._ace_devices)
                or not a._connected_per_ace.get(ace) or not a.head_uses_ace(slot)
                or a._unit_key(ace)!=uid or getattr(a._protocols.get(ace),'NAME','')!='ace2k'
                or a._serials.get(ace) is None):
            raise ValueError('Selected ACE/slot is unavailable or identity changed')
        return (str(a._ace_devices[ace]), a._serials[ace])

    def _same(self,s):
        try:
            port,link=self._identity(s.ace,s.slot,s.uid)
            return port==s.port and link is s.link
        except ValueError:
            return False

    def ota_motion_idle(self):
        """Report actual outstanding motion/recovery rather than invented legacy fields."""
        a=self.ace
        return not (self.head_running is not None
                    or any(t.state in ACTIVE for t in self.tasks.values())
                    or a._loading_heads or a._bg_staged or a._retract_pending
                    or a._calibration_route_occupied())

    def head_park_plan(self,head,requested_ace,requested_slot,override):
        """Resolve the actual source and select its return policy."""
        a=self.ace
        if (a._ace_mode!='multi' or not a.head_uses_ace(head)
                or getattr(a,'_swap_in_progress',False)):
            return None,override
        source=a._head_source.get(head)
        staged=a._bg_staged.get(head)
        route=((source.get('ace_index'),source.get('slot'))
               if isinstance(source,dict) else staged)
        detected=a._head_sensor_present(head)
        if route is None:
            if (detected is False and requested_ace is None
                    and requested_slot is None):
                return None,override  # ordinary empty-head no-op
            if not any(getattr(p,'NAME','')=='ace2k'
                       for p in a._protocols.values()):
                return None,override
            raise ValueError('Unconfirmed head source; inspect before returning filament')
        if (not isinstance(route,(tuple,list)) or len(route)!=2
                or not all(index(x) for x in route) or route[1]!=head):
            raise ValueError('Unconfirmed Multi mode head route')
        ace,slot=route
        if getattr(a._protocols.get(ace),'NAME','')!='ace2k':
            if not a._connected_per_ace.get(ace):
                raise ValueError('Selected ACE is offline')
            return None,override
        if ((requested_ace is not None or requested_slot is not None)
                and (requested_ace,requested_slot)!=(ace,slot)):
            raise ValueError('Requested lane differs from the actual head source')
        if detected is not True or (isinstance(source,dict)
                                    and (source.get('load_failed') or source.get('gate_empty'))):
            raise ValueError('Head/route position is not confirmed; inspect before retry')
        if self.native.mask(ace)&(1<<slot):
            raise ValueError('Selected lane has an unfinished native exit or recovery')
        uid=a._unit_key(ace)
        port,link=self._identity(ace,slot,uid)
        selection=Selection(ace,slot,uid,port,link,'head_unload')
        if source and self._print_idle() and not override:
            captured=self._loaded_park.get(head)
            if (captured is not None and (captured.ace,captured.slot)==(ace,slot)
                    and self._park_link_intact(captured)):
                return captured,PROBE_MM
        length=self._park_length(selection)
        if override not in (0,length):
            raise ValueError('Requested retract length differs from verified ACE park length')
        return selection,length

    def _park_store(self):
        import os
        return ParkReturnStore(os.path.join(self.ace.paths['config_dir'],
            'extended','multiace','ace_park_return.json'))

    def _park_length(self,s):
        return self._park_store().length(s.uid,s.slot)

    def _park_link_intact(self, selection):
        gates = self.ace._gate_status_per_ace.get(selection.ace, ())
        return (self._same(selection)
                and (len(gates) <= selection.slot or gates[selection.slot] != 0))

    def capture_load_park(self, head, ace, slot, staged):
        """Sample at the existing load boundary, before any material moves."""
        self._loaded_park.pop(head, None)
        a = self.ace
        if (not self._print_idle() or a._swap_in_progress or staged
                or head != slot or not a.head_uses_ace(head)
                or getattr(a._protocols.get(ace), 'NAME', '') != 'ace2k'
                or a._head_sensor_present(head) is not False or a._head_source.get(head)
                or self.native.mask(ace) or a._bg_lanes_on(ace)):
            return None
        uid = a._unit_key(ace)
        previous = self._stopped_park.pop((uid, slot), None)
        try:
            port, link = self._identity(ace, slot, uid)
            selection = Selection(ace, slot, uid, port, link, 'encoder_unload')
            stopped = self._settled_park_sample(selection)
            # Repeated cycles must return to the original point, not advance
            # by the safety margin every time. Reuse it only at the exact
            # previously verified end position on the same connection.
            origin = stopped
            if (previous is not None and self._same(previous[0])
                    and previous[1] == stopped):
                origin = previous[0].anchor
            return replace(selection, anchor=origin)
        except (ValueError, RuntimeError, OSError) as exc:
            logging.warning('[multiACE] park reference unavailable for ACE %d slot %d: %s',
                            ace, slot, exc)
            return None

    def record_load_park(self, head, selection):
        if selection is None:
            return
        a = self.ace
        if (a._last_load_ok is True and a._head_sensor_present(head) is True
                and self._park_source_matches(head, selection) and self._park_link_intact(selection)):
            self._loaded_park[head] = selection
            logging.info('[multiACE] captured return origin for ACE %d slot %d',
                         selection.ace, selection.slot)

    def _park_source_matches(self, head, selection):
        source = self.ace._head_source.get(head) or {}
        return ((source.get('ace_index'), source.get('slot')) == (selection.ace, selection.slot)
                and not source.get('load_failed') and not source.get('gate_empty'))

    def _settled_park_sample(self, selection):
        before = self._fresh_park_sample(selection, self.now())
        after = self._fresh_park_sample(selection, self.now())
        if before != after:
            raise RuntimeError('ACE park counters have not settled')
        return after

    def _thermal_route(self, selection, head_has_filament):
        slot = selection.slot
        if (not self._print_idle() or not self._park_link_intact(selection)
                or not self._park_source_matches(slot, selection)
                or self.ace._head_sensor_present(slot) is not head_has_filament
                or self.native.mask(selection.ace) & (1 << slot)):
            raise RuntimeError('ACE park route or sensor state changed; inspect selected slot')

    def start_thermal_return(self, selection):
        self._thermal_route(selection, True)
        sample = self._settled_park_sample(selection)
        delta = encoder_delta_mm(selection.anchor[0], sample[0])
        # Leave room for the stock tip-form and its bounded short probes.
        minimum = 200 + PROBE_MM * self.ace.unload_retry
        if not minimum <= abs(delta) <= 5000:
            raise RuntimeError('ACE measured travel cannot safely use the thermal return path')
        self.thermal_return = ThermalReturn(selection, 1 if delta > 0 else -1)
        self._loaded_park.pop(selection.slot, None)

    def _thermal_distance(self, task):
        sample = self._settled_park_sample(task.selection)
        return (task.direction * encoder_delta_mm(task.selection.anchor[0], sample[0]), sample)

    def _retract_event(self, selection):
        response = self.ace._tipform_send(selection.ace, {
            'method': 'get_last_feed_event', 'params': {'index': selection.slot}}, timeout=3.)
        event = (response or {}).get('result') or {}
        if ((response or {}).get('code') != 0 or event.get('lane') != selection.slot
                or type(event.get('seq')) is not int):
            raise RuntimeError('ACE park return has no matching MCU event')
        return event

    def thermal_retract(self, head, ace, slot, speed):
        task = self.thermal_return
        selection = task.selection
        if (head, ace, slot) != (selection.slot, selection.ace, selection.slot):
            raise RuntimeError('ACE park return rejected a different retract route')
        self._thermal_route(selection, False)
        remaining, sample = self._thermal_distance(task)
        if not 0 < remaining <= 5000:
            raise RuntimeError('ACE park return position is outside the measured route')
        # Single MCU moves on the supported ACE2K firmware. Leave a coarse
        # reserve then trim at lower speed. A motion fault never retries.
        segment_limit = 1500
        coarse_reserve = 60
        trim_reserve = 3
        while remaining > RETURN_WINDOW_MM:
            coarse = remaining > coarse_reserve + 5
            length = min(segment_limit, int(remaining) - (coarse_reserve if coarse else trim_reserve))
            if length < 5:
                raise RuntimeError('ACE park return stopped outside the verified window')
            self._thermal_route(selection, False)
            previous = self._retract_event(selection)
            seq = self.ace._move_report_seq(ace, slot)
            if self.ace._retract(slot, length, min(speed, 30 if coarse else 15),
                                 head=head, ace=ace) is not True:
                raise RuntimeError('ACE park retract did not start')
            self._thermal_route(selection, False)
            event = self._retract_event(selection)
            report = self.ace._move_report(ace, slot) or {}
            if (event.get('kind') != 0 or event.get('seq') == previous['seq']
                    or event.get('mode') != 2 or type(event.get('filament_um')) is not int
                    or report.get('seq', 0) <= seq or report.get('running') is not False
                    or report.get('kind') != 'retract'
                    or report.get('stop') not in ('distance', 'lost_event')):
                raise RuntimeError('ACE park retract did not reach a normal MCU terminal')
            new_remaining, new_sample = self._thermal_distance(task)
            travelled = remaining - new_remaining
            logging.info('[multiACE] park retract ACE %d slot %d command=%d event=%r remaining=%.3f',
                         ace, slot, length, event, new_remaining)
            if (not 0 < new_remaining < remaining or sample[1] == new_sample[1]
                    or abs(travelled - abs(event['filament_um']) / 1000.) > 2
                    or abs(travelled - length) > 3
                    or not coarse and new_remaining > RETURN_WINDOW_MM):
                raise RuntimeError('ACE park retract did not match the stationary encoder and motor readings')
            remaining, sample = new_remaining, new_sample
        task.result = sample

    def verify_thermal_return(self):
        task = self.thermal_return
        selection = task.selection
        self._thermal_route(selection, False)
        remaining, sample = self._thermal_distance(task)
        if sample != task.result or not 0 < remaining <= RETURN_WINDOW_MM:
            raise RuntimeError('ACE park return has no verified stationary endpoint')
        self._stopped_park[selection.uid, selection.slot] = (selection, sample)
        logging.info('[multiACE] parked ACE %d slot %d at %.3f mm from origin',
                     selection.ace, selection.slot, remaining)

    def _fresh_park_sample(self,s,after):
        # Fresh independent native reports must all postdate completion.
        deadline=self.now()+6.
        while self.now()<deadline:
            if not self._same(s):
                raise RuntimeError('ACE connection changed during park verification')
            replies=[]
            def cb(self,response,replies=replies):
                replies.append(response)
            self.ace.send_request_to(s.ace,{'method':'get_lane_state',
                'params':{'index':s.slot}},cb)
            until=min(deadline,self.now()+1.5)
            while not replies and self.now()<until:
                self.ace.reactor.pause(min(until,self.now()+.05))
            if replies and isinstance(replies[0],dict):
                d=replies[0].get('result')
                if isinstance(d,dict):
                    stamps=d.get('stamp')
                    fresh=(isinstance(stamps,(list,tuple)) and len(stamps)==4
                           and all(type(v) in (int,float) and isfinite(v)
                               and 0<=self.now()-v<=3.5 and v>after for v in stamps))
                    if fresh and d.get('index')==s.slot and replies[0].get('code')==0:
                        if d.get('present') is False:
                            raise RuntimeError('ACE inlet is empty; park return failed')
                        if d.get('state')=='ready':
                            if not self._same(s):
                                raise RuntimeError('ACE changed during sample')
                            return self._sample(replies[0],s.slot)
            self.ace.reactor.pause(min(deadline,self.now()+.1))
        raise RuntimeError('No fresh stationary ACE inlet evidence')

    def _park_selection(self,gcmd):
        ace,slot=gcmd.get_int('ACE'),gcmd.get_int('SLOT')
        a=self.ace
        if not index(ace) or not index(slot) or ace>=len(a._ace_devices):
            raise ValueError('Invalid ACE/slot')
        if (not self._idle() or getattr(a,'_swap_in_progress',False)
                or getattr(a,'_unload_all_active',False)
                or any(t.state in ACTIVE for t in self.tasks.values())
                or self.native.mask(ace)):
            raise ValueError('Finish active material operations first')
        uid=a._unit_key(ace)
        port,link=self._identity(ace,slot,uid)
        return Selection(ace,slot,uid,port,link,'park_reference')

    def _park_empty_head(self,s):
        a=self.ace
        if (a._head_sensor_present(s.slot) is not False
                or a._head_source.get(s.slot) is not None or a._bg_staged.get(s.slot)
                or s.slot in a._loading_heads or s.slot in a._bg_lanes_on(s.ace)):
            raise ValueError('Head must be empty and route stationary')

    @park_command
    def park_reference(self,gcmd):
        s=self._park_selection(gcmd)
        self._park_empty_head(s)
        if gcmd.get_int('CONFIRM_PARKED',0)!=1 or self.ace._calibration_route_occupied():
            raise gcmd.error('Confirm physical ACE park before starting calibration')
        sample=self._fresh_park_sample(s,self.now())
        self._park_reference=(s,sample[0],getattr(self.ace,'_calibration_seq',0))
        gcmd.respond_info('ACE park encoder reference captured; no motion issued')

    @park_command
    def park_measure(self,gcmd):
        s=self._park_selection(gcmd)
        ref=self._park_reference
        a=self.ace
        cal=getattr(a,'_calibration',{})
        if (not ref or ref[0]!=s or not self._same(ref[0])
                or cal.get('state')!='at_sensor'
                or (cal.get('ace'),cal.get('slot'),cal.get('head'))!=(s.ace,s.slot,s.slot)
                or cal.get('scope')!='slot'
                or getattr(a,'_calibration_seq',0)!=ref[2]+1
                or a._head_sensor_present(s.slot) is not True):
            raise gcmd.error('No matching park-to-head calibration sensor event')
        sample=self._fresh_park_sample(s,self.now())
        mm=encoder_distance_mm(ref[1],sample[0])
        self._park_reference=None
        gcmd.respond_info('Measured park-to-head sensor: %.1f mm. Proposal only; verify actual full return.'%mm)

    @park_command
    def park_record(self,gcmd):
        s=self._park_selection(gcmd)
        self._park_empty_head(s)
        if gcmd.get_int('CONFIRM_VERIFIED',0)!=1 or self.ace._calibration_route_occupied():
            raise gcmd.error('Confirm verified full unload ended inside ACE')
        self._fresh_park_sample(s,self.now())
        self._park_store().record(s.uid,s.slot,gcmd.get_int('RETRACT_LENGTH'))
        gcmd.respond_info('Verified route-specific ACE park return saved')

    @park_command
    def park_forget(self,gcmd):
        s=self._park_selection(gcmd)
        self._park_store().forget(s.uid,s.slot)
        self._park_reference=None
        gcmd.respond_info('ACE park return invalidated')

    def _route(self,ace,slot,action):
        a=self.ace
        if not self._idle():
            raise ValueError('Printer is not idle or is not in Multi mode')
        if self.native.mask(ace) & (1<<slot):
            raise ValueError('Selected lane has an unfinished exit or recovery hold')
        if slot in a._loading_heads or slot in a._bg_lanes_on(ace):
            raise ValueError('Selected route is already moving')
        source=a._head_source.get(slot)
        staged=a._bg_staged.get(slot)
        recorded=(source.get('ace_index'),source.get('slot')) if isinstance(source,dict) else staged
        if recorded is not None:
            if not isinstance(recorded,(tuple,list)) or len(recorded)!=2 or not all(index(x) for x in recorded) or recorded[1]!=slot:
                raise ValueError('Head source is not confirmed')
            recorded=tuple(recorded)
        detected=a._head_sensor_present(slot)
        selected=recorded==(ace,slot)
        cal=a._calibration_route_occupied()
        if cal and (cal.get('ace'),cal.get('slot'))==(ace,slot):
            raise ValueError('Selected lane is in calibration')
        if action=='load':
            if detected is not False or recorded is not None:
                raise ValueError('Unload the current head source before loading a new spool')
            if cal:
                raise ValueError('Finish path calibration before loading')
            return 'head_load'
        if action=='unload' or selected:
            if not selected or detected is not True or (isinstance(source,dict) and source.get('load_failed')):
                raise ValueError('Selected spool is not a verified source in the head')
            self.head_park_plan(slot,ace,slot,0)
            return 'head_unload'
        # No head_source entry is NOT proof of parking. The user must
        # explicitly inspect this exact strand before the common junction.
        if detected is None:
            raise ValueError('Head sensor is unavailable')
        if detected and recorded is None:
            raise ValueError('Head contains material from an unknown source; inspect it first')
        if a._get_heads_for_ace_slot(ace,slot):
            raise ValueError('Selected lane has an unresolved head source')
        if any(tuple(v)==(ace,slot) for v in a._bg_staged.values() if v):
            raise ValueError('Selected lane is staged in a toolhead')
        return 'inspect_parked'

    def _sample(self,response,slot):
        if not isinstance(response,dict) or type(response.get('code')) is not int or response['code']!=0:
            raise ValueError('Native lane telemetry unavailable')
        d=response.get('result')
        if not isinstance(d,dict) or type(d.get('index')) is not int or d['index']!=slot:
            raise ValueError('Lane telemetry identity mismatch')
        stamp=d.get('stamp')
        now=self.now()
        if (not isinstance(stamp,(list,tuple)) or len(stamp)!=4
                or any(type(t) not in (int,float) or not isfinite(t) or not 0<=now-t<=3.5 for t in stamp)
                or type(d.get('encoder')) is not int or not -(2**31)<=d['encoder']<2**31
                or type(d.get('motor')) is not int or not 0<=d['motor']<2**32):
            raise ValueError('Lane telemetry is stale or incomplete')
        if d.get('state')!='ready' or d.get('present') is not True:
            raise ValueError('Selected slot is not stationary with filament')
        return d['encoder'],d['motor']

    def _prune(self):
        now=self.now()
        for key,(expiry,*_) in tuple(self.tickets.items()):
            if now>=expiry:
                del self.tickets[key]
        if len(self.tickets)>=self.MAX_PREVIEWS:
            del self.tickets[next(iter(self.tickets))]
        # Keep the most recent finished rows; never discard a recovery hold.
        finished=[key for key,t in self.tasks.items() if t.state in TERMINAL]
        for key in finished[:-128]:
            del self.tasks[key]

    def _preview_rows(self,action,rows,continuation=None):
        if action not in ACTIONS or not isinstance(rows,list) or not 1<=len(rows)<=16:
            raise ValueError('Invalid batch selection')
        self._prune()
        seen=set(); selections=[]; rejected=[]; pending={}; data={}
        for row in rows:
            if not isinstance(row,dict) or set(row)!= {'ace','slot','serial'}:
                raise ValueError('Explicit ACE, slot and serial required')
            ace,slot,uid=row['ace'],row['slot'],row['serial']
            if not index(ace) or not index(slot) or (ace,slot) in seen:
                raise ValueError('Invalid or duplicated slot')
            if action=='load' and any(s==slot for _,s in seen):
                raise ValueError('Only one spool per head can be loaded')
            seen.add((ace,slot))
            try:
                port,link=self._identity(ace,slot,uid)
                if any(t.state in ACTIVE and (t.selection.ace,t.selection.slot)==(ace,slot)
                       and (continuation is None or t.id!=continuation.get((ace,slot)))
                       for t in self.tasks.values()):
                    raise ValueError('Selected slot already has a task')
                route=self._route(ace,slot,action)
                if continuation is not None and route!='inspect_parked':
                    raise ValueError('Head unload has not finished')
                s=Selection(ace,slot,uid,port,link,route)
                if route=='inspect_parked':
                    pending[(ace,slot)]=s
                else:
                    selections.append(s)
            except ValueError as e:
                rejected.append(dict(ace=ace,slot=slot,state='rejected',detail=str(e)))
        # Issue read-only telemetry requests to all selected lanes before
        # waiting for replies. No serial lock or motor wait on the reactor.
        for key,s in pending.items():
            def cb(self,response,key=key):
                data[key]=response
            try:
                self.ace.send_request_to(s.ace,{'method':'get_lane_state','params':{'index':s.slot}},cb)
            except Exception:
                data[key]=None
        deadline=self.now()+5.
        while len(data)<len(pending) and self.now()<deadline:
            self.ace.reactor.pause(min(deadline,self.now()+.05))
        for key,s in pending.items():
            try:
                if not self._same(s) or self._route(s.ace,s.slot,action)!=s.route:
                    raise ValueError('Selected route changed during preview')
                anchor=self._sample(data.get(key),s.slot)
                selections.append(Selection(s.ace,s.slot,s.uid,s.port,s.link,s.route,anchor))
            except ValueError as e:
                rejected.append(dict(ace=s.ace,slot=s.slot,state='rejected',detail=str(e)))
        if not selections:
            # A response can still explain each rejection; no ticket/no motion.
            return dict(ticket='',slots=rejected,needs_parking_confirmation=False)
        token=uuid4().hex
        self.tickets[token]=(self.now()+self.TICKET_TTL,action,tuple(selections),continuation)
        return dict(ticket=token,needs_parking_confirmation=any(s.route=='inspect_parked' for s in selections),
                    slots=[dict(ace=s.ace,slot=s.slot,state=s.route) for s in selections]+rejected)

    def preview(self,body):
        if set(body)!= {'action','slots'}:
            raise ValueError('Expected action and slots')
        return self._preview_rows(body['action'],body['slots'])

    def parking_preview(self,batch):
        tasks=[t for t in self.tasks.values() if t.batch==batch and t.state=='awaiting_parking']
        rows=[dict(ace=t.selection.ace,slot=t.selection.slot,serial=t.selection.uid) for t in tasks]
        keys={(t.selection.ace,t.selection.slot):t.id for t in tasks}
        if not rows: raise ValueError('No slot is awaiting parking confirmation')
        return self._preview_rows('eject',rows,keys)

    def start(self,body):
        if set(body)!= {'ticket','confirm_parked'} or type(body.get('confirm_parked')) is not bool:
            raise ValueError('Explicit parking confirmation field required')
        token=body['ticket']
        if not isinstance(token,str):
            raise ValueError('Invalid ticket')
        self._prune()
        saved=self.tickets.pop(token,None)  # one attempt, no replay
        if saved is None or saved[0]<=self.now() or not self._idle():
            raise ValueError('Preview expired or printer state changed')
        _,action,rows,continuation=saved
        if any(s.route=='inspect_parked' for s in rows) and body['confirm_parked'] is not True:
            raise ValueError('Physically confirm each selected tip is before the common tube')
        batch=uuid4().hex
        if continuation is not None:
            batch=self.tasks[next(iter(continuation.values()))].batch
        accepted=[]
        for s in rows:
            if continuation is not None:
                t=self.tasks.get(continuation[(s.ace,s.slot)])
                if t is None or t.state!='awaiting_parking':
                    continue
                t.selection=s
                t.state='queued'
                t.detail=''
                t.cancel_requested=False
            else:
                if any(t.state in ACTIVE and (t.selection.ace,t.selection.slot)==(s.ace,s.slot)
                       for t in self.tasks.values()):
                    continue
                t=Task(uuid4().hex,batch,action,s)
                self.tasks[t.id]=t
            accepted.append(t.id)
        if not accepted:
            raise ValueError('No selected task remains available')
        return dict(batch=batch,tasks=accepted)

    def _native_begin(self,t):
        s=t.selection
        if s.anchor is None:
            raise ValueError('Missing physical parking confirmation')
        t.state='sampling'
        t.deadline=self.now()+5.
        request=t.sample_request=object()
        runtime=self
        def cb(self,response):
            if t.state!='sampling' or t.sample_request is not request:
                return
            if runtime._own_homing():
                # This reply no longer proves a dispatch at an idle boundary.
                # Resample and revalidate the original route after homing.
                t.state='queued'
                return
            try:
                if not runtime._same(s) or runtime._route(s.ace,s.slot,'eject')!='inspect_parked':
                    raise ValueError('Route changed after preview')
                if runtime._sample(response,s.slot)!=s.anchor:
                    raise ValueError('Filament moved after preview; check its position again')
                speed=int(round(runtime.ace.get_retract_speed(s.ace)*1000))
                proof=ParkedProof(s.ace,s.slot,s.uid,s.port,s.link,*s.anchor,runtime.now())
                t.token=runtime.native.begin_parked(proof,speed)
                t.state='native'
            except Exception as e:
                t.state='rejected';t.detail=str(e)[:200]
        self.ace.send_request_to(s.ace,{'method':'get_lane_state','params':{'index':s.slot}},cb)

    def _head_begin(self,t):
        a=self.ace
        self.head_running=t.id
        t.state='head_running'
        def run(eventtime):
            moved=False
            try:
                with a.gcode.get_mutex():
                    if t.cancel_requested:
                        t.state='cancelled';return
                    s=t.selection
                    if not self._same(s) or self._route(s.ace,s.slot,t.action)!=s.route:
                        raise ValueError('Head source changed while queued')
                    if getattr(a,'_swap_in_progress',False) or getattr(a,'_unload_all_active',False):
                        t.state='queued';return
                    # Serialize only the existing toolhead movement; independent
                    # native exits are driven by their own asynchronous timers.
                    command='ACE_LOAD_HEAD' if s.route=='head_load' else 'ACE_UNLOAD_HEAD'
                    line='%s HEAD=%d ACE=%d SLOT=%d'%(command,s.slot,s.ace,s.slot)
                    if command=='ACE_UNLOAD_HEAD':
                        plan,length=self.head_park_plan(s.slot,s.ace,s.slot,0)
                        if plan.route=='head_unload':
                            line+=' RETRACT_LENGTH=%d'%length
                    moved=True
                    self._owns_head_motion=True
                    a.gcode.run_script_from_command(line)
                    motion_end=self.now()
                    if not self._same(s):
                        raise RuntimeError('ACE disconnected during head operation')
                    src=a._head_source.get(s.slot)
                    if command=='ACE_LOAD_HEAD':
                        if (a._last_load_ok is not True or a._head_sensor_present(s.slot) is not True
                                or not isinstance(src,dict) or src.get('load_failed')
                                or (src.get('ace_index'),src.get('slot'))!=(s.ace,s.slot)):
                            raise RuntimeError('Head load was not verified')
                        t.state='complete'
                    else:
                        if (a._last_unload_ok is not True or a._head_sensor_present(s.slot) is not False
                                or src is not None or a._bg_staged.get(s.slot)):
                            raise RuntimeError('Head unload was not verified')
                        self._fresh_park_sample(s,motion_end)
                        if not self._same(s):
                            raise RuntimeError('ACE changed after park verification')
                        if t.action=='eject' and not t.cancel_requested:
                            # Head clear does not prove the strand has crossed
                            # the common junction. A separate real inspection
                            # and counter-bound ticket is required.
                            t.state='awaiting_parking'
                            t.detail='Inspect the tip before the common tube, then confirm parking'
                        else:
                            t.state='complete' if t.action=='unload' else 'cancelled'
            except Exception as e:
                t.state='recovery' if moved else 'rejected'
                t.detail=str(e)[:200]
            finally:
                self._owns_head_motion=False
                self.head_running=None
        a.reactor.register_callback(run)

    def tick(self,eventtime):
        self._prune()
        for head, selection in tuple(self._loaded_park.items()):
            if (not self._park_link_intact(selection)
                    or not self._park_source_matches(head, selection)):
                del self._loaded_park[head]
        for key,(selection,_) in tuple(self._stopped_park.items()):
            if not self._park_link_intact(selection):
                del self._stopped_park[key]
        idle=self._idle()
        own_homing=not idle and self._own_homing()
        for t in tuple(self.tasks.values()):
            if not idle and not own_homing and t.state in ('queued','sampling','awaiting_parking'):
                t.state='rejected';t.detail='Printer no longer idle'
                continue
            if not idle and not own_homing and t.state=='native' and t.token and self.native.cancel(t.token):
                t.state='stopping';t.detail='Printer no longer idle'
            if t.state=='sampling' and eventtime>=t.deadline:
                if own_homing:
                    t.state='queued'
                else:
                    t.state='rejected';t.detail='Lane telemetry timed out'
            if t.state in ('native','stopping') and t.token:
                job=self.native.jobs.get(t.token)
                if job is None:
                    t.state='recovery';t.detail='Native task missing'
                elif job.state in ('complete','cancelled','recovery'):
                    t.state=job.state;t.detail=job.reason
        if not idle:
            return eventtime+.25
        # Parked work must not be delayed by a queued toolhead command.
        for t in tuple(self.tasks.values()):
            if t.state!='queued' or t.selection.route!='inspect_parked':
                continue
            try:
                if not self._same(t.selection): raise ValueError('Connection changed')
                self._native_begin(t)
            except Exception as e:
                t.state='rejected';t.detail=str(e)[:200]
        if self.head_running is None:
            for t in self.tasks.values():
                if t.state!='queued': continue
                # A selected native lane may still be exiting. Avoid a
                # contradictory head command; unrelated heads remain usable.
                if self.native.mask(t.selection.ace) & (1<<t.selection.slot):
                    continue
                self._head_begin(t)
                break
        return eventtime+.25

    def stop(self,ident):
        t=self.tasks.get(ident)
        if t is None: raise ValueError('Unknown task')
        if t.state in ('queued','sampling','awaiting_parking'):
            t.state='cancelled';return dict(accepted=True,state=t.state)
        if t.state=='head_running':
            t.cancel_requested=True
            return dict(accepted=True,state='after_head')
        if t.state in ('native','stopping') and t.token and self.native.cancel(t.token):
            t.state='stopping'
            return dict(accepted=True,state=t.state)
        return dict(accepted=False,state=t.state)

    def status(self,batch=None):
        return [t.public() for t in self.tasks.values() if batch is None or t.batch==batch]

    def dispatch(self,op,body):
        if not isinstance(body,dict): raise ValueError('Invalid request')
        if op=='preview': return self.preview(body)
        if op=='start': return self.start(body)
        if op=='status' and set(body)=={'batch'} and isinstance(body['batch'],str):
            return dict(tasks=self.status(body['batch']))
        if op=='parking_preview' and set(body)=={'batch'}:
            return self.parking_preview(body['batch'])
        if op=='stop' and set(body)=={'task'} and isinstance(body['task'],str):
            return self.stop(body['task'])
        raise ValueError('Unknown material action')

    def webhook(self,request):
        try:
            op=request.get_str('op')
            body=json.loads(request.get_str('payload'))
            request.send(self.dispatch(op,body))
        except (ValueError,RuntimeError,TypeError,KeyError) as e:
            request.send({'error':str(e)[:200]})

    def single_eject(self,gcmd):
        ace,slot=gcmd.get_int('ACE'),gcmd.get_int('SLOT')
        if not index(ace) or not index(slot):
            raise gcmd.error('ACE and SLOT must be 0..3')
        try:
            preview=self.preview({'action':'eject','slots':[dict(ace=ace,slot=slot,serial=self.ace._unit_key(ace))]})
            if not preview['ticket']:
                raise ValueError(preview['slots'][0]['detail'])
            batch=self.start(dict(ticket=preview['ticket'],confirm_parked=gcmd.get_int('CONFIRM_PARKED',0)==1))
        except ValueError as e:
            raise gcmd.error(str(e))
        gcmd.respond_info('Material batch accepted: '+batch['batch'])
