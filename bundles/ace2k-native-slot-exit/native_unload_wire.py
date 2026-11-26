"""Unwired ACE2K native UNLOAD transport, for integration and simulation only.

The caller must hold a real route lease and connect this object to the existing
driver's request, event and cancellation paths. This file is deliberately not
installed into the printer or registered as a G-code command.
"""
from math import isfinite
import struct

from native_eject_contract import fresh_sample, native_start_fields, outcome, start_gate, terminal_event


class NativeUnloadWire:
    """One lane on one existing Ace2kLink; never opens another serial port."""

    def __init__(self, link, op, route_valid):
        self.link = link
        self.op = op
        self.uid = bytes(link.uid)
        words = struct.unpack('<3I', self.uid) if len(self.uid) == 12 else ()
        self.uid_key = ('uid:' + '-'.join('%08x' % word for word in words)
                        if words and any(words) else '')
        self.route_valid = route_valid
        self.state = 'new'
        self.event = None
        self.event_at = None
        self.start_at = None
        self.last_queried = False
        self.stop_sent = False

    def _same_link(self):
        return bool(self.uid_key == self.op.device_uid and self.uid_key
                    and self.link.is_open and bytes(self.link.uid) == self.uid)

    def _require_link(self):
        if not self._same_link():
            raise RuntimeError('ACE connection or identity changed')

    def _stop(self):
        # A transport ACK is only an accepted STOP frame, not stopped proof.
        self.stop_sent = self.link._send('ace2k_feed_stop', lane=self.op.lane) is True
        return self.stop_sent

    def start(self, a, b, *, device_uid, connection_epoch, speed_um_s):
        """Start once; a missing or malformed ACK leaves the lane held."""
        with self.link._io:
            self._require_link()
            if self.state != 'new':
                raise RuntimeError('UNLOAD already attempted')
            if not self.route_valid() or not start_gate(
                    self.op, a, b, now=self.link._now(), device_uid=device_uid,
                    connection_epoch=connection_epoch):
                raise RuntimeError('route or lane is not confirmed')
            lane = self.op.lane
            owners = getattr(self.link, '_native_unload_owners', None)
            if owners is not None and lane in owners:
                raise RuntimeError('lane has another native UNLOAD owner')
            if (lane in self.link._jobs or lane in self.link._follow
                    or lane in self.link._follow_want or lane in self.link._tag_reads):
                raise RuntimeError('lane has another owner')
            d = self.link.dictionary
            if not all(d.has(name) for name in ('ace2k_feed_start', 'ace2k_feed_stop',
                                                'ace2k_feed_last_query')):
                raise RuntimeError('native UNLOAD unavailable')
            feed = self.link._feed
            if (not self.link._fresh(feed) or not isinstance(feed[1], dict)
                    or not isinstance(feed[1].get('seq'), (bytes, bytearray, list, tuple))
                    or len(feed[1]['seq']) != 4
                    or type(feed[1]['seq'][lane]) is not int
                    or not 0 <= feed[1]['seq'][lane] <= 255):
                raise RuntimeError('lane sequence report unavailable')
            previous = self.link._lane_seq.get(lane)
            if previous is not None and previous != feed[1]['seq'][lane]:
                raise RuntimeError('lane sequence report not current')
            if self.link._next_seq(lane) != self.op.seq:
                raise RuntimeError('lane sequence changed')
            fields = native_start_fields(
                self.op, speed_um_s,
                d.constant('ACE2K_LANE_SPEED_MIN_UM_S', 9000),
                d.constant('ACE2K_LANE_SPEED_MAX_UM_S', 70000))
            # Nothing may yield between the final owner check and the write.
            if not self._same_link() or not self.route_valid():
                raise RuntimeError('route changed before start')
            if owners is None:
                owners = self.link._native_unload_owners = {}
            owners[lane] = self
            self.state = 'starting'
            self.start_at = self.link._now()
            try:
                reply = self.link._call('ace2k_feed_start',
                                        'ace2k_feed_start_response', **fields)
            except Exception as start_error:
                self.link._lane_seq[lane] = self.op.seq
                self.state = 'uncertain'
                try:
                    self._stop()
                except Exception as stop_error:
                    raise RuntimeError('UNLOAD start and STOP both unconfirmed: %s' % stop_error) from start_error
                raise
            if (not isinstance(reply, dict) or type(reply.get('lane')) is not int
                    or reply['lane'] != lane
                    or type(reply.get('accepted')) is not int
                    or reply['accepted'] not in (0, 1)):
                self.link._lane_seq[lane] = self.op.seq
                self.state = 'uncertain'
                self._stop()
                return 'unconfirmed'
            if reply['accepted'] == 0:
                self.state = 'refused'
                del owners[lane]
                return 'refused'
            self.link._lane_seq[lane] = self.op.seq
            self.state = 'running'
            return 'started'

    def note_event(self, event, *, received_at, device_uid, connection_epoch):
        """Keep the first matching final event; later frames cannot erase it."""
        with self.link._io:
            if (not self._same_link() or self.state not in ('starting', 'running', 'uncertain', 'stopping')
                    or self.event is not None or type(received_at) not in (int, float)
                    or not isfinite(received_at) or self.start_at is None
                    or received_at < self.start_at):
                return False
            verdict = terminal_event(self.op, event, device_uid=device_uid,
                                     connection_epoch=connection_epoch)
            if verdict == 'unconfirmed':
                return False
            self.event, self.event_at = dict(event), received_at
            return True

    def query_last(self, sample, *, device_uid, connection_epoch):
        """Recover a lost event once, only while the original task is owned."""
        with self.link._io:
            self._require_link()
            if self.state not in ('running', 'uncertain', 'stopping') or self.event is not None or self.last_queried:
                return False
            if device_uid != self.op.device_uid or connection_epoch != self.op.connection_epoch:
                return False
            if (self.start_at is None or not fresh_sample(sample, after=self.start_at, now=self.link._now())
                    or sample.state not in ('ready', 'feed_error')):
                return False
            self.last_queried = True
            reply = self.link._call('ace2k_feed_last_query', 'ace2k_feed_last', lane=self.op.lane)
            # The reply can block. Post-event telemetry must be newer than the
            # actual response, not the earlier time when the query began.
            return self.note_event(reply, received_at=self.link._now(), device_uid=device_uid,
                                   connection_epoch=connection_epoch)

    def cancel(self, *, lane, seq, connection_epoch):
        """A stale screen action may never stop a newer lane operation."""
        with self.link._io:
            self._require_link()
            if ((lane, seq, connection_epoch) !=
                    (self.op.lane, self.op.seq, self.op.connection_epoch)
                    or self.state not in ('starting', 'running', 'uncertain', 'stopping')):
                return False
            self.state = 'stopping'
            return self._stop()

    def verdict(self, a, b, *, now, device_uid, connection_epoch):
        with self.link._io:
            if not self._same_link() or self.event is None:
                return 'unconfirmed'
            return outcome(self.op, self.event, a, b, event_received_at=self.event_at,
                           now=now, device_uid=device_uid, connection_epoch=connection_epoch)
