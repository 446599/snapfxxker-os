"""Candidate Klipper motion primitive. Not wired into production."""
from .ace_segmented_preload import MotionFault

class PreloadMotion:
    def __init__(self, ace, reader, idx, slot, valid):
        self.ace, self.reader, self.idx, self.slot, self.valid = ace, reader, idx, slot, valid
        self.serial = ace._serials.get(idx)

    def head_clear(self):
        now = self.ace.reactor.monotonic()
        for n in range(4):
            obj = self.ace.printer.lookup_object('filament_motion_sensor e%d_filament' % n, None)
            if obj is None:
                return False
            raw = getattr(obj, 'runout_buttun_state', None)
            state = obj.get_status(now)
            if not isinstance(state, dict) or state.get('filament_detected') is not False or raw is not False:
                return False
        return True

    def _transport_current(self):
        return self.serial is not None and self.ace._serials.get(self.idx) is self.serial

    def _current(self):
        return self._transport_current() and self.valid()

    def _send_monitor(self, method, params, timeout, monitor):
        reactor = self.ace.reactor
        response = []
        def cb(*args, **kw):
            if self._transport_current():
                response.append(kw.get('response', args[-1] if args else None))
        self.ace.send_request_to(self.idx, {'method':method, 'params':params}, cb)
        deadline = reactor.monotonic() + timeout
        while not response and reactor.monotonic() < deadline:
            if monitor and (not self._current() or not self.head_clear()):
                return None
            reactor.pause(min(deadline, reactor.monotonic()+.05))
        return response[0] if response else None

    @staticmethod
    def _accepted(response):
        return (isinstance(response, dict) and type(response.get('code')) is int
                and response['code'] == 0 and response.get('_v2_payload_valid') is True
                and str(response.get('msg', '')).strip().upper() != 'FORBIDDEN')

    def stop(self):
        response = self._send_monitor('stop_feed_filament', {'index':self.slot}, 1.5, False)
        if not self._accepted(response):
            raise MotionFault('STOP acknowledgement missing or rejected')

    def move_and_stop(self, length):
        if type(length) is not int or not 0 < length <= 10 or not self._current() or not self.head_clear():
            raise MotionFault('movement admission failed')
        try:
            ack = self._send_monitor('feed_or_rollback_raw',
                {'index':self.slot, 'speed':5, 'length':length, 'mode':0}, 1.5, True)
            if not self._accepted(ack):
                raise MotionFault('movement acknowledgement missing or rejected; position unknown')
            deadline = self.ace.reactor.monotonic() + length / 5.0 + .3
            while self.ace.reactor.monotonic() < deadline:
                if not self._current() or not self.head_clear():
                    break  # STOP and verify the measured partial segment
                self.ace.reactor.pause(min(deadline, self.ace.reactor.monotonic()+.05))
        finally:
            # STOP even if the motion ACK is absent; never retry the move.
            self.stop()
        if not self._transport_current() or not self.reader._wait_motion_ready(self.idx, self.slot):
            raise MotionFault('first READY unavailable')
        first = self.reader._fresh_encoder(self.idx, self.slot)
        self.reader._pause(.3)
        if not self._transport_current() or not self.reader._wait_motion_ready(self.idx, self.slot):
            raise MotionFault('second READY unavailable')
        second = self.reader._fresh_encoder(self.idx, self.slot)
        if (type(first) is not int or first != second or not 0 < first <= length * 1.5 + 2):
            raise MotionFault('encoder absent, unstable, or outside bounds')
        return first
