"""Observe one legacy V2 load until the physical head switch or a fault.

This is a live integration helper, not a second transport or a position oracle.
One CMD76 request is outstanding at most; a sensor poll never waits on serial.
The writer checks ``allows_wire`` before sending queued motion. A failed load
does not automatically issue another full-length feed or infer a clear Y tube.
"""
import logging
import math

from .ace_stop_guard import StopUnconfirmedError
from .ace_v2_standstill import stop_and_verify


class FeedSupervisionError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class FeedChannelBusy(FeedSupervisionError):
    def __init__(self):
        super().__init__('channel_busy', '送料通道正在执行另一操作')


class FeedSupervisor:
    # These are conservative software observation limits, not pipe calibration.
    POLL_S = 0.25
    PRECHECK_TIMEOUT_S = 3.0
    ACK_TIMEOUT_S = 2.0
    TELEMETRY_TIMEOUT_S = 1.5
    NO_PROGRESS_S = 2.0
    WINDOW_MOTOR_MM = 30.0
    MIN_PROGRESS_RATIO = 0.5
    BAD_WINDOWS = 2
    STARTUP_RESET_S = 1.0
    STOP_VERIFY_S = 3.0
    MOVING = frozenset(('feeding', 'rollback', 'assisting', 'rollback_assisting',
                        'preloading', 'upgrading'))
    STOPPED = frozenset(('ready', 'feed_error', 'rollback_error', 'assist_error',
                        'preload_error', 'stuck_error', 'tangled_error', 'motor_error'))
    MOTION_METHODS = frozenset(('feed_filament', 'unwind_filament',
                              'feed_or_rollback_raw', 'start_feed_assist',
                              'update_feeding_speed', 'insert_policy_start', 'raw'))

    def __init__(self, ace, idx, slot, head, sensor, length, speed):
        self.ace, self.idx, self.slot, self.head = ace, idx, slot, head
        previous_seq = getattr(ace, '_flow_feed_seq', 0)
        self.seq = (previous_seq if type(previous_seq) is int and previous_seq >= 0 else 0) + 1
        ace._flow_feed_seq = self.seq
        self.epoch = getattr(getattr(ace, '_slot_actions', None), 'epoch', None)
        self.sensor = sensor
        self.length, self.speed = float(length), float(speed)
        if not all(math.isfinite(x) and x > 0 for x in (self.length, self.speed)):
            raise FeedSupervisionError('invalid_motion_budget', '送料长度或速度无效')
        self.transport = ace._serials.get(idx)
        self.identity = ace._location_identity(idx)
        self.insertion_epoch = getattr(ace, '_insert_epochs', {}).get((idx, slot))
        self.token = object()
        self.active = False
        self.cancelled = False
        self.stopping = False
        self.ack = False
        self.feed_admitted = False
        self.feed_enqueued = False
        self.error = None
        self.pending = None
        self.pending_since = None
        self.last_sample = None
        self.origin_sample = None
        self.observed_samples = 0
        self.measurement_error = None
        self.window = None
        self.decoder_sign = None
        direction = getattr(ace, '_insert_encoder_direction', {}).get((idx, slot))
        if (direction and len(direction) == 3 and direction[0] is self.transport
                and direction[1] == self.identity and direction[2] in (-1, 1)):
            self.decoder_sign = direction[2]
        self.known_decoder_sign = self.decoder_sign
        self.bad_windows = 0
        self.startup_rebases = 0
        self.terminal_sample = None
        self.terminal_slot_status = None
        self.started_at = ace.reactor.monotonic()
        self.last_valid_at = self.started_at
        self.last_progress_at = self.started_at
        self.next_poll_at = self.started_at
        self.deadline = self.started_at + self.length / self.speed + 5.0
        self.result = 'preparing'
        self._check_identity()
        present = self.sensor()
        if present is None:
            raise FeedSupervisionError('head_sensor_unknown', '喷头原始到料状态未知')
        if present is not False:
            raise FeedSupervisionError('head_already_loaded', '喷头原始开关已检测到料，未开始送料')
        if self.gate() is not True:
            raise FeedSupervisionError('inlet_unconfirmed', 'ACE 入口有料状态未确认')
        owners = getattr(ace, '_feed_supervisors', None)
        if owners is None:
            ace._feed_supervisors = owners = {}
        old = owners.get((idx, slot))
        if old is not None and old.active:
            raise FeedChannelBusy()
        owners[(idx, slot)] = self
        self.active = True
        self.result = 'feeding'

    def gate(self):
        rows = self.ace._gate_status_per_ace.get(self.idx) or []
        value = rows[self.slot] if self.slot < len(rows) else None
        return True if value == 1 else False if value == 0 else None

    def _check_identity(self, require_insertion=True):
        a = self.ace
        if (self.transport is None or a._serials.get(self.idx) is not self.transport
                or not a._connected_per_ace.get(self.idx)
                or a._reconnecting_per_ace.get(self.idx)
                or a._location_identity(self.idx) != self.identity
                or not self.identity or self.identity[1] is None):
            raise FeedSupervisionError('connection_changed', '送料期间 ACE 连接或设备身份改变')
        if require_insertion and getattr(a, '_insert_epochs', {}).get((self.idx, self.slot)) != self.insertion_epoch:
            raise FeedSupervisionError('insertion_changed', '送料期间入口插料代次改变')

    @staticmethod
    def _ok(response, command):
        return (isinstance(response, dict) and type(response.get('code')) is int
                and response['code'] == 0
                and response.get('_v2_command') == command
                and response.get('_v2_frame_valid') is True
                and response.get('_v2_payload_valid') is True
                and str(response.get('msg', '')).strip().upper() != 'FORBIDDEN')

    def _decode(self, response):
        if not self._ok(response, 76):
            raise FeedSupervisionError('telemetry_invalid', '送料编码器响应无效')
        result = response.get('result') or {}
        rows = [row for row in result.get('feed_info', [])
                if isinstance(row, dict) and row.get('index') == self.slot]
        if (result.get('_feed_wire_valid') is not True or len(rows) != 1
                or rows[0].get('_wire_valid') is not True
                or rows[0].get('_decoder_present') is not True):
            raise FeedSupervisionError('decoder_unavailable', '送料编码器未提供有效数值')
        row = rows[0]
        if any(type(row.get(name)) is not int or not 0 <= row[name] < 1 << 64
               for name in ('length', 'steps', 'decoder')):
            raise FeedSupervisionError('telemetry_invalid', '送料计数格式无效')
        decoder = row['decoder'] - (1 << 64) if row['decoder'] >= 1 << 63 else row['decoder']
        return (float(row['length']), float(decoder), row['steps'])

    def feed_request(self, request):
        request['_feed_supervisor'] = self
        request['_feed_supervisor_token'] = self.token
        self.feed_enqueued = True

    def _arm_deadlines(self):
        # Preflight can wait for the old assist to stop. Its budget is separate
        # from the subsequent command ACK and movement observation windows.
        now = self.ace.reactor.monotonic()
        self.started_at = self.last_valid_at = self.last_progress_at = now
        self.next_poll_at = now
        self.deadline = now + self.length / self.speed + 5.0

    def wait_before_feed(self):
        """Fresh, bounded CMD6 admission, without legacy reconnect/wait loops."""
        needs_stop = getattr(self.ace, '_native_assist_needs_stop', None)
        if needs_stop is not None and needs_stop(self.idx, self.slot):
            # An armed buffer can report ready while waiting for the toolhead.
            # Hand off this slot only; other heads may still need assistance.
            self.result = 'stopping_assist'
            self.stop_bound()
            self.ace._clear_fa_cache_for(self.idx, self.slot)
            self.stopping = False
            self.terminal_sample = self.terminal_slot_status = None
        deadline = self.ace.reactor.monotonic() + self.PRECHECK_TIMEOUT_S
        self.result = 'preparing'
        while self.ace.reactor.monotonic() < deadline:
            response = self._query('get_status', 6, deadline=deadline,
                                   cancellable=True, require_insertion=True)
            result = response.get('result') or {}
            rows = [row for row in result.get('slots', [])
                    if isinstance(row, dict) and row.get('index') == self.slot]
            if (result.get('_status_wire_valid') is not True or len(rows) != 1
                    or rows[0].get('_wire_valid') is not True
                    or result.get('status') not in ('ready', 'busy')):
                raise FeedSupervisionError('preflight_invalid', '送料前的槽位状态无效')
            if self.sensor() is not False or self.gate() is not True:
                raise FeedSupervisionError('preflight_changed', '送料前的到料或入口信号改变，未开始长推')
            all_rows = result.get('slots') or []
            if (len(all_rows) != 4
                    or any(not isinstance(row, dict) or row.get('_wire_valid') is not True
                           or type(row.get('index')) is not int for row in all_rows)
                    or sorted(row['index'] for row in all_rows) != [0, 1, 2, 3]):
                raise FeedSupervisionError('preflight_invalid', '送料前四槽状态缺失或重复')
            owned_assist = getattr(self.ace, '_native_assist_is_armed', None)
            other_busy = [row for row in all_rows if row.get('index') != self.slot
                          and row.get('slot_status') not in self.STOPPED
                          and not (row.get('slot_status') in ('assisting', 'rollback_assisting')
                                   and owned_assist is not None
                                   and owned_assist(self.idx, row.get('index')))]
            if rows[0].get('slot_status') == 'ready' and not other_busy:
                self._arm_deadlines()
                self.result = 'feeding'
                return
            self.ace.reactor.pause(min(deadline, self.ace.reactor.monotonic() + 0.1))
        raise FeedSupervisionError('preflight_busy', 'ACE 未在短时预检内就绪，未开始长推')

    def allows_wire(self, idx, request, serial):
        method = request.get('method')
        if method not in self.MOTION_METHODS:
            return True  # STOP/read commands must remain available.
        try:
            self._check_identity()
            params = request.get('params') or {}
            allowed = (self.active and not self.stopping and not self.cancelled
                    and not self.feed_admitted
                    and idx == self.idx and serial is self.transport
                    and method == 'feed_filament'
                    and request.get('_feed_supervisor') is self
                    and request.get('_feed_supervisor_token') is self.token
                    and params.get('index') == self.slot
                    and params.get('length') == self.length
                    and params.get('speed') == self.speed
                    and self.sensor() is False and self.gate() is True)
            if allowed:
                # Consumed at the last writer gate, before a possibly partial
                # serial write. An uncertain write is never resent as a new feed.
                self.feed_admitted = True
                self._arm_deadlines()
            return allowed
        except Exception:
            return False

    def feed_ack(self, response, token):
        if not self.active or self.token is not token:
            return
        if not self._ok(response, 8):
            self.error = FeedSupervisionError('feed_rejected', 'ACE 未确认本次送料指令')
        else:
            self.ack = True
            # Capture a possible zero-origin sample before the firmware finishes
            # its motor setup, instead of losing the first polling interval.
            self._poll(self.ace.reactor.monotonic())

    def cancel(self):
        self.cancelled = True
        self.result = 'cancelling'

    def _observe(self, sample, now):
        current = sample + (now,)
        previous = self.last_sample
        self.last_valid_at = now
        if previous is None:
            self.last_sample = self.window = current
            self.origin_sample = current
            self.observed_samples = 1
            self.last_progress_at = now
            return
        motor = sample[0] - previous[0]
        decoder = sample[1] - previous[1]
        elapsed = now - previous[3]
        reset = motor < 0 or sample[2] < previous[2]
        implausible = (elapsed <= 0 or
                       max(abs(motor), abs(decoder)) > self.speed * max(elapsed, 0) * 3 + 10)
        if reset or implausible:
            if now - self.started_at <= self.STARTUP_RESET_S and self.startup_rebases == 0:
                # One initial per-command reset is expected on legacy V2. It
                # supplies no movement evidence and never adds a travel span.
                self.startup_rebases += 1
                self.last_sample = self.window = current
                self.origin_sample = current
                self.observed_samples = 1
                self.decoder_sign = self.known_decoder_sign
                return
            raise FeedSupervisionError('counter_reset', '送料计数重置或跳变，本次位置待核')
        if abs(decoder) >= 1:
            sign = 1 if decoder > 0 else -1
            if self.decoder_sign is not None and sign != self.decoder_sign:
                raise FeedSupervisionError('decoder_reversed', '同一次前送中编码器方向反转')
            self.decoder_sign = sign
            self.last_progress_at = now
        self.last_sample = current
        self.observed_samples += 1
        motor_window = current[0] - self.window[0]
        if motor_window >= self.WINDOW_MOTOR_MM:
            material_window = abs(current[1] - self.window[1])
            ratio = material_window / motor_window
            self.bad_windows = self.bad_windows + 1 if ratio < self.MIN_PROGRESS_RATIO else 0
            logging.info('[multiACE feed-supervisor] ACE=%d slot=%d head=%d motor=%.1f decoder=%.1f ratio=%.3f bad=%d',
                         self.idx, self.slot, self.head, motor_window, material_window,
                         ratio, self.bad_windows)
            self.window = current
            if self.bad_windows >= self.BAD_WINDOWS:
                raise FeedSupervisionError('filament_slip', '送料机构在转，但耗材连续明显少走，已停止长推')

    def _poll(self, now):
        if self.pending is not None or now < self.next_poll_at or not self.ack:
            return
        marker = object()
        self.pending, self.pending_since = marker, now
        self.next_poll_at = now + self.POLL_S
        token = self.token
        request = {'method': 'get_feed_info', '_insert_stop_transport': self.transport}

        def callback(*args, **kwargs):
            if not self.active or self.token is not token or self.pending is not marker or self.stopping:
                return
            self.pending = None
            response = kwargs.get('response', args[-1] if args else None)
            try:
                self._check_identity()
                if (not isinstance(response, dict) or type(request.get('id')) is not int
                        or type(response.get('id')) is not int
                        or response['id'] != request['id']):
                    raise FeedSupervisionError('telemetry_invalid', '送料遥测不是本次请求的响应')
                self._observe(self._decode(response), self.ace.reactor.monotonic())
            except FeedSupervisionError as exc:
                self.error = exc
            except Exception as exc:
                self.error = FeedSupervisionError('telemetry_invalid', '送料遥测处理失败：%s' % exc)
        try:
            accepted = self.ace.send_request_to(self.idx, request, callback)
            if accepted is False:
                self.pending = None
                self.error = FeedSupervisionError('telemetry_rejected', '送料遥测请求被拒绝')
        except Exception as exc:
            self.pending = None
            self.error = FeedSupervisionError('telemetry_unavailable', '送料遥测请求失败：%s' % exc)

    def check(self):
        self._check_identity()
        now = self.ace.reactor.monotonic()
        if self.cancelled:
            raise FeedSupervisionError('cancelled', '已取消本次进料，正在确认停止')
        value = self.sensor()
        if value is None:
            raise FeedSupervisionError('head_sensor_unknown', '喷头原始到料信号未知')
        if self.error is not None:
            raise self.error
        if value is True:
            return True
        gate = self.gate()
        if gate is not True:
            raise FeedSupervisionError('inlet_empty' if gate is False else 'inlet_unknown',
                                       'ACE 入口已无料' if gate is False else 'ACE 入口状态未知')
        if not self.ack and now - self.started_at >= self.ACK_TIMEOUT_S:
            raise FeedSupervisionError('feed_ack_timeout', '送料指令确认超时')
        if self.ack and now - self.last_valid_at >= self.TELEMETRY_TIMEOUT_S:
            raise FeedSupervisionError('telemetry_timeout', '送料遥测超时，已停止后续长推')
        if self.ack and now - self.last_progress_at >= self.NO_PROGRESS_S:
            raise FeedSupervisionError('no_progress', '送料编码器持续无进展')
        if now >= self.deadline:
            raise FeedSupervisionError('head_not_reached', '已到本次送料限程，喷头仍未确认到料')
        self._poll(now)
        if self.error is not None:
            raise self.error
        return False

    def _query(self, method, command, params=None, deadline=None,
               cancellable=False, require_insertion=False):
        def guard():
            self._check_identity(require_insertion=require_insertion)
            if cancellable and self.cancelled:
                raise FeedSupervisionError('cancelled', '已取消本次送料预检，未开始长推')
        guard()
        box = []
        token = self.token

        def callback(*args, **kwargs):
            if self.active and self.token is token:
                box.append(kwargs.get('response', args[-1] if args else None))
        request = {'method': method, 'params': params or {},
                   '_insert_stop_transport': self.transport, '_insert_reject': callback}
        self.ace.send_request_to(self.idx, request, callback)
        end = min(deadline, self.ace.reactor.monotonic() + 1.0) if deadline is not None else self.ace.reactor.monotonic() + 1.0
        while not box and self.ace.reactor.monotonic() < end:
            guard()
            self.ace.reactor.pause(min(end, self.ace.reactor.monotonic() + 0.02))
        guard()
        if (not box or not self._ok(box[0], command)
                or type(request.get('id')) is not int
                or type(box[0].get('id')) is not int or box[0]['id'] != request['id']):
            if cancellable:
                raise FeedSupervisionError('preflight_unavailable', '送料前的新鲜状态不可用，未开始长推')
            raise FeedSupervisionError('stop_evidence_unavailable', '停止确认遥测不可用')
        return box[0]

    def stop_bound(self):
        """Original transport STOP ACK plus fresh per-slot stationary samples."""
        self.stopping = True
        self.pending = None  # Late in-flight feeding polls cannot mutate evidence.
        try:
            receipt = stop_and_verify(self.ace, self.idx, self.slot,
                transport=self.transport, identity=self.identity, timeout=self.STOP_VERIFY_S)
            self.terminal_sample = receipt['sample']
            self.terminal_slot_status = receipt['slot_status']
            self.result = 'stopped'
            if self.feed_admitted and self.ack:
                try:
                    # Include the final coasting distance. A counter reset here
                    # invalidates measurement, not the independent STOP receipt.
                    self._observe(self.terminal_sample, receipt['at'])
                except FeedSupervisionError as exc:
                    self.measurement_error = str(exc)
            return
        except StopUnconfirmedError:
            self.result = 'stop_unconfirmed'
            raise

    def travel_receipt(self):
        """Observed material travel for this motion, never commanded distance.

If the first fresh sample was delayed, its prefix remains unmeasured. The
returned lower bound is still useful for a conservative ordinary retreat.
"""
        if (not self.ack or not self.feed_admitted or self.cancelled or self.error is not None
                or self.terminal_sample is None
                or self.terminal_slot_status != 'ready' or self.measurement_error
                or self.origin_sample is None or self.observed_samples < 2
                or self.decoder_sign not in (-1, 1)):
            return None
        distance = (self.terminal_sample[1] - self.origin_sample[1]) * self.decoder_sign
        # A stale counter from the previous reverse move must not make a
        # zero-crossing look like additional forward material travel.
        distance = min(distance, self.terminal_sample[1] * self.decoder_sign)
        if not math.isfinite(distance) or not 0 < distance <= self.length:
            return None
        zero_origin = self.origin_sample[:3] == (0.0, 0.0, 0)
        return {'distance_mm': distance,
                'basis': 'observed_zero_origin' if zero_origin else 'observed_span_lower_bound',
                'samples': self.observed_samples, 'seq': self.seq,
                'ace': self.idx, 'slot': self.slot, 'head': self.head,
                'uid': list(self.identity[1]), 'insert_epoch': self.insertion_epoch}

    def snapshot(self):
        return {'ace': self.idx, 'slot': self.slot, 'head': self.head,
                'seq': self.seq, 'kind': 'feed', 'active': self.active,
                'epoch': self.epoch,
                'message': str(self.error) if self.error is not None else self.result,
                'state': self.result, 'cancelled': self.cancelled,
                'code': self.error.code if self.error is not None else None,
                'motor_mm': self.last_sample[0] if self.last_sample else None,
                'decoder_mm': self.last_sample[1] if self.last_sample else None,
                'bad_windows': self.bad_windows,
                'travel_receipt': self.travel_receipt(),
                'measurement_error': self.measurement_error,
                'terminal_slot_status': self.terminal_slot_status,
                'stop_verified': self.terminal_sample is not None}

    def close(self):
        self.active = False
        self.pending = None
        self.token = None
        owners = getattr(self.ace, '_feed_supervisors', {})
        if owners.get((self.idx, self.slot)) is self:
            del owners[(self.idx, self.slot)]
        results = getattr(self.ace, '_feed_supervisor_results', None)
        if results is None:
            self.ace._feed_supervisor_results = results = {}
        results[(self.idx, self.slot)] = self.snapshot()
