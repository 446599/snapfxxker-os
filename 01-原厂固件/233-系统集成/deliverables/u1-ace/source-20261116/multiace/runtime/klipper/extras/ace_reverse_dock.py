"""One authorized 20 mm reverse docking attempt; no automatic follow-up.

SELECT success requests STOP, not a UID read or slot assignment. The shared
reader can see the neighbouring spool. A delayed/failed STOP can consume the
rest of the finite 20 mm rollback, so landing in the RF window is not assured.
"""
from dataclasses import dataclass
import json
import os

from .ace_exploration import (
    ControlledExploration, ExplorationPlan, OneShotAuthorization, finite)
from .ace_async_uid import packed_index

AUTHORIZATION_PATH = '/home/lava/printer_data/controlled-exploration/reverse-dock-slot3.json'


class ReverseDockAuthorization(OneShotAuthorization):
    """Separate kind/path; forward permits cannot authorize reverse movement."""
    def check(self):
        r, now = self.record, self.clock()
        if (not isinstance(r, dict) or r.get('kind') != 'one_shot_ace_reverse_dock'
                or r.get('direction') != 'rollback'
                or any(type(r.get(k)) is not int for k in ('ace_index', 'slot', 'length_mm', 'speed_mm_s'))
                or (r['ace_index'], r['slot'], r['length_mm'], r['speed_mm_s']) != (0, 3, 20, 10)
                or not isinstance(r.get('authorization_id'), str) or not r['authorization_id'].strip()
                or not finite(r.get('expires_at_utc')) or not 0 < r['expires_at_utc'] - now <= 600):
            raise ValueError('missing, expired or wrong-scope one-shot reverse docking authorization')
        if not self.consumed and os.path.exists(self.path + '.consumed'):
            raise ValueError('this one-shot reverse docking authorization was already consumed')


@dataclass(frozen=True)
class ReverseDockPlan(ExplorationPlan):
    length_mm: int = 20

    def validate(self, now):
        if (not finite(now) or (self.ace_index, self.slot, self.head, self.length_mm, self.speed_mm_s) != (0, 3, 3, 20, 10)
                or not isinstance(self.session, str) or not self.session):
            raise ValueError('only ACE0 SLOT3/head3, one 20 mm rollback at 10 mm/s is supported')


