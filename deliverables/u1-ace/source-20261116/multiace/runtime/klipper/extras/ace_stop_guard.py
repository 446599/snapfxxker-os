"""OFFLINE CANDIDATE: host-side sticky fault, not an emergency stop.

The recovery controller releases an unchanged latch only after fresh STOP and
telemetry checks. A configured journal preserves the actual fault on restart.
It cannot cancel work
already accepted by an ACE, serial driver, macro interpreter, or MCU.
"""
import threading
import os
import logging
import json
import stat
from .ace_controlled_motion import request_operation, MotionError, QUERY, STOP, CLOSE


def controlled_cleanup(request):
    try:
        return request_operation(request) in (QUERY, STOP, CLOSE)
    except MotionError:
        return False

def recovery_disables_insertion(request):
    return (isinstance(request, dict) and request.get('_recovery_mode_disable') is True
            and request.get('method') == 'insert_policy_set'
            and request.get('params', {}).get('enabled') is False)


FAULT_PATH = '/home/lava/printer_data/controlled-exploration/fault-checkpoint4.json'
LEGACY_FAULT_PATH = '/home/lava/printer_data/controlled-exploration/fault-checkpoint3.json'


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
        'get_key_state', 'get_filament_info', 'discover_device', 'insert_policy_query', 'insert_policy_discard'))

    def __init__(self, store_path=None):
        self.store_path = store_path
        self._faults = []
        self.persistence_error = None
        self._lock = threading.Lock()
        # Device writes remain serialized by the existing per-device writer.
        # This lock protects only admission/publication, never a syscall.
        self._fault = None
        self._generation = 0

    def snapshot(self):
        with self._lock:
            return None if self._fault is None else dict(self._fault)

    def recovery_snapshot(self):
        with self._lock:
            faults = [dict(f) for f in self._faults]
            if self._fault is not None and not faults:
                faults = [dict(self._fault)]
            return self._generation, faults

    def _persist(self, faults):
        if self.store_path is None:
            return
        directory = os.path.dirname(self.store_path)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        temp = self.store_path + '.tmp-' + os.urandom(8).hex()
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump({'version': 1, 'faults': faults}, stream)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temp, self.store_path)
            fd = os.open(directory, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        finally:
            if os.path.exists(temp): os.unlink(temp)

    def restore_persistent(self):
        if self.store_path is None:
            return
        try:
            fd = os.open(self.store_path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 65536:
                    raise ValueError('invalid STOP journal')
                data = json.load(stream)
            faults = data.get('faults') if isinstance(data, dict) and data.get('version') == 1 else None
            if not isinstance(faults, list) or len(faults) > 64:
                raise ValueError('invalid STOP journal entries')
            for f in faults:
                if (not isinstance(f, dict) or type(f.get('ace_idx')) is not int or not -1 <= f['ace_idx'] < 4
                    or type(f.get('slot')) is not int or not -1 <= f['slot'] < 4 or not isinstance(f.get('reason'), str)
                    or (f.get('uid') is not None and (not isinstance(f['uid'], list) or len(f['uid']) != 3 or any(type(v) is not int for v in f['uid'])))):
                    raise ValueError('invalid STOP journal identity')
        except FileNotFoundError:
            return
        except Exception as exc:
            self.persistence_error = str(exc)
            faults = [{'ace_idx': -1, 'slot': -1, 'reason': 'STOP record invalid: ' + str(exc)}]
        with self._lock:
            if self._fault is not None:
                faults.insert(0, dict(self._fault))
            self._faults = faults
            if faults:
                self._fault = {key: faults[0][key] for key in ('ace_idx', 'slot', 'reason')}
                self._generation += 1

    def latch(self, ace_idx, slot, reason, identity=None):
        with self._lock:
            self._generation += 1
            if self._fault is None:
                self._fault = {'ace_idx': int(ace_idx), 'slot': int(slot),
                               'reason': str(reason)}
            detail = {'ace_idx': int(ace_idx), 'slot': int(slot), 'reason': str(reason)}
            if identity is not None:
                detail['port'], uid = identity
                detail['uid'] = list(uid) if uid is not None else None
            if detail not in self._faults:
                self._faults.append(detail)
            if self.store_path is not None:
                try:
                    self._persist(self._faults)
                    self.persistence_error = None
                except Exception as exc:
                    self.persistence_error = str(exc)
                    logging.exception('Could not persist STOP fault')
                return dict(self._fault)
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
                and (request.get('method') in self.ALLOWED or controlled_cleanup(request) or recovery_disables_insertion(request)))

    def write(self, serial, data, request):
        # Admission is atomic with fault publication. The syscall is not:
        # a previously admitted write may BEGIN after a fault is published.
        # Do not hold the publication lock across a potentially blocked write.
        with self._lock:
            if self._fault is not None and not (
                    isinstance(request, dict)
                    and (request.get('method') in self.ALLOWED or controlled_cleanup(request) or recovery_disables_insertion(request))):
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
