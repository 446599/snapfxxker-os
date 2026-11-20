"""One-slot STOP with fresh V2 evidence, shared by feed and buffer handoff.

The first STOP is immediate. A second STOP after the setup window handles a
firmware task that accepted the first request before its motor was armed.
Neither an ACK nor cached ready releases ownership on its own.
"""
import math

from .ace_stop_guard import StopUnconfirmedError


STOPPED = frozenset(('ready', 'feed_error', 'rollback_error', 'assist_error',
                    'preload_error', 'stuck_error', 'tangled_error', 'motor_error'))


def decode_sample(response, slot, allow_empty_zero=False):
    result = response.get('result') or {}
    rows = result.get('feed_info')
    if result.get('_feed_wire_valid') is not True or not isinstance(rows, list):
        raise ValueError('CMD76 编码器帧无效')
    rows = [row for row in rows if isinstance(row, dict)
            and type(row.get('index')) is int and row['index'] == slot]
    if len(rows) != 1 or rows[0].get('_wire_valid') is not True:
        raise ValueError('CMD76 原槽反馈缺失或重复')
    row = rows[0]
    if any(type(row.get(key)) is not int or not 0 <= row[key] < (1 << 64)
           for key in ('steps', 'length', 'decoder')):
        raise ValueError('CMD76 计数无效')
    if row.get('_decoder_present') is not True and not (
            allow_empty_zero and row.get('_decoder_present') is False
            and row['steps'] == row['length'] == row['decoder'] == 0):
        raise ValueError('CMD76 缺少耗材编码器，未用电机距离代替')
    decoder = row['decoder'] - (1 << 64) if row['decoder'] >= (1 << 63) else row['decoder']
    return float(row['length']), float(decoder), row['steps']


def stop_and_verify(ace, idx, slot, *, transport=None, identity=None,
                    timeout=3.0, monitor=None):
    """Return a target-lane stillness receipt; retain a fault on uncertainty.

Other slots may keep assisting their own toolheads. This receipt says nothing
about an empty tube, a cleared mechanical fault, or the position of a Y joint.
The caller must inhibit rearming its own slot until its transaction finishes.
"""
    if (type(idx) is not int or not 0 <= idx < 4 or type(slot) is not int
            or not 0 <= slot < 4 or type(timeout) not in (int, float)
            or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError('停止目标或时限无效')
    reactor = ace.reactor
    transport = ace._serials.get(idx) if transport is None else transport
    identity = ace._location_identity(idx) if identity is None else identity
    started = reactor.monotonic()
    deadline = started + timeout

    def guard():
        if (transport is None or ace._serials.get(idx) is not transport
                or ace._connected_per_ace.get(idx) is not True
                or getattr(ace, '_reconnecting_per_ace', {}).get(idx)
                or ace._location_identity(idx) != identity
                or not isinstance(identity, (tuple, list)) or len(identity) != 2
                or not identity[0] or identity[1] is None):
            raise ValueError('原 ACE 连接或 UID 已变化，停止证据未转移到新设备')
        if reactor.monotonic() >= deadline:
            raise ValueError('停止确认超过时限')
        if monitor is not None:
            monitor()

    def query(method, command, params=None):
        guard()
        box = {'active': True, 'response': None}
        def callback(*args, **kwargs):
            if box['active'] and box['response'] is None:
                box['response'] = kwargs.get('response', args[-1] if args else None)
        request = {'method': method, 'params': params or {},
                   '_insert_stop_transport': transport, '_insert_reject': callback}
        try:
            accepted = ace.send_request_to(idx, request, callback)
            if accepted is False:
                raise ValueError('%s 请求未发送' % method)
            reply_deadline = min(deadline, reactor.monotonic() + .8)
            while box['response'] is None and reactor.monotonic() < reply_deadline:
                guard()
                reactor.pause(min(reply_deadline, reactor.monotonic() + .02))
            guard()
            response = box['response']
            if (not isinstance(response, dict) or type(response.get('code')) is not int
                    or response['code'] != 0 or response.get('_v2_command') != command
                    or response.get('_v2_frame_valid') is not True
                    or response.get('_v2_payload_valid') is not True
                    or type(request.get('id')) is not int
                    or type(response.get('id')) is not int or response['id'] != request['id']
                    or str(response.get('msg', '')).strip().upper() == 'FORBIDDEN'):
                raise ValueError('%s 本次响应缺失、被拒绝或帧无效' % method)
            return response
        finally:
            box['active'] = False

    def lane_state(response):
        result = response.get('result') or {}
        rows = result.get('slots')
        if result.get('_status_wire_valid') is not True or not isinstance(rows, list):
            raise ValueError('CMD6 槽位状态帧无效')
        rows = [row for row in rows if isinstance(row, dict)
                and type(row.get('index')) is int and row['index'] == slot]
        if len(rows) != 1 or rows[0].get('_wire_valid') is not True:
            raise ValueError('CMD6 原槽状态缺失或重复')
        return rows[0].get('slot_status')

    try:
        # An uncertain first ACK is retryable because STOP is idempotent.
        first_error = None
        try:
            query('stop_feed_filament', 9, {'index': slot})
        except ValueError as exc:
            first_error = str(exc)
        while reactor.monotonic() < started + .25:
            guard()
            reactor.pause(min(started + .25, reactor.monotonic() + .05))
        query('stop_feed_filament', 9, {'index': slot})
        acknowledged_at = reactor.monotonic()
        previous = None
        stable_since = None
        count = 0
        last_error = first_error
        while reactor.monotonic() < deadline:
            guard()
            try:
                state = lane_state(query('get_status', 6))
                gates = ace._gate_status_per_ace.get(idx) or []
                empty = slot < len(gates) and gates[slot] == 0
                sample = decode_sample(query('get_feed_info', 76), slot,
                                       allow_empty_zero=empty and state == 'ready')
                after = lane_state(query('get_status', 6))
                now = reactor.monotonic()
                current = (state, sample)
                if state not in STOPPED or after != state:
                    previous, stable_since, count = None, None, 0
                elif current == previous:
                    count += 1
                else:
                    previous, stable_since, count = current, now, 1
                if count >= 3 and now - stable_since >= .3:
                    guard()
                    return {'slot_status': state, 'sample': sample, 'at': now,
                            'acknowledged_at': acknowledged_at, 'samples': count,
                            'transport': transport, 'identity': identity,
                            'first_stop_error': first_error}
            except ValueError as exc:
                # Read-only queries may be retried within the same finite deadline.
                # A missed response cannot become a stationary sample.
                guard()
                last_error = str(exc)
                previous, stable_since, count = None, None, 0
            reactor.pause(min(deadline, reactor.monotonic() + .15))
        raise ValueError(last_error or '停止后仍在运动或缺少连续静止证据')
    except Exception as exc:
        ace._latch_stop_fault(idx, slot, str(exc), identity=identity)
        raise StopUnconfirmedError('ACE %d 槽 %d 停止未确认：%s' %
                                   (idx + 1, slot + 1, exc)) from exc
