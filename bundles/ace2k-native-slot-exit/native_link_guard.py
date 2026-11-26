"""Driver-side reservation of one ACE2K lane.

Not a public motion API. A host route coordinator must retain a durable claim
on the actual tube/heads before calling reserve, and keep it through verified
completion. All native wire access is serialized on the existing link._io.
"""
from dataclasses import dataclass
import uuid


@dataclass(frozen=True)
class LaneHold:
    wire: object
    raw: object
    uid: bytes
    after: float


@dataclass(frozen=True)
class QuietLaneHold:
    token: str
    lane: int
    raw: object
    uid: bytes
    epoch: str
    after: float


class NativeExitLinkGuard:
    def __init__(self, link):
        self.link = link
        self.epoch = uuid.uuid4().hex
        self._holds = {}
        self._quiet = {}
        self._samples = {}
        self._post = {}
        self._invalid = False

    def invalidate(self):
        # Called on close/reset. A host's durable route claim is NOT released.
        with self.link._io:
            self._invalid = True

    def live(self):
        link = self.link
        return (not self._invalid and link._ready and link.is_open
                and link._dead is None and link._shutdown is None)

    def blocked(self, lane):
        return (type(lane) is int and 0 <= lane < 4
                and (lane in self._holds or lane in self._quiet))

    def mask(self):
        return (sum(1 << lane for lane in self._holds)
                | sum(1 << lane for lane in self._quiet))

    def reserve_quiet_lane(self, token, lane):
        """Protect a different unit's same-head lane, no motion or G-code.

        Coordinator must hold the persistent physical route, serialize all
        callers and acquire all required lanes before issuing native UNLOAD.
        Caller must also obtain fresh stationary reports after each mask ACK.
        """
        link = self.link
        with link._io:
            if (not isinstance(token, str) or not token
                    or type(lane) is not int or not 0 <= lane < 4
                    or not self.live() or len(bytes(link.uid)) != 12 or not any(link.uid)
                    or not link._lane_report() or link._in_use is None
                    or self.blocked(lane) or link._in_use & (1 << lane)
                    or any(lane in owners for owners in (link._jobs, link._follow,
                        link._follow_want, link._tag_reads, link._sessions,
                        getattr(link, '_native_unload_owners', {})))):
                raise RuntimeError('shared head lane unavailable')
            modes = link._feed[1].get('mode', ()) if link._fresh(link._feed) else ()
            if len(modes) != 4 or modes[lane] != 0:
                raise RuntimeError('shared head lane not idle')
            hold = QuietLaneHold(token, lane, link._raw, bytes(link.uid), self.epoch, link._now())
            self._quiet[lane] = hold
            try:
                link._in_use_sent = (0.0, None)
                link._lanes_tick(link._now())
                sent_at, mask = link._in_use_sent
                if mask is None or not mask & (1 << lane) or sent_at < hold.after:
                    raise RuntimeError('shared head lane mask not acknowledged')
                hold = QuietLaneHold(token, lane, link._raw, bytes(link.uid), self.epoch, link._now())
                self._quiet[lane] = hold
                return hold
            except BaseException:
                # No native motion sent by this reservation; never authorize
                # any start based on an unsuccessful mask exchange.
                del self._quiet[lane]
                raise

    def _same_quiet(self, hold):
        return (isinstance(hold, QuietLaneHold) and self.live()
                and self._quiet.get(hold.lane) is hold and hold.epoch == self.epoch
                and self.link._raw is hold.raw and bytes(self.link.uid) == hold.uid)

    def quiet_sample(self, hold):
        from .ace_protocol import native_sample
        link = self.link
        with link._io:
            if not self._same_quiet(hold):
                return None
            _, mask = link._in_use_sent
            if mask is None or not mask & (1 << hold.lane):
                return None
            return native_sample(link._evidence_sensors, link._feed,
                                 link._evidence_enc, link._fg, hold.lane,
                                 link._now(), after=hold.after)

    def release_quiet_lane(self, hold, *, token):
        """Coordinator-only; caller verifies no dispatch or completed route.

        No timeout release, stale token, or release on a replaced connection.
        Durable recovery on a dead link belongs to the host coordinator.
        """
        with self.link._io:
            if not self._same_quiet(hold) or hold.token != token:
                raise RuntimeError('stale shared head reservation')
            del self._quiet[hold.lane]

    def _current(self, hold):
        link = self.link
        wire = hold.wire
        return (self.live() and self._holds.get(wire.op.lane) is hold
                and wire.link is link and link._raw is hold.raw
                and bytes(link.uid) == hold.uid and wire.op.connection_epoch == self.epoch
                and wire.op.device_uid == wire.uid_key and wire.route_valid())

    def reserve(self, wire):
        """Hold only the requested lane, acknowledge the MCU mask, then sample anew.

        Caller has already established actual route and persistent ownership;
        no current or queued competing motion may exist on this lane.
        No route proof is inferred from inlet state or head numbering.
        """
        link = self.link
        with link._io:
            op = wire.op
            if (not self.live() or wire.link is not link or not wire._same_link()
                    or op.connection_epoch != self.epoch or not wire.route_valid()
                    or wire.state != 'new' or type(op.lane) is not int or not 0 <= op.lane < 4
                    or not link._lane_report() or link._in_use is None):
                raise RuntimeError('native exit connection or route unavailable')
            group = op.lane
            pair = (op.lane,)
            mask = 1 << op.lane
            if any(self.blocked(lane) for lane in pair) or link._in_use & mask or any(
                    lane in owners for lane in pair for owners in (
                        link._jobs, link._follow, link._follow_want, link._tag_reads,
                        link._sessions, getattr(link, '_native_unload_owners', {}))):
                raise RuntimeError('lane is in use')
            modes = link._feed[1].get('mode', ()) if link._fresh(link._feed) else ()
            if len(modes) != 4 or any(modes[lane] != 0 for lane in pair):
                raise RuntimeError('lane is not idle')
            # Publish the hold BEFORE sending the in-use mask, under the same
            # lock as ordinary starts and the automatic driver tick.
            hold = LaneHold(wire, link._raw, bytes(link.uid), link._now())
            self._holds[group] = hold
            try:
                link._in_use_sent = (0.0, None)
                link._lanes_tick(link._now())
                sent_at, sent_mask = link._in_use_sent
                if sent_mask is None or sent_mask & mask != mask or sent_at < hold.after:
                    raise RuntimeError('lane reservation mask not acknowledged')
                # The host MUST obtain two independent reports AFTER this ACK.
                hold = LaneHold(wire, link._raw, bytes(link.uid), link._now())
                self._holds[group] = hold
                self._samples[group] = None
                return hold
            except BaseException:
                # No native start was sent. The next normal tick can restore
                # the mask; the durable host claim is managed by its caller.
                del self._holds[group]
                raise

    def _read_lane(self, hold):
        from .ace_protocol import native_sample
        link = self.link
        lane = hold.wire.op.lane
        if not self._current(hold):
            return None
        pair = tuple(native_sample(link._evidence_sensors, link._feed,
                                   link._evidence_enc, link._fg, n,
                                   link._now(), after=hold.after)
                     for n in (lane,))
        return pair if all(s is not None for s in pair) else None

    def capture(self, hold):
        """Capture a fresh lane sample from the real link, not a caller-supplied sample."""
        with self.link._io:
            pair = self._read_lane(hold)
            if pair is None:
                return False
            self._samples[hold.wire.op.lane] = pair
            return True

    def start(self, hold, speed_um_s):
        """Start once only after the selected lane is still following mask ACK.

        The host must mark its durable transaction dispatched BEFORE calling
        this method. Failed/uncertain starts retain the host's ownership.
        """
        from .ace_protocol import still
        link = self.link
        with link._io:
            lane = hold.wire.op.lane
            first = self._samples.get(lane)
            second = self._read_lane(hold)
            _, mask = link._in_use_sent
            if (first is None or second is None or mask is None
                    or not mask & (1 << lane)
                    or not all(still(a, b) for a, b in zip(first, second))
                    or not second[0].present):
                raise RuntimeError('lane standstill not established')
            wire = hold.wire
            return wire.start(first[0], second[0], device_uid=wire.op.device_uid,
                              connection_epoch=self.epoch, speed_um_s=speed_um_s)

    def observe(self, event, received_at):
        """Real RX dispatch hook; consume only this exact native event."""
        lane = event.get('lane') if isinstance(event, dict) else None
        if type(lane) is not int or not 0 <= lane < 4 or lane not in self._holds:
            return False
        hold = self._holds[lane]
        wire = hold.wire
        # Do not require the route to still be safe to RECORD a terminal event.
        # A route failure must block success/release, never hide a STOP/fault.
        if (not self.live() or self.link._raw is not hold.raw
                or bytes(self.link.uid) != hold.uid or wire.op.lane != lane
                or wire.op.connection_epoch != self.epoch):
            return False
        return wire.note_event(event, received_at=received_at,
                               device_uid=wire.op.device_uid, connection_epoch=self.epoch)

    def _same_hold(self, hold):
        wire = hold.wire
        return (self.live() and self._holds.get(wire.op.lane) is hold
                and wire.link is self.link and self.link._raw is hold.raw
                and bytes(self.link.uid) == hold.uid and wire.op.connection_epoch == self.epoch)

    def cancel(self, hold, *, lane, seq, epoch):
        # STOP must remain available even if the host loses route proof.
        with self.link._io:
            if not self._same_hold(hold):
                return False
            return hold.wire.cancel(lane=lane, seq=seq, connection_epoch=epoch)

    def query_last(self, hold):
        with self.link._io:
            if not self._same_hold(hold):
                return False
            from .ace_protocol import native_sample
            wire = hold.wire
            link = self.link
            if wire.start_at is None:
                return False
            sample = native_sample(link._evidence_sensors, link._feed,
                                   link._evidence_enc, link._fg, wire.op.lane,
                                   link._now(), after=wire.start_at)
            return wire.query_last(sample, device_uid=wire.op.device_uid,
                                   connection_epoch=self.epoch)

    def capture_after_event(self, hold):
        with self.link._io:
            wire = hold.wire
            if not self._current(hold) or wire.event_at is None:
                return False
            pair = self._read_lane(hold)
            if pair is None or any(t <= wire.event_at for s in pair for t in s.stamp):
                return False
            self._post[wire.op.lane] = pair
            return True

    def finish(self, hold, commit_release):
        """Release only after actual unloaded + post-event standstill.

        commit_release(op, event, a, b, event_at, now) MUST synchronously
        remove the exact durable host claim using this evidence. It must
        return True on success, not just on acceptance. The caller must
        arrange the shared host lock order; this hook alone cannot own
        physical tubes or protect another transport.
        """
        from .ace_protocol import still
        with self.link._io:
            wire = hold.wire
            group = wire.op.lane
            first = self._post.get(group)
            second = self._read_lane(hold)
            now = self.link._now()
            if (first is None or second is None or wire.event_at is None
                    or any(t <= wire.event_at for pair in (first, second) for s in pair for t in s.stamp)
                    or not all(still(a,b) for a,b in zip(first,second))
                    or wire.verdict(first[0], second[0], now=now,
                        device_uid=wire.op.device_uid, connection_epoch=self.epoch) != 'complete'):
                return False
            owners = getattr(self.link, '_native_unload_owners', {})
            if owners.get(wire.op.lane) is not wire:
                return False
            if commit_release(wire.op, dict(wire.event), first[0], second[0], wire.event_at, now) is not True:
                return False
            del owners[wire.op.lane]
            self._remove(group)
            return True

    def _remove(self, group):
        del self._holds[group]
        self._samples.pop(group, None)
        self._post.pop(group, None)

    def release_before_start(self, hold):
        with self.link._io:
            if not self._current(hold) or hold.wire.state != 'new':
                raise RuntimeError('native motion may have started')
            group = hold.wire.op.lane
            self._remove(group)

    # Unknown or failed tasks deliberately retain the hold. A host recovery
    # coordinator must supply a separate verified recovery procedure.
