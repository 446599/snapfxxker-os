"""Stage scoped 1902.8 motion waits and dryer routing against 1.90.2.

No modification of RFID/thermal algorithms or normal motion lengths.
Apply to the composed provider, not the older checked-in provider tree.
"""
from pathlib import Path


def replace(text, old, new):
    if text.count(old) != 1:
        raise ValueError('scoped runtime anchor mismatch: ' + old[:90])
    return text.replace(old, new, 1)


def method(text, name, change):
    start = text.index('    def ' + name + '(')
    end = text.index('\n    def ', start + 1)
    return text[:start] + change(text[start:end]) + text[end:]


WAIT = '''    def _is_native_ace2k(self, idx):
        return getattr(self._protocols.get(idx), 'NAME', '') == 'ace2k'

    def wait_ace_motion_ready_on(self, idx, slot, timeout=30.0):
        """Wait for the actual native lane, never reconnect for another lane's motion."""
        if not self._is_native_ace2k(idx):
            return self.wait_ace_ready_on(idx, timeout=timeout)
        transport = self._serials.get(idx)
        deadline = self.reactor.monotonic() + timeout
        if type(slot) is not int or not 0 <= slot < 4:
            raise self.printer.command_error('[multiACE] Invalid slot')
        while True:
            if (transport is None or self._serials.get(idx) is not transport
                    or not self._connected_per_ace.get(idx)
                    or not self._is_native_ace2k(idx)):
                self._raise_ace_gone(idx, '[multiACE] Selected ACE connection changed')
            remain = deadline - self.reactor.monotonic()
            if remain <= 0:
                self._raise_ace_gone(idx, '[multiACE] ACE %d slot %d motion not ready' %
                                     (self._disp(idx), self._disp(slot)))
            reply = self._tipform_send(idx, {'method': 'get_lane_state',
                                            'params': {'index': slot}}, min(2., remain))
            if (self._serials.get(idx) is not transport
                    or not self._connected_per_ace.get(idx)
                    or not self._is_native_ace2k(idx)):
                self._raise_ace_gone(idx, '[multiACE] Selected ACE connection changed')
            if isinstance(reply, dict) and reply.get('code') == 0:
                data = reply.get('result')
                if isinstance(data, dict) and type(data.get('index')) is int and data['index'] == slot:
                    if data.get('state') == 'feed_error':
                        self._raise_ace_gone(idx, '[multiACE] ACE %d slot %d feed error' %
                                             (self._disp(idx), self._disp(slot)))
                    if data.get('move_admissible') is True:
                        return
            self.reactor.pause(min(deadline, self.reactor.monotonic() + .25))

'''


def patch_engine(text):
    text = replace(text, '    def wait_ace_ready_on(self, idx, timeout=30.0, max_reconnects=2):\n',
                   WAIT + '    def wait_ace_ready_on(self, idx, timeout=30.0, max_reconnects=2):\n')
    for name in ('_feed', '_retract'):
        text = method(text, name, lambda s: replace(s, '        self.wait_ace_ready_on(idx)\n',
                       '        self.wait_ace_motion_ready_on(idx, index)\n'))
    def drying(s):
        start = s.index('        self._wait_homing_clear()')
        end = s.index('        def callback', start)
        s = s[:start] + '''        dry_idx = gcmd.get_int('ACE', self._active_device_index)
        if dry_idx < 0 or dry_idx >= len(self._ace_devices):
            raise self._ace_error(gcmd, 'Wrong ACE index', code=200)
        native = self._is_native_ace2k(dry_idx)
        if not native:
            self._wait_homing_clear()
        if not self._connected_per_ace.get(dry_idx):
            raise self._ace_error(gcmd, 'Selected ACE not connected', code=200)

''' + s[end:]
        return replace(s, '        self.wait_ace_ready()\n        self.send_request(\n',
                       '        if not native:\n            self.wait_ace_ready_on(dry_idx)\n        self.send_request_to(dry_idx,\n')
    text = method(text, 'cmd_ACE_START_DRYING', drying)
    def stopping(s):
        s = replace(s, '        self._wait_homing_clear()\n',
                    '        native = self._is_native_ace2k(ace_idx)\n        if not native:\n            self._wait_homing_clear()\n')
        return replace(s, '        self.wait_ace_ready_on(ace_idx)\n',
                       '        if not native:\n            self.wait_ace_ready_on(ace_idx)\n')
    text = method(text, 'cmd_ACE_STOP_DRYING', stopping)
    text = method(text, 'cmd_ACE_DRY', lambda s: replace(s, '        self._wait_homing_clear()\n', '''        if self._is_native_ace2k(ace_idx):
            self.gcode.run_script_from_command('ACE_START_DRYING ACE=%d TEMP=%d DURATION=%d' %
                                               (ace_idx, temp, duration))
            return
        self._wait_homing_clear()
'''))
    text = replace(text, "        if request.get('method') not in TELEMETRY_METHODS:\n            info['status'] = 'busy'\n",
                   "        if (request.get('method') not in TELEMETRY_METHODS\n                and not (self._is_native_ace2k(idx) and request.get('method') in ('drying', 'drying_stop'))):\n            info['status'] = 'busy'\n")
    compile(text, 'ace.py', 'exec')
    return text


def patch_driver(text):
    return replace(text, "            return ok({'index': slot, 'present': sample.present, 'state': state,\n",
                   '''            guard = getattr(self, '_native_exit', None)
            admissible = (slot not in self._jobs and slot not in self._tag_reads
                          and (guard is None or not guard.blocked(slot))
                          and (state == 'ready' or
                               (sample.state == 'assisting' and slot in self._follow)))
            return ok({'index': slot, 'present': sample.present, 'state': state,
                       'move_admissible': admissible,
''')


def stage(extras):
    extras = Path(extras)
    engine, driver = extras/'ace.py', extras/'ace_protocol_ace2k.py'
    a,b = patch_engine(engine.read_text()), patch_driver(driver.read_text())
    compile(b, str(driver), 'exec')
    engine.write_text(a)
    driver.write_text(b)
