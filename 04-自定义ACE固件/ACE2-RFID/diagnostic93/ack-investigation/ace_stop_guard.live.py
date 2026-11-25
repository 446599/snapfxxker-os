"""OFFLINE CANDIDATE: host-side sticky fault, not an emergency stop.

No reset API. A new Python process loses this latch. It cannot cancel work
already accepted by an ACE, serial driver, macro interpreter, or MCU.
"""
import threading
import os
import logging

FAULT_PATH = '/home/lava/printer_data/controlled-exploration/fault-checkpoint4.json'


class StopUnconfirmedError(RuntimeError):
    pass


def stop_response_accepted(response):
    """Validate STOP acceptance only. This is not mechanical standstill."""
    return (isinstance(response, dict)
            and type(response.get('code')) is int and response['code'] == 0
            and str(response.get('msg', '')).strip().upper() != 'FORBIDDEN'
            and response.get('_v2_payload_valid', True) is True)


class StopFaultLatch:
    # Positive allowlist: raw/tunnel/unknown commands cannot bypass a fault.
    ALLOWED = frozenset((
        'stop_feed_filament', 'stop_feed_assist',
        'get_info', 'get_status', 'get_temp', 'get_feed_info',
        'get_key_state', 'get_filament_info'))

    def __init__(self):
        self._lock = threading.Lock()
        # Device writes remain serialized by the existing per-device writer.
        # This lock protects only admission/publication, never a syscall.
        self._fault = None
        self._generation = 0

    def snapshot(self):
        with self._lock:
            return None if self._fault is None else dict(self._fault)

    def latch(self, ace_idx, slot, reason):
        with self._lock:
            self._generation += 1
            if self._fault is None:
                self._fault = {'ace_idx': int(ace_idx), 'slot': int(slot),
                               'reason': str(reason)}
            # During a durable recovery, preserve ANY additional fault
            # across restart even though first-fault reporting is retained.
            path = FAULT_PATH
            if os.path.lexists(path):
                try:
                    extra=path+'.additional-fault'
                    fd=os.open(extra,os.O_CREAT|os.O_WRONLY|os.O_APPEND,0o600)
                    try:
                        os.write(fd,b'additional fault while latched\n')
                        os.fsync(fd)
                    finally:os.close(fd)
                    fd=os.open(os.path.dirname(path),os.O_RDONLY)
                    try:os.fsync(fd)
                    finally:os.close(fd)
                except Exception:
                    logging.exception('STOP guard could not persist additional fault')
            return dict(self._fault)

    def message(self):
        f = self.snapshot()
        if f is None:
            return None
        return ('[multiACE] STOP UNCONFIRMED: ACE %d slot %d; motor may still '
                'run. New host motion requests are blocked. Do not reload or resume; '
                'inspect the device. %s'
                % (f['ace_idx'] + 1, f['slot'] + 1, f['reason']))

    def require_clear(self):
        msg = self.message()
        if msg is not None:
            raise StopUnconfirmedError(msg)

    def allows(self, request):
        with self._lock:
            return self._fault is None or (
                isinstance(request, dict)
                and request.get('method') in self.ALLOWED)

    def write(self, serial, data, request):
        # Admission is atomic with fault publication. The syscall is not:
        # a previously admitted write may BEGIN after a fault is published.
        # Do not hold the publication lock across a potentially blocked write.
        with self._lock:
            if self._fault is not None and not (
                    isinstance(request, dict)
                    and request.get('method') in self.ALLOWED):
                return False
        written = serial.write(data)
        if type(written) is not int or written != len(data):
            raise IOError('short serial write: %r of %d bytes' % (written, len(data)))
        return True


def guard_for_printer(printer):
    ace = printer.lookup_object('ace', None)
    if ace is None:
        return None
    guard = getattr(ace, '_stop_guard', None)
    if guard is None:
        raise StopUnconfirmedError('STOP guard candidate bundle is incomplete')
    return guard


def require_printer_clear(printer, gcmd=None):
    guard = guard_for_printer(printer)
    if guard is not None:
        message = guard.message()
        if message is not None and gcmd is not None:
            raise gcmd.error(message=message, action='pause', id=525,
                             code=210, oneshot=1, level=2)
        guard.require_clear()


class StopGuardGCode:
    """Guard scripts issued by these three candidate modules only.

    An already-running macro may continue internally. Other users of the
    original gcode object are NOT intercepted. No global monkey-patching.
    """
    CLEANUP = frozenset((
        'M107', 'M104 S0', 'SET_ACTION_CODE ACTION=IDLE',
        'SET_MAIN_STATE MAIN_STATE=IDLE ACTION=IDLE'))

    def __init__(self, target, get_guard):
        self._target = target
        self._get_guard = get_guard

    def __getattr__(self, name):
        return getattr(self._target, name)

    def _check(self, script):
        guard = self._get_guard()
        if guard is None or guard.snapshot() is None:
            return
        lines = [s.strip().upper() for s in str(script).splitlines()
                 if s.strip()]
        if not lines or not all(s in self.CLEANUP for s in lines):
            guard.require_clear()

    def run_script_from_command(self, script):
        self._check(script)
        return self._target.run_script_from_command(script)

    def run_script(self, script):
        self._check(script)
        return self._target.run_script(script)
