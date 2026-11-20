"""Asynchronous, leased ACE material motion using the CMD92 application API."""

import math
import secrets
import struct
import time


COMMAND = 92
QUERY, OPEN, MOVE, RENEW, STOP, CLOSE, EJECT_TO_EMPTY, ASSIST, SEEK_REFERENCE, REBASE_REFERENCE = range(10)
WAIT_INSERTION, GRIPPING, HELD, MOVING, STOPPING, EXPIRED, FAULT, WAIT_SESSION = range(8)
TARGET, REVOKED, LEASE, INLET_REMOVED = 1, 2, 3, 4
POSITION_VALID = 4
GRIP_COMPLETE = 2
HOST_HEAD_REFERENCE = 8
LEASED_ASSIST = 2048
REQUIRED_CAPABILITIES = 1 | 2 | 4 | 8 | 16 | 32 | 64 | 128 | 256 | 1024
REQUEST = struct.Struct('<4sBBBBQQIIIiHHii12s')
RESPONSE = struct.Struct('<4sBBBBQQIIIIiiHHIIiiIiIII8s')
RESPONSE_NAMES = (
    'magic', 'version', 'result', 'slot', 'device_state', 'boot_epoch',
    'session_id', 'sequence', 'insertion_epoch', 'motion_id', 'sample_sequence',
    'position_um', 'target_position_um', 'speed_mm_s', 'lease_remaining_ms',
    'sensor_bits', 'capabilities', 'lower_um', 'upper_um', 'stop_reason',
    'encoder_raw', 'motor_counts', 'device_ticks', 'reference_flags', 'reserved',
)


class MotionError(RuntimeError):
    pass


def integer(value, minimum, maximum, name):
    if type(value) is not int or not minimum <= value <= maximum:
        raise MotionError('%s is outside its supported range' % name)
    return value


def micrometres(value, name):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise MotionError('%s must be a finite distance' % name)
    return integer(round(value * 1000), -5000000, 5000000, name)


def encode_request(fields):
    """Encode the fixed application frame described by protocol.json."""
    op = integer(fields['op'], QUERY, REBASE_REFERENCE, 'operation')
    slot = integer(fields['slot'], 0, 3, 'slot')
    boot = integer(fields.get('boot_epoch', 0), 0, (1 << 64) - 1, 'boot epoch')
    session = integer(fields.get('session_id', 0), 0, (1 << 64) - 1, 'session')
    seq = integer(fields.get('sequence', 0), 0, (1 << 32) - 1, 'sequence')
    insertion = integer(fields.get('insertion_epoch', 0), 0, (1 << 32) - 1, 'insertion epoch')
    motion = integer(fields.get('motion_id', 0), 0, (1 << 32) - 1, 'motion id')
    target = integer(fields.get('target_position_um', 0), -1000000000, 1000000000, 'target')
    speed = integer(fields.get('speed_mm_s', 0), 0, 100, 'speed')
    lease = integer(fields.get('lease_ms', 0), 0, 2000, 'lease')
    lower = integer(fields.get('lower_um', 0), -1000000000, 1000000000, 'lower bound')
    upper = integer(fields.get('upper_um', 0), -1000000000, 1000000000, 'upper bound')
    flags = integer(fields.get('flags', 0), 0, 1 if op in (ASSIST, RENEW) else 0, 'flags')
    if lower > upper:
        raise MotionError('motion bounds are reversed')
    if op in (MOVE, RENEW, EJECT_TO_EMPTY, ASSIST, SEEK_REFERENCE):
        integer(speed, 1, 100, 'speed')
        integer(lease, 100, 2000, 'lease')
        if not motion:
            raise MotionError('motion target or identity is invalid')
    if op in (MOVE, EJECT_TO_EMPTY, SEEK_REFERENCE, REBASE_REFERENCE) and not lower <= target <= upper:
        raise MotionError('motion target exceeds the route bounds')
    if op == ASSIST and speed != 50:
        raise MotionError('buffer-following assistance requires speed 50')
    if op != QUERY and (not boot or not session or not seq):
        raise MotionError('mutating requests require boot, session and sequence')
    return REQUEST.pack(b'ACM1', 1, op, slot, flags, boot, session, seq, insertion,
                        motion, target, speed, lease, lower, upper, bytes(12))


def decode_response(payload):
    if type(payload) is not bytes or len(payload) != RESPONSE.size:
        raise MotionError('CMD92 response has the wrong size')
    reply = dict(zip(RESPONSE_NAMES, RESPONSE.unpack(payload)))
    if (reply['magic'] != b'ACR1' or reply['version'] != 1
            or reply['reserved'] != bytes(8)):
        raise MotionError('CMD92 response identity is invalid')
    integer(reply['result'], 0, 13, 'device result')
    integer(reply['slot'], 0, 3, 'response slot')
    integer(reply['device_state'], WAIT_INSERTION, WAIT_SESSION, 'device state')
    integer(reply['boot_epoch'], 1, (1 << 64) - 1, 'device boot epoch')
    integer(reply['speed_mm_s'], 0, 100, 'device speed')
    integer(reply['lease_remaining_ms'], 0, 2000, 'device lease')
    integer(reply['position_um'], -1000000000, 1000000000, 'device position')
    integer(reply['target_position_um'], -1000000000, 1000000000, 'device target')
    integer(reply['lower_um'], -1000000000, 1000000000, 'device lower bound')
    integer(reply['upper_um'], -1000000000, 1000000000, 'device upper bound')
    integer(reply['stop_reason'], 0, 10, 'stop reason')
    if reply['lower_um'] > reply['upper_um'] or reply['reference_flags'] & ~15:
        raise MotionError('CMD92 reference or bounds are invalid')
    return reply


