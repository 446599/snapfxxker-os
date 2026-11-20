"""Scoped slot-4 measurement coordinator with crash-safe position protection.

The coordinator performs bounded movement and binds STOP to its original transport.
"""
import copy
import logging
import hashlib
import json
import math
import os
import stat
import time
from .ace_location_hold import PATH, PORT, UID, LocationHold

class MeasurementUnavailable(RuntimeError):
    pass

class RFObservationError(MeasurementUnavailable):
    pass

class MeasureOwner:
    """Single-use wire permits, valid only for a captured route identity."""
    def __init__(self, ace, transport, epoch, uid, seq):
        self.ace, self.transport, self.epoch, self.uid, self.seq = ace, transport, epoch, uid, seq
        self.sources = copy.deepcopy(ace._head_source)
        self.epochs = copy.deepcopy(getattr(ace, '_insert_epochs', {}) or {})
        self.gates = copy.deepcopy((getattr(ace, '_gate_status_per_ace', {}) or {}).get(0))
        self.print_stats = ace.printer.lookup_object('print_stats', None)
        self.heads = [ace.printer.lookup_object('filament_motion_sensor e%d_filament' % h, None) for h in range(4)]
        self.deadline = 0
        self.active = False
        self.phase = 'idle'
        self.permits = {}
        self.forward_count = self.return_count = 0
        self.return_mm = None
        self.motion_active = False
        self.progress_time = None
        self.progress_value = 0
        self.progress_mode = None

    def route_valid(self):
        a = self.ace
        try:
            ps, heads = self.print_stats, self.heads
            return (self.active and self.transport is not None and time.monotonic() <= self.deadline and
                ps is not None and (getattr(ps, 'state', '') or '').lower() in ('standby', 'complete') and
                all(h is not None and getattr(h, 'runout_buttun_state', None) is False for h in heads) and
                not getattr(a._slot_actions, 'cancel', False) and
                (getattr(a, '_insert_epochs', {}) or {}) == self.epochs and
                (getattr(a, '_gate_status_per_ace', {}) or {}).get(0) == self.gates and
                a._serials.get(0) is self.transport and a._connected_per_ace.get(0) is True and
                not a._serial_failed_per_ace.get(0, False) and a._tag_read_busy is True and
                a._tag_op_seq == self.seq and a._tag_op_kind == 'read' and
                (a._tag_op_ace, a._tag_op_slot) == (0, 3) and
                (getattr(a, '_insert_epochs', {}) or {}).get((0, 3)) == self.epoch and
                bool(self.uid) and (getattr(a, '_rc_last_uid', {}) or {}).get((0, 3)) == self.uid and
                a._head_source == self.sources and not a._swap_in_progress and
                not a._unload_all_active and a._stop_guard.message() is None)
        except Exception:
            return False

    def issue(self, mode, length):
        if not self.route_valid() or type(length) is not int:
            raise MeasurementUnavailable('measurement owner unavailable')
        if mode == 0 and self.phase == 'forward' and not self.forward_count and length == 1200:
            self.forward_count = 1
        elif (mode == 1 and self.phase == 'restore' and self.forward_count == 1 and
              not self.return_count and length == self.return_mm and 0 < length <= 1283):
            self.return_count = 1
        else:
            raise MeasurementUnavailable('measurement motion budget invalid')
        params = {'index': 3, 'speed': 5 if mode == 0 else 10, 'length': length, 'mode': mode}
        token = object()
        self.permits[token] = params
        return {'method': 'feed_or_rollback_raw', 'params': params, '_manual_measure_token': token}

    def allows(self, idx, request, transport):
        try:
            params = self.permits.get(request.get('_manual_measure_token'))
            return bool(idx == 0 and transport is self.transport and self.route_valid() and
                        request.get('method') == 'feed_or_rollback_raw' and params is not None and
                        request.get('params') == params)
        except Exception:
            return False

    def consumed(self, request):
        params = self.permits.pop(request.get('_manual_measure_token'), None)
        if params is not None:
            self.progress_mode = params['mode']
            self.progress_value = 0
            self.progress_time = time.monotonic()
            self.motion_active = True

    def note_encoder(self, value):
        if type(value) is not int or not self.motion_active:
            return
        if ((self.progress_mode == 0 and value > self.progress_value) or
                (self.progress_mode == 1 and value < self.progress_value)):
            self.progress_value = value
            self.progress_time = time.monotonic()

    def revoke(self):
        self.active = False
        self.permits.clear()

