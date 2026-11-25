"""Per-device RFID lease and serialized writer admission (host candidate)."""
import threading

RF_METHODS = frozenset(('filament_identify', 'filament_recognition',
                        'set_rfid_enable', 'rfid_test', 'raw'))

class RfDenied(Exception):
    pass

class RfGate:
    def __init__(self):
        self._owners = {}
        self._generation = {}
        self._lock = threading.RLock()

    def acquire(self, ace, slot, connection):
        if type(ace) is not int or type(slot) is not int or slot not in range(4) or connection is None:
            raise ValueError('invalid session identity')
        with self._lock:
            if ace in self._owners:
                return None
            generation = self._generation.get(ace, 0) + 1
            self._generation[ace] = generation
            token = (ace, slot, generation, connection)
            self._owners[ace] = token
            return token

    def invalidate(self, ace):
        with self._lock:
            self._generation[ace] = self._generation.get(ace, 0) + 1
            self._owners.pop(ace, None)

    def release(self, token):
        with self._lock:
            if self._owners.get(token[0]) != token:
                return False
            del self._owners[token[0]]
            return True

    def allows(self, ace, request, token=None, connection=None):
        with self._lock:
            if request.get('method') not in RF_METHODS:
                return True
            owner = self._owners.get(ace)
            return (token is None if owner is None else
                    token is owner and connection is owner[3])

    def write(self, ace, request, connection, write):
        # Lease acquisition cannot race an already admitted serial write.
        # Previously transmitted operations still require a device-side drain.
        with self._lock:
            if not self.allows(ace, request, request.get('_rf_token'), connection):
                raise RfDenied('RF lease held by another operation')
            return write()