def request_operation(request):
    """Return a validated CMD92 operation for the existing writer guards."""
    if not isinstance(request, dict) or request.get('method') != 'raw':
        return None
    params = request.get('params')
    if not isinstance(params, dict) or params.get('cmd') != COMMAND:
        return None
    if type(params.get('cmd')) is not int or not isinstance(params.get('hex'), str):
        raise MotionError('invalid controlled-motion raw request')
    try:
        values = REQUEST.unpack(bytes.fromhex(params['hex']))
    except (ValueError, struct.error) as error:
        raise MotionError('invalid controlled-motion raw payload') from error
    if values[0] != b'ACM1' or values[1] != 1 or values[-1] != bytes(12):
        raise MotionError('invalid controlled-motion request identity')
    names = ('op', 'slot', 'boot_epoch', 'session_id', 'sequence', 'insertion_epoch',
             'motion_id', 'target_position_um', 'speed_mm_s', 'lease_ms', 'lower_um', 'upper_um')
    fields = dict(zip(names, values[2:4] + values[5:-1]))
    fields['flags'] = values[4]
    encode_request(fields)
    return fields['op']


class DeviceSession:
    """One boot-wide session, sequence allocator and mutation queue per ACE."""

    def __init__(self, original=None):
        self.original = original
        self.boot_epoch = None
        self.session_id = 0
        self.sequence = 0
        self.opened = False
        self.opening = False
        self.open_waiters = []
        self.controllers = []
        self.active = None
        self.queue = []
        self.motion_owners = {}

    def observe_boot(self, boot):
        if self.boot_epoch is None:
            self.boot_epoch = boot
        elif boot != self.boot_epoch:
            self.boot_epoch = boot
            self.session_id = self.sequence = 0
            self.opened = self.opening = False
            self.open_waiters.clear()
            self.queue.clear()
            self.active = None
            self.motion_owners.clear()
            for controller in self.controllers:
                controller._generation += 1
                controller._pending.clear()
                controller._failed('ACE boot epoch changed', send_stop=False)

    def next_sequence(self, boot, observed):
        if self.boot_epoch is not None and boot != self.boot_epoch:
            raise MotionError('sequence allocator belongs to another ACE boot')
        self.sequence = integer(max(observed, self.sequence) + 1, 1, (1 << 32) - 1, 'sequence')
        return self.sequence

    def dispatch(self, entry):
        if entry['fields']['op'] == QUERY:
            entry['send'](entry['callback'])
            return
        if entry['fields']['op'] == STOP:
            slot = entry['fields']['slot']
            discarded = [pending for pending in self.queue if pending['fields']['slot'] == slot]
            retained = [pending for pending in self.queue if pending['fields']['slot'] != slot]
            self.queue = [entry] + retained
            self.active = None
            # STOP overtakes other lanes' pending writes. Give retained writes
            # fresh global sequence numbers before any new snapshot can arrive.
            for pending in retained:
                fields = pending['fields']
                fields['sequence'] = self.next_sequence(fields['boot_epoch'], fields['sequence'])
                controller = next((item for item in self.controllers
                                   if item.token is pending['owner']), None)
                if controller is not None:
                    controller.sequence = max(controller.sequence, fields['sequence'])
                    controller._required_sequence = max(controller._required_sequence, fields['sequence'])
            for pending in discarded:
                pending['callback'](None, MotionError('queued motion was revoked'))
        else:
            self.queue.append(entry)
        self._advance()

    def _advance(self):
        if self.active is not None or not self.queue:
            return
        entry = self.queue.pop(0)
        self.active = entry

        def complete(reply, error):
            if self.active is entry:
                self.active = None
            entry['callback'](reply, error)
            self._advance()

        entry['send'](complete)

    def abandon(self, owner):
        self.queue = [item for item in self.queue if item['owner'] is not owner]
        if self.active is not None and self.active['owner'] is owner:
            self.active = None


