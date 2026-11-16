"""Stock ACE2 buffer feedback and connection-scoped assist ownership.

The MCU closes the fast Hall-switch loop in mode 2/3.  ``ready`` is also
reported while an accepted assist is armed and waiting for filament demand;
it is not evidence that the host must arm again.  This module never moves a
motor, guesses a buffer distance, or attributes the shared full switch to a
particular slot.

Protocol evidence: Simon-CR/ace2-pro-firmware-research,
docs/05-protocol-notes.md (ready/assist and CMD73); tobecwb/ace2k,
docs/hardware.md (per-slot rest/pushed and wired-OR pulled input).
"""
import threading


def decode_buffer_keys(fields):
    """Decode CMD73 field 1 only when its complete 17-bit value is present."""
    values = fields.get(1) if isinstance(fields, dict) else None
    if (not isinstance(values, list) or len(values) != 1
            or not isinstance(values[0], tuple) or len(values[0]) != 2
            or values[0][0] != 0 or type(values[0][1]) is not int
            or not 0 <= values[0][1] < (1 << 17)):
        return None
    mask = values[0][1]
    slots = []
    for slot in range(4):
        offset = 4 * slot
        slots.append({
            'index': slot,
            'inserted': bool(mask & (1 << offset)),
            # EMPTY is a stub in the stock firmware. Retain the raw bit for
            # diagnostics only; never turn it into a filament-empty verdict.
            'empty_raw': bool(mask & (1 << (offset + 1))),
            'rest': bool(mask & (1 << (offset + 2))),
            'pushed': bool(mask & (1 << (offset + 3))),
        })
    return {'raw_mask': mask, 'slots': slots,
            'pulled_any': bool(mask & (1 << 16)), 'full_slot': None}


def _uid(value):
    return tuple(value) if isinstance(value, (list, tuple)) else None


