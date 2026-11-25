"""Per-ACE host RFID arbitration for bounded load sessions (candidate)."""

# RC522 operations share firmware scratch even across readers; arbitrate the
# entire ACE, not merely a reader. Unknown raw commands fail closed while held.
RF_METHODS = frozenset(('filament_identify', 'filament_recognition',
                        'set_rfid_enable', 'rfid_test', 'raw'))

class RfGate:
    def __init__(self):
        self._owners = {}
        self._generation = {}

    def acquire(self, ace, slot, connection):
        if type(ace) is not int or type(slot) is not int or slot not in range(4) or connection is None:
            raise ValueError('invalid session identity')
        if ace in self._owners:
            return None
        generation = self._generation.get(ace, 0) + 1
        self._generation[ace] = generation
        token = (ace, slot, generation, connection)
        self._owners[ace] = token
        return token

    def invalidate(self, ace):
        self._generation[ace] = self._generation.get(ace, 0) + 1
        self._owners.pop(ace, None)

    def release(self, token):
        if self._owners.get(token[0]) != token:
            return False
        self._owners.pop(token[0])
        return True

    def allows(self, ace, request, token=None, connection=None):
        if request.get('method') not in RF_METHODS:
            return True
        owner = self._owners.get(ace)
        if owner is None:
            return token is None
        return token == owner and connection is owner[3]
