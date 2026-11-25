"""Offline sensor-seek safety state; NO G-code registration or device I/O.

This is a reviewable component, not a deployable or hardware-safe runner.
A caller must arrange reactor polling, fresh telemetry and bounded commands.
"""

class Monitor:
    def __init__(self, ace, slot, head, transport):
        self.target = (ace, slot, head)
        self.transport = transport
        self.state = 'idle'
        self.ack = False
        self.hit = None
        self.reason = None
        self.stop_requested = False
        self.decoder = None

    def begin(self, transport, sensors):
        if self.state != 'idle' or transport is not self.transport:
            raise ValueError('invalid session or transport')
        if len(sensors) != 4 or any(x is not False for x in sensors):
            raise ValueError('all four sensors must be clear')
        self.state = 'moving'

    def stop(self, reason):
        if self.state == 'moving':
            self.state = 'stopping'
            self.reason = reason
            self.stop_requested = True
            return True
        return False

    def sample_sensors(self, transport, sensors):
        if self.state != 'moving':
            return False
        if transport is not self.transport:
            return self.stop('transport changed')
        if len(sensors) != 4 or any(type(x) is not bool for x in sensors):
            return self.stop('sensor unavailable')
        hits = tuple(i for i, value in enumerate(sensors) if value)
        if hits:
            self.hit = hits
            return self.stop('sensor triggered')
        return False

    def feed_ack(self, accepted):
        if self.state not in ('moving', 'stopping'):
            return
        if accepted is not True:
            if self.state == 'moving':
                self.stop('feed acknowledgement missing or rejected')
            self.reason = 'feed acknowledgement missing or rejected'
            return
        self.ack = True

    def stopped(self, accepted, ready, decoder1, decoder2):
        if self.state != 'stopping':
            raise ValueError('unexpected stop completion')
        if (accepted is not True or ready is not True or not self.ack
                or type(decoder1) is not int or decoder1 != decoder2):
            self.state = 'uncertain'
            return
        self.decoder = decoder2
        if self.reason == 'sensor triggered' and self.hit == (self.target[2],):
            self.state = 'at_sensor'
        else:
            self.state = 'failed'

class Runner:
    """Reactor adapter. IO methods must be asynchronous and freshly correlated.

    NO registration or production wiring is provided. The caller must enforce
    the preflight identity, route, idle, gate, and exclusive-session checks.
    """
    def __init__(self, reactor, io, transport, session, head=2):
        self.reactor, self.io, self.transport, self.session = reactor, io, transport, session
        self.head = head
        self.started = reactor.monotonic()
        self.total_commanded = 0
        self.total_encoder = 0
        self.state = 'new'
        self.stop_sent = False
        self.move_ack = None
        self.hit = None
        self.reason = None
        self.generation = 0

    def start(self):
        if self.state != 'new' or self.io.preflight(self.transport, self.session) is not True:
            raise ValueError('fresh preflight rejected')
        if self.io.sensors() != (False, False, False, False):
            raise ValueError('sensors not clear')
        self.state = 'between'
        self.reactor.register_timer(self.tick, self.reactor.monotonic())

    def _stop(self, reason):
        if self.stop_sent:
            return
        self.stop_sent = True
        self.reason = reason
        self.state = 'stopping'
        self.generation += 1
        self.io.stop(self._stopped)

    def _stopped(self, accepted):
        if self.state != 'stopping':
            return
        if accepted is not True:
            self.io.latch('STOP not acknowledged')
            self.state = 'uncertain'
            return
        self.state = 'verifying'
        self.io.verify_still(self._verified)

    def _verified(self, ready, first, second):
        if self.state != 'verifying':
            return
        if (ready is not True or type(first) is not int or first != second
                or self.move_ack is not True or first < 0 or first > 55):
            self.io.latch('movement or standstill uncertain')
            self.state = 'uncertain'
            return
        self.total_encoder += first
        self.state = 'at_sensor' if self.reason == 'sensor' and self.hit == (self.head,) else 'failed'

    def _acked(self, generation, accepted):
        if generation != self.generation or self.state != 'moving':
            return
        self.move_ack = accepted is True
        if not self.move_ack:
            self._stop('feed ACK missing or rejected')

    def _complete(self, generation, ready, first, second):
        if generation != self.generation or self.state != 'moving':
            return
        if (self.move_ack is not True or ready is not True or type(first) is not int
                or first != second or first <= 0 or first > 55):
            self._stop('segment completion uncertain')
            return
        self.total_encoder += first
        self.state = 'between'

    def tick(self, now):
        if self.state in ('at_sensor', 'failed', 'uncertain'):
            return self.reactor.NEVER
        if self.state in ('stopping', 'verifying'):
            return now + .05
        try:
            sensors = self.io.sensors()
            if (self.io.valid_session(self.transport, self.session) is not True
                    or len(sensors) != 4 or any(type(x) is not bool for x in sensors)):
                self._stop('session or sensor invalid')
            elif any(sensors):
                self.hit = tuple(i for i, x in enumerate(sensors) if x)
                self._stop('sensor')
            elif now - self.started >= 450 or self.total_commanded >= 2000:
                if self.state == 'moving':
                    self._stop('limit')
                else:
                    self.state = 'failed'; self.reason = 'limit'
            elif self.state == 'between':
                self.generation += 1
                generation = self.generation
                self.state = 'moving'; self.move_ack = None; self.stop_sent = False
                self.total_commanded += 50
                self.io.feed_once(50, 5, lambda accepted: self._acked(generation, accepted),
                                  lambda ready, a, b: self._complete(generation, ready, a, b))
        except Exception:
            if self.state == 'moving':
                self._stop('I/O exception')
            else:
                self.io.latch('I/O exception'); self.state = 'uncertain'
        return now + .05