class NativeBufferControl:
    """An ACK is an assist lease, not a physical motion/stop confirmation.

    Generations also guard requests already waiting in the serial writer:
    stopping a lane invalidates an older queued arm and its delayed callback.
    State is bounded to the four slots of each connected unit.
    """
    POLL_INTERVAL = 1.0
    POLL_TIMEOUT = 3.0
    KEY_MAX_AGE = 3.0
    PENDING_MAX_AGE = 5.0

    def __init__(self):
        self._lock = threading.RLock()
        self._generation = 0
        self._leases = {}
        self._intents = {}
        self._keys = {}
        self._polls = {}
        self._holds = {}

    def invalidate(self, idx, slot=None, reason='revoked'):
        with self._lock:
            for key in list(self._intents):
                if key[0] == idx and (slot is None or key[1] == slot):
                    self._generation += 1
                    self._intents[key] = self._generation
            for key, lease in self._leases.items():
                if key[0] == idx and (slot is None or key[1] == slot):
                    self._generation += 1
                    lease.update(generation=self._generation, phase=reason)
            if slot is None:
                self._keys.pop(idx, None)
                self._polls.pop(idx, None)

    def hold(self, idx, slot, reason):
        with self._lock:
            self.invalidate(idx, slot, reason='review_required')
            self._holds[(idx, slot)] = str(reason)

    def hold_reason(self, idx, slot):
        with self._lock:
            return self._holds.get((idx, slot))

    def clear_hold(self, idx, slot):
        with self._lock:
            self._holds.pop((idx, slot), None)

    def reserve_intent(self, idx, slot):
        with self._lock:
            self._generation += 1
            self._intents[(idx, slot)] = self._generation
            return idx, slot, self._generation

    def intent_current(self, token):
        with self._lock:
            return self._intents.get((token[0], token[1])) == token[2]

    def begin_request(self, idx, request, transport, uid, now):
        method = request.get('method')
        params = request.get('params') or {}
        slot = params.get('index')
        if type(slot) is not int or not 0 <= slot < 4:
            return None
        mode = 2 if method == 'start_feed_assist' else (
            params.get('mode') if method == 'feed_or_rollback_raw' else None)
        with self._lock:
            if mode in (2, 3):
                self._generation += 1
                token = (idx, slot, self._generation)
                self._leases[(idx, slot)] = {
                    'generation': self._generation, 'phase': 'pending',
                    'mode': mode, 'transport': transport, 'uid': _uid(uid),
                    'since': now,
                }
                request['_native_buffer_token'] = token
                return token
            if method in ('stop_feed_assist', 'stop_feed_filament',
                          'feed_filament', 'unwind_filament',
                          'feed_or_rollback_raw'):
                self.invalidate(idx, slot, reason=('stop_requested'
                    if method in ('stop_feed_assist', 'stop_feed_filament') else 'replaced'))
        return None

    def _matches(self, token, transport, uid):
        if not isinstance(token, (tuple, list)) or len(token) != 3:
            return False
        lease = self._leases.get((token[0], token[1]))
        return bool(lease and lease['generation'] == token[2]
                    and lease['transport'] is transport
                    and lease['uid'] == _uid(uid))

    def allows_write(self, request, transport, uid):
        token = request.get('_native_buffer_token')
        if token is None:
            return True
        with self._lock:
            return self._matches(token, transport, uid)

    def accept_response(self, token, transport, uid, response, now):
        if token is None:
            return True
        with self._lock:
            if not self._matches(token, transport, uid):
                return False
            lease = self._leases[(token[0], token[1])]
            accepted = (isinstance(response, dict)
                        and response.get('_v2_frame_valid') is True
                        and response.get('_v2_payload_valid') is True
                        and response.get('_v2_command') == 8
                        and type(response.get('code')) is int
                        and response['code'] == 0
                        and str(response.get('msg', '')).lower()
                            in ('', 'success'))
            lease.update(phase='armed' if accepted else 'rejected', since=now)
            return True

    def armed(self, idx, slot, transport, uid, now=None, pending=False):
        with self._lock:
            lease = self._leases.get((idx, slot))
            if not lease or lease['transport'] is not transport or lease['uid'] != _uid(uid):
                return False
            if lease['phase'] == 'armed':
                return True
            return bool(pending and lease['phase'] == 'pending'
                        and now is not None
                        and 0 <= now - lease['since'] <= self.PENDING_MAX_AGE)

    def recovery_token(self, idx, slot, transport, uid, now):
        """A stopped/unknown lane never re-arms itself merely because of E motion."""
        with self._lock:
            lease = self._leases.get((idx, slot))
            if not lease or lease['transport'] is not transport or lease['uid'] != _uid(uid):
                return None
            phase = lease['phase']
            if phase == 'pending' and now - lease['since'] <= self.PENDING_MAX_AGE:
                return None
            if phase in ('pending', 'rejected', 'assist_error', 'unwind_assist_error'):
                return idx, slot, lease['generation']
            return None

    def recovery_current(self, token, transport, uid):
        with self._lock:
            return self._matches(token, transport, uid)

    def direction(self, idx, slot):
        with self._lock:
            return (self._leases.get((idx, slot)) or {}).get('mode')

    def active_slots(self, idx, transport, uid):
        with self._lock:
            return [slot for (device, slot), lease in sorted(self._leases.items())
                    if device == idx and lease['transport'] is transport
                    and lease['uid'] == _uid(uid)
                    and lease['phase'] in ('armed', 'pending', 'rejected')]

    def automatic_control_blocked(self, idx, slot):
        with self._lock:
            lease = self._leases.get((idx, slot))
            return bool((idx, slot) in self._holds or lease and lease['phase'] not in
                        ('armed', 'pending', 'rejected', 'assist_error', 'unwind_assist_error'))

    def observe_status(self, idx, slots, transport):
        with self._lock:
            for row in slots:
                lease = self._leases.get((idx, row.get('index')))
                if lease is None or lease['transport'] is not transport:
                    continue
                state = row.get('slot_status')
                # ready is intentionally not a disarm signal. Errors are.
                if (isinstance(state, str) and state.endswith('_error')
                        and lease['phase'] in ('pending', 'rejected', 'armed',
                                               'assist_error', 'unwind_assist_error')):
                    if lease['phase'] != state:
                        self.invalidate(idx, row['index'], reason=state)
                elif (state in ('feeding', 'rollback', 'preloading', 'upgrading')
                      and lease['phase'] in ('pending', 'rejected', 'armed')):
                    # A firmware-owned insertion/preload also takes this lane.
                    self.invalidate(idx, row['index'], reason='replaced')

    def poll_due(self, idx, transport, now):
        with self._lock:
            old = self._polls.get(idx)
            if old and old[0] is transport:
                interval = self.POLL_TIMEOUT if old[2] else self.POLL_INTERVAL
                if now - old[1] < interval:
                    return None
            token = (transport, now, True)
            self._polls[idx] = token
            return token

    def accept_keys(self, idx, token, transport, response, now):
        with self._lock:
            if self._polls.get(idx) is not token or token[0] is not transport:
                return False
            self._polls[idx] = (transport, token[1], False)
            result = (response or {}).get('result') or {}
            keys = result.get('buffer_keys')
            if not (isinstance(response, dict)
                    and response.get('_v2_frame_valid') is True
                    and response.get('_v2_payload_valid') is True
                    and response.get('_v2_command') == 73
                    and response.get('code') == 0
                    and result.get('_key_wire_valid') is True
                    and isinstance(keys, dict)):
                return False
            self._keys[idx] = (transport, now, keys)
            return True

    def status(self, idx, transport, uid, now):
        with self._lock:
            saved = self._keys.get(idx)
            fresh = bool(saved and saved[0] is transport
                         and 0 <= now - saved[1] <= self.KEY_MAX_AGE)
            keys = saved[2] if fresh else {}
            assists = []
            for (device, slot), lease in sorted(self._leases.items()):
                if device != idx:
                    continue
                current = lease['transport'] is transport and lease['uid'] == _uid(uid)
                assists.append({'slot': slot,
                                'phase': lease['phase'] if current else 'unknown',
                                'direction': 'forward' if lease['mode'] == 2 else 'reverse'})
            return {'mode': 'native', 'feedback': 'digital_hall',
                    'fresh': fresh,
                    'age_s': round(max(0.0, now - saved[1]), 2) if saved else None,
                    'assist': assists, 'slots': keys.get('slots', []),
                    'reconnect_hold': [{'slot': key[1], 'reason': reason}
                                       for key, reason in sorted(self._holds.items()) if key[0] == idx],
                    'raw_mask': keys.get('raw_mask'),
                    'pulled_any': keys.get('pulled_any'), 'full_slot': None}