class ReverseDock(ControlledExploration):
    def __init__(self, reader, authorization):
        if not isinstance(authorization, ReverseDockAuthorization):
            raise ValueError('independent reverse docking authorization required')
        authorization.check()
        super().__init__(reader, authorization)
        self.plan = ReverseDockPlan(authorization.record['authorization_id'])
        self.select_observation = None
        self.negative_encoder_seen = False
        self.provisional_zero_samples = 0
        self.last_encoder_provisional_zero = False
        self.provisional_zero_observation = None

    def _motion_mode(self):
        return 1

    def _active_motion_state(self):
        return 'rollback'

    def _target_decoder_available(self, target, now):
        if target.get('_decoder_present') is True:
            self.last_encoder_provisional_zero = False
            return True
        # In GET_FEED_INFO the deployed parser sets _decoder_present=False
        # only when field 3 is absent, not when its type is wrong. Together
        # with valid outer/4-lane structure (checked by the caller) and valid
        # lane protobuf framing, its integer zero is protobuf's default.
        # Permit it only before this command has shown negative displacement.
        # Post-STOP omissions can NEVER contribute to rest verification.
        if (self.state in ('preflight', 'scanning') and not self.stop_sent
                and not self.negative_encoder_seen
                and target.get('_decoder_present') is False
                and target.get('_wire_valid') is True
                and type(target.get('decoder')) is int and target['decoder'] == 0
                and all(type(target.get(k)) is int and target[k] >= 0
                        for k in ('steps', 'length'))
                and target['length'] <= self.plan.length_mm):
            self.last_encoder_provisional_zero = True
            self.provisional_zero_samples += 1
            self.provisional_zero_observation = {
                'received_at': now, 'steps': target['steps'], 'length': target['length'],
                'decoder': 0, 'state': self.state,
                'label': 'provisional protobuf default zero; not observed movement or rest'}
            return True
        return False

    def _record_motion_encoder(self, raw):
        # Caller has decoded protobuf uint64's two's-complement form. Keep
        # the signed value; do not relabel a rollback as forward movement.
        if not -self.plan.length_mm <= raw <= 0:
            raise ValueError('rollback encoder outside finite negative command')
        if self.last_encoder is not None and raw > self.last_encoder:
            raise ValueError('rollback encoder moved forward')
        self.last_encoder = raw
        if raw < 0:
            self.negative_encoder_seen = True
        if raw < 0 and self.active_status_seen:
            self.motion_seen = True

    def _launch(self, now):
        super()._launch(now)
        if self.move_sent and not self.stop_sent:
            # Observe active rollback and its negative encoder first. An
            # early SELECT can still stop before those samples, in which
            # case rest stays uncertain rather than trusting old READY.
            self.cycle = ['get_key_state', 'get_status', 'get_feed_info',
                          '__rf__', '__rf__', 'get_key_state', '__rf__',
                          'get_status', 'get_feed_info', '__rf__']
            self.cycle_index = 0

    def _reply(self, token, method, reply, kind, now):
        rf = self.rf
        intent = self.rf_intent
        stage = rf.stage if rf is not None else None
        selected_request = (kind == 'rf' and self.pending is not None
            and self.pending[0] == token and intent is not None
            and rf is not None and rf.pending is intent
            and intent.index == packed_index(self.plan.slot, 6))
        super()._reply(token, method, reply, kind, now)
        # Only the existing RF parser can advance SELECT to its buffer stage:
        # echo, complete frame/payload, timing and code semantics are checked
        # there. status=1, collision, missing echo and stale callbacks cannot
        # be mistaken for a detection. No op9 request is sent while moving.
        if (selected_request and self.state == 'scanning' and not self.stop_sent
                and rf.state == 'reading' and rf.stage == stage + 1):
            self.select_observation = {
                'received_at': now, 'reader': self.plan.slot // 2,
                'assigned_slot': None, 'uid': None,
                'protocol_default_zero_assumption': rf.default_zero_used,
                'label': 'unassigned SELECT detection; static UID verification required'}
            self._request_stop('SELECT detected a possible tag; stop requested for later static UID verification')

    def _finish_watch(self, eventtime):
        if self.state not in self.TERMINAL:
            return eventtime + .05
        ace = self.io.ace
        if getattr(ace, '_exploration_owner', None) is self:
            ace._exploration_owner = None
            ace._tag_read_busy = False
        ace._reverse_dock_last_result = self.report()
        return self.io.reactor.NEVER

    def report(self):
        out = super().report()
        out.update(mode='one-shot lower-assurance reverse docking', direction='rollback',
            commanded_mm=20, speed_mm_s=10, signed_encoder_mm=self.last_encoder,
            last_encoder_provisional_zero=self.last_encoder_provisional_zero,
            provisional_zero_samples=self.provisional_zero_samples,
            provisional_zero_observation=(dict(self.provisional_zero_observation)
                if self.provisional_zero_observation else None),
            select_observation=dict(self.select_observation) if self.select_observation else None,
            history={'basis': 'Current-session recorded forward travel; one short reversal over that path',
                     'limit': 'Travel history is not precise tag location or guaranteed free spool uptake.'},
            residual_risk='A delayed or failed STOP may allow the rest of the 20 mm rollback; the tag may pass out of the read window.',
            uid_classification='static_verification_required' if self.select_observation else 'not_observed_in_this_bounded_scan',
            full_turn_verified=False, slot_assignment=None)
        return out


def start_reverse_dock_command(ace, gcmd):
    """ACE_TAG_READ REVERSE_DOCK=1 [STATUS=1 | CANCEL=1]."""
    if gcmd.get_int('STATUS', 0, minval=0, maxval=1):
        current = getattr(ace, '_reverse_dock_scan', None)
        result = current.report() if current is not None else getattr(
            ace, '_reverse_dock_last_result', {'state': 'no reverse docking result in this process'})
        gcmd.respond_info(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return result
    if gcmd.get_int('CANCEL', 0, minval=0, maxval=1):
        current = getattr(ace, '_reverse_dock_scan', None)
        if current is not None:
            current.cancel()
            gcmd.respond_info('Reverse docking cancellation requested; STOP is not proof of standstill')
        return current
    args = (gcmd.get_int('ACE', 0), gcmd.get_int('SLOT', 3),
            gcmd.get_int('MAX_MM', 20), gcmd.get_int('SPEED', 10))
    if args != (0, 3, 20, 10):
        raise gcmd.error('Reverse docking supports only ACE0 SLOT3, 20 mm at 10 mm/s')
    try:
        authorization = ReverseDockAuthorization(AUTHORIZATION_PATH)
        from .ace_rc522 import AceTagReader
        scan = ReverseDock(AceTagReader(ace), authorization)
        ace._reverse_dock_scan = scan
        gcmd.respond_info('One-shot 20 mm rollback; SELECT requests STOP, UID remains unassigned; no automatic follow-up')
        scan.start()
        if scan.state == 'blocked':
            raise ValueError(scan.reason)
        return scan
    except Exception as exc:
        raise gcmd.error('Reverse docking refused: ' + str(exc))