class AuthOwner(MeasureOwner):
    """Limited reader motion during attribution, before a new UID is known."""
    def __init__(self, ace, transport, epoch, seq):
        super().__init__(ace, transport, epoch, None, seq)
        self.observed_uid = (getattr(ace, '_rc_last_uid', {}) or {}).get((0, 3))
        self.total_mm = 0
        self.last_motion_start = None

    def route_valid(self):
        a = self.ace
        try:
            ps, heads = self.print_stats, self.heads
            return (self.active and self.transport is not None and time.monotonic() <= self.deadline and
                ps is not None and (getattr(ps, 'state', '') or '').lower() in ('standby', 'complete') and
                all(h is not None and getattr(h, 'runout_buttun_state', None) is False for h in heads) and
                not getattr(a._slot_actions, 'cancel', False) and
                (getattr(a, '_insert_epochs', {}) or {}) == self.epochs and
                (getattr(a, '_gate_status_per_ace', {}) or {}).get(0) == self.gates and
                a._serials.get(0) is self.transport and a._connected_per_ace.get(0) is True and
                not a._serial_failed_per_ace.get(0, False) and a._tag_read_busy is True and
                a._tag_op_seq == self.seq and a._tag_op_kind == 'read' and
                (a._tag_op_ace, a._tag_op_slot) == (0, 3) and
                (getattr(a, '_rc_last_uid', {}) or {}).get((0, 3)) == self.observed_uid and
                a._head_source == self.sources and not a._swap_in_progress and
                not a._unload_all_active and a._stop_guard.message() is None)
        except Exception:
            return False

    def adopt_uid(self, uid):
        if not uid or (getattr(self.ace, '_rc_last_uid', {}) or {}).get((0, 3)) != uid:
            raise MeasurementUnavailable('attributed UID unavailable')
        self.observed_uid = uid

    def issue_auth(self, params):
        if (not self.route_valid() or not isinstance(params, dict) or params.get('index') != 3 or
            type(params.get('mode')) is not int or params['mode'] not in (0, 1) or
            type(params.get('length')) is not int or not 0 < params['length'] <= 2000 or
            type(params.get('speed')) is not int or not 0 < params['speed'] <= 40 or
            self.total_mm + params['length'] > 3000):
            raise MeasurementUnavailable('attribution motion budget unavailable')
        self.total_mm += params['length']
        token = object()
        self.permits[token] = dict(params)
        return token

    def consumed(self, request):
        if self.permits.pop(request.get('_manual_measure_token'), None) is not None:
            self.last_motion_start = time.monotonic()

