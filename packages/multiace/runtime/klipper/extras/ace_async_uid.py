"""Offline 1.1.60O RC522 UID state machine; no device I/O or motor operations.

Caller owns the reactor, transport queue and firmware RF exclusivity. A request
already handed to transport/device is NOT preemptible by cancel(). All methods
and callbacks must run on the same owner/event-loop thread. No automatic retries.
"""
from dataclasses import dataclass
import math


def finite(x):
    return type(x) in (int, float) and math.isfinite(x)


def packed_index(slot, op, arg=0):
    if type(slot) is not int or slot not in range(4):
        raise ValueError('slot must be zero-based 0..3')
    if op not in (6, 9) or type(op) is not int:
        raise ValueError('only SELECT and buffer reads are allowed')
    if type(arg) is not int or not 0 <= arg <= 255 or (op == 6 and arg != 0):
        raise ValueError('invalid argument')
    return 0x80000000 | ((slot // 2) << 24) | (op << 16) | (arg << 8)


@dataclass(frozen=True)
class Context:
    scan_generation: str
    connection_generation: int
    active: bool = False
    stop_requested: bool = True
    connected: bool = False
    rf_exclusive: bool = False


@dataclass(frozen=True)
class Request:
    ace_index: int
    reader: int
    generation: int
    sequence: int
    index: int
    deadline: float

    @property
    def request(self):
        # A fresh dictionary; transport can add its own request id safely.
        return {'method': 'filament_identify', 'params': {'index': self.index}}


class AsyncUidRead:
    """Two matching SELECT + 4-byte UID/BCC passes, with bounded SELECT polling.

    BCC checks integrity, not spool attribution or freedom from another reader's
    valid buffer overwrite. Caller must independently enforce RF exclusivity.
    SELECT status 1 remains an unknown observation and permits paced polling.
    Timeouts, collisions and cascade UIDs terminate as unknown, never clear. A successful result remains
    an unassigned candidate UID.
    """
    OPS = ((6, 0), (9, 13), (9, 14), (9, 15), (9, 16), (9, 17)) * 2

    def __init__(self, ace_index, slot, scan_generation, connection_generation,
                 timeout_s=None, max_select_attempts=None,
                 select_interval_s=0.25, max_duration_s=None, allow_default_code_zero=False):
        packed_index(slot, 6)
        if (type(ace_index) is not int or ace_index < 0
                or not isinstance(scan_generation, str) or not scan_generation
                or type(connection_generation) is not int or connection_generation < 0
                or not finite(timeout_s) or not 0 < timeout_s <= 1):
            raise ValueError('explicit ACE, generations and bounded timeout required')
        if (type(max_select_attempts) is not int or not 2 <= max_select_attempts <= 512
                or not finite(select_interval_s) or not 0.05 <= select_interval_s <= 5
                or not finite(max_duration_s) or not 0 < max_duration_s <= 120):
            raise ValueError('bounded SELECT count, interval and session duration required')
        if type(allow_default_code_zero) is not bool:
            raise ValueError('compatibility mode must be an explicit bool')
        self.allow_default_code_zero = allow_default_code_zero
        self.default_zero_used = False
        self.compat_uid = None
        self.ace_index, self.slot = ace_index, slot
        self.reader = slot // 2
        self.scan_generation = scan_generation
        self.connection_generation = connection_generation
        self.timeout_s = timeout_s
        self.max_select_attempts = max_select_attempts
        self.select_interval_s = select_interval_s
        self.max_duration_s = max_duration_s
        self.select_attempts = 0
        self.select_no_response_count = 0
        self.request_count = 0
        self.next_select_at = None
        self.started_at = None
        self.generation = 1
        self.stage = 0
        self.pending = None
        self.last_time = None
        self.bytes = []
        self.first_uid = None
        self.first_pass_received_at = None
        self.uid = None
        self.state = 'reading'
        self.reason = None

    def cancel(self, reason='STOP requested'):
        if self.state != 'reading':
            return
        self.generation += 1
        self.pending = None
        self.bytes = []
        self.uid = None
        self.state, self.reason = 'unknown', str(reason)

    def _guard(self, now, context):
        if self.state != 'reading':
            return False
        if (not finite(now) or self.last_time is not None and now < self.last_time):
            self.cancel('invalid or reversed clock')
            return False
        self.last_time = now
        if self.started_at is not None and now >= self.started_at + self.max_duration_s:
            self.cancel('bounded RF observation time exhausted')
            return False
        if (not isinstance(context, Context)
                or context.scan_generation != self.scan_generation
                or type(context.connection_generation) is not int
                or context.connection_generation != self.connection_generation
                or context.active is not True or context.stop_requested is not False
                or context.connected is not True or context.rf_exclusive is not True):
            self.cancel('STOP, stale scan/connection, disconnected or RF ownership lost')
            return False
        if self.pending is not None and now >= self.pending.deadline:
            self.cancel('no response before deadline')
            return False
        return True

    def next_request(self, now, context):
        if not self._guard(now, context) or self.pending is not None:
            return None
        op, arg = self.OPS[self.stage]
        if op == 6:
            if self.next_select_at is not None and now < self.next_select_at:
                return None
            if self.select_attempts >= self.max_select_attempts:
                self.cancel('SELECT attempt budget exhausted')
                return None
            self.select_attempts += 1
        if self.started_at is None:
            self.started_at = now
        self.request_count += 1
        self.pending = Request(self.ace_index, self.reader, self.generation,
                               self.request_count, packed_index(self.slot, op, arg),
                               now + self.timeout_s)
        return self.pending

    def tick(self, now, context):
        self._guard(now, context)
        return self.result()

    def receive(self, intent, response, now, context):
        # Old/duplicate callbacks must not mutate or cancel another stage.
        if intent is not self.pending or not isinstance(intent, Request):
            return False
        if not self._guard(now, context):
            return False
        result = response.get('result') if isinstance(response, dict) else None
        if (not isinstance(response, dict) or response.get('_v2_payload_valid') is not True
                or type(response.get('code')) is not int or response['code'] != 0
                or str(response.get('msg', '')).strip().upper() == 'FORBIDDEN'
                or not isinstance(result, dict)
                or result.get('_index_present') is not True
                or type(result.get('index')) is not int or result['index'] != intent.index
                or type(result.get('code')) is not int or not 0 <= result['code'] <= 255):
            self.cancel('malformed, absent fields, wrong echo or transport rejection')
            return False
        if result.get('_code_present') is not True:
            # Only an explicitly known absent field in a complete CRC-checked
            # frame may take protobuf's zero default. Wrong-type/duplicate
            # fields are not absence. Legacy decoder dictionaries lack proof.
            if (not self.allow_default_code_zero
                    or response.get('_v2_frame_valid') is not True
                    or result.get('_code_present') is not False
                    or result.get('_code_absent') is not True or result['code'] != 0):
                self.cancel('field 12 absent or malformed; default zero not authorized')
                return False
            self.default_zero_used = True
        byte = result['code']  # Field 12, NOT outer response.code or identity SKU.
        self.pending = None
        if self.stage % 6 == 0:
            if byte == 1:
                self.select_no_response_count += 1
                self.next_select_at = now + self.select_interval_s
                if self.select_attempts >= self.max_select_attempts:
                    self.cancel('SELECT attempt budget exhausted; presence unknown')
                return True  # No card selected this time; never a clear sample.
            if byte != 0:
                self.cancel('SELECT not successful; presence unknown (status %d)' % byte)
                return False
            self.next_select_at = None
            self.bytes = []
        else:
            self.bytes.append(byte)
            if len(self.bytes) == 5:
                a, b, c, d, bcc = self.bytes
                if a == 0x88:
                    self.cancel('cascade/multisize UID unsupported; unknown')
                    return False
                if not any((a, b, c, d)) or a ^ b ^ c ^ d != bcc:
                    self.cancel('zero UID or BCC mismatch; collision/unknown')
                    return False
                uid = bytes((a, b, c, d)).hex().upper()
                if self.first_uid is None:
                    self.first_uid = uid
                    self.first_pass_received_at = now
                elif uid != self.first_uid:
                    self.cancel('SELECT UIDs disagree; collision/unknown')
                    return False
                else:
                    if self.default_zero_used:
                        self.compat_uid = uid
                        self.state = 'unconfirmed_candidate'
                    else:
                        self.uid = uid
                        self.state = 'candidate'
        self.stage += 1
        return True

    def callback(self, intent, context_provider, now_provider):
        """Compatible with ACE callback(self=..., response=...). Never sends."""
        def done(*args, **kwargs):
            if intent is not self.pending:
                return False
            try:
                now, context = now_provider(), context_provider()
            except Exception:
                self.cancel('callback context unavailable')
                return False
            reply = kwargs.get('response', args[-1] if args else None)
            return self.receive(intent, reply, now, context)
        return done

    def result(self):
        return {'state': self.state, 'reason': self.reason, 'candidate_uid': self.uid,
                'bcc_verified_twice': self.state == 'candidate',
                'protocol_default_zero_assumption': self.default_zero_used,
                'compatibility_candidate_uid': self.compat_uid,
                'compatibility_label': ('unconfirmed; assumes omitted field 12 means zero'
                                        if self.default_zero_used else None),
                'single_pass_candidate_uid': self.first_uid,
                'single_pass_received_at': self.first_pass_received_at,
                'single_pass_label': 'unconfirmed, unassigned historical candidate',
                'assigned_slot': None,
                'remaining_material': None, 'reader': self.reader,
                'sent_or_reserved_requests': self.request_count,
                'select_no_response_count': self.select_no_response_count,
                'can_preempt_device_frame': False}


class ScannerRFBridge:
    """Non-I/O bridge for GuardedContinuousScan's owner-thread callbacks.

    Caller MUST reserve scanner.pending for the returned request before handing
    it to serial and release that exact reservation after its callback. STOP is
    the sole exception and may invalidate it immediately. Do not independently
    run a second poller. No fields/methods on the scanner are changed here.
    This bridge does not enable active RF in the scanner's admission policy.
    """
    def __init__(self, scanner, connection_generation, timeout_s=None,
                 max_select_attempts=None, max_duration_s=None, select_interval_s=0.25):
        self.scanner = scanner
        self.identity = (scanner.plan.ace_index, scanner.plan.slot, scanner.plan.session)
        self.transport = scanner.io.transport_handle
        self.connection_generation = connection_generation
        self.rf_exclusive = False  # Owner must explicitly supply verified RF lease.
        self.core = AsyncUidRead(*self.identity, connection_generation, timeout_s,
                                 max_select_attempts, select_interval_s, max_duration_s)

    def context(self):
        s = self.scanner
        identity = (s.plan.ace_index, s.plan.slot, s.plan.session)
        serial = s.io.ace._serials.get(self.identity[0])
        fixed = identity == self.identity
        return Context(self.identity[2] if fixed else '', self.connection_generation,
                       active=s.state == 'scanning', stop_requested=s.stop_sent is not False,
                       connected=(serial is self.transport and serial is not None
                                  and getattr(serial, 'is_open', False) is True),
                       rf_exclusive=self.rf_exclusive is True)

    def next_request(self, now):
        self.core.tick(now, self.context())
        if self.scanner.pending is not None:
            return None
        return self.core.next_request(now, self.context())

    def callback(self, intent):
        return self.core.callback(intent, self.context, self.scanner.io.now)

    def on_stop(self):
        self.core.cancel('scanner STOP/head trigger')
