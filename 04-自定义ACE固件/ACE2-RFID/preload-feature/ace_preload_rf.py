"""93O-only RFID session. Per-ACE writer lease; no movement in this module."""
import time

EXPECTED_UID = (808780566, 17445170, 942880330)

class RfUnavailable(Exception):
    pass

class PreloadRf:
    def __init__(self, ace, reader, idx, slot, valid):
        self.ace, self.reader, self.idx, self.slot, self.valid = ace, reader, idx, slot, valid
        self.connection = ace._serials.get(idx)
        self.token = None
        self.original = None
        self.changed = set()

    def _request(self, method, params, allow_invalid=False):
        if (not allow_invalid and not self.valid()) or self.ace._serials.get(self.idx) is not self.connection:
            raise RfUnavailable('session changed')
        reply = self.reader._req(self.idx, method, params, timeout=1.5)
        if not isinstance(reply, dict) or reply.get('_v2_payload_valid') is not True or reply.get('code') != 0:
            raise RfUnavailable('%s response missing or rejected' % method)
        return reply.get('result') or {}

    def _status(self):
        status = self._request('get_status', {})
        lanes = status.get('slots') or []
        if not status.get('_status_wire_valid') or len(lanes) != 4:
            raise RfUnavailable('invalid fresh lane status')
        if any(lane.get('slot_status') != 'ready' for lane in lanes):
            raise RfUnavailable('lanes not ready')
        return status

    def _state(self, lane, allow_invalid=False):
        word = 0x800a0000 | (lane << 8) | 3
        result = self._request('filament_identify', {'index':word}, allow_invalid)
        if result.get('index') != word or type(result.get('code')) is not int:
            raise RfUnavailable('invalid 93O reader-state reply')
        value = result['code']
        if value == 0xffffffff:
            raise RfUnavailable('reader-state unavailable')
        return value

    def _wait_state(self, lane, disabled, allow_invalid=False):
        deadline = self.ace.reactor.monotonic() + 8
        while self.ace.reactor.monotonic() < deadline:
            state = self._state(lane, allow_invalid)
            if (state == 8) == disabled:
                return
            self.reader._pause(.15)
        raise RfUnavailable('reader state did not settle')

    def acquire(self):
        version = ((self.ace._ace_models.get(self.idx) or ('',''))[1] or '').strip().upper()
        if version != 'V1.1.93O':
            raise RfUnavailable('unsupported firmware for private opcode')
        self.token = self.ace._rf_gate.acquire(self.idx, self.slot, self.connection)
        if self.token is None:
            return False
        self.reader._rf_token = self.token
        try:
            info = self._request('get_info', {})
            if str(info.get('firmware', '')).strip().upper() != 'V1.1.93O':
                raise RfUnavailable('fresh firmware identity mismatch')
            ident = self._request('discover_device', {})
            if tuple(ident.get(k) for k in ('uid1','uid2','uid3')) != EXPECTED_UID:
                raise RfUnavailable('device identity mismatch')
            status = self._status()
            self.original = (status.get('rfid1_enable_raw'), status.get('rfid2_enable_raw'))
            if any(type(v) is not int or v not in (0,1) for v in self.original):
                raise RfUnavailable('reader enable state unavailable')
            for lane, enabled in zip((0,2), self.original):
                if enabled:
                    # Mark before sending: even a lost ACK may have changed it.
                    self.changed.add(lane)
                    self._request('set_rfid_enable', {'index':lane, 'enable':False})
                self._wait_state(lane, True)
            return True
        except Exception:
            self.release()
            raise

    def observe(self):
        status = self._status()
        # Shared antenna cannot identify which of two stationary spools answered.
        keys = self._request('get_key_state', {}).get('fields') or {}
        raw = keys.get('1')
        if type(raw) is not int:
            raise RfUnavailable('fresh insert bits unavailable')
        if not raw & (1 << (4 * self.slot)):
            return ('ambiguous', None)
        if raw & (1 << (4 * (self.slot ^ 1))):
            return ('conflict', None)
        if self._state(0) != 8 or self._state(2) != 8:
            raise RfUnavailable('native reader no longer disabled')
        # All raw operations, including the probe itself, require a
        # separately verified bounded firmware transport contract.
        if getattr(self.reader, '_preload_rf_bounded_v1', False) is not True:
            return ('ambiguous', None)
        # SELECT code 1 itself is never evidence of absence.
        probe = getattr(self.reader, '_preload_probe_presence', None)
        if callable(probe):
            presence = probe(self.idx, self.slot)
            if (isinstance(presence, dict) and presence.get('source') == 'raw_reqa_v1'
                    and presence.get('state') == 'not_detected'
                    and presence.get('verified') is True):
                return ('absent', None)
            if not (isinstance(presence, dict) and presence.get('source') == 'raw_reqa_v1'
                    and presence.get('state') == 'present' and presence.get('verified') is True):
                return ('ambiguous', None)
        status = self.reader._rc(self.idx, self.slot, 6)
        if status != 0:
            return ('ambiguous', None)
        uid = self.reader._selected_single_uid(self.idx, self.slot)
        if not uid:
            uid, _ = self.reader._rc_stable_uid(self.idx, self.slot)
        if not uid:
            return ('ambiguous', None)
        return ('present', uid)

    def identity(self, uid):
        if getattr(self.reader, '_preload_rf_bounded_v1', False) is not True:
            return None
        return self.reader._read_bambu_classic(self.idx, self.slot, uid)

    def release(self):
        error = None
        try:
            for lane in sorted(self.changed):
                try:
                    self._request('set_rfid_enable', {'index':lane, 'enable':True}, True)
                    self._wait_state(lane, False, True)
                except Exception as exc:
                    error = exc
        finally:
            if self.token is not None:
                self.ace._rf_gate.release(self.token)
            self.reader._rf_token = None
            self.token = None
        if error is not None:
            raise RfUnavailable('RF enable restore not verified: %s' % error)
