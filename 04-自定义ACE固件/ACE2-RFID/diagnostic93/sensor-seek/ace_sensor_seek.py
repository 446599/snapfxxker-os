"""Experimental bounded seek, deliberately not installed or enabled.

Load as a Klipper extra only after review. The command performs motion.
"""
import logging

UID = (808780566, 17445170, 942880330)

class SensorSeek:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command('ACE_SENSOR_SEEK_ONCE', self.cmd_seek)
        self.state = 'idle'
        self.generation = 0
        self.result = {}

    @staticmethod
    def accepted(r):
        return (isinstance(r, dict) and r.get('_v2_frame_valid') is True
                and r.get('_v2_payload_valid') is True and r.get('code') == 0
                and str(r.get('msg', '')).upper() != 'FORBIDDEN')

    def query(self, method):
        r = self.ace._tipform_send(0, {'method': method}, timeout=2.0)
        if not self.accepted(r) or not isinstance(r.get('result'), dict):
            raise ValueError(method + ' reply invalid')
        return r['result']

    def sensors(self):
        values = []
        for head in range(4):
            s = self.printer.lookup_object('filament_motion_sensor e%d_filament' % head, None)
            if s is None:
                raise ValueError('head sensor missing')
            v = s.get_status(self.reactor.monotonic()).get('filament_detected')
            if type(v) is not bool:
                raise ValueError('head sensor invalid')
            values.append(v)
        return tuple(values)

    @staticmethod
    def lanes(result, field, valid):
        rows = result.get(field)
        if result.get(valid) is not True or not isinstance(rows, list) or len(rows) != 4:
            raise ValueError('malformed telemetry')
        lanes = {x.get('index'): x for x in rows if isinstance(x, dict)}
        if set(lanes) != set(range(4)):
            raise ValueError('malformed lane indices')
        return lanes

    def preflight(self):
        self.ace = self.printer.lookup_object('ace')
        self.ace._require_no_stop_fault()
        self.transport = self.ace._serials.get(0)
        if self.transport is None or getattr(self.transport, 'is_open', False) is not True:
            raise ValueError('ACE transport not open')
        state = self.ace.get_status(self.reactor.monotonic())
        device = state['aces'][0]
        ps = self.printer.lookup_object('print_stats')
        if (getattr(ps, 'state', None) != 'standby' or state.get('mode') != 'multi'
                or state.get('active_device') != 0 or state.get('tag_op', {}).get('busy')
                or state.get('swap_in_progress') or state.get('unload_all_active')
                or state.get('calibration', {}).get('state') not in ('idle', 'cancelled', 'complete', 'verified', 'failed')
                or device.get('firmware') != 'V1.1.93O' or device.get('connected') is not True
                or device.get('fw_hold') is not False or device.get('dryer', {}).get('status') != 'stop'
                or device.get('auto_dry_running') or device.get('auto_dry', {}).get('enabled')):
            raise ValueError('device not idle or route differs')
        if self.query('get_info').get('firmware') != 'V1.1.93O':
            raise ValueError('fresh firmware mismatch')
        ident = self.query('discover_device')
        if tuple(ident.get('uid%d' % i) for i in (1,2,3)) != UID:
            raise ValueError('wrong device identity')
        status = self.lanes(self.query('get_status'), 'slots', '_status_wire_valid')
        if any(v.get('slot_status') != 'ready' for v in status.values()):
            raise ValueError('lane not ready')
        key = self.query('get_key_state').get('fields', {}).get('1')
        if type(key) is not int or not key & (1 << 8):
            raise ValueError('target gate absent')
        if self.sensors() != (False, False, False, False):
            raise ValueError('head sensors not clear')

    def cmd_seek(self, gcmd):
        if self.state != 'idle':
            raise gcmd.error('sensor seek is one-shot; restart required')
        self.preflight()
        self.started = self.reactor.monotonic()
        self.state = 'between'
        self.result = {'commanded_mm': 0, 'encoder_mm': 0, 'state': self.state}
        self.reactor.register_timer(self.tick, self.started)
        gcmd.respond_info('Sensor seek started, ACE0 slot2 head2')

    def send(self, method, params, callback):
        generation = self.generation
        def cb(self, response):
            if (generation == owner.generation
                    and owner.ace._serials.get(0) is owner.transport
                    and owner.state not in ('idle', 'failed', 'uncertain', 'at_sensor')):
                callback(response)
        owner = self
        self.ace.send_request_to(0, {'method': method, 'params': params}, cb)

    def move_reply(self, response):
        if self.state != 'moving': return
        if not self.accepted(response):
            self.stop('feed rejected')
        else:
            self.ack = True

    def begin_move(self, now):
        self.generation += 1
        self.ack = False
        self.busy_seen = False
        self.booked = False
        self.pending = False
        self.motion_started = now
        self.last_poll = now
        self.state = 'moving'
        self.result['commanded_mm'] += 50
        self.send('feed_filament', {'index': 2, 'length': 50, 'speed': 5}, self.move_reply)

    def stop(self, reason):
        if self.state in ('stopping', 'verifying', 'failed', 'uncertain', 'at_sensor'):
            return
        self.reason = reason
        self.generation += 1
        self.state = 'stopping'
        # Uses the installed checked STOP with bounded retries and sticky fault.
        try:
            ok = self.ace._stop_feeding(2, idx=0)
        except Exception:
            ok = False
        if not ok:
            self.ace._latch_stop_fault(0, 2, 'sensor seek STOP unconfirmed')
            self.finish('uncertain')
            return
        self.state = 'verifying'
        self.verify_started = self.reactor.monotonic()
        self.pending = False
        self.samples = []
        self.last_poll = 0

    def finish(self, state):
        self.generation += 1
        self.state = state
        self.result.update(state=state, reason=getattr(self, 'reason', None),
                           hit=getattr(self, 'hit', None))
        logging.warning('[sensor-seek] %s', self.result)
        self.gcode.respond_info('ACE sensor seek: %s' % self.result)

    def status_reply(self, response):
        self.pending = False
        if self.state not in ('moving', 'verifying'): return
        try:
            if not self.accepted(response): raise ValueError('status ACK')
            lanes = self.lanes(response['result'], 'slots', '_status_wire_valid')
            if any(lanes[i].get('slot_status') != 'ready' for i in (0,1,3)):
                raise ValueError('other lane moving')
            target = lanes[2].get('slot_status')
            if target == 'feeding' and self.state == 'moving':
                self.busy_seen = True
            elif target == 'ready':
                if self.state == 'verifying' or (self.ack and self.busy_seen):
                    self.pending = True
                    self.send('get_feed_info', {}, self.decoder_reply)
            elif target != 'feeding': raise ValueError('unexpected lane state')
        except Exception:
            if self.state == 'moving': self.stop('invalid status')
            else:
                self.ace._latch_stop_fault(0, 2, 'invalid post-STOP status')
                self.finish('uncertain')

    def decoder_reply(self, response):
        if self.state not in ('moving', 'verifying'):
            return
        self.pending = False
        try:
            if not self.accepted(response): raise ValueError('decoder ACK')
            row = self.lanes(response['result'], 'feed_info', '_feed_wire_valid')[2]
            v = row.get('decoder')
            if row.get('_decoder_present') is not True or type(v) is not int:
                raise ValueError('decoder missing')
            if v >= 1 << 63: v -= 1 << 64
            if not 0 <= v <= 55: raise ValueError('decoder range')
            self.samples.append(v)
            if len(self.samples) < 2: return
            if self.samples[-1] != self.samples[-2]:
                self.samples = self.samples[-1:]; return
            if not self.ack: raise ValueError('feed ACK absent')
            if not self.booked:
                self.result['encoder_mm'] += v
                self.booked = True
            if self.state == 'verifying':
                self.finish('at_sensor' if self.reason == 'sensor' and self.hit == (2,) else 'failed')
            else:
                if v == 0: raise ValueError('no movement')
                self.state = 'between'
        except Exception:
            if self.state == 'moving': self.stop('invalid decoder')
            else:
                self.ace._latch_stop_fault(0, 2, 'invalid post-STOP decoder')
                self.finish('uncertain')

    def get_status(self, eventtime):
        return dict(self.result, state=self.state)

    def check_session(self, now):
        if self.ace._serials.get(0) is not self.transport:
            raise ValueError('transport changed')
        self.ace._require_no_stop_fault()
        if getattr(self.printer.lookup_object('print_stats'), 'state', None) != 'standby':
            raise ValueError('printer no longer standby')
        current = self.ace.get_status(now)
        if (current.get('active_device') != 0 or current.get('mode') != 'multi'
                or current.get('swap_in_progress') or current.get('unload_all_active')
                or current.get('tag_op', {}).get('busy')):
            raise ValueError('active route or operation changed')

    def tick(self, now):
        if self.state in ('failed', 'uncertain', 'at_sensor'):
            return self.reactor.NEVER
        if self.state == 'stopping': return now + .05
        try:
            self.check_session(now)
            values = self.sensors()
            if self.state in ('moving', 'between') and any(values):
                self.hit = tuple(i for i, x in enumerate(values) if x)
                self.stop('sensor')
            elif self.state == 'moving' and not self.ack and now - self.motion_started > 2:
                self.stop('feed ACK timeout')
            elif self.state == 'moving' and now - self.motion_started > 16:
                self.stop('movement timeout')
            elif self.state == 'verifying' and now - self.verify_started > 5:
                self.ace._latch_stop_fault(0, 2, 'standstill not confirmed')
                self.finish('uncertain')
            elif now - self.started >= 450 and self.state == 'moving':
                self.stop('budget')
            elif self.state == 'between' and (now - self.started >= 450 or self.result['commanded_mm'] >= 2000):
                self.reason = 'budget'; self.finish('failed')
            elif self.state == 'between':
                self.samples = []
                self.begin_move(now)
            elif self.state in ('moving', 'verifying') and not self.pending and now - self.last_poll >= .25:
                self.pending = True; self.last_poll = now
                self.send('get_status', {}, self.status_reply)
        except Exception:
            if self.state in ('moving', 'between'): self.stop('sensor or transport error')
            else:
                self.ace._latch_stop_fault(0, 2, 'post-STOP communication error')
                self.finish('uncertain')
        return now + .05

def load_config(config):
    return SensorSeek(config)