class AceMotionTransport:
    """Use the existing asynchronous writer and retain its original transport."""

    def __init__(self, ace, idx, slot):
        self.ace = ace
        self.idx = integer(idx, 0, 3, 'ACE index')
        self.slot = integer(slot, 0, 3, 'slot')
        self.original = ace._serials.get(idx)
        registry = getattr(ace, '_controlled_motion_devices', None)
        if registry is None:
            registry = {}
            ace._controlled_motion_devices = registry
        device = registry.get(id(self.original))
        if device is None or device.original is not self.original:
            device = DeviceSession(self.original)
            registry[id(self.original)] = device
        self.device = device

    def connected(self):
        return (self.original is not None
                and self.ace._serials.get(self.idx) is self.original
                and self.ace._connected_per_ace.get(self.idx) is True
                and not self.ace._serial_failed_per_ace.get(self.idx, False))

    def next_sequence(self, boot, observed):
        return self.device.next_sequence(boot, observed)

    def send(self, fields, callback, owner):
        if not self.connected():
            callback(None, MotionError('original ACE transport is unavailable'))
            return
        encode_request(fields)

        transport = self

        def decode(self=None, response=None):
            try:
                if not transport.connected():
                    raise MotionError('original ACE transport changed')
                if (not isinstance(response, dict)
                        or response.get('_v2_frame_valid') is not True
                        or response.get('_v2_payload_valid') is not True
                        or response.get('_v2_command') != COMMAND
                        or type(response.get('code')) is not int
                        or response['code'] != 0):
                    raise MotionError('CMD92 transport response is invalid')
                body = response.get('result')
                raw = body.get('controlled_wire_hex') if isinstance(body, dict) else None
                if not isinstance(raw, str):
                    raise MotionError('CMD92 application payload is unavailable')
                reply = decode_response(bytes.fromhex(raw))
                if reply['slot'] != transport.slot:
                    raise MotionError('CMD92 response belongs to another slot')
            except (MotionError, ValueError, TypeError) as error:
                return None, error
            return reply, None

        def send(completion):
            request = {
                'method': 'raw',
                'params': {'cmd': COMMAND, 'hex': encode_request(fields).hex()},
                '_controlled_owner': owner, '_controlled_transport': transport.original,
            }
            if fields.get('_origin') is not None:
                request['_controlled_origin'] = fields['_origin']
            def forward(self=None, response=None):
                reply, error = decode(self=self, response=response)
                completion(reply, error)

            request['_insert_reject'] = forward
            transport.ace.send_request_to(transport.idx, request, forward)

        self.device.dispatch({'fields': fields, 'send': send, 'callback': callback, 'owner': owner})