class PendingMarker:
    """Keep the verified route recoverable only after explicit complete proof."""
    def __init__(self, ace, path=PATH):
        self.ace, self.path, self.old, self.pending_hash = ace, path, None, None
        self.pending = None
        self.offset = None

    def begin(self):
        st = os.lstat(self.path)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o600 or st.st_size > 8192:
            raise MeasurementUnavailable('route marker unavailable')
        with open(self.path, 'rb') as f:
            old = f.read()
        marker = json.loads(old)
        if (not LocationHold(self.path).valid_marker or LocationHold(self.path).active or
            marker.get('state') != 'verified_recovered' or marker.get('ace_uid') != list(UID) or
            marker.get('port') != PORT or marker.get('slot') != 3 or marker.get('head') != 3):
            raise MeasurementUnavailable('verified route marker required')
        offset = marker.get('park_offset_mm', 0)
        if type(offset) is not int or abs(offset) > 3:
            raise MeasurementUnavailable('park offset unavailable')
        self.offset = offset
        pending = dict(marker, state='manual_measure_in_progress', event_time=time.time())
        self.pending = json.dumps(pending).encode()
        # Runtime fails closed even if rename/fsync fails after partial commit.
        self.ace._location_hold.active = True
        speed = getattr(self.ace, '_normal_load_speed', None)
        if speed is not None: speed.park = None
        self._replace(self.pending)
        self.old = old
        self.ace._location_hold = LocationHold(self.path)
        if not self.ace._location_hold.active:
            raise MeasurementUnavailable('pending marker not latched')
        with open(self.path, 'rb') as f:
            self.pending_hash = hashlib.sha256(f.read()).digest()

    def finish(self, offset):
        if type(offset) is not int or abs(offset) > 3:
            raise MeasurementUnavailable('park offset outside tolerance')
        if self.old is None or self.pending_hash is None:
            raise MeasurementUnavailable('measurement marker missing')
        with open(self.path, 'rb') as f:
            if hashlib.sha256(f.read()).digest() != self.pending_hash:
                raise MeasurementUnavailable('measurement marker changed')
        try:
            restored = json.loads(self.old)
            restored['park_offset_mm'] = offset
            self._replace(json.dumps(restored).encode())
            hold = LocationHold(self.path)
            if not hold.valid_marker or hold.active:
                raise MeasurementUnavailable('verified marker restore failed')
        except Exception as exc:
            self.ace._location_hold.active = True
            try:
                self._replace(self.pending)
            except Exception as rollback_exc:
                latch = getattr(self.ace, '_latch_stop_fault', None)
                if latch is not None: latch(0, 3, 'measurement marker rollback failed: %s' % rollback_exc)
            raise MeasurementUnavailable('measurement marker commit failed: %s' % exc)
        self.ace._location_hold = hold
        self.old = None

    def _replace(self, content):
        path = self.path + '.measure-%d-%d' % (os.getpid(), time.time_ns())
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(content); f.flush(); os.fsync(f.fileno())
            os.replace(path, self.path)
            directory = os.open(os.path.dirname(self.path), os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            if os.path.exists(path): os.unlink(path)

class ReaderQuarantine:
    """Save and restore both native readers; never equate ACK with state."""
    def __init__(self, actions, reader, monitor, cleanup_monitor=None):
        self.actions, self.reader, self.monitor = actions, reader, monitor
        self.cleanup_monitor = cleanup_monitor or monitor
        self.original = None
        self.touched = []

    def _state(self, slot):
        # Must check the complete response; reader._rc masks the response to
        # a byte and defaults missing values to zero, unsuitable here.
        packed = 0x800A0000 | (slot << 8) | 3
        self.monitor()
        response = self.reader._req(0, 'filament_identify', {'index': packed})
        self.monitor()
        if (not isinstance(response, dict) or response.get('_v2_frame_valid') is not True or
            response.get('_v2_payload_valid') is not True or response.get('_v2_command') != 68 or
            type(response.get('code')) is not int or response['code'] != 0 or
            str(response.get('msg', '')).strip().upper() == 'FORBIDDEN'):
            raise MeasurementUnavailable('reader state frame unavailable')
        data = response.get('result')
        if (not isinstance(data, dict) or data.get('_index_present') is not True or data.get('index') != packed or
            type(data.get('code')) is not int or
            not (data.get('_code_present') is True or (data.get('_code_absent') is True and data['code'] == 0))):
            raise MeasurementUnavailable('reader state response unavailable')
        if data['code'] not in (0, 8):
            raise MeasurementUnavailable('reader not idle or disabled')
        return data['code']

    def _flags(self):
        self.monitor()
        status = self.actions._request(0, 'get_status', {}, 6, monitor=self.monitor)
        if status.get('_status_wire_valid') is not True or status.get('_rfid_flags_wire_valid') is not True:
            raise MeasurementUnavailable('reader flags unavailable')
        flags = (status.get('rfid1_enable_raw'), status.get('rfid2_enable_raw'))
        if any(type(v) is not int or v not in (0, 1) for v in flags):
            raise MeasurementUnavailable('reader flags unavailable')
        return flags

    def _check(self, expected):
        flags = self._flags()
        states = (self._state(0), self._state(2))
        if flags != tuple(int(state != 8) for state in states):
            raise MeasurementUnavailable('reader flags/state disagree')
        if expected is not None and flags != tuple(expected):
            raise MeasurementUnavailable('reader disable/restore unconfirmed')
        return flags

    def _await(self, expected, timeout=2.0):
        reactor = self.actions.ace.reactor
        deadline = reactor.monotonic() + timeout
        while True:
            self.monitor()
            try:
                if self._check(expected) == tuple(expected):
                    return
            except MeasurementUnavailable:
                pass
            if reactor.monotonic() >= deadline:
                raise MeasurementUnavailable('reader state transition timed out')
            reactor.pause(min(deadline, reactor.monotonic() + .05))

    def begin(self):
        self.original = self._check(None)
        try:
            for reader, enabled in enumerate(self.original):
                if enabled:
                    # Track before sending: timeout may follow a real GPIO change.
                    self.touched.append(reader)
                    self.actions._request(0, 'set_rfid_enable',
                        {'index': reader * 2, 'enable': False}, 14, monitor=self.monitor)
                    wanted = list(self.original)
                    for changed in self.touched: wanted[changed] = 0
                    self._await(wanted)
            self._await((0, 0))
        except Exception:
            self.restore()
            raise

    def restore(self):
        if self.original is None:
            raise MeasurementUnavailable('original reader state unknown')
        saved = self.monitor
        self.monitor = self.cleanup_monitor
        try:
            # Raw SELECT may power an originally-disabled antenna as well.
            for reader in (1, 0):
                self.actions._request(0, 'set_rfid_enable',
                    {'index': reader * 2, 'enable': bool(self.original[reader])}, 14, monitor=self.monitor)
            self._await(self.original)
            self.touched.clear()
        finally:
            self.monitor = saved

class MotionWatch:
    """Bounded independent head/route watch; failure queues only bound STOP."""
    def __init__(self, owner):
        self.owner = owner
        self.timer = None
        self.error = None
        self.stop_reply = None

    def start(self):
        if not self.owner.route_valid():
            raise MeasurementUnavailable('measurement route unavailable')
        self.timer = self.owner.ace.reactor.register_timer(self.tick, self.owner.ace.reactor.NOW)

    def tick(self, eventtime):
        no_progress = (self.owner.motion_active and self.owner.progress_time is not None and
                       time.monotonic() - self.owner.progress_time >= 1.5)
        if not self.owner.route_valid() or no_progress:
            self.error = ('measurement encoder stopped progressing' if no_progress else
                          'measurement route or head sensor changed')
            self.owner.revoke()
            ace, tr = self.owner.ace, self.owner.transport
            if ace._serials.get(0) is not tr:
                ace._latch_stop_fault(0, 3, 'measurement original transport lost')
                return ace.reactor.NEVER
            def cb(self, response):
                self_watch.stop_reply = response
            self_watch = self
            try:
                request = {'method': 'stop_feed_filament', 'params': {'index': 3},
                           '_insert_stop_transport': tr, '_insert_reject': cb}
                ace.send_request_to(0, request, cb)
            except Exception as exc:
                ace._latch_stop_fault(0, 3, 'measurement stop enqueue failed: %s' % exc)
            return ace.reactor.NEVER
        return eventtime + .05

    def check(self):
        if self.error is not None or not self.owner.route_valid():
            raise MeasurementUnavailable(self.error or 'measurement route invalid')

    def close(self):
        if self.timer is not None:
            self.owner.ace.reactor.unregister_timer(self.timer)
            self.timer = None

class MotionProtocol:
    """Only this owner's single feed and measured reverse may reach writer."""
    def __init__(self, owner, watch, reader, actions):
        self.owner, self.watch, self.reader, self.actions = owner, watch, reader, actions
        self.motion_start = None

    def _send_motion(self, mode, length):
        request = self.owner.issue(mode, length)
        box = [None]
        def cb(self, response): box[0] = response if response is not None else {}
        request['_insert_reject'] = cb
        self.watch.check()
        self.motion_start = self.owner.ace.reactor.monotonic()
        self.owner.ace.send_request_to(0, request, cb)
        reactor = self.owner.ace.reactor
        deadline = reactor.monotonic() + 3
        while box[0] is None and reactor.monotonic() < deadline:
            self.watch.check()
            reactor.pause(min(deadline, reactor.monotonic() + .05))
        self.watch.check()
        r = box[0]
        if (not isinstance(r, dict) or r.get('_v2_frame_valid') is not True or
            r.get('_v2_payload_valid') is not True or r.get('_v2_command') != 8 or
            type(r.get('code')) is not int or r['code'] != 0 or
            str(r.get('msg', '')).strip().upper() == 'FORBIDDEN'):
            raise MeasurementUnavailable('motion acknowledgement uncertain')

    def _stop_and_stable(self, monitor=None):
        a, tr = self.owner.ace, self.owner.transport
        original_monitor = monitor or self.watch.check
        reactor = a.reactor
        deadline = reactor.monotonic() + 8
        def monitor():
            original_monitor()
            if reactor.monotonic() >= deadline:
                raise MeasurementUnavailable('stop confirmation timed out')
        if a._serials.get(0) is not tr:
            raise MeasurementUnavailable('original transport lost')
        self.actions._request(0, 'stop_feed_filament', {'index': 3}, 9,
                              monitor=monitor, stop_transport=tr)
        self.owner.motion_active = False
        if a._serials.get(0) is not tr:
            raise MeasurementUnavailable('original transport lost after STOP')
        while self.motion_start is not None and reactor.monotonic() < self.motion_start + .3:
            monitor()
            reactor.pause(min(self.motion_start + .3, reactor.monotonic() + .05))
        repeated = False
        while True:
            monitor()
            lanes = self.actions._lane_states(0, monitor=monitor)
            if any(lanes.get(i) != 'ready' for i in (0, 1, 2)):
                raise MeasurementUnavailable('neighbour movement or status unknown')
            if lanes.get(3) == 'ready':
                break
            if lanes.get(3) not in ('feeding', 'rollback') or reactor.monotonic() >= deadline:
                raise MeasurementUnavailable('target stop not confirmed')
            if not repeated:
                self.actions._request(0, 'stop_feed_filament', {'index': 3}, 9,
                                      monitor=monitor, stop_transport=tr)
                repeated = True
            reactor.pause(min(deadline, reactor.monotonic() + .05))
        gate, decoder = self.actions._stable(0, 3, allow_initial_zero=True,
                                             monitor=monitor)
        if gate is not True or not isinstance(decoder, tuple) or len(decoder) != 2:
            raise MeasurementUnavailable('standstill or gate unavailable')
        value = decoder[1]
        if type(value) is not int:
            raise MeasurementUnavailable('encoder unavailable')
        return value - (1 << 64) if value >= (1 << 63) else value

    def measure(self, prior_offset=0, stationary_auth=None, neighbour_signature=None):
        from .ace_remainder_edges import PassageIntervals, EdgeAmbiguous
        a, reactor = self.owner.ace, self.owner.ace.reactor
        edges = PassageIntervals(self.owner.uid)
        self.owner.phase = 'forward'
        self._send_motion(0, 1200)
        # A stale counter from a prior command is not this feed's position.
        first = self.reader._fresh_encoder(0, 3)
        if type(first) is not int or not 0 <= first <= 2:
            raise MeasurementUnavailable('forward counter reset unconfirmed')
        deadline = reactor.monotonic() + 245
        sample_error = None
        uncertain_start = None
        last_after = first
        entry_after = None
        auth_result = None
        while reactor.monotonic() < deadline:
            self.watch.check()
            lanes = self.actions._lane_states(0, monitor=self.watch.check)
            if any(lanes.get(i) != 'ready' for i in (0, 1, 2)):
                raise MeasurementUnavailable('neighbour movement or status unknown')
            if lanes.get(3) not in ('ready', 'feeding'):
                raise MeasurementUnavailable('target feed status unavailable')
            if neighbour_signature is not None and self.reader._neighbour_signature(0, 3) != neighbour_signature:
                raise MeasurementUnavailable('neighbour changed during measurement')
            if lanes.get(3) == 'ready':
                # READY at startup may precede the actual motor start. Only a
                # consumed command with reset and fresh forward progress can
                # establish that this command has naturally finished.
                ready_encoder = self.reader._fresh_encoder(0, 3)
                self.watch.check()
                if type(ready_encoder) is not int or ready_encoder < last_after:
                    raise MeasurementUnavailable('ready encoder unavailable or reversed')
                self.owner.note_encoder(ready_encoder)
                if (last_after > 2 and self.owner.motion_active and self.owner.progress_mode == 0 and
                        self.owner.progress_value > 2 and self.motion_start is not None and
                        reactor.monotonic() - self.motion_start >= .3):
                    break
                last_after = ready_encoder
            before = self.reader._fresh_encoder(0, 3)
            self.owner.note_encoder(before)
            self.watch.check()
            observe = getattr(self.reader, '_measurement_observed_uid', None)
            if observe is None:
                raise MeasurementUnavailable('RF absence proof unavailable')
            poll_start = reactor.monotonic()
            try:
                observed = observe(0, 3, before)
                error = None
            except RFObservationError as exc:
                observed, error = 'ambiguous', str(exc)
                if uncertain_start is None:
                    uncertain_start = (before, poll_start)
                sample_error = error
            self.watch.check()
            after = self.reader._fresh_encoder(0, 3)
            self.owner.note_encoder(after)
            self.watch.check()
            if (type(before) is not int or type(after) is not int or
                    before < last_after or after < before):
                raise MeasurementUnavailable('measurement encoder unavailable or reversed')
            last_after = after
            if uncertain_start is not None:
                if after - uncertain_start[0] > 20 or reactor.monotonic() - uncertain_start[1] > 2:
                    sample_error = 'RF ambiguity exceeded 20 mm or 2 s: %s' % sample_error
                    break
                if observed not in ('pending', 'ambiguous'):
                    uncertain_start = None
                    sample_error = None
            try:
                if observed not in ('pending', 'ambiguous'):
                    edges.observe(before, after, observed)
                if edges.circumference() is not None:
                    entry_after = after
                    break
                if after >= 1190:
                    break
            except EdgeAmbiguous as exc:
                sample_error = str(exc)
                break
        if entry_after is not None and stationary_auth is not None:
            # Continue the same feed briefly inside the observed field; never
            # issue another forward command or exceed the original budget.
            center_target = min(entry_after + 10, 1190)
            while last_after < center_target:
                self.watch.check()
                lanes = self.actions._lane_states(0, monitor=self.watch.check)
                if any(lanes.get(i) != 'ready' for i in (0, 1, 2)) or lanes.get(3) not in ('ready','feeding'):
                    raise MeasurementUnavailable('neighbour or target movement changed')
                if neighbour_signature is not None and self.reader._neighbour_signature(0, 3) != neighbour_signature:
                    raise MeasurementUnavailable('neighbour changed during centering')
                value = self.reader._fresh_encoder(0, 3)
                self.owner.note_encoder(value)
                if type(value) is not int or value < last_after:
                    raise MeasurementUnavailable('centering encoder unavailable or reversed')
                last_after = value
                if last_after >= center_target:
                    break
                reactor.pause(reactor.monotonic() + .05)
        forward = self._stop_and_stable()
        if type(forward) is not int or not 0 < forward <= 1200:
            raise MeasurementUnavailable('forward stop encoder unavailable')
        if stationary_auth is not None and entry_after is not None:
            if forward < entry_after or forward - entry_after > 20:
                sample_error = 'post-entry feed outside 20 mm bound'
            else:
                try:
                    auth_result = stationary_auth()
                    if auth_result is None:
                        sample_error = 'fresh stationary authentication unavailable'
                except RFObservationError as exc:
                    sample_error = str(exc)
        target = forward + prior_offset
        if type(prior_offset) is not int or abs(prior_offset) > 21 or not 0 < target <= 1221:
            raise MeasurementUnavailable('prior park offset unavailable')
        reserve = max(target + 30, math.ceil(target * 1.05))
        if reserve > 1283:
            raise MeasurementUnavailable('reverse reserve exceeds budget')
        self.owner.return_mm = reserve
        self.owner.phase = 'restore'
        self._send_motion(1, reserve)
        deadline = reactor.monotonic() + target / 10.0 + 3
        seen_reverse = False
        previous = 0
        while reactor.monotonic() < deadline:
            self.watch.check()
            lanes = self.actions._lane_states(0, monitor=self.watch.check)
            if any(lanes.get(i) != 'ready' for i in (0, 1, 2)):
                raise MeasurementUnavailable('neighbour movement or status unknown')
            if lanes.get(3) not in ('ready', 'rollback'):
                raise MeasurementUnavailable('target reverse status unavailable')
            value = self.reader._fresh_encoder(0, 3)
            self.owner.note_encoder(value)
            if type(value) is not int:
                raise MeasurementUnavailable('reverse encoder unavailable')
            # Positive values can be stale forward telemetry; never stop on them.
            if value < 0:
                if value > previous:
                    raise MeasurementUnavailable('reverse encoder not monotonic')
                seen_reverse = True
                previous = value
                if abs(value) >= max(1, target - 1):
                    break
            if seen_reverse and lanes.get(3) == 'ready':
                raise MeasurementUnavailable('motor stopped before encoder target')
            reactor.pause(min(deadline, reactor.monotonic() + .05))
        if not seen_reverse or abs(previous) < max(1, target - 1):
            raise MeasurementUnavailable('reverse target not reached')
        reverse = self._stop_and_stable()
        if reverse >= 0:
            raise MeasurementUnavailable('reverse stop encoder direction invalid')
        offset = prior_offset + forward + reverse
        if abs(offset) > 3:
            raise MeasurementUnavailable('park offset outside 3 mm')
        result = {'circumference': None if sample_error else edges.circumference(), 'sample_error': sample_error, 'forward_mm': forward,
                  'reverse_mm': reverse, 'residual_mm': offset, 'auth_result': auth_result}
        _diagnostic_log('motion_result', {'uid': self.owner.uid, 'edges': list(edges.edges), 'sample_error': sample_error, 'forward_mm': forward, 'reverse_mm': reverse, 'prior_offset_mm': prior_offset, 'residual_mm': offset})
        return result

class StrictReader:
    """Temporarily require coherent CMD68 replies for existing UID parsing."""
    def __init__(self, reader, watch):
        self.reader, self.watch = reader, watch
        self.old = None

    def __enter__(self):
        self.old = self.reader._rc
        self.reader._rc = self.rc
        return self

    def __exit__(self, *_):
        self.reader._rc = self.old

    def rc(self, idx, slot, op, a1=0, a2=0):
        packed = (0x80000000 | ((slot >> 1) << 24) | (op << 16) | (a1 << 8) | a2)
        self.watch.check()
        response = self.reader._req(idx, 'filament_identify', {'index': packed})
        self.watch.check()
        if (not isinstance(response, dict) or response.get('_v2_frame_valid') is not True or
            response.get('_v2_payload_valid') is not True or response.get('_v2_command') != 68 or
            type(response.get('code')) is not int or response['code'] != 0 or
            str(response.get('msg','')).strip().upper() == 'FORBIDDEN'):
            return None
        data = response.get('result')
        if (not isinstance(data, dict) or data.get('_index_present') is not True or
            data.get('index') != packed or type(data.get('code')) is not int or
            not 0 <= data['code'] <= 255 or
            not (data.get('_code_present') is True or (data['code'] == 0 and data.get('_code_absent') is True))):
            return None
        return data['code']

def _diagnostic_log(event, data):
    # Logging must never alter motion or STOP handling.
    try:
        logging.info('[multiACE] measurement diagnostic %s %s', event, data)
    except Exception:
        pass


class MeasuredUID:
    """Conservative no-response candidate, never treating SELECT 1 alone as absent."""
    def __init__(self, reader, uid, minimum_separation=3):
        self.reader, self.uid, self.absent_count = reader, uid, 0
        self.minimum_separation = minimum_separation
        self.absent_first = None
        self.present_count = 0
    def _ambiguous(self, message):
        self.present_count = 0
        self.absent_count = 0
        self.absent_first = None
        raise RFObservationError(message)
    def observe(self, idx, slot, position):
        status = self.reader._rc(idx, slot, 6)
        if status == 0:
            self.absent_count = 0
            self.absent_first = None
            uid = self.reader._current_single_uid(idx, slot)
            if not uid:
                self._ambiguous('UID unavailable')
            if uid != self.uid:
                self._ambiguous('reader saw a different UID')
            self.present_count += 1
            return uid if self.present_count >= 2 else 'pending'
        if status != 1:
            _diagnostic_log('select_unavailable', {'slot': slot, 'position': position, 'select': status})
            self._ambiguous('RF collision or transport error: select=%r' % status)
        # Device register map is inferred from stock F32C and local decoder;
        # the timer-only signature requires real-hardware validation before use.
        irq = self.reader._rc(idx, slot, 0, 0x04)
        error = self.reader._rc(idx, slot, 0, 0x06)
        fifo = self.reader._rc(idx, slot, 0, 0x0a)
        control = self.reader._rc(idx, slot, 0, 0x0c)
        _diagnostic_log('no_response_registers', {'slot': slot, 'position': position, 'select': status, 'irq': irq, 'error': error, 'fifo': fifo, 'control': control})
        if (any(type(v) is not int or not 0 <= v < 255 for v in (irq, error, fifo, control)) or
            (irq & 0x01) == 0 or (irq & 0x30) != 0 or (error & 0x1b) != 0 or
            (fifo & 0x7f) != 0 or (control & 7) != 0):
            self._ambiguous('RF no-response evidence unavailable: select=%r error=%r irq=%r fifo=%r control=%r' % (status,error,irq,fifo,control))
        self.present_count = 0
        if self.absent_first is None:
            self.absent_first = position
        self.absent_count += 1
        return '' if self.absent_count >= 2 and position - self.absent_first >= self.minimum_separation else 'pending'

class AuthRFLease:
    """Protect the full attribution and measurement using one RF lease."""
    def __init__(self, ace, reader, transport, epoch, seq):
        self.ace, self.reader, self.transport = ace, reader, transport
        self.owner = AuthOwner(ace, transport, epoch, seq)
        self.owner.deadline = time.monotonic() + 600
        self.owner.active = True
        self.watch = MotionWatch(self.owner)
        self.marker = PendingMarker(ace)
        self.quarantine = None
        self.marker_started = False
        self.transferred = False
        self.cleanup_deadline = None

    def cleanup_monitor(self):
        if (self.ace._serials.get(0) is not self.transport or
            (self.cleanup_deadline is not None and time.monotonic() >= self.cleanup_deadline)):
            raise MeasurementUnavailable('original transport lost or RF cleanup timed out')

    def begin(self):
        a, actions = self.ace, self.ace._slot_actions
        if (str((a._ace_models.get(0) or ('', ''))[1]).upper() not in ('V1.1.98O', 'V1.2.00O', 'V1.2.01O') or
            len(a._ace_devices) < 1 or str(a._ace_devices[0]) != PORT):
            raise MeasurementUnavailable('unverified RF route')
        a._manual_measure_owner = self.owner
        self.watch.start()
        try:
            discovery = actions._request(0, 'discover_device', {}, 0, monitor=self.watch.check)
            if tuple(discovery.get('uid%d' % i) for i in (1, 2, 3)) != UID:
                raise MeasurementUnavailable('ACE hardware UID mismatch')
            status = actions._request(0, 'get_status', {}, 6, monitor=self.watch.check)
            if status.get('_status_wire_valid') is not True or status.get('_dryer_stopped_confirmed') is not True:
                raise MeasurementUnavailable('dryer running or status unavailable')
            if any(v != 'ready' for v in actions._lane_states(0, monitor=self.watch.check).values()):
                raise MeasurementUnavailable('all lanes must be stopped')
            if not actions._gate(0, 3, monitor=self.watch.check):
                raise MeasurementUnavailable('slot gate not present')
            self.marker.begin()
            self.marker_started = True
            self.quarantine = ReaderQuarantine(actions, self.reader, self.watch.check, self.cleanup_monitor)
            self.quarantine.begin()
            self.reader._manual_auth_owner = self.owner
            self.watch.check()
            return self
        except Exception:
            self.close(False)
            raise

    def adopt_uid(self, uid):
        self.owner.adopt_uid(uid)
        self.watch.check()

    def close(self, success=False):
        a = self.ace
        self.reader._manual_auth_owner = None
        try:
            if self.transferred:
                return
            if self.quarantine is not None and self.quarantine.original is not None:
                self.cleanup_deadline = time.monotonic() + 8
                # STOP immediately, then verify/retry after the motor-start window.
                motion = MotionProtocol(self.owner, self.watch, self.reader, a._slot_actions)
                motion.motion_start = self.owner.last_motion_start
                motion._stop_and_stable(monitor=self.cleanup_monitor)
                self.quarantine.restore()
            if (self.marker_started and success and self.owner.route_valid() and
                not self.reader._motion_uncertain and not any(self.reader._motion_ledger.values()) and
                not any(self.reader._encoder_ledger.values())):
                self.marker.finish(self.marker.offset)
        except Exception as exc:
            a._latch_stop_fault(0, 3, 'attribution RF restore unconfirmed: %s' % exc)
            raise
        finally:
            self.owner.revoke()
            self.watch.close()
            if getattr(a, '_manual_measure_owner', None) is self.owner:
                a._manual_measure_owner = None

def run_measurement(ace, reader, transport, epoch, uid, seq, density, read_residual=0, auth_lease=None, stationary_auth=None):
    """Measure the verified route with a writer-bound, crash-safe session.

    A failure after pending-marker publication never releases that marker.
    """
    from .ace_filament_approximation import estimate_remaining
    a, actions = ace, ace._slot_actions
    model = (a._ace_models.get(0) or ('', ''))[1]
    if (str(model).upper() not in ('V1.1.98O', 'V1.2.00O', 'V1.2.01O') or len(a._ace_devices) < 1 or
        str(a._ace_devices[0]) != PORT):
        raise MeasurementUnavailable('unverified measurement device')
    owner = MeasureOwner(a, transport, epoch, uid, seq)
    owner.deadline = time.monotonic() + 480
    owner.active = True
    watch = MotionWatch(owner)
    marker = auth_lease.marker if auth_lease is not None else PendingMarker(a)
    quarantine = None
    marker_started = False
    stopped = False
    motion = None
    old_observer = getattr(reader, '_measurement_observed_uid', None)
    def cleanup_monitor():
        if a._serials.get(0) is not transport:
            raise MeasurementUnavailable('original transport lost during cleanup')
    if auth_lease is not None:
        auth_lease.watch.check()
        if auth_lease.owner.observed_uid != uid or auth_lease.quarantine is None:
            raise MeasurementUnavailable('attribution lease identity unavailable')
        auth_lease.reader._manual_auth_owner = None
    a._manual_measure_owner = owner
    try:
        watch.start()
        if auth_lease is not None:
            quarantine = auth_lease.quarantine
            marker_started = True
            auth_lease.transferred = True
        discovery = actions._request(0, 'discover_device', {}, 0, monitor=watch.check)
        if tuple(discovery.get('uid%d' % i) for i in (1, 2, 3)) != UID:
            raise MeasurementUnavailable('ACE hardware UID mismatch')
        fresh_status = actions._request(0, 'get_status', {}, 6, monitor=watch.check)
        if fresh_status.get('_status_wire_valid') is not True or fresh_status.get('_dryer_stopped_confirmed') is not True:
            raise MeasurementUnavailable('dryer running or status unavailable')
        if any(x != 'ready' for x in actions._lane_states(0, monitor=watch.check).values()):
            raise MeasurementUnavailable('all lanes must be stopped')
        stopped = True
        if not actions._gate(0, 3, monitor=watch.check):
            raise MeasurementUnavailable('slot gate not present')
        neighbour = reader._neighbour_signature(0, 3)
        if neighbour is None:
            raise MeasurementUnavailable('neighbour state unknown')
        with StrictReader(reader, watch):
            if auth_lease is None:
                quarantine = ReaderQuarantine(actions, reader, watch.check, cleanup_monitor)
                marker.begin()
                marker_started = True
                quarantine.begin()
            else:
                quarantine.monitor = watch.check
                quarantine.cleanup_monitor = cleanup_monitor
                quarantine._await((0, 0))
            # Leave pending across process failure, including before first wire write.
            measured_uid = MeasuredUID(reader, uid)
            reader._measurement_observed_uid = measured_uid.observe
            motion = MotionProtocol(owner, watch, reader, actions)
            stopped = False
            if type(read_residual) is not int or abs(read_residual) > 18:
                raise MeasurementUnavailable('RFID return residual unavailable')
            if stationary_auth is None:
                result = motion.measure(marker.offset + read_residual)
            else:
                result = motion.measure(marker.offset + read_residual, stationary_auth=stationary_auth,
                                        neighbour_signature=neighbour)
            stopped = True
            if reader._neighbour_signature(0, 3) != neighbour:
                raise MeasurementUnavailable('neighbour changed during measurement')
            # Fast path authenticated the target while stationary at the
            # second passage; the final park can be outside the antenna field.
            if stationary_auth is None:
                try:
                    final_check = MeasuredUID(reader, uid, minimum_separation=0)
                    final_check.observe(0, 3, 0)
                    final_uid = final_check.observe(0, 3, 0)
                    if final_uid == 'pending' or (final_uid and final_uid != uid):
                        result['circumference'] = None
                except RFObservationError as exc:
                    result['circumference'] = None
                    result['sample_error'] = str(exc)
            watch.check()
            quarantine.restore()
            quarantine = None
            watch.check()
            marker.finish(result['residual_mm'])
            _diagnostic_log('final_result', {'uid': uid, 'circumference': result.get('circumference'), 'sample_error': result.get('sample_error'), 'forward_mm': result.get('forward_mm'), 'reverse_mm': result.get('reverse_mm'), 'residual_mm': result.get('residual_mm')})
            if stationary_auth is not None and result.get('auth_result') is not None:
                density = result['auth_result']['density']
            if result['circumference'] is not None and density is not None:
                result['estimate'] = estimate_remaining(result['circumference']['mid_mm'], 87, 60, 1.75, density)
            else:
                result['estimate'] = None
            return result
    except Exception:
        if marker_started and not stopped:
            try:
                cleanup_motion = motion or MotionProtocol(owner, watch, reader, actions)
                cleanup_motion._stop_and_stable(monitor=cleanup_monitor)
                stopped = True
            except Exception as stop_error:
                a._latch_stop_fault(0, 3, 'measurement stop unconfirmed: %s' % stop_error)
        if quarantine is not None and stopped and quarantine.original is not None:
            try: quarantine.restore()
            except Exception as restore_error:
                a._latch_stop_fault(0, 3, 'measurement reader restore unconfirmed: %s' % restore_error)
                if not marker_started:
                    from .ace_slot_actions import persist_rfid_position_uncertain
                    persist_rfid_position_uncertain(a, 0, 3)
        raise
    finally:
        owner.revoke()
        watch.close()
        if old_observer is None:
            try: del reader._measurement_observed_uid
            except AttributeError: pass
        else:
            reader._measurement_observed_uid = old_observer
        if getattr(a, '_manual_measure_owner', None) is owner:
            a._manual_measure_owner = None

def capture_spool(ace, uid):
    if getattr(ace, 'spool_mode', None) != 'local':
        return None
    key = ace._spool_key(0, 3)
    sid = ace._spool_binding.get(key)
    if not uid or (getattr(ace, '_rc_last_uid', {}) or {}).get((0, 3)) != uid:
        return None
    matches = [key for key, item in ace._spools.items() if ace._sku_canon(uid) in ace._sku_codes(item.get('sku'))]
    if sid is None:
        if matches:
            return None
        info = ((getattr(ace, '_v2_filament_info_per_ace', {}) or {}).get(0) or {}).get(3) or {}
        if (info.get('host') is not True or info.get('uid') != uid or not info.get('type') or
                ace._spool_by_sku(uid)[1] is not None):
            return None
        material = str(info['type']).split()[0]
        identity = {'material': material, 'vendor': info.get('brand') or '',
                    'subtype': info.get('subtype') or '', 'color': tuple(info.get('color') or ())}
        return {'sid': None, 'uid': uid, 'identity': identity, 'material': material,
                'density': ace._spool_density(identity), 'density_source':'material_or_default', 'weight_g': None}
    spool = ace._spools.get(sid)
    if not isinstance(spool, dict) or matches != [sid]:
        return None
    try:
        explicit_density = float(spool.get('density') or 0)
        density_source = 'spool' if math.isfinite(explicit_density) and explicit_density > 0 else 'material_or_default'
    except (TypeError, ValueError):
        density_source = 'material_or_default'
    return {'sid': sid, 'uid': uid, 'material': spool.get('material'),
            'density': ace._spool_density(spool), 'density_source': density_source,
            'weight_g': spool.get('weight_g')}

def publish_spool(ace, snapshot, result):
    import math
    if not snapshot or capture_spool(ace, snapshot['uid']) != snapshot:
        raise MeasurementUnavailable('spool binding or data changed')
    estimate, bounds = result.get('estimate'), result.get('circumference')
    if not isinstance(estimate, dict) or not isinstance(bounds, dict):
        raise MeasurementUnavailable('measurement estimate unavailable')
    grams = estimate.get('remaining_g')
    if type(grams) not in (int, float) or not math.isfinite(grams) or grams < 0:
        raise MeasurementUnavailable('measurement estimate invalid')
    sid = snapshot['sid']
    if sid is None:
        identity = snapshot['identity']
        sid = str(ace._spool_next_id)
        if sid in ace._spools or ace._spool_by_sku(snapshot['uid'])[1] is not None:
            raise MeasurementUnavailable('spool identity conflict')
        color = identity['color']
        if len(color) != 3 or any(type(v) is not int or not 0 <= v <= 255 for v in color):
            raise MeasurementUnavailable('spool colour unavailable')
        # Spool ADD immediately saves and suppresses I/O failure; use the
        # existing local schema and dirty flush so the caller can verify it.
        spool = {'id':sid, 'material':identity['material'],
                 'color':'%02X%02X%02X' % color, 'vendor':identity['vendor'],
                 'subtype':identity['subtype'], 'label':'', 'spoolman_id':'',
                 'sku':snapshot['uid'], 'used_mm':0.0}
        ace._spools[sid] = spool
        ace._spool_binding[ace._spool_key(0, 3)] = sid
        ace._spool_next_id += 1
    else:
        spool = ace._spools[sid]
    spool['weight_g'] = round(grams, 1)
    spool['remaining_measurement'] = {'source': 'rfid_encoder_geometry',
        'uid': snapshot['uid'], 'time': time.time(), 'circumference_mm': dict(bounds),
        'park_offset_mm': result['residual_mm'], 'quality': 'estimated',
        'assumptions': copy.deepcopy(estimate.get('assumptions')),
        'density_source': snapshot['density_source'], 'method': estimate.get('method')}
    ace._spool_dirty = True
    return spool['weight_g']
