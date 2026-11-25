"""REVIEW ONLY: local native-insertion watcher; never commands feed or heat.

Requires externally established exclusive serial ownership. No main entrypoint;
not executable until release/resume and restoration orchestration are reviewed.
"""
import time

class Watch:
    def __init__(self, io, clock=time.monotonic):
        self.io, self.clock = io, clock
        if hasattr(io, 'before_request'): io.before_request = self.check_heads
        self.phase = 'waiting_empty'
        self.started = clock()
        self.inserted_at = None
        self.stop_at = None
        self.forward_peak = 0
        self.seen_active = False
        self.stop_sent = set()
        self.triggered = set()
        self.last_decoder = None
        self.stable = 0
        self.stable_since = None
        self.reason = None

    def check_heads(self):
        if self.phase != 'watching': return
        values = self.io.heads()
        if len(values) != 4 or any(type(v) is not bool for v in values):
            raise ValueError('head sensor unavailable')
        hits = [i for i, v in enumerate(values) if v]
        if hits:
            self.triggered.update(hits)
            raise ValueError('head sensor triggered')

    def stop(self, slots, reason):
        self.reason = reason
        self.phase = 'stopping'
        self.stop_at = self.clock()
        accepted = True
        for slot in slots:
            if slot not in self.stop_sent:
                self.stop_sent.add(slot)
                if self.io.stop(slot) is not True:
                    accepted = False
        if not accepted:
            self.phase = 'uncertain'
        return accepted

    def tick(self):
        if self.phase in ('complete', 'uncertain'):
            return self.phase
        try:
            if self.phase in ('waiting_empty', 'waiting_insert') and self.clock() - self.started >= 300:
                self.stop([2], 'waiting timeout')
            if self.phase == 'watching' and self.clock() - self.inserted_at >= 90:
                self.stop([2], 'insertion timeout')
            if self.phase == 'stopping' and self.clock() - self.stop_at >= 8:
                self.phase = 'uncertain'; return self.phase
            heads = self.io.heads()
            if len(heads) != 4 or any(type(x) is not bool for x in heads):
                raise ValueError('invalid head sensor')
            if self.phase == 'watching':
                self.triggered.update(i for i, value in enumerate(heads) if value)
            if self.phase == 'watching' and any(heads):
                self.stop([2], 'head sensor')
                return self.phase
            states = self.io.status()
            if len(states) != 4:
                raise ValueError('invalid lane state')
            other = [i for i in (0, 1, 3) if states[i] != 'ready']
            if other:
                self.stop([2] + other, 'other lane active')
                self.phase = 'uncertain'
                return self.phase
            if self.phase == 'stopping':
                decoder = self.io.decoder()
                if states[2] == 'ready' and decoder == self.last_decoder:
                    self.stable += 1
                    if self.stable_since is None: self.stable_since = self.clock()
                else:
                    if self.last_decoder is not None and (states[2] != 'ready' or decoder != self.last_decoder):
                        if self.io.stop(2) is not True:
                            self.phase = 'uncertain'; return self.phase
                    self.stable = 0; self.stable_since = None
                self.last_decoder = decoder
                if self.stable >= 3 and self.stable_since is not None and self.clock() - self.stable_since >= 1.5:
                    self.phase = 'complete'
                return self.phase
            gate = self.io.gate()
            if type(gate) is not bool:
                raise ValueError('invalid gate')
            if self.phase == 'waiting_empty':
                if states[2] != 'ready':
                    self.stop([2], 'movement while waiting empty')
                elif not gate and not any(heads):
                    self.phase = 'waiting_insert'
                    self.io.event('ready_for_insert')
            elif self.phase == 'waiting_insert':
                if any(heads):
                    self.stop([2], 'head asserted before insert')
                elif gate:
                    self.phase = 'watching'
                    self.inserted_at = self.clock()
                    self.seen_active = states[2] != 'ready'
                    self.io.event('insert_seen')
            elif self.phase == 'watching':
                decoder = self.io.decoder()
                if type(decoder) is not int or abs(decoder) > 1700:
                    raise ValueError('encoder outside budget')
                self.forward_peak = max(self.forward_peak, decoder)
                if states[2] != 'ready': self.seen_active = True
                if self.seen_active and states[2] == 'ready':
                    if decoder == self.last_decoder:
                        self.stable += 1
                        if self.stable_since is None: self.stable_since = self.clock()
                    else:
                        self.stable = 0; self.stable_since = None
                    if self.stable >= 3 and self.stable_since is not None and self.clock() - self.stable_since >= 1.5:
                        self.phase = 'complete'
                self.last_decoder = decoder
            return self.phase
        except Exception as exc:
            if self.phase == 'watching' and self.triggered and str(exc) == 'head sensor triggered':
                self.stop([2], 'head sensor')
            else:
                self.stop([2], 'I/O failure: '+str(exc))
                self.phase = 'uncertain'
            return self.phase

class DeviceIO:
    """ACE2Transport adapter. Caller owns release, restoration and lifecycle."""
    def __init__(self, transport, ota, http):
        self.transport, self.ota, self.http = transport, ota, http
        self.before_request = None
    def send(self, command, payload=b'', timeout=.2):
        if command != 9 and self.before_request is not None: self.before_request()
        expected = (self.transport._seq % 0xffff) + 1
        replies = self.transport.send_recv(command, payload, timeout)
        if len(replies) != 1 or not replies[0]['is_resp'] or replies[0]['seq'] != expected:
            raise ValueError('unmatched or missing reply cmd %d' % command)
        return self.ota.pb_decode(replies[0]['payload'])
    def status(self):
        fields = self.send(6); rows = fields.get(9, [])
        if len(rows) != 4 or any(w != 2 for w, _ in rows): raise ValueError('bad status')
        names = {0:'ready',1:'feeding',2:'rollback',3:'assisting',4:'rollback_assisting',5:'preloading'}
        return [names.get(self.ota.get_field(self.ota.pb_decode(data), 1, 0),'error') for _,data in rows]
    def decoder(self):
        rows = self.send(76).get(1, [])
        if len(rows) != 4 or any(w != 2 for w, _ in rows): raise ValueError('bad decoder rows')
        value = self.ota.get_field(self.ota.pb_decode(rows[2][1]), 3, None)
        if type(value) is not int: raise ValueError('missing decoder')
        return value-(1<<64) if value >= 1<<63 else value
    def gate(self):
        value = self.ota.get_field(self.send(73),1,None)
        if type(value) is not int: raise ValueError('missing gate')
        return bool(value & (1<<8))
    def heads(self):
        names='&'.join('filament_motion_sensor%%20e%d_filament'%i for i in range(4))
        state=self.http('/printer/objects/query?print_stats&virtual_sdcard&webhooks&'+names,timeout=.5)['result']['status']
        if (state['print_stats']['state'] != 'standby' or state['virtual_sdcard']['is_active']
                or state['webhooks']['state'] != 'ready'): raise ValueError('printer not idle')
        return [state['filament_motion_sensor e%d_filament'%i]['filament_detected'] for i in range(4)]
    def stop(self, slot):
        for _ in range(3):
            try:
                fields=self.send(9,self.ota._field_uint32(1,slot),timeout=.3)
                if self.ota.get_field(fields,1,0)==0: return True
            except Exception:
                pass
        return False
    def event(self, text):
        print(text, flush=True)