class ControlledMotion:
    """One route's asynchronous action and material-position evidence.

    The caller invokes tick on its reactor and persists task ownership before
    start. Every request carries token for the writer's final admission check.
    OPEN sessions are per ACE. Slot ownership remains independent, and the
    caller authorizes each route against its physical shared-head geometry.
    """

    def __init__(self, transport, clock=None, lease_ms=1200, poll_interval=0.1,
                 response_timeout=0.5, settle_seconds=0.1):
        self.transport = transport
        self.device = getattr(transport, 'device', None) or DeviceSession()
        self.device.controllers.append(self)
        self.clock = clock or time.monotonic
        self.lease_ms = integer(lease_ms, 100, 2000, 'lease')
        if not 0 < poll_interval < response_timeout < lease_ms / 1000.0:
            raise MotionError('poll and response deadlines must fit the lease')
        if not 0 < settle_seconds <= response_timeout:
            raise MotionError('settling interval is invalid')
        self.poll_interval = poll_interval
        self.response_timeout = response_timeout
        self.settle_seconds = settle_seconds
        self.token = object()
        self.state = 'unavailable'
        self.capability_reason = 'controlled firmware handshake required'
        self.available = False
        self.error = None
        self.reply = None
        self.boot_epoch = None
        self.insertion_epoch = None
        self.session_id = 0
        self.sequence = 0
        self.motion_id = 0
        self.lower_um = None
        self.upper_um = None
        self.direction = None
        self.action = None
        self.receipts = {}
        self._generation = 0
        self._request_id = 0
        self._pending = {}
        self._next_poll = 0.0
        self._probe_attempts = 0
        self._last_sample = None
        self._last_fresh = None
        self._last_lease = None
        self._settled = None
        self._faulted = False
        self._required_sequence = 0
        self._cancel_requested = False
        self._binding_refresh = False
        self._reference_recovery_epoch = None
        self._wire_origin = None

    def snapshot(self):
        reply = self.reply or {}
        position = reply.get('position_um')
        action = self.action or {}
        inlet = bool(reply.get('sensor_bits', 0) & (1 << (self.transport.slot * 4)))
        reference = reply.get('reference_flags', 0)
        trusted = bool(reference & POSITION_VALID and reference & (GRIP_COMPLETE | HOST_HEAD_REFERENCE)
                       and inlet and not self._faulted)
        result = {
            'state': 'opening' if self._binding_refresh else self.state, 'available': self.available,
            'capability_reason': self.capability_reason, 'error': self.error,
            'position_mm': position / 1000.0 if position is not None else None,
            'trusted': trusted,
            'inlet': inlet,
            'boot_epoch': reply.get('boot_epoch'),
            'insertion_epoch': reply.get('insertion_epoch'),
            'motion_id': action.get('motion_id', reply.get('motion_id', 0)),
            'action_id': action.get('id'), 'complete': self.state == 'complete',
            'target_mm': action.get('target_um', 0) / 1000.0 if action else None,
            'target_error_mm': (position - action['target_um']) / 1000.0
                if position is not None and action else None,
            'travelled_mm': action.get('travelled_um', 0) / 1000.0,
            'device_state': reply.get('device_state'),
            'reference_flags': reply.get('reference_flags', 0),
            'sample_sequence': reply.get('sample_sequence'),
            'telemetry_age_s': self.clock() - self._last_fresh if self._last_fresh is not None else None,
            'lease_remaining_ms': reply.get('lease_remaining_ms', 0),
            'stop_reason': reply.get('stop_reason'),
        }
        return result

    def retire_actions(self, prefix):
        """Release receipts whose upper-level owner rejects expired replays."""
        if self.action is not None and self.action['id'].startswith(prefix):
            return
        for action_id in list(self.receipts):
            if action_id.startswith(prefix):
                del self.receipts[action_id]

    def _fields(self, op):
        action = self.action or {}
        return {
            'op': op, 'slot': self.transport.slot,
            'boot_epoch': self.boot_epoch or 0, 'session_id': self.session_id,
            'sequence': self.sequence, 'insertion_epoch': self.insertion_epoch or 0,
            'motion_id': action.get('motion_id', 0),
            'target_position_um': action.get('target_um', 0),
            'speed_mm_s': action.get('speed', 0), 'lease_ms': self.lease_ms,
            'lower_um': self.lower_um or 0, 'upper_um': self.upper_um or 0,
            'flags': action.get('flags', 0) if op in (ASSIST, RENEW) else 0,
        }

    def _send(self, op, callback=None, changes=None, origin=None):
        if op != QUERY:
            self.sequence = self.transport.next_sequence(self.boot_epoch, self.sequence)
            self._next_poll = min(self._next_poll, self.clock() + self.poll_interval)
        fields = self._fields(op)
        if op not in (QUERY, STOP, CLOSE):
            inherited = origin if origin is not None else self._wire_origin
            if inherited is not None:
                fields['_origin'] = inherited
        if changes:
            fields.update(changes)
        if op in (MOVE, EJECT_TO_EMPTY, ASSIST, SEEK_REFERENCE, RENEW, STOP):
            self._required_sequence = fields['sequence']
        encode_request(fields)
        self._request_id += 1
        request_id = self._request_id
        generation = self._generation
        self._pending[request_id] = {
            'op': op, 'sequence': fields['sequence'], 'generation': generation,
            'deadline': self.clock() + self.response_timeout, 'callback': callback,
        }

        def receive(reply, error):
            pending = self._pending.pop(request_id, None)
            if pending is None or generation != self._generation:
                return
            finished = pending['callback']
            if error is not None:
                self._failed(str(error), send_stop=op not in (STOP, CLOSE))
                if finished:
                    finished(self.snapshot(), error)
                return
            try:
                self._accept(reply, op, fields)
                # A real reply arrives after query() scheduled the unknown-
                # capability backoff. Resume normal telemetry immediately.
                self._next_poll = min(self._next_poll, self.clock() + self._poll_delay())
            except MotionError as failure:
                self._failed(str(failure), send_stop=op not in (STOP, CLOSE))
                if finished:
                    finished(self.snapshot(), failure)
                return
            if finished:
                finished(self.snapshot(), None)

        try:
            self.transport.send(fields, receive, self.token)
        except Exception as error:
            receive(None, error)

    def query(self, callback=None):
        pending = next((item for item in self._pending.values()
                        if item['op'] == QUERY and item['generation'] == self._generation), None)
        if pending is not None:
            if callback is not None:
                previous = pending['callback']
                def together(snapshot, error):
                    if previous is not None:
                        previous(snapshot, error)
                    callback(snapshot, error)
                pending['callback'] = together
            return self.snapshot()
        if not self.available:
            self._probe_attempts += 1
        self._send(QUERY, callback)
        self._next_poll = self.clock() + self._poll_delay()
        return self.snapshot()

    def _poll_delay(self):
        if not self.available:
            # Unsupported firmware silently ignores CMD92. Probe a bounded
            # number of times per transport, never build an endless queue.
            return (5.0, 15.0, float('inf'))[min(max(self._probe_attempts - 1, 0), 2)]
        if (self.device.motion_owners.get(self.transport.slot) is not None
                or self.state in ('opening', 'starting', 'running', 'stopping')
                or self._binding_refresh):
            return self.poll_interval
        return max(0.25, self.poll_interval)

    def bind(self, *, lower_mm, upper_mm, direction=None, callback=None, origin=None):
        if not self.available or self.reply is None:
            raise MotionError(self.capability_reason)
        if self.state in ('running', 'starting', 'stopping', 'opening'):
            raise MotionError('a controlled action is still active')
        if self._binding_refresh:
            raise MotionError('a session position refresh is already pending')
        lower, upper = micrometres(lower_mm, 'lower bound'), micrometres(upper_mm, 'upper bound')
        if lower >= upper or direction not in (None, -1, 1) or type(direction) is bool:
            raise MotionError('route limits or permitted direction are invalid')
        if self._last_fresh is None or self.clock() - self._last_fresh > self.response_timeout:
            self._binding_refresh = True

            def refreshed(snapshot, error):
                self._binding_refresh = False
                if error is not None:
                    if callback:
                        callback(snapshot, error)
                    return
                self.bind(lower_mm=lower_mm, upper_mm=upper_mm, direction=direction,
                          callback=callback, origin=origin)

            self.query(refreshed)
            return self.snapshot()
        self._generation += 1
        self._pending.clear()
        self.lower_um, self.upper_um, self.direction = lower, upper, direction
        self.boot_epoch = self.reply['boot_epoch']
        self.insertion_epoch = self.reply['insertion_epoch']
        self.sequence = max(self.sequence, self.reply['sequence'])
        self.action = None
        self._faulted = False
        self.error = None
        self._settled = None
        self._last_sample = None
        self._reference_recovery_epoch = None
        self._wire_origin = origin
        if self.device.opened:
            self.session_id = self.device.session_id
            self.state = 'ready'
            if callback:
                callback(self.snapshot(), None)
        elif self.device.opening:
            self.state = 'opening'
            self.device.open_waiters.append((self, callback))
        else:
            self.session_id = secrets.randbits(64) or 1
            self.device.session_id = self.session_id
            self.device.opening = True
            self.state = 'opening'

            def opened(snapshot, error):
                self.device.opening = False
                self.device.opened = error is None
                waiters = self.device.open_waiters
                self.device.open_waiters = []
                for controller, finished in waiters:
                    controller.session_id = self.session_id if error is None else 0
                    controller.state = 'ready' if error is None else 'unknown'
                    controller.error = str(error) if error is not None else None
                    if finished:
                        finished(controller.snapshot(), error)
                if callback:
                    callback(snapshot, error)

            self._send(OPEN, opened)
        return self.snapshot()

    def _begin(self, action_id, target_um, speed, kind, callback, flags=0):
        if not isinstance(action_id, str) or not action_id or len(action_id) > 128:
            raise MotionError('action id must be a nonempty stable string')
        speed = integer(speed, 1, 100, 'speed')
        specification = (target_um, speed, kind, flags) if kind == 'assist' else (
            target_um, speed, kind, self.lower_um, self.upper_um, self.direction, flags)
        previous = self.receipts.get(action_id)
        if previous is not None:
            if previous['specification'] != specification:
                raise MotionError('action id was reused with different parameters')
            return previous.get('result') or self.snapshot()
        if self.state not in ('ready', 'complete', 'cancelled') or not self.session_id:
            raise MotionError('route session is not ready')
        if self._binding_refresh:
            raise MotionError('session position refresh is still pending')
        if self.device.motion_owners.get(self.transport.slot) not in (None, self):
            raise MotionError('another controller owns this ACE slot trajectory')
        if self._last_fresh is None or self.clock() - self._last_fresh > self.response_timeout:
            raise MotionError('fresh device position is required')
        current = self.reply['position_um']
        if kind == 'move':
            if not self.snapshot()['trusted'] or not self.snapshot()['inlet']:
                raise MotionError('an observed insertion reference is required')
            if not self.reply['reference_flags'] & (GRIP_COMPLETE | HOST_HEAD_REFERENCE):
                raise MotionError('local filament grip is incomplete')
            if not self.lower_um <= current <= self.upper_um:
                raise MotionError('current position is outside the route bounds')
            direction = 1 if target_um > current else -1
            if target_um == current or self.direction not in (None, direction):
                raise MotionError('target direction is unavailable')
        elif kind in ('eject', 'seek'):
            if not self.snapshot()['inlet'] or (target_um >= 0 if kind == 'eject' else target_um == 0):
                raise MotionError('relative reference motion requires material and a valid direction')
            direction = -1 if target_um < 0 else 1
            current = 0
        else:
            if not self.snapshot()['inlet']:
                raise MotionError('assistance requires material at the inlet')
            direction = -1 if flags else 1
            self._assist_bounds(current)
        if kind != 'assist' and not self.lower_um <= target_um <= self.upper_um:
            raise MotionError('target exceeds the authorized route bounds')
        self._generation += 1
        self._pending.clear()
        self.motion_id = integer(self.motion_id + 1, 1, (1 << 32) - 1, 'motion id')
        self.action = {
            'id': action_id, 'motion_id': self.motion_id, 'target_um': target_um,
            'speed': speed, 'kind': kind, 'direction': direction,
            'last_position_um': current, 'travelled_um': 0, 'flags': flags,
        }
        self.receipts[action_id] = {'specification': specification}
        self._settled = None
        self._faulted = False
        self._cancel_requested = False
        self.error = None
        self._last_lease = self.clock()
        self.state = 'starting'
        self.device.motion_owners[self.transport.slot] = self
        operation = {'move': MOVE, 'eject': EJECT_TO_EMPTY, 'assist': ASSIST, 'seek': SEEK_REFERENCE}[kind]
        self._send(operation, callback)
        return self.snapshot()

    def start(self, action_id, target_mm, speed_mm_s, callback=None):
        return self._begin(action_id, micrometres(target_mm, 'target'), speed_mm_s, 'move', callback)

    def eject_to_empty(self, action_id, length_mm, speed_mm_s=80, callback=None):
        length = micrometres(length_mm, 'eject budget')
        if length <= 0:
            raise MotionError('eject budget must be positive')
        return self._begin(action_id, -length, speed_mm_s, 'eject', callback)

    def _assist_bounds(self, position):
        # Keep room for material consumed between this snapshot and device
        # receipt. The device checks each side against its full lease budget.
        distance = (50 * self.lease_ms + 10000) // 2
        self.lower_um = max(-1000000000, position - distance)
        self.upper_um = min(1000000000, position + distance)

    def seek_reference(self, action_id, length_mm, speed_mm_s=50, callback=None):
        length = micrometres(length_mm, 'reference-search budget')
        if length <= 0:
            raise MotionError('reference-search budget must be positive')
        return self.reference_move(action_id, length_mm, speed_mm_s, callback)

    def reference_move(self, action_id, signed_length_mm, speed_mm_s=80, callback=None):
        length = micrometres(signed_length_mm, 'relative-reference budget')
        if not length:
            raise MotionError('relative-reference budget must be nonzero')
        return self._begin(action_id, length, speed_mm_s, 'seek', callback)

    def rebase_reference(self, position_mm, *, lower_mm, upper_mm, callback=None):
        if (self.state != 'cancelled' or self.action is None or self.action['kind'] != 'seek'
                or self.action['direction'] != 1
                or self._faulted or self.reply['stop_reason'] != REVOKED):
            raise MotionError('rebasing requires a stably stopped reference search')
        position = micrometres(position_mm, 'head reference')
        lower, upper = micrometres(lower_mm, 'lower bound'), micrometres(upper_mm, 'upper bound')
        if not lower < upper or not lower <= position <= upper:
            raise MotionError('head reference exceeds its geometry bounds')
        self.lower_um, self.upper_um = lower, upper
        self.state = 'opening'
        self._send(REBASE_REFERENCE, callback, {'target_position_um': position})
        return self.snapshot()

    def assist(self, action_id, reverse=False, callback=None):
        if type(reverse) is not bool:
            raise MotionError('assist direction must be a boolean')
        if self.reply is None or not self.reply['capabilities'] & LEASED_ASSIST:
            raise MotionError('leased buffer-following assistance is unavailable')
        return self._begin(action_id, 0, 50, 'assist', callback, flags=int(reverse))

    def update(self, action_id, target_mm=None, speed_mm_s=None, callback=None, origin=None):
        if self.action is None or self.action['id'] != action_id or self.state != 'running':
            raise MotionError('the addressed trajectory is not running')
        if self.action['kind'] == 'assist':
            if target_mm is not None or speed_mm_s not in (None, 50):
                raise MotionError('buffer-following assistance uses its device speed policy')
            return self.renew(callback, origin=origin)
        target = self.action['target_um'] if target_mm is None else micrometres(target_mm, 'target')
        speed = self.action['speed'] if speed_mm_s is None else integer(speed_mm_s, 1, 100, 'speed')
        direction = self.action['direction']
        if not self.lower_um <= target <= self.upper_um:
            raise MotionError('updated target exceeds the authorized bounds')
        if direction * (target - self.action['target_um']) < 0:
            raise MotionError('continuous updates must preserve direction and extend the target')
        self.action.update(target_um=target, speed=speed)
        self._settled = None
        self._send(RENEW, callback, origin=origin)
        return self.snapshot()

    def renew(self, callback=None, origin=None):
        if self.state != 'running':
            raise MotionError('trajectory is not running')
        if self.action['kind'] == 'assist':
            self._assist_bounds(self.reply['position_um'])
        self._send(RENEW, callback, origin=origin)
        return self.snapshot()

    def stop(self, action_id=None, reason='cancelled', callback=None):
        if action_id is not None and (self.action is None or self.action['id'] != action_id):
            raise MotionError('stop belongs to a different action')
        if self.state in ('complete', 'cancelled') or (
                self.state == 'stopping' and self._cancel_requested):
            return self.snapshot()
        if self._binding_refresh:
            self._binding_refresh = False
            self._generation += 1
            self._pending.clear()
        if not self.session_id:
            raise MotionError('controlled session is unavailable')
        self._generation += 1
        self._pending.clear()
        self._settled = None
        self._cancel_requested = True
        if not self._faulted:
            self.state = 'stopping'
            self.error = reason
        self._send(STOP, callback)
        return self.snapshot()

    cancel = stop

    def confirm_stopped(self, action_id, callback=None):
        if not self.session_id or self.reply is None or self.state not in ('ready', 'complete', 'cancelled'):
            raise MotionError('a ready session is required for stop confirmation')
        if not isinstance(action_id, str) or not action_id or len(action_id) > 128:
            raise MotionError('stop confirmation requires a stable action id')
        self.action = {
            'id': action_id, 'motion_id': self.reply['motion_id'],
            'target_um': self.reply['position_um'], 'speed': 0, 'kind': 'stop',
            'direction': 0, 'last_position_um': self.reply['position_um'], 'travelled_um': 0,
        }
        self.receipts.setdefault(action_id, {'specification': ('stop', self.reply['motion_id'])})
        self.state = 'ready'
        self._cancel_requested = False
        self.device.motion_owners[self.transport.slot] = self
        return self.stop(action_id, reason='confirm stopped', callback=callback)

    def close(self, callback=None):
        if self.device.motion_owners:
            raise MotionError('stop the current trajectory before closing')
        if self.session_id:
            self._send(CLOSE, callback)
        return self.snapshot()

    def _failed(self, message, send_stop=True):
        was_active = self.state in ('running', 'starting', 'stopping')
        self._faulted = True
        self._binding_refresh = False
        self.error = message
        self.state = 'unknown' if self.session_id else 'unavailable'
        self.capability_reason = message
        self.device.abandon(self.token)
        if was_active and send_stop:
            self.stop(reason=message)

    def _unfinished_action(self):
        return (self.action is not None
                and self.receipts.get(self.action['id'], {}).get('result') is None)

    def _recover_read_reference(self, reply):
        reference = reply['reference_flags']
        eligible = (not self.session_id and self.action is None
                    or self._reference_recovery_epoch == reply['insertion_epoch'])
        valid_reference = (reply['device_state'] == HELD and reference & POSITION_VALID
                           and reference & (GRIP_COMPLETE | HOST_HEAD_REFERENCE)
                           and reply['sensor_bits'] & (1 << (self.transport.slot * 4)))
        if (eligible and valid_reference and not self._unfinished_action()
                and self.device.motion_owners.get(self.transport.slot) is None):
            self._faulted = False
            self.error = None
            self.capability_reason = None
            self._reference_recovery_epoch = None
            self.state = 'ready' if self.session_id else 'idle'

    def _is_settled(self, signature):
        if self._settled is None or self._settled[0] != signature:
            self._settled = (signature, self.clock())
            return False
        return self.clock() - self._settled[1] + 1e-9 >= self.settle_seconds

    def _accept(self, reply, op, fields):
        if not isinstance(reply, dict) or reply['slot'] != self.transport.slot:
            raise MotionError('device snapshot belongs to another route')
        self.device.observe_boot(reply['boot_epoch'])
        self.available = reply['capabilities'] & REQUIRED_CAPABILITIES == REQUIRED_CAPABILITIES
        self.capability_reason = None if self.available else 'required controlled-motion capabilities are absent'
        if not self.available:
            raise MotionError(self.capability_reason)
        if self.boot_epoch is not None and reply['boot_epoch'] != self.boot_epoch:
            self.reply = reply
            raise MotionError('ACE boot epoch changed')
        if (self.session_id and reply['session_id'] != self.session_id
                and not (op == CLOSE and reply['session_id'] == 0)):
            raise MotionError('controlled session changed')
        if self.insertion_epoch is not None and reply['insertion_epoch'] != self.insertion_epoch:
            if self.state in ('running', 'starting', 'stopping') or self._unfinished_action():
                self.reply = reply
                raise MotionError('filament insertion epoch changed')
            self.insertion_epoch = reply['insertion_epoch']
            self.action = None
            self._settled = None
            self._reference_recovery_epoch = reply['insertion_epoch']
            if not self._faulted:
                self.state = 'ready' if self.session_id else 'idle'
        completed_during_renew = (
            op == RENEW and reply['result'] in (6, 8) and self.action is not None
            and reply['motion_id'] == self.action['motion_id']
            and reply['device_state'] in (HELD, STOPPING) and reply['stop_reason'] == TARGET
        )
        if completed_during_renew:
            self.action['target_um'] = reply['target_position_um']
            self._required_sequence = reply['sequence']
        elif reply['result']:
            raise MotionError('CMD92 operation was rejected with result %d' % reply['result'])
        if op != QUERY and not completed_during_renew and reply['sequence'] < fields['sequence']:
            raise MotionError('device did not acknowledge the requested sequence')
        self.sequence = max(self.sequence, reply['sequence'])
        self.device.sequence = max(self.device.sequence, reply['sequence'])
        self.reply = reply
        if op in (OPEN, MOVE, RENEW, EJECT_TO_EMPTY, ASSIST, SEEK_REFERENCE):
            self._last_lease = self.clock()
        if op == OPEN:
            self.state = 'opening' if reply['device_state'] == STOPPING else 'ready'
        if op == REBASE_REFERENCE:
            if ((reply['reference_flags'] & (POSITION_VALID | HOST_HEAD_REFERENCE))
                    != POSITION_VALID | HOST_HEAD_REFERENCE
                    or reply['position_um'] != fields['target_position_um']):
                raise MotionError('device did not confirm the head reference')
            self.action = None
            self.state = 'ready'
            self._settled = None
        if op == CLOSE:
            self.device.opened = False
            self.device.session_id = 0
            for controller in self.device.controllers:
                controller.session_id = 0
                if controller.state not in ('running', 'starting', 'stopping', 'unknown'):
                    controller.state = 'idle'
            return
        if self.action is not None and reply['sequence'] >= self._required_sequence:
            if reply['motion_id'] != self.action['motion_id']:
                raise MotionError('motion identity changed')
            if op in (MOVE, RENEW, EJECT_TO_EMPTY, ASSIST, SEEK_REFERENCE) and not completed_during_renew:
                if (reply['lower_um'], reply['upper_um']) != (fields['lower_um'], fields['upper_um']):
                    raise MotionError('device acknowledged different motion bounds')
                if self.action['kind'] != 'assist' and reply['target_position_um'] != fields['target_position_um']:
                    raise MotionError('device acknowledged a different motion target')
        sample = reply['sample_sequence']
        if self._last_sample is not None:
            delta = (sample - self._last_sample) & 0xffffffff
            if delta == 0:
                return
            if delta >= 0x80000000:
                raise MotionError('device telemetry sequence moved backwards')
        self._last_sample = sample
        self._last_fresh = self.clock()
        if op == QUERY:
            self._recover_read_reference(reply)
        if self.action is None:
            if not self.session_id:
                self.state = 'idle'
            elif self.state == 'opening' and reply['device_state'] != STOPPING:
                self.state = 'ready'
            return
        if op == QUERY and self.state in ('complete', 'cancelled'):
            return
        if reply['motion_id'] != self.action['motion_id']:
            if reply['sequence'] < self._required_sequence:
                return
            raise MotionError('motion identity changed')
        position = reply['position_um']
        self.action['travelled_um'] += position - self.action['last_position_um']
        self.action['last_position_um'] = position
        if self.action['kind'] == 'move':
            if not reply['reference_flags'] & POSITION_VALID:
                raise MotionError('material-position reference was lost')
            if not self.lower_um <= position <= self.upper_um:
                raise MotionError('material position exceeded the authorized bounds')
        if reply['device_state'] in (EXPIRED, FAULT) and not self._faulted:
            raise MotionError('device stopped with a lease or motion fault')
        if reply['device_state'] == MOVING:
            self._settled = None
            if not self._faulted and self.state != 'stopping':
                self.state = 'running'
            return
        if reply['device_state'] == STOPPING:
            if not self._faulted:
                self.state = 'stopping'
            return
        if self._faulted:
            if reply['device_state'] in (HELD, WAIT_INSERTION, EXPIRED, FAULT) and self._is_settled((
                    position, reply['device_state'], reply['motion_id'])):
                if self.device.motion_owners.get(self.transport.slot) is self:
                    self.device.motion_owners.pop(self.transport.slot)
            return
        if reply['sequence'] < self._required_sequence:
            return
        cancelled = self._cancel_requested
        ejected = (self.action['kind'] == 'eject' and reply['device_state'] == WAIT_INSERTION
                   and reply['stop_reason'] == INLET_REMOVED
                   and not self.snapshot()['inlet'])
        reached = (reply['device_state'] == HELD and reply['stop_reason'] == TARGET
                   and reply['target_position_um'] == self.action['target_um'])
        revoked = (cancelled and reply['device_state'] in (HELD, WAIT_INSERTION)
                   and reply['stop_reason'] == REVOKED)
        if not (ejected or reached or revoked):
            self._settled = None
            if self.state in ('starting', 'running', 'stopping'):
                raise MotionError('trajectory stopped before its terminal condition')
            return
        self.state = 'stopping'
        signature = (position, reply['device_state'], reply['stop_reason'], reply['motion_id'])
        if not self._is_settled(signature):
            return
        self.state = 'cancelled' if cancelled else 'complete'
        if self.device.motion_owners.get(self.transport.slot) is self:
            self.device.motion_owners.pop(self.transport.slot)
        result = self.snapshot()
        self.receipts[self.action['id']]['result'] = result

    def tick(self):
        now = self.clock()
        expired = [key for key, item in self._pending.items() if now >= item['deadline']]
        if expired:
            items = [self._pending.pop(key) for key in expired]
            self._failed('controlled-motion response timed out',
                         send_stop=any(item['op'] not in (STOP, CLOSE) for item in items))
            for item in items:
                if item['callback']:
                    item['callback'](self.snapshot(), MotionError(self.error))
        active = self.state in ('starting', 'running', 'stopping')
        if active and self._last_fresh is not None and now - self._last_fresh >= self.response_timeout:
            self._failed('controlled-motion telemetry is stale')
        if self.state == 'running':
            if self._last_lease is None or now - self._last_lease >= self.lease_ms / 1000.0:
                self._failed('controlled-motion lease expired locally')
            elif now - self._last_lease >= self.lease_ms / 3000.0 and not any(
                    item['op'] == RENEW for item in self._pending.values()):
                self.renew()
        if now + 1e-9 >= self._next_poll:
            self.query()
        return self.snapshot()
