"""Offline candidate for one finite ACE feed supervised by Klipper timers.

No G-code entry is registered. Default construction cannot start: independent
mapping, sensor health, buffer calibration, travel and transport proofs are
required. A FIFO-delayed/refused STOP never makes a long move safe. The entire
accepted command must be safe even if every STOP fails. Polling is NOT realtime.
"""
from dataclasses import dataclass, replace
import math
import re
try:
    from .ace_async_uid import AsyncUidRead, Context as RFContext
except ImportError:
    from ace_async_uid import AsyncUidRead, Context as RFContext


def number(x):
    return type(x) in (int, float) and math.isfinite(x)


def source(x):
    return isinstance(x, str) and bool(x.strip())


@dataclass(frozen=True)
class ScanPlan:
    ace_index: int
    slot: int
    head: int
    session: str
    length_mm: int
    speed_mm_s: int
    # Current tip-to-obstacle clearance, not total tube or configured load length.
    guaranteed_clearance_mm: float | None = None
    reserve_mm: float | None = None
    valid_until: float | None = None
    clearance_source: str = ''
    bounded_command_source: str = ''
    mapping_source: str = ''
    transport_source: str = ''
    buffer_source: str = ''
    buffer_field: str = ''
    buffer_mask: int | None = None
    buffer_safe_value: int | None = None
    enable_rfid: bool = False
    rf_transaction_source: str = ''
    max_dispatch_delay_s: float | None = None
    max_end_to_end_completion_s: float | None = None
    max_reply_s: float = 0.3
    health_max_age_s: float = 0.25
    poll_interval_s: float = 0.05
    telemetry_max_age_s: float = 0.5
    stop_timeout_s: float = 1.0
    rest_timeout_s: float = 3.0
    minimum_rest_interval_s: float = 0.2
    completion_slack_s: float = 1.0

    def validate(self, now):
        if self.enable_rfid is not False and not source(self.rf_transaction_source):
            raise ValueError('active RFID requests disabled until reader selection and isolation are validated')
        if (type(self.ace_index) is not int or self.ace_index < 0
                or type(self.slot) is not int or self.slot not in range(4)
                or type(self.head) is not int or self.head not in range(4)
                or not source(self.session)
                or type(self.length_mm) is not int or not 0 < self.length_mm <= 600
                or type(self.speed_mm_s) is not int or not 0 < self.speed_mm_s <= 100):
            raise ValueError('explicit ACE/slot/head and finite command required')
        if (not all(number(x) for x in (now, self.guaranteed_clearance_mm,
                                       self.reserve_mm, self.valid_until))
                or self.reserve_mm <= 0 or self.valid_until < now
                or self.length_mm+self.reserve_mm > self.guaranteed_clearance_mm):
            raise ValueError('current full-command clearance unverified or expired')
        if not all(source(x) for x in (self.clearance_source, self.bounded_command_source,
                                      self.mapping_source, self.transport_source, self.buffer_source)):
            raise ValueError('mapping, finite firmware bound, transport or 60O buffer mapping unverified')
        if (not source(self.buffer_field) or type(self.buffer_mask) is not int
                or self.buffer_mask <= 0 or self.buffer_mask >= 1 << 32
                or type(self.buffer_safe_value) is not int or self.buffer_safe_value < 0
                or self.buffer_safe_value & ~self.buffer_mask):
            raise ValueError('explicit calibrated buffer field/mask/polarity required')
        for name in ('max_reply_s', 'health_max_age_s', 'poll_interval_s',
                     'telemetry_max_age_s', 'stop_timeout_s', 'rest_timeout_s',
                     'minimum_rest_interval_s', 'completion_slack_s'):
            if not number(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError('invalid timing configuration: '+name)
        # These bounds must include host queueing, serial/firmware startup,
        # and the entire finite motion. A send timestamp is not motor start.
        if (not number(self.max_dispatch_delay_s) or self.max_dispatch_delay_s <= 0
                or not number(self.max_end_to_end_completion_s)
                or self.max_end_to_end_completion_s < self.max_dispatch_delay_s+self.length_mm/self.speed_mm_s):
            raise ValueError('verified worst-case dispatch and end-to-end completion bounds required')
        if self.enable_rfid and self.max_end_to_end_completion_s+self.completion_slack_s > 120:
            raise ValueError('bounded RF observation cannot cover a scan exceeding 120 seconds')
        # Lease covers even the latest permitted non-cancellable endpoint.
        if self.valid_until < now + self.max_end_to_end_completion_s + self.completion_slack_s:
            raise ValueError('travel evidence expires before finite command endpoint')


@dataclass(frozen=True)
class SensorHealth:
    session: str = ''
    sequence: int = -1
    sampled_at: float | None = None
    valid_until: float | None = None
    source: str = ''
    connected: bool = False


class KlipperScanIO:
    """Actual existing object/request interfaces, with no guessed calibration.

    health_provider must establish actual raw-sensor/MCU health; simply stamping
    time when get_status is called is NOT sufficient. mapping_provider returns
    the independently verified head for this physical ACE/slot. session_check
    must enforce exclusivity outside this module too. None has a permissive
    default. Buffer polarity is only supplied by a validated ScanPlan.
    """
    def __init__(self, reader, health_provider=None, mapping_provider=None,
                 session_check=None):
        self.reader = reader
        self.ace = reader.ace
        self.printer, self.reactor = self.ace.printer, self.ace.reactor
        self.health_provider = health_provider
        self.mapping_provider = mapping_provider
        self.session_check = session_check
        self.transport_handle = None

    def now(self):
        return self.reactor.monotonic()

    def schedule(self, fn, when):
        return self.reactor.register_timer(fn, when)

    def later(self, fn):
        self.reactor.register_callback(fn)

    def send(self, idx, method, params, callback):
        self.ace.send_request_to(idx, {'method': method, 'params': dict(params)}, callback)

    def require_environment(self, plan, now):
        guard = getattr(self.ace, '_stop_guard', None)
        if guard is None or not callable(getattr(self.ace, '_latch_stop_fault', None)):
            raise ValueError('compatible STOP fault latch bundle not installed')
        guard.require_clear()
        transports = getattr(self.ace, '_serials', None)
        current = transports.get(plan.ace_index) if isinstance(transports, dict) else None
        if current is None or getattr(current, 'is_open', False) is not True:
            raise ValueError('transport is not connected')
        if self.transport_handle is None:
            self.transport_handle = current
        elif current is not self.transport_handle:
            raise ValueError('transport reconnected; scan provenance invalid')
        if not callable(self.mapping_provider) or self.mapping_provider(plan.ace_index, plan.slot, now) != plan.head:
            raise ValueError('physical ACE/slot/head mapping not independently verified')
        if not callable(self.session_check) or self.session_check(plan.session, plan.ace_index, plan.slot, now) is not True:
            raise ValueError('exclusive motion/RF session not verified')
        ps = self.printer.lookup_object('print_stats', None)
        if ps is None or getattr(ps, 'state', None) not in ('standby', 'complete', 'cancelled'):
            raise ValueError('printer is not known idle')
        if self.ace._get_heads_for_ace_slot(plan.ace_index, plan.slot):
            raise ValueError('target slot already feeds a loaded head')

    def head(self, plan, now):
        sensor = self.printer.lookup_object('filament_motion_sensor e%d_filament' % plan.head, None)
        if sensor is None:
            return 'unknown', None
        raw = getattr(sensor, 'runout_buttun_state', None)
        try:
            status = sensor.get_status(now)
            public = status.get('filament_detected') if isinstance(status, dict) else None
        except Exception:
            return 'unknown', None
        # Either asserted signal stops immediately. Disagreement never permits
        # continued feed. Do not rely on runout event_delay or extruder motion.
        if raw is True or public is True:
            return 'triggered', None
        if raw is not False or public is not False:
            return 'unknown', None
        health = self.health_provider(plan.head, now) if callable(self.health_provider) else None
        if (not isinstance(health, SensorHealth) or health.session != plan.session
                or health.connected is not True or type(health.sequence) is not int
                or health.sequence < 0 or not source(health.source)
                or not number(health.sampled_at) or not number(health.valid_until)
                or not 0 <= now-health.sampled_at <= plan.health_max_age_s
                or now > health.valid_until):
            return 'unknown', health
        return 'clear', health

    def latch(self, plan, reason):
        return self.ace._latch_stop_fault(plan.ace_index, plan.slot, reason)


class GuardedContinuousScan:
    """One slot, one bounded forward command. No retry, rewind or auto-next.

    UID records stay unassigned. Head/buffer trigger requests one STOP;
    rejected/missing STOP latches globally. Even accepted STOP is followed by
    telemetry checks; the final claim is only 'telemetry shows rest'.
    """
    TERMINAL = ('blocked', 'uncertain', 'telemetry_rest')
    def __init__(self, reader, plan, health_provider=None, mapping_provider=None,
                 session_check=None, rf_context_provider=None):
        self.plan = plan
        self.io = KlipperScanIO(reader, health_provider, mapping_provider, session_check)
        self.state = 'new'
        self.reason = None
        self.pending = None
        self.serial = 0
        self.move_sent = False
        self.move_acked = False
        self.motion_seen = False
        self.active_status_seen = False
        self.stop_sent = False
        self.stop_acked = False
        self.stop_time = None
        self.stop_deadline = None
        self.rest_deadline = None
        self.started_at = None
        self.head_sequence = None
        self.snapshots = {}
        self.neighbour_initial = None
        self.last_encoder = None
        self.rest = []
        self.poststop_read_retries = 0
        self.uids = []
        self.rf = None
        self.rf_intent = None
        self.rf_row = None
        self.rf_generation = None
        self.rf_context_provider = rf_context_provider
        self.cycle = ['get_key_state', 'get_status', 'get_feed_info']
        self.cycle_index = 0
        self.timer = None

    @staticmethod
    def accepted(reply):
        return (isinstance(reply, dict) and type(reply.get('code')) is int
                and reply['code'] == 0 and reply.get('_v2_payload_valid') is True
                and str(reply.get('msg', '')).strip().upper() != 'FORBIDDEN')

    @staticmethod
    def _lanes(rows):
        if (not isinstance(rows, list) or len(rows) != 4
                or any(not isinstance(v, dict) or type(v.get('index')) is not int for v in rows)
                or {v['index'] for v in rows} != set(range(4))):
            raise ValueError('exactly four distinct lane indices required')
        return {v['index']: v for v in rows}

    def observe_uid(self, uid, received_at, evidence_source):
        """Passive caller-provided observation, no RF transaction or binding."""
        if (self.state == 'scanning' and isinstance(uid, str)
                and re.fullmatch(r'(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{14})', uid)
                and not (len(uid) == 8 and uid.upper().startswith('88'))
                and source(evidence_source) and number(received_at)
                and 0 <= self.io.now()-received_at <= self.plan.telemetry_max_age_s):
            self.uids.append({'uid': uid.upper(), 'received_at': received_at,
                              'assigned_slot': None, 'integrity_verified': False})

    def _rf_context(self, now):
        if not callable(self.rf_context_provider):
            raise ValueError('RF connection generation and exclusive lease provider absent')
        context = self.rf_context_provider(self.plan, now)
        if (not isinstance(context, RFContext) or context.scan_generation != self.plan.session
                or type(context.connection_generation) is not int or context.connection_generation < 0
                or context.connected is not True or context.rf_exclusive is not True
                or context.stop_requested is not False):
            raise ValueError('RF context, connection generation or exclusivity invalid')
        if self.rf_generation is None:
            self.rf_generation = context.connection_generation
        elif context.connection_generation != self.rf_generation:
            raise ValueError('RF connection generation changed')
        return replace(context, active=self.state == 'scanning', stop_requested=self.stop_sent)

    def _check_buffer(self, result, now):
        fields = result.get('fields')
        word = fields.get(self.plan.buffer_field) if isinstance(fields, dict) else None
        if type(word) is not int or not 0 <= word < 1 << 32:
            raise ValueError('buffer raw word absent or malformed')
        if word & self.plan.buffer_mask != self.plan.buffer_safe_value:
            raise ValueError('buffer interlock triggered')

    def _command_window_seconds(self):
        return self.plan.max_end_to_end_completion_s+self.plan.completion_slack_s

    def _move_ack_seconds(self):
        return self.plan.max_dispatch_delay_s

    def _motion_mode(self):
        return 0

    def _active_motion_state(self):
        return 'feeding'

    def _target_decoder_available(self, target, now):
        return target.get('_decoder_present') is True

    def _record_motion_encoder(self, raw):
        if not 0 <= raw <= self.plan.length_mm:
            raise ValueError('forward encoder outside finite command')
        if self.last_encoder is not None and raw < self.last_encoder:
            raise ValueError('forward encoder moved backwards')
        self.last_encoder = raw
        if raw > 0 and self.active_status_seen:
            self.motion_seen = True

    def _head_ok(self, now):
        verdict, health = self.io.head(self.plan, now)
        if verdict != 'clear':
            raise ValueError('toolhead sensor '+verdict)
        if self.head_sequence is not None and health.sequence < self.head_sequence:
            raise ValueError('head health sequence went backwards')
        self.head_sequence = health.sequence

    def _check(self, now):
        self.plan.validate(now if not self.move_sent else self.started_at)
        if now > self.plan.valid_until:
            raise ValueError('safety evidence expired')
        self.io.require_environment(self.plan, now)
        self._head_ok(now)
        if self.plan.enable_rfid:
            self._rf_context(now)

    def start(self):
        if self.state != 'new':
            raise RuntimeError('scan is single use')
        now = self.io.now()
        try:
            self._check(now)
        except Exception as exc:
            self.state, self.reason = 'blocked', str(exc)
            return self.report()
        self.state = 'preflight'
        # Timer is registered BEFORE any serial request or potential motion.
        self.timer = self.io.schedule(self.tick, now+self.plan.poll_interval_s)
        self._next_read()
        return self.report()

    def _request(self, method, params, kind='read'):
        if self.state in self.TERMINAL or (self.stop_sent and kind not in ('stop', 'poststop')):
            return
        self.serial += 1
        token = self.serial
        sent = self.io.now()
        if kind != 'stop':
            if self.pending is not None:
                self._uncertain('multiple scan requests attempted')
                return
            self.pending = (token, method, sent+self.plan.max_reply_s, kind)
        def callback(*args, **kw):
            response = kw.get('response', args[-1] if args else None)
            # Marshal device-reader callbacks onto the reactor; the timer and
            # callback must share one state machine, not race writer threads.
            self.io.later(lambda eventtime: self._reply(token, method, response, kind, eventtime))
        try:
            self.io.send(self.plan.ace_index, method, params, callback)
        except Exception as exc:
            if self.move_sent:
                self._uncertain('send outcome uncertain: '+str(exc))
            else:
                self.state, self.reason = 'blocked', 'preflight send failed: '+str(exc)

    def _next_read(self):
        if self.pending is not None or self.state in self.TERMINAL:
            return
        if self.stop_sent and not self.stop_acked:
            return
        method = self.cycle[self.cycle_index % len(self.cycle)]
        self.cycle_index += 1
        if self.stop_sent:
            method = ('get_status', 'get_feed_info')[(self.cycle_index-1) % 2]
        if method == '__rf__':
            try:
                intent = self.rf.next_request(self.io.now(), self._rf_context(self.io.now()))
                if intent is not None:
                    self.rf_intent = intent
                    request = intent.request
                    self._request(request['method'], request['params'], 'rf')
            except Exception as exc:
                self._request_stop('RF scheduling context invalid: '+str(exc))
            return
        self._request(method, {}, 'poststop' if self.stop_sent else 'read')

    def _reply(self, token, method, reply, kind, now):
        if kind == 'move' and self.stop_sent and self.state != 'uncertain':
            self._uncertain('move ACK after STOP; execution order unknown')
            return
        # A fault remains latched even if the STOP reply arrives after the
        # scan entered uncertain. Record a valid, timely reply for diagnosis;
        # do not restart work or clear the fault.
        if self.state == 'uncertain' and kind == 'stop' and token == self.stop_token:
            if self.accepted(reply) and now <= self.stop_deadline:
                self.stop_acked = True
            return
        if self.state in self.TERMINAL:
            return
        if kind == 'stop':
            if token != self.stop_token:
                return
            if not self.accepted(reply) or now > self.stop_deadline:
                self._uncertain('STOP rejected, malformed, forbidden or late; motor may continue')
                return
            self.stop_acked = True
            self.rest_deadline = now+self.plan.rest_timeout_s
            self.state = 'verifying_rest'
            self.snapshots, self.rest = {}, []
            self.pending = None
            self._next_read()
            return
        if self.pending is None or self.pending[0] != token:
            return  # Cancelled pre-stop request cannot restart work.
        deadline = self.pending[2]
        self.pending = None
        if now > deadline:
            self._uncertain('request response exceeded validated bound')
            return
        if kind == 'move':
            if now > self.started_at+self._move_ack_seconds():
                self._uncertain('movement ACK exceeded validated dispatch bound')
                return
            if not self.accepted(reply):
                self._uncertain('move not positively acknowledged; never retry')
                return
            self.move_acked = True
            self.state = 'scanning'
            self._next_read()
            return
        try:
            if not self.stop_sent:
                self._check(now)
            else:
                self.io.require_environment(self.plan, now)
            if kind == 'rf':
                self.rf.receive(self.rf_intent, reply, now, self._rf_context(now))
                outcome = self.rf.result()
                single = outcome.get('single_pass_candidate_uid')
                if single and self.rf_row is None:
                    self.rf_row = {'uid': single, 'received_at': outcome['single_pass_received_at'],
                        'assigned_slot': None, 'integrity_verified': False, 'bcc_verified': True,
                        'confirmation_count': 1, 'label': 'single-pass unconfirmed unassigned candidate'}
                    self.uids.append(self.rf_row)
                if outcome['state'] == 'unconfirmed_candidate' and self.rf_row is not None:
                    self.rf_row.update(integrity_verified=False, confirmation_count=1,
                        matching_passes=2, protocol_default_zero_assumption=True,
                        label='unconfirmed unassigned candidate; assumes omitted field12 is zero')
                if outcome['state'] == 'candidate' and self.rf_row is not None:
                    self.rf_row.update(integrity_verified=True, confirmation_count=2,
                        confirmed_at=now, label='twice-matching BCC-verified unassigned candidate')
                self.rf_intent = None
                return
            if not self.accepted(reply):
                raise ValueError(method+' invalid response')
            result = reply.get('result')
            if not isinstance(result, dict):
                raise ValueError(method+' missing result')
            if method == 'get_key_state':
                self._check_buffer(result, now)
            elif method == 'get_status':
                if result.get('_status_wire_valid') is not True or not isinstance(result.get('slots'), list):
                    raise ValueError('status structure invalid')
                lanes = self._lanes(result['slots'])
                if len(lanes) != 4:
                    raise ValueError('all four lane states required')
                target, other = lanes[self.plan.slot], lanes[self.plan.slot ^ 1]
                if other.get('slot_status') != 'ready' or other.get('status') == 'identifying':
                    raise ValueError('neighbour is not stationary')
                target_state = target.get('slot_status')
                if self.state == 'preflight' and target_state != 'ready':
                    raise ValueError('target is not ready')
                if target_state not in ('ready', self._active_motion_state()):
                    raise ValueError('unexpected target motion state')
                if self.move_sent and target_state == self._active_motion_state():
                    self.active_status_seen = True
            elif method == 'get_feed_info':
                if result.get('_feed_wire_valid') is not True or not isinstance(result.get('feed_info'), list):
                    raise ValueError('encoder structure invalid')
                lanes = self._lanes(result['feed_info'])
                target, other = lanes[self.plan.slot], lanes[self.plan.slot ^ 1]
                if (other.get('_decoder_present') is not True
                        or not self._target_decoder_available(target, now)):
                    raise ValueError('encoder absent')
                marker = tuple(other.get(k) for k in ('steps', 'length', 'decoder'))
                if not all(type(v) is int for v in marker):
                    raise ValueError('neighbour counters malformed')
                if self.neighbour_initial is None:
                    self.neighbour_initial = marker
                elif marker != self.neighbour_initial:
                    raise ValueError('neighbour moved')
                raw = target.get('decoder')
                if type(raw) is not int:
                    raise ValueError('encoder malformed')
                raw = raw-(1 << 64) if raw >= 1 << 63 else raw
                if self.move_sent:
                    self._record_motion_encoder(raw)
            self.snapshots[method] = (now, result)
        except Exception as exc:
            if self.stop_sent:
                self._uncertain('post-STOP telemetry invalid: '+str(exc))
            elif self.move_sent:
                self._request_stop(str(exc))
            else:
                self.state, self.reason = 'blocked', str(exc)
            return
        if self.state == 'preflight' and all(m in self.snapshots for m in self.cycle):
            self._launch(now)
        elif self.stop_acked:
            self._verify_rest(now)
        elif self.move_sent and self.motion_seen and method == 'get_feed_info':
            status = self.snapshots.get('get_status')
            if status and next(x for x in status[1]['slots'] if x['index'] == self.plan.slot).get('slot_status') == 'ready':
                self._request_stop('bounded motion reported READY; encoder distance is not proof of commanded endpoint or a full spool turn')
        # Reads paced by next timer, not an unbounded callback recursion.

    def _launch(self, now):
        try:
            self._check(now)
            if any(now-self.snapshots[m][0] > self.plan.telemetry_max_age_s for m in self.cycle):
                raise ValueError('preflight telemetry expired')
        except Exception as exc:
            self.state, self.reason = 'blocked', str(exc)
            return
        self.started_at = now
        self.move_sent = True  # Set BEFORE send; an exception can follow acceptance.
        self.state = 'awaiting_move_ack'
        if self.plan.enable_rfid:
            budget = self._command_window_seconds()
            self.rf = AsyncUidRead(self.plan.ace_index, self.plan.slot, self.plan.session,
                self.rf_generation, timeout_s=min(self.plan.max_reply_s, 1.0),
                max_select_attempts=max(2, min(512, int(math.ceil(budget/.25))+2)),
                select_interval_s=.25, max_duration_s=budget,
                allow_default_code_zero=getattr(self.plan,'allow_default_code_zero',False))
            self.cycle.append('__rf__')
        self._request('feed_or_rollback_raw', {'index': self.plan.slot,
                      'speed': self.plan.speed_mm_s, 'length': self.plan.length_mm, 'mode': self._motion_mode()}, 'move')

    def _request_stop(self, why):
        if not self.move_sent:
            self.state, self.reason = 'blocked', why
            return
        if self.stop_sent:
            return
        self.reason = why
        if self.rf is not None:
            self.rf.cancel('supervisor STOP: '+why)
        self.rf_intent = None
        self.stop_sent = True
        self.pending = None
        self.state = 'stopping'
        self.stop_time = self.io.now()
        self.stop_deadline = self.stop_time+self.plan.stop_timeout_s
        self.stop_token = self.serial+1
        self._request('stop_feed_filament', {'index': self.plan.slot}, 'stop')

    def _uncertain(self, why):
        if self.state == 'uncertain':
            return
        self._request_stop(why)
        self.state, self.reason = 'uncertain', why
        self.snapshots, self.rest = {}, []
        try:
            self.io.latch(self.plan, why)
        except Exception:
            self.reason += '; global latch publication failed, local session remains locked'

    def _verify_rest(self, now):
        if not all(k in self.snapshots for k in ('get_status', 'get_feed_info')):
            return
        stime, status = self.snapshots['get_status']
        ftime, feed = self.snapshots['get_feed_info']
        if (min(stime, ftime) <= self.stop_time or now-min(stime, ftime) > self.plan.telemetry_max_age_s):
            return
        target = next(x for x in status['slots'] if x['index'] == self.plan.slot)
        raw = next(x for x in feed['feed_info'] if x['index'] == self.plan.slot)['decoder']
        if target['slot_status'] != 'ready':
            self.rest = []
            return
        if not self.motion_seen or not self.move_acked:
            self._uncertain('READY without correlated observed motion/ACK cannot prove rest')
            return
        # Consume each paired fresh status/encoder only once.
        self.snapshots = {}
        if self.rest and self.rest[-1][1] != raw:
            self.rest = []
        self.rest.append((now, raw))
        if len(self.rest) >= 2 and self.rest[-1][0]-self.rest[0][0] >= self.plan.minimum_rest_interval_s:
            self.state = 'telemetry_rest'

    def tick(self, eventtime):
        if self.state in self.TERMINAL:
            return self.io.reactor.NEVER
        now = self.io.now()
        try:
            if not self.stop_sent:
                self._check(now)
        except Exception as exc:
            self._request_stop(str(exc))
        if self.move_sent and not self.stop_sent and 'get_key_state' in self.snapshots and now-self.snapshots['get_key_state'][0] > self.plan.telemetry_max_age_s:
            self._request_stop('buffer telemetry stale')
        if self.move_sent and not self.stop_sent and now > self.started_at+self._command_window_seconds():
            self._uncertain('finite command deadline exceeded')
        elif self.stop_sent and not self.stop_acked and now > self.stop_deadline:
            self._uncertain('STOP acknowledgment timeout; motor may continue')
        elif self.stop_acked and now > self.rest_deadline:
            self._uncertain('telemetry rest verification timeout; motor may continue')
        elif self.pending and now > self.pending[2]:
            pending = self.pending
            if self.stop_acked and pending[3] == 'poststop' and self.poststop_read_retries < 1:
                # Only a read-only query after acknowledged STOP may retry.
                # The old callback is ignored by token; no motion is resent.
                self.pending = None
                self.poststop_read_retries += 1
                self._next_read()
            elif self.move_sent:
                self._uncertain('serial request timeout during supervised scan: '+str(pending[1]))
            else:
                self._block('preflight response timeout: '+str(pending[1]))
        elif not self.pending:
            self._next_read()
        return self.io.reactor.NEVER if self.state in self.TERMINAL else now+self.plan.poll_interval_s

    def _block(self, why):
        self.state, self.reason = 'blocked', why

    def cancel(self):
        self._request_stop('cancelled by caller')

    def report(self):
        return {'state': self.state, 'reason': self.reason, 'motion_sent': self.move_sent,
                'stop_requested': self.stop_sent, 'stop_acknowledged': self.stop_acked,
                'physical_standstill_verified': False, 'sensor_polling_is_realtime': False,
                'uid_observations': [dict(row) for row in self.uids], 'slot_assignment': None,
                'rf': self.rf.result() if self.rf else {'state': 'disabled'},
                'remaining_material': None, 'automatic_retry_or_restore': False,
                'full_turn_verified': False,
                'uid_classification': 'unassigned_candidate' if self.uids else 'not_observed_in_this_bounded_scan'}
