"""Bounded command-readiness queries for legacy V2 devices.

Readiness is not a STOP/standstill receipt. An explicitly owned assist may
accept the next command while its motor remains active. This helper neither
stops that owner nor clears a source, physical hold, or STOP fault.
"""
import math


REPLY_SECONDS = 3.0
POLL_SECONDS = .25
ASSIST_STATES = frozenset(('assisting', 'rollback_assisting'))


class ReadyWaitError(RuntimeError):
    pass


def _identity(ace, idx):
    identity = ace._location_identity(idx)
    if not isinstance(identity, (tuple, list)) or len(identity) != 2:
        return None
    port, uid = identity
    if (not isinstance(port, str) or not port or not isinstance(uid, (tuple, list))
            or len(uid) != 3 or any(type(value) is not int for value in uid)):
        return None
    return port, tuple(uid)


def _classify(response, request, assist_slot):
    """Return (ready, diagnostic); raise only a validated device/host error."""
    if not isinstance(response, dict):
        return False, 'CMD6 未收到有效响应'
    code = response.get('code')
    message = str(response.get('msg', ''))
    if type(code) is int and (code != 0 or message.strip().upper() == 'FORBIDDEN'):
        raise ReadyWaitError('CMD6 请求被拒绝: code=%s %s' % (code, message))
    if (type(code) is not int or code != 0
            or response.get('_v2_frame_valid') is not True
            or response.get('_v2_payload_valid') is not True
            or type(response.get('_v2_command')) is not int or response['_v2_command'] != 6
            or type(request.get('id')) is not int
            or type(response.get('id')) is not int or response['id'] != request['id']):
        return False, 'CMD6 帧或本次请求编号无效'
    result = response.get('result')
    if not isinstance(result, dict) or result.get('_status_wire_valid') is not True:
        return False, 'CMD6 状态载荷无效'
    slots = result.get('slots')
    if (not isinstance(slots, list) or len(slots) != 4
            or any(not isinstance(row, dict) or type(row.get('index')) is not int
                   or row.get('_wire_valid') is not True
                   or not isinstance(row.get('slot_status'), str) or not row['slot_status']
                   for row in slots)
            or sorted(row['index'] for row in slots) != [0, 1, 2, 3]):
        return False, 'CMD6 四槽状态缺失、重复或无效'
    states = {row['index']: row['slot_status'] for row in slots}
    faults = [(slot, state) for slot, state in sorted(states.items())
              if state.endswith('_error') or state == 'error']
    if faults:
        raise ReadyWaitError('槽位实际错误: ' + ', '.join(
            '槽 %d=%s' % (slot + 1, state) for slot, state in faults))
    assist_slots = ({assist_slot} if type(assist_slot) is int
                    else set(assist_slot) if isinstance(assist_slot, (set, tuple, list)) else set())
    busy = [(slot, state) for slot, state in sorted(states.items())
            if state != 'ready' and not (
                state in ASSIST_STATES and slot in assist_slots)]
    if busy:
        return False, ', '.join('槽 %d=%s%s' % (
            slot + 1, state, '（助送料无当前归属）' if state in ASSIST_STATES else '')
            for slot, state in busy)
    if result.get('status') != 'ready':
        return False, '设备聚合状态=%s' % result.get('status', 'unknown')
    return True, 'ready'


def _wait_ready(ace, idx, timeout):
    if (type(idx) is not int or not 0 <= idx < 4 or type(timeout) not in (int, float)
            or not math.isfinite(timeout) or timeout <= 0):
        raise ReadyWaitError('等待目标或总时限无效')
    reactor = ace.reactor
    transport = ace._serials.get(idx)
    identity = _identity(ace, idx)
    if identity is None:
        raise ReadyWaitError('原 ACE UID 未确认，不能使用缓存 ready')
    deadline = reactor.monotonic() + float(timeout)
    last = '尚未收到本次 CMD6'

    def check():
        if (transport is None or ace._serials.get(idx) is not transport
                or ace._connected_per_ace.get(idx) is not True
                or getattr(ace, '_serial_failed_per_ace', {}).get(idx)
                or _identity(ace, idx) != identity):
            raise ReadyWaitError('原 ACE 连接或 UID 已变化；等待已结束，未重连或改写来源')
        if reactor.monotonic() >= deadline:
            raise ReadyWaitError('等待 ready 达到总时限 %g 秒；%s；连接和来源保留' % (timeout, last))

    while True:
        check()
        box = {'done': False, 'response': None, 'active': True}

        def callback(self, response, target=box):
            if target['active'] and not target['done']:
                target.update(done=True, response=response)

        request = {'method': 'get_status', 'params': {},
                   '_insert_stop_transport': transport, '_insert_reject': callback}
        reply_deadline = min(deadline, reactor.monotonic() + REPLY_SECONDS)
        try:
            try:
                ace.send_request_to(idx, request, callback)
            except Exception as exc:
                raise ReadyWaitError('CMD6 状态请求失败，原连接保留: %s' % exc)
            while not box['done'] and reactor.monotonic() < reply_deadline:
                check()
                reactor.pause(min(reply_deadline, reactor.monotonic() + .05))
            check()  # A reply arriving at/after the total deadline cannot win.
            armed = getattr(ace, '_native_assist_is_armed', None)
            assist_slot = ({slot for slot in range(4) if armed(idx, slot)} if armed is not None
                           else getattr(ace, '_feed_assist_per_ace', {}).get(idx, -1))
            if box['done']:
                ready, last = _classify(box['response'], request, assist_slot)
                if ready:
                    return
            else:
                last = 'CMD6 响应超时，未使用缓存 ready'
        finally:
            # Late callbacks of an earlier query cannot complete a later one.
            box['active'] = False
        reactor.pause(min(deadline, reactor.monotonic() + POLL_SECONDS))


def wait_ready(ace, idx, timeout=30.0):
    """Wait on fresh CMD6 within one deadline and preserve resumable errors.

    Wire into ``wait_ace_ready_on`` only after its controlled-motion branch,
    for ``ace._is_v2_idx(idx)``. The existing ``_raise_ace_gone`` chooses a
    resumable pause during printing and a normal command error when idle.
    No cached status is written or accepted as evidence by this function.
    """
    try:
        return _wait_ready(ace, idx, timeout)
    except ReadyWaitError as exc:
        display = idx + 1 if type(idx) is int else repr(idx)
        ace._raise_ace_gone(idx, '[multiACE] ACE %s: %s' % (display, exc))
        raise  # Fail closed if a peripheral/test implementation returns.
