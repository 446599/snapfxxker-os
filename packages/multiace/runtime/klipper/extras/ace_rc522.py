# ace_rc522.py - multiACE tag reading on an ACE 2 through the RC522 line
# (stage 1: read/bind). Plain helper module imported lazily by ace.py -
# deliberately NOT a Klipper extra: no [ace_rc522] config section exists,
# so an old config cannot hit the unknown-section halt, and a printer without this file just gets a
# clear "module missing" from the command instead of a dead Klipper.
#
# What stage 1 does - and what it deliberately does not:
#   The antenna is fixed in the bay and the tag rides on the spool, so a
#   stationary read usually fails (Simon-CR HW: 192 polls / 45 s on a
#   known-good tag, all FAILED). The PARK routine rotates the lane in
#   small FEED-FORWARD steps (rotates the spool toward the tag; rollback
#   winds the strand back out and UNLOADED the spool on HW) and
#   probes with a normal FILAMENT_IDENTIFY between steps until the tag
#   answers - then it sits parked in the field. On ACE2-Open firmware
#   (V1.1.3O, the Simon-CR UID patch) the identify answers for ANY tag:
#   Anycubic format with full identity (version 101), everything else
#   with the card UID as the sku (version 513 = 0x0201 sentinel). Stock
#   V1.1.31 still answers for Anycubic tags, so the routine degrades
#   gracefully there. The Anycubic result feeds the EXISTING ingest/bind
#   paths.
#   Stage 2 (raw RC522 ops, ACE2-Open only): for a foreign tag the firmware
#   identify is blind to, op 6 SELECT sees ANY ISO14443A card and we read
#   the NTAG pages directly - UID from pages 0-1, and the OpenSpool NDEF/
#   JSON from the user pages (material/color/brand), fed through the same
#   ingest as a full identity. Bambu is MIFARE (encrypted): UID via the
#   firmware sentinel only, no page decode (needs auth); read-only.
#   Snapmaker's own RFID is read by the printer's INTERNAL feeder readers,
#   NOT the ACE - out of scope here.
#
# HW choreography rules baked in (each cost Simon-CR a wrong result):
#   - Slots 0/1 and 2/3 share ONE antenna per pair. If the neighbour bay
#     holds a spool AND a tag already answers before we moved anything,
#     the read is ambiguous (could be the neighbour's tag) -> refuse with
#     instructions instead of binding the wrong spool.
#   - Search rotates the spool FORWARD (a rollback unloaded it on HW);
#     the restore reverses the same distance. No autonomous
#     preload on this firmware, so feeding forward to rotate is safe.
#   - Motor commands can be busy-rejected (code=0 FORBIDDEN): every
#     move goes through _rejected with a paced retry.

import hashlib
import hmac
import copy
import json
import logging

PARK_STEP_MM = 20        # feed per probe step (Simon-CR: 15-25 works)
CONTINUOUS_SCAN_MM = 600
CONTINUOUS_SCAN_SPEED = 20
PARK_TOLERANCE_MM = 10
PARK_SPEED = 40          # mm/s. Faster only shortens the time BETWEEN probe
# points - the probe is stationary after each step,
# so speed costs no detection.
# Simon used 20; the ACE allows up to 100.
MOVE_MAX_MM = 2000       # hard cap on ONE reader move, feed or rollback:
# nothing this module does legitimately moves a lane
# further than a full sweep plus a park correction.
# A corrupted depth once produced a rollback of 80738
# units that threw the strand out of the unit.
# Refused, logged.
PARK_MAX_MM = 600        # ~one revolution of a 1 kg spool - allow at least
# that before concluding there is no tag
REQ_TIMEOUT = 3.0        # per-request reply wait
RC_IDEMPOTENT_OPS = (0, 4, 5, 6)   # reg read, rx byte, rx bits, SELECT
RC_IDEMPOTENT_RETRIES = 2          # extra asks per lost reply (see _rc)
MOVE_RETRIES = 3         # busy-rejection retries per motor command
MAX_ENCODER_RESIDUAL_MM = 18  # aggregate manual-read round-trip bound
MOVE_RETRY_PAUSE = 1.0   # s between them (50 ms retries are HW-proven useless)
# ACE2-Open reply version sentinels for "this sku is a card UID": 0x0201 =
# the 1.1.3O passthrough and the Bambu path up to 46O, 0x0102 = the Bambu
# path from 60O (mirror of ace.UID_SENTINEL_VERSIONS - no import, see below).
UID_VERSIONS = (0x0201, 0x0102)
# Mirror of ace.V2_ACTIVE_MOTION_STATES (no import: ace loads this module
# lazily, and the reader only needs the tuple for the neighbour gate).
V2_ACTIVE_MOTION_STATES = ('feeding', 'rollback', 'rollback_assisting',
                           'preloading')
# Search direction: FEED (mode 0), not rollback. Rollback winds the strand
# back and UNLOADED the spool on HW. There is no autonomous preload on
# this firmware, so feeding forward to rotate
# is safe; the restore reverses it (rollback the same distance).
SEARCH_MODE = 0          # FEED_OR_ROLLBACK mode: 0 = feed forward
RESTORE_MODE = 1         # opposite of SEARCH_MODE
PROBE_OFFSETS = (0, -10, -20, 10, 20, -30, -40, 30, 40)
# READ-guided centering: offsets from the
# SELECT hit point, tried in this order until ONE page-0 READ returns a
# full 16-byte frame. A fixed offset hits a narrow-field tag only by luck
# (a clean frame can sit 40 units BEFORE the hit, and spool hysteresis is
# about the field width). SELECT is no probe: it still answers at the edge
# where READ already fails.
CENTER_MM = 20           # after a sweep brake the tag sits at the FIELD
# EDGE (detection = field entry) - reads there are
# unreliable (identify+NDEF fail and even a
# double-read UID can pass with a flipped byte).
# Advance this
# far to center the tag before reading.
START_PROBE_MM = 160     # rotation test for a card answering at sweep
# start: move this far - a STATIONARY card (still
# answering, same UID) is the neighbour's; a card
# that left the field moved WITH us = our own tag.
# Must exceed the antenna field width (80 did NOT
# for a flange tag). 160 is twice the
# widest read window seen (+-40 around the hit,
# PROBE_OFFSETS) and still one short lane move
# each way (~8 s round trip).
CLEAR_STEP_MM = 20       # rotate-the-neighbour-away step - the neighbour
CLEAR_SILENT_STEPS = 2   # lane is fed forward in these steps until its card
CLEAR_MAX_MM = 200       # has stayed silent for CLEAR_SILENT_STEPS, capped.
# Forward only: a rollback could drop the neighbour
# out of its gate, forward is harmless far beyond
# the 390 park depth (the firmware's own pull-in
# goes to ~1650). Needed by the insert read AND the
# write: a parked neighbour tag in the shared field
# is a clean single card, and a write must never
# land on it.
NEIGHBOUR_SETTLE_S = 12  # a neighbour in its OWN firmware insert (identifying
# / feeding / preloading) drags its tag THROUGH the
# shared field: neither the rotation test nor a clear
# can tell whose card answers while both lanes move.
# Bounded wait for
# the neighbour to settle before deciding; a held
# 'preloading' simply runs the clock out.

# --- Stage 2: raw RC522 passthrough (ACE2-Open firmware only) -------------
# The UID passthrough patch only fires on the firmware's READ-FAILED exit
# (Bambu, read-refused). A tag that READS but does not PARSE as Anycubic
# (OpenSpool, blank NTAG) takes another exit -> FILAMENT_IDENTIFY sees
# nothing.
# So stage 2 goes under the firmware's identify to the RC522 itself, via
# Simon-CR's op tunnel (docs/04-tag-operations, tools/ace_reader.py): the
# sub-command is packed into FILAMENT_IDENTIFY's index field and the op's
# return byte comes back as the response 'code' (field 12).
#
#   INDEX = 0x80000000 | reader<<24 | op<<16 | (a1&0x3F)<<8 | (a2&0xFF)
#   op 0 read reg a1 | 1 write a2->reg a1 | 2 stage a2 at TX+a1
#   op 3 transceive(cmd a2, a1 TX bytes) | 4 read RX byte a1 | 5 rx bit len
#   op 6 SELECT (powers reader + REQA/anticollision/SELECT; 0 = card present)
#
# op 6 answers for ANY ISO14443A card (the reliable presence signal the
# park loop needs); the UID we read from NTAG pages 0-1 (a plain READ,
# since op 6's own UID lands at an offset the ops cannot read back).
RC_BIT31 = 0x80000000
# RC522 registers (subset, from ace_reader.py)
REG_BITFRAMING = 0x0D
REG_TXMODE = 0x12
REG_RXMODE = 0x13
PCD_TRANSCEIVE = 0x0C
RC_MAX_MM_STAGE2 = None   # follow the caller's max_mm
# op 6 select status meaning
SELECT_OK = 0


class MotionUncertain(RuntimeError):
    """Motion may have executed or a known move could not be restored."""


class ScanBudgetExhausted(RuntimeError):
    """A scan request was refused before any movement was sent."""


class AceTagReader:
    def __init__(self, ace, debug=False, dump=False):
        self.ace = ace
        self._debug = bool(debug)
        self._dump = bool(dump)
        # UID of a MIFARE card the firmware identify has just talked to:
        # it now sits HALTed and ignores our REQA, so presence checks ask
        # the identify for this UID instead (_card_answers).
        self._halted_uid = ''
        self._motion_uncertain = None
        self._motion_ledger = {}
        self._encoder_direction = {}
        self._encoder_ledger = {}

    def new_guarded_continuous_scan(self, plan, health_provider=None,
                                    mapping_provider=None, session_check=None,
                                    rf_context_provider=None):
        """Opt-in finite scan; no I/O until explicit start and verified admission."""
        from .ace_guarded_scan import GuardedContinuousScan
        return GuardedContinuousScan(self, plan, health_provider,
                                     mapping_provider, session_check, rf_context_provider)

    # -- plumbing ---------------------------------------------------------

    def _pause(self, seconds):
        r = self.ace.reactor
        r.pause(r.monotonic() + seconds)

    @staticmethod
    def _rejected(resp):
        """True when a motor command was NOT accepted:
        None / code!=0 / code=0 msg=FORBIDDEN (busy rejection). Own copy:
        the reference lives in ace_bg_swap (not on the ace object), and
        this module must not depend on the bg module being present."""
        if not resp:
            return True
        if resp.get('code', -1) != 0:
            return True
        return str(resp.get('msg', '')).strip().upper() == 'FORBIDDEN'

    def _req(self, idx, method, params, timeout=REQ_TIMEOUT):
        """Synchronous request from inside the greenlet: send + poll the
        reply box. The dispatcher invokes callbacks with KEYWORD
        arguments - callback(self=<ace>, response=<ret>) - so the
        parameters must literally be named 'self' and 'response'; a
        differently-named positional signature raises TypeError, which the
        response wrapper only logs, so the reply never arrives and the poll
        times out. **kw form tolerates both call styles."""
        deadline = self.ace.reactor.monotonic() + timeout
        stage_deadline = getattr(self, '_telemetry_deadline', None)
        if stage_deadline is not None:
            deadline = min(deadline, stage_deadline)
        if self.ace.reactor.monotonic() >= deadline:
            return None
        box = {}

        def _cb(*args, **kw):
            box['r'] = kw.get('response',
                              args[-1] if args else None)

        try:
            request = {'method': method, 'params': dict(params or {})}
            context = getattr(self, '_insert_checked_context', None)
            auth_owner = getattr(self, '_manual_auth_owner', None)
            if auth_owner is not None and method == 'feed_or_rollback_raw':
                if idx != 0:
                    raise MotionUncertain('attribution device changed')
                request['_manual_measure_token'] = auth_owner.issue_auth(request['params'])
                request['_insert_reject'] = _cb
            elif auth_owner is not None and method == 'stop_feed_filament':
                if idx != 0 or self.ace._serials.get(idx) is not auth_owner.transport:
                    raise MotionUncertain('attribution stop transport changed')
                request['_insert_stop_transport'] = auth_owner.transport
                request['_insert_reject'] = _cb
            elif context is not None and method == 'feed_or_rollback_raw':
                self._insert_guard()
                orig_idx, slot, transport, epoch, ps, active, token = context
                if idx != orig_idx:
                    raise MotionUncertain('insert device changed')
                uid = (getattr(self.ace, '_rc_last_uid', {}) or {}).get((idx, slot))
                request['_insert_wire'] = (transport, slot, epoch, ps, uid, active, token)
                request['_insert_reject'] = _cb
            elif context is not None and method == 'stop_feed_filament':
                orig_idx, slot, transport, epoch, ps, active, token = context
                if idx != orig_idx:
                    raise MotionUncertain('insert stop device changed')
                request['_insert_stop_transport'] = transport
                request['_insert_reject'] = _cb
            if method in ('feed_or_rollback_raw', 'stop_feed_filament'):
                fields = {k:(params or {}).get(k) for k in ('index','length','speed','mode')}
                logging.info('[multiACE] RFID motion intent ace=%s method=%s motion=%s time=%.3f guard=%s',
                             idx,method,fields,self.ace.reactor.monotonic(),context is not None)
            self.ace.send_request_to(idx, request, _cb)
        except Exception as e:
            logging.info('[multiACE] [rc522] send %s failed: %s'
                         % (method, e))
            return None
        # Poll granularity is the real cost of a page read: ~20 tunnel ops
        # per page, and a 50 ms poll wastes up to 50 ms per op though the
        # reply comes back in a few ms. Poll every 5 ms - reads/writes become reply-bound, not
        # sleep-bound (~5-10x). reactor.pause yields, so a greenlet at 5 ms
        # does not busy-spin the reactor.
        while 'r' not in box and self.ace.reactor.monotonic() < deadline:
            self._pause(min(0.005, deadline - self.ace.reactor.monotonic()))
        response = box.get('r')
        if method in ('feed_or_rollback_raw', 'stop_feed_filament'):
            ack = {k:response.get(k) for k in ('id','code','_v2_command','_v2_frame_valid','_v2_payload_valid')} if isinstance(response,dict) else None
            logging.info('[multiACE] RFID motion ack ace=%s method=%s ack=%s time=%.3f',
                         idx, method, ack, self.ace.reactor.monotonic())
        return response

    def _move(self, idx, slot, length, mode, respond, speed=PARK_SPEED):
        """Move once; retry only an explicit busy rejection, never a timeout."""
        if ((getattr(self,'_relative_unknown',False) or getattr(self,'_scan_measured',False)) and not getattr(self,'_relative_sweep',False)
                and not getattr(self,'_relative_measured',False)):
            return self._move_and_confirm(idx,slot,length,mode,respond)
        context = getattr(self,'_insert_checked_context',None)
        tracked = bool(getattr(self,'_scan_measured',False) and context is not None and (idx,slot)==context[:2])
        if tracked:
            if self._scan_motor_pending is not None:
                raise MotionUncertain('previous scan motor movement unconfirmed')
            if mode == SEARCH_MODE and (self._scan_attempts >= 48 or self._scan_zero_segments >= 2):
                raise ScanBudgetExhausted('scan movement attempts exhausted')
            total_limit = self._scan_motor_total_limit - (self._scan_forward_limit if mode == SEARCH_MODE else 0)
            if self._scan_motor_total + int(length) > total_limit:
                raise ScanBudgetExhausted('scan total motor budget exhausted')
            if mode == SEARCH_MODE and self._scan_motor_position + int(length) > self._scan_forward_limit:
                raise ScanBudgetExhausted('scan forward bound reached')
        self._insert_guard()
        if self._motion_uncertain:
            raise MotionUncertain(self._motion_uncertain)
        length = int(length)
        if length <= 0 or length > MOVE_MAX_MM:
            respond('rc522: refusing unsafe move length %d' % length)
            return False
        continuous_budget = getattr(self, '_continuous_budget', None)
        if continuous_budget is not None:
            if (idx, slot) != continuous_budget['key']:
                raise MotionUncertain('continuous RFID cannot move another slot')
            direction = self._encoder_direction.get((idx, slot), 1)
            position = self._encoder_ledger.get((idx, slot), 0) * direction
            if mode == SEARCH_MODE:
                if (continuous_budget['forward_commanded'] + length > continuous_budget['forward_limit']
                        or position + length > continuous_budget['limit']):
                    raise ScanBudgetExhausted('RFID 正送累计或最大位置达到本次程限')
                # Reserve before dispatch. A lost ACK never replenishes budget.
                continuous_budget['forward_commanded'] += length
            elif mode == RESTORE_MODE:
                if (continuous_budget['reverse_commanded'] + length > continuous_budget['reverse_limit']
                        or position - length < -PARK_TOLERANCE_MM):
                    raise ScanBudgetExhausted('RFID 回程累计达到本次程限')
                continuous_budget['reverse_commanded'] += length
            else:
                raise MotionUncertain('continuous RFID only permits bounded forward or reverse moves')
        if getattr(self, '_manual_proof', False):
            remaining = getattr(self, '_manual_motor_budget', 0)
            if length > remaining:
                raise MotionUncertain('manual scan motor budget exhausted')
            self._manual_motor_budget = remaining - length
        for attempt in range(MOVE_RETRIES):
            self._insert_guard()
            if getattr(self,'_single_auto',False):
                self._single_pending_stop = (idx,slot)
            resp = self._req(idx, 'feed_or_rollback_raw',
                             {'index': slot, 'speed': int(speed),
                              'length': length, 'mode': mode})
            if (not isinstance(resp, dict) or 'code' not in resp or
                    resp.get('_v2_payload_valid') is not True):
                self._motion_uncertain = (
                    'ACE %d slot %d %s %d mm acknowledgement missing; '
                    'motion may have happened; no retry or automatic restore'
                    % (idx, slot, 'rollback' if mode else 'feed', length))
                respond('rc522: WARNING - ' + self._motion_uncertain)
                raise MotionUncertain(self._motion_uncertain)
            code = resp.get('code')
            if isinstance(code, bool) or not isinstance(code, int):
                self._motion_uncertain = (
                    'ACE %d slot %d %s %d mm ambiguous acknowledgement; '
                    'no retry or automatic restore'
                    % (idx, slot, 'rollback' if mode else 'feed', length))
                respond('rc522: WARNING - ' + self._motion_uncertain)
                raise MotionUncertain(self._motion_uncertain)
            message = str(resp.get('msg', '')).strip().upper()
            if code == 0 and message != 'FORBIDDEN':
                if tracked:
                    self._scan_motor_pending = (mode,length)
                    self._scan_attempts += 1
                key = (idx, slot)
                self._motion_ledger[key] = (self._motion_ledger.get(key, 0)
                                            + (-length if mode else length))
                return True
            if getattr(self,'_single_auto',False):
                self._single_pending_stop = None
            if code != 2 and message != 'FORBIDDEN':
                respond('rc522: motor command explicitly rejected (code %s)'
                        % code)
                return False
            if attempt + 1 < MOVE_RETRIES:
                self._pause(MOVE_RETRY_PAUSE)
        respond('rc522: motor command explicitly busy - aborting')
        return False

    def _commit_scan_motor(self, idx, slot, mode, commanded, actual, reset):
        context = getattr(self,'_insert_checked_context',None)
        if not getattr(self,'_scan_measured',False) or context is None or (idx,slot)!=context[:2]:
            return
        if (self._scan_motor_pending != (mode,commanded) or reset is not True
                or type(actual) is not int or actual < 0 or actual > commanded):
            raise MotionUncertain('scan motor position unconfirmed')
        self._scan_motor_position += actual if mode == SEARCH_MODE else -actual
        self._scan_motor_total += actual
        self._scan_zero_segments = self._scan_zero_segments + 1 if actual == 0 else 0
        self._scan_motor_pending = None
        if getattr(self,'_single_pending_stop',None) == (idx,slot):
            self._single_pending_stop = None
        logging.info('[multiACE] RFID motor ledger ace=%s slot=%s mode=%s actual=%s position=%s total=%s',
                     idx,slot,mode,actual,self._scan_motor_position,self._scan_motor_total)

    def _wait_motion_ready(self, idx, slot, timeout=15.0):
        """Fresh device status, not a cached or inferred standstill."""
        deadline = self.ace.reactor.monotonic() + timeout
        while self.ace.reactor.monotonic() < deadline:
            res = self._req(idx, 'get_status', {},
                            timeout=min(REQ_TIMEOUT, deadline - self.ace.reactor.monotonic()))
            if (not isinstance(res, dict) or res.get('code') != 0 or
                    res.get('_v2_payload_valid') is not True or
                    not (res.get('result') or {}).get('_status_wire_valid')):
                self._pause(min(0.1, max(0, deadline - self.ace.reactor.monotonic())))
                continue
            lanes = (res.get('result') or {}).get('slots') or []
            lane = next((x for x in lanes if x.get('index') == slot), None)
            if not lane:
                self._pause(min(0.1, max(0, deadline - self.ace.reactor.monotonic())))
                continue
            state = lane.get('slot_status')
            if state == 'ready' and self.ace.reactor.monotonic() < deadline:
                return True
            if state not in V2_ACTIVE_MOTION_STATES:
                return False
            self._pause(min(0.25, max(0, deadline - self.ace.reactor.monotonic())))
        return False

    def _fresh_encoder(self, idx, slot):
        response = self._req(idx, 'get_feed_info', {})
        data = response if isinstance(response, dict) else {}
        payload = data.get('result') if isinstance(data.get('result'), dict) else {}
        rows = payload.get('feed_info')
        rows = rows if isinstance(rows, list) else []
        lane = next((v for v in rows if isinstance(v, dict) and v.get('index') == slot), None)
        self._encoder_diagnostic = {
            'id': data.get('id'), 'frame': data.get('_v2_frame_valid'),
            'payload': data.get('_v2_payload_valid'), 'cmd': data.get('_v2_command'),
            'code': data.get('code'), 'feed_wire': payload.get('_feed_wire_valid'),
            'row': {k: lane.get(k) for k in ('index', '_wire_valid', '_decoder_present', 'decoder', 'steps', 'length')} if lane else None,
        }
        if (data.get('_v2_frame_valid') is not True or data.get('_v2_payload_valid') is not True
                or data.get('_v2_command') != 76 or type(data.get('code')) is not int
                or data.get('code') != 0 or str(data.get('msg', '')).strip().upper() == 'FORBIDDEN'
                or payload.get('_feed_wire_valid') is not True or not lane
                or lane.get('_wire_valid') is not True or type(lane.get('_decoder_present')) is not bool):
            return None
        raw = lane.get('decoder')
        if type(raw) is not int or (not lane['_decoder_present'] and raw != 0):
            return None
        return raw - (1 << 64) if raw >= (1 << 63) else raw

    def _neighbour_signature(self, idx, slot):
        """Fresh ready state plus last move counters for the paired lane."""
        neighbour = slot ^ 1
        def readonly(method):
            response = self._req(idx, method, {})
            if response is None and not getattr(self,'_single_auto',False):
                self._insert_guard()
                response = self._req(idx, method, {})
            return response
        state = readonly('get_status')
        if (not isinstance(state, dict) or state.get('code') != 0 or
                state.get('_v2_payload_valid') is not True):
            return None
        data = state.get('result') or {}
        if not data.get('_status_wire_valid'):
            return None
        lane = next((x for x in data.get('slots', [])
                     if x.get('index') == neighbour), None)
        if not lane or lane.get('slot_status') != 'ready' or lane.get('status') == 'identifying':
            return None
        info = readonly('get_feed_info')
        if (not isinstance(info, dict) or info.get('code') != 0 or
                info.get('_v2_payload_valid') is not True):
            return None
        result = info.get('result') or {}
        if not result.get('_feed_wire_valid'):
            return None
        feed = next((x for x in result.get('feed_info', [])
                     if x.get('index') == neighbour), None)
        if not feed or not all(isinstance(feed.get(k), int)
                               and not isinstance(feed.get(k), bool)
                               for k in ('steps', 'length', 'decoder')):
            return None
        return (lane.get('status'), feed['steps'], feed['length'],
                feed['decoder'], bool(feed.get('_decoder_present')))

    def _target_motion_allowed(self, idx, slot):
        """Never scan a known loaded lane or a printing/paused printer."""
        try:
            ps = self.ace.printer.lookup_object('print_stats', None)
            if ps is None or (getattr(ps, 'state', '') or '').lower() not in (
                    'standby', 'complete', 'cancelled'):
                return False
            return not self.ace._get_heads_for_ace_slot(idx, slot)
        except Exception:
            return False

    def _remember_encoder_direction(self, idx, slot, direction):
        """Keep an independently observed direction only for this physical connection."""
        try:
            transport = self.ace._serials.get(idx)
            identity = self.ace._location_identity(idx)
            if (
                transport is not None
                and identity[1] is not None
                and direction in (-1, 1)
            ):
                if not hasattr(self.ace, "_insert_encoder_direction"):
                    self.ace._insert_encoder_direction = {}
                self.ace._insert_encoder_direction[(idx, slot)] = (
                    transport,
                    identity,
                    direction,
                )
        except Exception:
            logging.exception("[multiACE] RFID encoder direction cache")

    def _manual_guard(self):
        guard = getattr(self, '_manual_session_guard', None)
        if guard is not None and not guard():
            raise MotionUncertain('manual original transport or route changed')

    def _manual_sample(self, idx, slot):
        self._manual_guard()
        result = self._fresh_encoder(idx, slot)
        self._manual_guard()
        return result

    def _manual_stable_terminal(self, idx, slot, length, first):
        """Require two consecutive fresh matching ready samples within a bound."""
        previous_deadline = getattr(self, '_telemetry_deadline', None)
        self._telemetry_deadline = self.ace.reactor.monotonic() + 6.0
        try:
            return self._manual_stable_terminal_bounded(idx, slot, length, first)
        finally:
            self._telemetry_deadline = previous_deadline

    def _manual_stable_terminal_bounded(self, idx, slot, length, first):
        previous = None
        raw = first
        deadline = self._telemetry_deadline
        for attempt in range(12):
            remaining = deadline - self.ace.reactor.monotonic()
            if remaining <= 0:
                break
            diag = dict(getattr(self, '_encoder_diagnostic', {}) or {})
            row = dict(diag.get('row') or {})
            sample = (raw, row.get('steps'), row.get('length'))
            # The decoder helper already validates frame, command and payload.
            valid = (raw is not None and row.get('_decoder_present') is True
                     and type(row.get('steps')) is int and type(row.get('length')) is int)
            self._manual_guard()
            ready = self._wait_motion_ready(idx, slot, min(0.5, remaining)) if valid else False
            self._manual_guard()
            if self.ace.reactor.monotonic() >= deadline:
                raise MotionUncertain('manual move completion telemetry timeout')
            logging.info('[multiACE] RFID manual terminal ace=%s slot=%s requested=%s attempt=%s sample=%s diag=%s ready=%s',
                         idx, slot, length, attempt, sample, diag, ready)
            if valid and ready and previous == sample and row['length'] >= max(1, length - 5):
                return raw, row
            # A missing reply is not a changed position, but breaks the proof
            # streak. Never accept samples separated by unknown telemetry.
            previous = sample if valid and ready else None
            if attempt == 11:
                break
            remaining = deadline - self.ace.reactor.monotonic()
            if remaining <= 0:
                break
            self._pause(min(0.3, remaining))
            if self.ace.reactor.monotonic() >= deadline:
                break
            raw = self._manual_sample(idx, slot)
            if self.ace.reactor.monotonic() >= deadline:
                raise MotionUncertain('manual move completion telemetry timeout')
        raise MotionUncertain('manual move completion telemetry unconfirmed')

    def _move_and_confirm(self, idx, slot, length, mode, respond, *, observe=None):
        """Manual scan move with fresh completion and encoder feedback."""
        if not self._target_motion_allowed(idx, slot):
            respond(
                "rc522: read failed - slot feeds a loaded head or "
                "printer state is unavailable"
            )
            return False
        manual = bool(getattr(self, '_manual_proof', False))
        checked = getattr(self, "_insert_checked_guard", None) is not None or manual
        baseline = (self._manual_sample(idx, slot) if manual else self._fresh_encoder(idx, slot)) if checked else None
        if manual and baseline is None:
            respond('rc522: read failed - fresh baseline unavailable; nothing moved')
            return False
        baseline_row = (
            dict((getattr(self, "_encoder_diagnostic", {}) or {}).get("row") or {})
            if baseline is not None
            else {}
        )
        self._relative_measured = True
        try:
            if observe is None:
                moved = self._move(idx, slot, length, mode, respond)
            else:
                moved = self._move(idx, slot, length, mode, respond, speed=CONTINUOUS_SCAN_SPEED)
            if not moved:
                return False
        finally:
            self._relative_measured = False
        initial = (self._manual_sample(idx, slot) if manual else self._fresh_encoder(idx, slot)) if checked else None
        initial_row = (
            dict((getattr(self, "_encoder_diagnostic", {}) or {}).get("row") or {})
            if initial is not None
            else {}
        )
        if manual:
            # ACK may precede motor start and per-command counter reset. Poll
            # a bounded window while the preceding terminal frame persists.
            for _ in range(6):
                if initial is None or initial_row != baseline_row or initial != baseline:
                    break
                if (baseline == 0 and baseline_row.get('length') == 0
                        and type(baseline_row.get('steps')) is int and baseline_row['steps'] <= 7):
                    break
                self._pause(0.1)
                initial = self._manual_sample(idx, slot)
                initial_row = dict((getattr(self, '_encoder_diagnostic', {}) or {}).get('row') or {})
        # An ACK can precede the motion; do not treat an immediate READY as
        # completion. Then use a fresh status and this move's reset counter.
        if observe is None:
            self._pause(length / float(PARK_SPEED) + 0.6)
        else:
            deadline = self.ace.reactor.monotonic() + length / float(CONTINUOUS_SCAN_SPEED)
            while self.ace.reactor.monotonic() < deadline:
                observe(baseline_row)
                remaining = deadline - self.ace.reactor.monotonic()
                if remaining > 0:
                    self._pause(min(0.08, remaining))
            self._pause(0.6)
        if not self._wait_motion_ready(idx, slot):
            self._motion_uncertain = (
                "ACE %d slot %d: motion completion "
                "not confirmed; inspect lane" % (idx, slot)
            )
            respond("rc522: WARNING - " + self._motion_uncertain)
            raise MotionUncertain(self._motion_uncertain)
        raw = self._manual_sample(idx, slot) if manual else self._fresh_encoder(idx, slot)
        if manual:
            try:
                raw, final_row = self._manual_stable_terminal(idx, slot, length, raw)
            except MotionUncertain as exc:
                self._motion_uncertain = str(exc)
                raise
        if raw is None or not (length * 0.5 <= abs(raw) <= length * 1.5 + 2):
            logging.warning(
                "[multiACE] RFID encoder diagnostic ACE=%s slot=%s requested=%s mode=%s value=%s detail=%s",
                idx,
                slot,
                length,
                mode,
                raw,
                getattr(self, "_encoder_diagnostic", None),
            )
            self._motion_uncertain = (
                "ACE %d slot %d: encoder feedback "
                "missing or inconsistent for %d mm; inspect lane" % (idx, slot, length)
            )
            respond("rc522: WARNING - " + self._motion_uncertain)
            raise MotionUncertain(self._motion_uncertain)
        key = (idx, slot)
        direction = (1 if raw > 0 else -1) * (-1 if mode else 1)
        old = self._encoder_direction.get(key)
        reset = (
                type(baseline_row.get("steps")) is int
                and type(baseline_row.get("length")) is int
                and type(initial_row.get("steps")) is int
                and type(initial_row.get("length")) is int
                and (
                    initial_row["steps"] < baseline_row["steps"]
                    or initial_row["length"] < baseline_row["length"]
                    or (initial == 0 and baseline != 0)
                )
            )
        if manual and not reset:
            # A command beginning at a validated zero counter need not exhibit
            # a downward transition, provided its first sample is also zero.
            reset = (baseline == 0 and baseline_row.get('length') == 0 and
                     type(baseline_row.get('steps')) is int and 0 <= baseline_row['steps'] <= 7 and
                     type(initial_row.get('steps')) is int and type(initial_row.get('length')) is int and
                     0 <= initial_row['length'] <= length and initial_row['steps'] >= 0 and
                     initial is not None and (initial == 0 or initial * raw > 0))
        if manual and not reset:
            self._motion_uncertain = 'manual move counter reset unconfirmed'
            raise MotionUncertain(self._motion_uncertain)
        if old is None:
            if not reset and checked:
                self._motion_uncertain = (
                    "ACE %d slot %d: encoder reset "
                    "unconfirmed; inspect lane" % (idx, slot)
                )
                raise MotionUncertain(self._motion_uncertain)
        if old is not None and old != direction:
            self._motion_uncertain = (
                "ACE %d slot %d: encoder direction "
                "inconsistent; inspect lane" % (idx, slot)
            )
            respond("rc522: WARNING - " + self._motion_uncertain)
            raise MotionUncertain(self._motion_uncertain)
        if manual:
            self._manual_guard()
        context = getattr(self,'_insert_checked_context',None)
        if getattr(self,'_scan_measured',False) and context is not None and (idx,slot)==context[:2]:
            final_row = dict((getattr(self,'_encoder_diagnostic',{}) or {}).get('row') or {})
            self._pause(0.3)
            confirm = self._fresh_encoder(idx,slot)
            confirm_row = dict((getattr(self,'_encoder_diagnostic',{}) or {}).get('row') or {})
            if (raw != confirm or final_row.get('_decoder_present') is not True
                    or confirm_row.get('_decoder_present') is not True
                    or any(type(final_row.get(k)) is not int or final_row.get(k)!=confirm_row.get(k) for k in ('steps','length'))
                    or not self._wait_motion_ready(idx,slot)):
                raise MotionUncertain('scan motor completion telemetry unconfirmed')
            self._commit_scan_motor(idx,slot,mode,length,final_row['length'],reset)
        self._encoder_direction[key] = direction
        if not manual and checked and (old is not None or reset):
            self._remember_encoder_direction(idx, slot, direction)
        self._encoder_ledger[key] = self._encoder_ledger.get(key, 0) + raw
        continuous_budget = getattr(self, '_continuous_budget', None)
        if (continuous_budget is not None
                and not -PARK_TOLERANCE_MM <= self._encoder_ledger[key] * direction <= continuous_budget['limit']):
            self._motion_uncertain = 'continuous scan measured position exceeded its authorized envelope'
            raise MotionUncertain(self._motion_uncertain)
        if continuous_budget is not None:
            continuous_budget['maximum_position_mm'] = max(
                continuous_budget['maximum_position_mm'], self._encoder_ledger[key] * direction)
        if manual:
            if not hasattr(self, '_manual_last_terminal'):
                self._manual_last_terminal = {}
            self._manual_last_terminal[key] = (raw, final_row['steps'], final_row['length'])
        logging.info('[multiACE] RFID measured move ace=%s slot=%s mode=%s command=%s raw=%s normalized=%s baseline=%s initial=%s final=%s ledger=%s',
                     idx, slot, mode, length, raw, raw*direction, baseline_row, initial_row,
                     getattr(self,'_encoder_diagnostic',None), self._encoder_ledger[key])
        return True

    def _stop_and_measure(self, idx, slot, leg, respond):
        """Stop a transport sweep and require stable status and encoder."""
        resp = self._req(idx, "stop_feed_filament", {"index": slot})
        if (not isinstance(resp,dict) or resp.get('code')!=0
                or resp.get('_v2_command')!=9 or resp.get('_v2_frame_valid') is not True
                or resp.get('_v2_payload_valid') is not True):
            self._pause(0.3)
            resp = self._req(idx, "stop_feed_filament", {"index": slot})
        if (
            not isinstance(resp, dict)
            or resp.get("code") != 0
            or resp.get('_v2_command') != 9
            or resp.get('_v2_frame_valid') is not True
            or resp.get("_v2_payload_valid") is not True
            or str(resp.get("msg", "")).strip().upper() == "FORBIDDEN"
        ):
            self._motion_uncertain = (
                "ACE %d slot %d: sweep stop not "
                "confirmed; motor may continue; inspect lane" % (idx, slot)
            )
            respond("rc522: WARNING - " + self._motion_uncertain)
            raise MotionUncertain(self._motion_uncertain)
        # STOP may be acknowledged before the drive has settled. Require
        # two fresh READY checks and identical valid encoder readings.
        self._pause(0.6)
        if not self._wait_motion_ready(idx, slot):
            self._motion_uncertain = (
                "ACE %d slot %d: sweep standstill "
                "unconfirmed; inspect lane" % (idx, slot)
            )
            respond("rc522: WARNING - " + self._motion_uncertain)
            raise MotionUncertain(self._motion_uncertain)
        first = self._fresh_encoder(idx, slot)
        first_row = dict(
            (getattr(self, "_encoder_diagnostic", {}) or {}).get("row") or {}
        )
        self._pause(0.3)
        second = self._fresh_encoder(idx, slot)
        diag = getattr(self, "_encoder_diagnostic", {})
        row = diag.get("row") or {}
        self._insert_guard()
        direction = self._encoder_direction.get((idx, slot))
        baseline = getattr(self, "_sweep_baseline", None)
        baseline_row = (baseline or {}).get('row') or {}
        context = getattr(self,'_insert_checked_context',None)
        subresolution_zero = (
            getattr(self,'_scan_measured',False) and context is not None and (idx,slot)==context[:2]
            and self.ace._serials.get(idx) is context[2]
            and isinstance(getattr(self,'_sweep_identity',None),tuple)
            and self._sweep_identity == self.ace._location_identity(idx)
            and self._sweep_identity[1] is not None
            and isinstance(baseline,dict) and baseline.get('frame') is True
            and baseline.get('payload') is True and baseline.get('cmd') == 76
            and baseline.get('code') == 0 and baseline.get('feed_wire') is True
            and baseline_row.get('_wire_valid') is True and baseline_row.get('_decoder_present') is True
            and type(baseline_row.get('decoder')) is int and baseline_row.get('decoder') != 0
            and getattr(self,'_sweep_reset_seen',False) and first == 0 and second == 0
            and first_row.get('_decoder_present') is False and row.get('_decoder_present') is False
            and first_row.get('decoder') == 0 and row.get('decoder') == 0
            and first_row.get('_wire_valid') is True and row.get('_wire_valid') is True
            and type(row.get('steps')) is int and 0 <= row['steps'] <= 7
            and first_row.get('steps') == row['steps'] and first_row.get('length') == 0 and row.get('length') == 0
        )
        if subresolution_zero:
            if not self._wait_motion_ready(idx,slot):
                raise MotionUncertain('short sweep standstill unconfirmed')
            self._commit_scan_motor(idx,slot,SEARCH_MODE,leg,0,True)
            logging.info('[multiACE] RFID short sweep ace=%s slot=%s command=%s decoder=0 present=False steps=%s length=0; physical sub-resolution displacement unknown',
                         idx,slot,leg,row['steps'])
            return 0
        if getattr(self, "_relative_unknown", False):
            samples = getattr(self, "_sweep_values", [])
            if (
                not getattr(self, "_sweep_reset_seen", False)
                or not samples
                or second in (None, 0)
                or any(value * second <= 0 for value in samples)
                or any(abs(b) < abs(a) for a, b in zip(samples, samples[1:]))
                or abs(second) < abs(samples[-1])
            ):
                raise MotionUncertain("relative sweep reset or movement unconfirmed")
        if direction is None and baseline is not None and second not in (None, 0):
            old = baseline.get("row") or {}
            samples = getattr(self, "_sweep_values", [])
            consistent = [v for v in samples if v * second > 0]
            progress = (
                len(consistent) == len(samples)
                and bool(consistent)
                and all(abs(b) >= abs(a) for a, b in zip(consistent, consistent[1:]))
            )
            if (
                getattr(self, "_sweep_reset_seen", False)
                and progress
                and type(old.get("steps")) is int
                and type(old.get("length")) is int
                and type(row.get("steps")) is int
                and type(row.get("length")) is int
                and abs(second) >= abs(consistent[-1])
            ):
                direction = 1 if second > 0 else -1
                self._encoder_direction[(idx, slot)] = direction
                self._remember_encoder_direction(idx, slot, direction)
        ready_after = self._wait_motion_ready(idx, slot)
        if (
            not ready_after
            or not isinstance(first, int)
            or isinstance(first, bool)
            or first != second
            or first_row.get("_decoder_present") is not True
            or row.get("_decoder_present") is not True
            or any(
                type(first_row.get(k)) is not int or first_row.get(k) != row.get(k)
                for k in ("steps", "length")
            )
            or direction not in (-1, 1)
            or not 0 < first * direction <= leg
        ):
            logging.warning('[multiACE] RFID sweep terminal rejected ace=%s slot=%s leg=%s first=%s first_row=%s second=%s second_row=%s ready=%s direction=%s reset=%s',
                            idx,slot,leg,first,first_row,second,row,ready_after,direction,
                            getattr(self,'_sweep_reset_seen',False))
            self._motion_uncertain = (
                "ACE %d slot %d: sweep encoder or "
                "standstill uncertain; no guessed correction; inspect lane"
                % (idx, slot)
            )
            respond("rc522: WARNING - " + self._motion_uncertain)
            raise MotionUncertain(self._motion_uncertain)
        if getattr(self, "_relative_unknown", False) or getattr(self,'_scan_measured',False):
            key = (idx, slot)
            self._encoder_ledger[key] = self._encoder_ledger.get(key, 0) + first
        self._commit_scan_motor(idx,slot,SEARCH_MODE,leg,row.get('length'),
                                getattr(self,'_sweep_reset_seen',False))
        logging.info('[multiACE] RFID sweep stop ace=%s slot=%s leg=%s raw=%s normalized=%s direction=%s baseline=%s final=%s ledger=%s',
                     idx,slot,leg,first,first*direction,direction,baseline,diag,
                     self._encoder_ledger.get((idx,slot)))
        return first * direction

    def _restore_recorded_motion(self, respond):
        """Return the measured relative encoder displacement, never command distance."""
        if self._motion_uncertain:
            respond('rc522: WARNING - movement outcome unknown; automatic '
                    'restore skipped; inspect the affected lane')
            return
        for (idx, slot), delta in reversed(list(self._motion_ledger.items())):
            if delta or self._encoder_ledger.get((idx, slot), 0):
                self._rewind_scan_origin(idx, slot, respond)
            self._manual_verify_stationary(idx, slot)
            respond('rc522: ACE %d slot %d relative encoder return confirmed'
                    % (idx, slot))

    def _manual_verify_stationary(self, idx, slot):
        self._manual_guard()
        if not self._wait_motion_ready(idx, slot):
            raise MotionUncertain('manual return standstill unconfirmed')
        self._manual_guard()
        first = self._manual_sample(idx, slot)
        row = dict((getattr(self, '_encoder_diagnostic', {}) or {}).get('row') or {})
        self._pause(0.3)
        second = self._manual_sample(idx, slot)
        row2 = dict((getattr(self, '_encoder_diagnostic', {}) or {}).get('row') or {})
        terminal = (getattr(self, '_manual_last_terminal', {}) or {}).get((idx, slot))
        if (first is None or first != second or row.get('_decoder_present') is not True
                or row2.get('_decoder_present') is not True
                or any(type(row.get(k)) is not int or row.get(k) != row2.get(k) for k in ('steps', 'length'))
                or (first, row.get('steps'), row.get('length')) != terminal
                or not self._wait_motion_ready(idx, slot)):
            raise MotionUncertain('manual return stability unconfirmed')
        self._manual_guard()

    def _identify(self, idx, slot):
        """One FILAMENT_IDENTIFY probe. Returns the result dict or None.
        'code' 0 = a tag answered (needs the protocol's field-12 decode);
        3 = nothing in the field right now."""
        resp = self._req(idx, 'filament_identify', {'index': slot})
        if not isinstance(resp, dict):
            return None
        res = resp.get('result')
        return res if isinstance(res, dict) else None

    @staticmethod
    def _answered(res):
        if not isinstance(res, dict):
            return False
        if res.get('code', 0) != 0:
            return False
        return bool(res.get('sku') or res.get('type'))

    @staticmethod
    def _is_uid_read(res):
        try:
            return int((res.get('tag') or {}).get('field2', 0)) in UID_VERSIONS
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _sentinel_uid(res):
        """The UID a sentinel reply carries in its sku, as BARE uppercase
        hex. The 46O+ Bambu path writes 'SM<hex>' (the 3O passthrough wrote
        it raw); 'SM' means Spoolman id to every other consumer, so it is
        stripped here. '' when the field is not a UID after all."""
        raw = (res.get('sku') or '').strip().upper().replace(':', '')
        if raw.startswith('SM') and len(raw) > 2:
            raw = raw[2:]
        if len(raw) in (8, 14) and all(c in '0123456789ABCDEF' for c in raw):
            return raw
        return ''

    @staticmethod
    def _is_truncated_uid(uid):
        """0x88 is the ISO14443-3 CASCADE TAG and never opens a real 4-byte
        UID: an 8-hex sentinel starting with 88 is the firmware's
        anticollision stopped after cascade level 1 - CT plus the first
        three bytes of a 7-byte UID (e.g. sentinel 88043EA5 for the NTAG
        our tunnel reads as 043EA551C32A81). For an NTAG
        our own page read has the full UID and wins. For a 7-byte MIFARE
        (pages encrypted, no own read) it is the only key the firmware
        gives us: stable per card, but only 3 of 7 bytes, so it is kept
        as a PARTIAL key and logged - the firmware should finish the
        cascade (Simon-CR)."""
        return bool(uid) and len(uid) == 8 and uid.upper().startswith('88')

    @staticmethod
    def _is_single_uid(uid):
        """A complete 4-byte (single-size) UID = MIFARE Classic (Bambu,
        Snapmaker): no readable pages, and on a Bambu spool the chip sits in
        the FLANGE, not on the filament. Every NTAG has a 7-byte UID; an
        8-hex value starting with 88 is a truncated NTAG (above)."""
        return (bool(uid) and len(uid) == 8
                and not uid.upper().startswith('88'))

    @staticmethod
    def _bambu_key(uid):
        # Public Bambu Classic HKDF-SHA256, Key A for sector 1.
        master = bytes.fromhex('9a759cf2c4f7caff222cb9769b41bc96')
        prk = hmac.new(master, bytes.fromhex(uid), hashlib.sha256).digest()
        return hmac.new(prk, b'RFID-A\x00\x01', hashlib.sha256).digest()[6:12]

    def _current_single_uid(self, idx, slot):
        """Read and validate the buffer from the immediately preceding SELECT."""
        b = [self._rc(idx, slot, 9, i) for i in range(13, 18)]
        if (None in b or b[0] == 0x88 or not any(b[:4]) or
                b[4] != (b[0] ^ b[1] ^ b[2] ^ b[3])):
            return ''
        return bytes(b[:4]).hex().upper()

    def _selected_single_uid(self, idx, slot):
        """Two matching SELECT UIDs, each with a valid single-size BCC."""
        seen = []
        for _ in range(2):
            if not self._rc_select(idx, slot):
                return ''
            uid = self._current_single_uid(idx, slot)
            if not uid:
                return ''
            seen.append(uid)
        return seen[0] if seen[0] == seen[1] else ''

    def _observed_uid(self, idx, slot):
        """UID, '' for explicit no-card, or None for error/collision/unknown."""
        status = self._rc(idx, slot, 6)
        if status == 1:
            return ''
        if status != SELECT_OK:
            return None
        uid = self._selected_single_uid(idx, slot)
        if uid:
            return uid
        uid, _ = self._rc_stable_uid(idx, slot)
        return uid or None

    def _observed_expected_uid(self, idx, slot, expected):
        """One fresh SELECT and one BCC-validated UID; callers require repeats."""
        if not isinstance(expected, str) or len(expected) not in (8,14):
            return None
        if not self._rc_select(idx,slot):
            return None
        if len(expected) == 8:
            if not self._is_single_uid(expected):
                return None
            value = self._current_single_uid(idx,slot)
        else:
            value = self._rc_read_uid(idx,slot)
        return value if value and len(value)==len(expected) else None

    def _ingest_bambu(self, idx, slot, uid, identity, respond):
        if getattr(self, '_defer_identity_commit', False):
            self._pending_identity = {'kind': 'bambu', 'idx': idx, 'slot': slot,
                                      'uid': uid, 'identity': copy.deepcopy(identity)}
            respond('rc522: authenticated content read; waiting for measured return')
            return
        respond('rc522: authenticated Bambu tag: %s #%02X%02X%02X (UID %s)'
                % (identity['type'], *identity['color'], uid))
        self._note_fmt(idx, slot, 'mifare')
        self.ace._v2_store_filament_read(idx, slot, identity, uid=uid,
                                         fmt='mifare')
        self._note_uid(idx, slot, uid)
        self._bind_uid(idx, slot, uid, respond, unbind=False)

    def _read_bambu_classic(self, idx, slot, uid, single=False):
        """Read only a verified Bambu identity; never trust firmware defaults.

        Verify the selected 4-byte UID and BCC, authenticate sector 1, and
        require two identical complete reads of blocks 4 and 5. On any error
        leave the tag UID-only. Do not write anything to the tag.
        """
        if not self._is_single_uid(uid):
            return None
        raw_uid = bytes.fromhex(uid)
        def write(reg, val):
            return self._rc(idx, slot, 1, reg, val) == 0
        try:
            self._wait_fw_identify(idx)
            if self._fw_identify_busy(idx):
                return None
            if not single:
                if not self._rc_select(idx, slot):
                    return None
                selected = [self._rc(idx, slot, 9, i) for i in range(13, 18)]
                if (None in selected or bytes(selected[:4]) != raw_uid
                        or selected[4] != (raw_uid[0] ^ raw_uid[1] ^ raw_uid[2] ^ raw_uid[3])):
                    return None
            seq = [(1, 0), (8, 0), (2, 0xf7), (4, 0x7f), (10, 0x80),
                   (0x12, 0), (0x13, 0), (0x0d, 0), (0x2a, 0x80)]
            packet = bytes([0x60, 4]) + self._bambu_key(uid) + raw_uid
            if not all(write(reg, val) for reg, val in seq):
                return None
            if not all(write(9, b) for b in packet) or not write(1, 0x0e):
                return None
            # Poll the hardware's authentication result; stop on timer/error.
            authenticated = False
            for _ in range(35):
                status = self._rc(idx, slot, 0, 8)
                irq = self._rc(idx, slot, 0, 4)
                if status is None or irq is None:
                    return None
                if status & 8:
                    authenticated = True
                    break
                if irq & 0x11:
                    break
                self._pause(0.001)
            if not authenticated:
                return None
            if not all(write(reg, val) for reg, val in
                       [(0x12, 0x80), (0x13, 0x80), (0x0d, 0)]):
                return None
            blocks = []
            for block in ((4,5) if single else (4,5,4,5)):
                # Inline the READ to distinguish a lost reply from zero data.
                if (self._rc(idx, slot, 2, 0, 0x30) != 0 or
                        self._rc(idx, slot, 2, 1, block) != 0):
                    return None
                if self._rc(idx, slot, 3, 2, PCD_TRANSCEIVE) != 0:
                    return None
                if self._rc(idx, slot, 5, 0) != 0x80:
                    return None
                data = [self._rc(idx, slot, 4, i) for i in range(16)]
                if None in data:
                    return None
                blocks.append(bytes(data))
            if not single and blocks[:2] != blocks[2:]:
                return None
            material_bytes = blocks[0].split(b'\x00', 1)[0]
            if not (3 <= len(material_bytes) <= 16 and
                    all(0x20 <= c <= 0x7e for c in material_bytes)):
                return None
            material = material_bytes.decode('ascii').strip()
            if not material:
                return None
            return {'type': material, 'brand': 'Bambu Lab', 'sku': uid,
                    'color': list(blocks[1][:3]), 'code': 0}
        finally:
            # Clear Crypto1 even on partial read, lost response or exception.
            write(1, 0)
            write(8, 0)

    # -- stage 2: raw RC522 ops ------------------------------------------

    def _rc(self, idx, slot, op, a1=0, a2=0):
        """One RC522 sub-command through the identify tunnel. `idx` is the
        ACE DEVICE index (which unit) - send_request_to's first arg; the
        packed sub-command's bit24 is the reader (slot>>1, the antenna pair
        WITHIN that unit); passing slot as the device index queries the
        wrong ACE. Returns the op's byte result (response
        'code', field 12) or None."""
        reader = 1 if slot >= 2 else 0
        packed = (RC_BIT31 | (reader << 24) | ((op & 0xFF) << 16)
                  | ((a1 & 0x3F) << 8) | (a2 & 0xFF))
        # A lost reply is re-asked for the IDEMPOTENT ops only (register
        # read, rx-buffer byte, rx-bit count, SELECT): a page read is ~20
        # ops over USB, and on a link that drops replies one lost op used
        # to sink the whole page. The
        # writes (op 1/2) and the command trigger (op 3) are NOT retried -
        # a lost reply there does not say whether the op ran, and a
        # doubled FIFO byte corrupts the frame; the bits/CRC check catches
        # that case and the outer retry re-reads.
        tries = 1 if getattr(self,'_single_auto',False) else 1 + (RC_IDEMPOTENT_RETRIES if op in RC_IDEMPOTENT_OPS else 0)
        for attempt in range(tries):
            if attempt:
                self._pause(0.01)
            resp = self._req(idx, 'filament_identify', {'index': packed})
            if (not isinstance(resp, dict) or
                    resp.get('_v2_payload_valid') is not True):
                continue
            res = resp.get('result')
            if not isinstance(res, dict):
                continue
            try:
                return int(res.get('code', 0)) & 0xFF
            except (TypeError, ValueError):
                continue
        return None

    # op 7 (bulk read) was PROBED and is a dead end: all
    # a1/a2 variants returned only a constant status byte (code 0x90) with
    # no page bytes in any response field - the tunnel return really is one
    # byte, so a bulk read cannot come back through it. The read speedup
    # came instead from the _req poll granularity (50 ms -> 5 ms). Do not
    # re-probe op 7 without a new firmware.

    def _probe_page(self, idx, slot):
        """SELECT + TWO page-0 READs: True only when both come back as a
        full 16-byte frame (rx_bits 0x80 inside _rc_read_page). The
        read-quality probe of _centre_on_read - a card answering SELECT at
        the field edge still fails this. Two frames, not one: a marginal
        spot returns a single lucky frame and then flickers. Costs one
        READ (~0.2 s) per probe."""
        if not self._rc_select(idx, slot):
            return False
        self._rc_setup_crc(idx, slot)
        if not any(self._rc_read_page(idx, slot, 0)):
            return False
        return any(self._rc_read_page(idx, slot, 0))

    def _centre_on_read(self, idx, slot, respond):
        """READ-guided centering after a SELECT hit: walk PROBE_OFFSETS
        (units relative to the hit point) and stop at the first position
        where a page read comes back whole; the lane is LEFT there.
        Returns the signed net units moved (callers add it to their
        depth/rolled accounting). No full frame anywhere -> the lane ends
        at the last offset and the caller's own read retries take over."""
        pos = 0
        for off in PROBE_OFFSETS:
            delta = off - pos
            if delta:
                if not self._move(idx, slot, abs(delta),
                                  SEARCH_MODE if delta > 0 else RESTORE_MODE,
                                  respond):
                    break
                self._pause(abs(delta) / float(PARK_SPEED) + 0.5)
                pos = off
            ok = self._probe_page(idx, slot)
            if respond and self._debug:
                respond('rc522[dbg] centre probe at %+d: %s'
                        % (off, 'full page' if ok else 'no page'))
            if ok:
                if off:
                    respond('rc522: tag centred at %+d units from the hit'
                            % off)
                return pos
        respond('rc522: no clean page within %+d..%+d units of the hit - '
                'reading anyway' % (min(PROBE_OFFSETS), max(PROBE_OFFSETS)))
        return pos

    def _card_answers(self, idx, slot):
        """Is a card in this slot's field? op 6 SELECT for any tag - except
        a MIFARE the firmware identify has HALTed (it ignores our REQA and
        would read as gone): that one is asked through the identify, by
        its UID. Then True = that card answered, False = nothing answered,
        None = another card answered (on the shared antenna the identify
        can pick the neighbour's, so this says nothing about ours)."""
        if not self._halted_uid:
            return self._rc_select(idx, slot)
        res = self._identify(idx, slot)
        # A lost reply is not an empty field: re-ask like op 6 does.
        for _ in range(RC_IDEMPOTENT_RETRIES):
            if res is not None:
                break
            res = self._identify(idx, slot)
        if not self._answered(res):
            return False
        if (self._is_uid_read(res)
                and self._sentinel_uid(res) == self._halted_uid):
            return True
        return None

    def _rc_select(self, idx, slot):
        """op 6: power the reader + REQA/anticollision/SELECT. Returns True
        when a card is in the field (status 0) - works for ANY ISO14443A
        tag, unlike the firmware identify."""
        return self._rc(idx, slot, 6) == SELECT_OK

    def _rc_setup_crc(self, idx, slot):
        # Rule 2: byte framing + CRC on (read-modify-write), then send
        # frames WITHOUT a manual CRC (the reader adds/checks it).
        v = self._rc(idx, slot, 0, REG_TXMODE)
        if v is not None:
            self._rc(idx, slot, 1, REG_TXMODE, v | 0x80)
        v = self._rc(idx, slot, 0, REG_RXMODE)
        if v is not None:
            self._rc(idx, slot, 1, REG_RXMODE, v | 0x80)
        self._rc(idx, slot, 1, REG_BITFRAMING, 0x00)

    def _rc_read_page(self, idx, slot, page, dbg=None):
        """One NTAG READ (0x30) of `page`: returns the 16 bytes it yields
        (pages page..page+3) as a list. `dbg` logs the raw reply when set."""
        for i, b in enumerate([0x30, page & 0xFF]):
            if self._rc(idx, slot, 2, i, b) != 0:
                return []
        st = self._rc(idx, slot, 3, 2, PCD_TRANSCEIVE)
        bits = self._rc(idx, slot, 5, 0)
        # A real page reply is 16 bytes = 0x80 rx bits (CRC stripped by
        # the reader; DEBUG runs: every good read 0x80, no card 0x00).
        # Anything else is a partial reply or NO reply, and the RX buffer
        # then still holds the PREVIOUS transceive's bytes (a "page 0" read
        # can return user-page text from a minute earlier). Report those as empty instead
        # of handing stale bytes to the UID/verify logic.
        if st != 0 or bits != 0x80:
            if dbg:
                dbg('rc522[dbg] READ p%d: status=%s rx_bits=%s -> no reply'
                    % (page, self._hx(st), self._hx(bits)))
            return []
        pg = []
        for i in range(16):
            b = self._rc(idx, slot, 4, i)
            if b is None:
                # Lost reply even after the _rc re-asks: the page is NOT
                # "byte 0x00 here" (that produced UID rejects with correct
                # UID bytes and one lost check byte) - it is no reply.
                if dbg:
                    dbg('rc522[dbg] READ p%d: byte %d lost -> no reply'
                        % (page, i))
                logging.info('[multiACE] [rc522] page %d read: byte %d '
                             'reply lost - treating as no reply'
                             % (page, i))
                return []
            pg.append(b)
        if dbg:
            dbg('rc522[dbg] READ p%d: status=%s rx_bits=%s bytes=%s'
                % (page, self._hx(st), self._hx(bits), bytes(pg).hex()))
        return pg

    def _rc_read_userdata(self, idx, slot, respond=None, first=4, last=39):
        """Read the NTAG user pages (4-39 = the NDEF area) as bytes. READ
        returns 4 pages at a time, so step by 4. Logs each line when
        `respond` is given (DUMP): the raw bytes for schema work."""
        data = []
        if respond:
            respond('rc522: dumping pages %d-%d' % (first, last))
        p = first
        while p <= last:
            pg = self._rc_read_page(idx, slot, p)
            if len(pg) != 16:
                return b''
            data += pg
            if respond:
                respond('rc522: p%02d-%02d %s' % (p, p + 3, bytes(pg).hex()))
            p += 4
        return bytes(data)

    @staticmethod
    def _openspool_decode(data):
        """Parse an OpenSpool NDEF tag (openspool.io): NDEF-message TLV
        (0x03) -> MIME record 'application/json' -> JSON with type /
        color_hex / brand / min_temp / max_temp. Verified against a real
        tag. Returns an identity dict or None
        (not OpenSpool / unparseable). Robust: skips NULL and lock/memory
        TLVs, tolerates the 3-byte length form."""
        try:
            i, n = 0, len(data)
            while i < n:
                t = data[i]
                if t == 0x00:            # NULL padding
                    i += 1; continue
                if t == 0x03:            # NDEF message TLV
                    break
                if t == 0xFE:            # terminator, no NDEF
                    return None
                if t in (0x01, 0x02):    # lock / memory-control TLV
                    i += 2 + data[i + 1]; continue
                return None
            else:
                return None
            ln = data[i + 1]
            if ln == 0xFF:
                ln = (data[i + 2] << 8) | data[i + 3]; p = i + 4
            else:
                p = i + 2
            msg = data[p:p + ln]
            if len(msg) != ln:
                return None
            hdr = msg[0]
            tl = msg[1]
            if hdr & 0x10:               # SR: 1-byte payload length
                pl = msg[2]; off = 3
            else:
                pl = int.from_bytes(msg[2:6], 'big'); off = 6
            id_len = 0
            if hdr & 0x08:
                id_len = msg[off]
                off += 1
            typ = msg[off:off + tl]; off += tl
            off += id_len
            if off + pl > len(msg):
                return None
            payload = msg[off:off + pl]
            if typ != b'application/json':
                return None
            j = json.loads(payload.decode('utf-8', 'replace'))
            if not isinstance(j, dict):
                return None
            if str(j.get('protocol', '')).lower() != 'openspool':
                return None
            if not all(isinstance(j.get(key) or '', str)
                       for key in ('type', 'color_hex', 'brand')):
                return None
            return {
                'material': (j.get('type') or '').strip(),
                'color': (j.get('color_hex') or '').lstrip('#').upper()[:6],
                'vendor': (j.get('brand') or '').strip(),
                'min_temp': j.get('min_temp'),
                'max_temp': j.get('max_temp'),
            }
        except (IndexError, ValueError, TypeError, UnicodeError):
            return None

    @staticmethod
    def _openspool_encode(material, color_hex, brand='',
                          min_temp='', max_temp='', version='1.0'):
        """Inverse of _openspool_decode: build the NDEF-message TLV bytes
        for an OpenSpool tag. Byte-identical structure to a real tag, padded to a 4-byte page boundary. Compact JSON
        (no spaces) to keep it inside NTAG213's 144-byte user area."""
        obj = {"protocol": "openspool", "version": version,
               "type": material or '',
               "color_hex": (color_hex or '').lstrip('#').upper()[:6],
               "brand": brand or ''}
        if str(min_temp or ''):
            obj["min_temp"] = str(min_temp)
        if str(max_temp or ''):
            obj["max_temp"] = str(max_temp)
        payload = json.dumps(obj, separators=(',', ':')).encode('utf-8')
        typ = b'application/json'
        if len(payload) <= 0xFF:
            rec = bytes([0xD2, len(typ), len(payload)]) + typ + payload
        else:
            rec = (bytes([0xC2, len(typ)]) + len(payload).to_bytes(4, 'big')
                   + typ + payload)
        if len(rec) < 0xFF:
            tlv = bytes([0x03, len(rec)]) + rec
        else:
            tlv = (bytes([0x03, 0xFF, (len(rec) >> 8) & 0xFF, len(rec) & 0xFF])
                   + rec)
        tlv += bytes([0xFE])                       # terminator TLV
        if len(tlv) % 4:                           # pad to a page
            tlv += bytes(4 - (len(tlv) % 4))
        return tlv

    # -- Anycubic layout (from app-written and factory tags; colour byte
    # order confirmed against the firmware's own decode). Pages relative to
    # page 4; u16 = little endian:
    #   p4     7B 00 + u16 version (101)            magic
    #   p5-9   sku, 20 bytes NUL-padded              (factory: per ARTICLE)
    #   p10-14 brand ("A" Sm-app, "AC" factory)
    #   p15-19 type incl. subtype ("PLA", "PLA Matte")
    #   p20    FF BB GG RR (u32 LE 0xRRGGBBAA)      colour
    #   p23    factory 50/250 (meaning unknown, Sm-app 0) - left 0
    #   p24    u16 nozzle min, u16 nozzle max
    #   p28    factory repeats p24 - left 0
    #   p29    u16 dry temp, u16 dry hours (Sm-app 50/60, factory 0)
    #   p30    u16 diameter*100 (175), u16 length m
    #   p31    u16 weight g
    # No checksum anywhere (all three tags are zero elsewhere). The firmware
    # fills missing fields from its defaults/DB. We write sku = the card
    # UID: then EVERY unit - V1 Pro, stock ACE 2 - reports the
    # per-chip key as sku through its ordinary read, and card_uids binding
    # (SpoolLink too) works without the RC522 tunnel.
    ANY_MAGIC = b'\x7b\x00'
    ANY_VERSION = 101
    ANY_PAGES = 28                              # p4..p31

    @staticmethod
    def _any_str(v, n=20):
        b = (v or '').encode('utf-8', 'replace')[:n - 1]
        return b + bytes(n - len(b))

    @classmethod
    def _anycubic_encode(cls, sku, material, subtype='', brand='',
                         color_hex='', min_temp='', max_temp='',
                         weight_g=0):
        import struct
        typ = (material or '').strip()
        if (subtype or '').strip():
            typ = '%s %s' % (typ, subtype.strip())
        col = (color_hex or '').lstrip('#')
        try:
            r, g, b = (int(col[0:2], 16), int(col[2:4], 16),
                       int(col[4:6], 16)) if len(col) == 6 else (0, 0, 0)
        except ValueError:
            r, g, b = 0, 0, 0
        def _u16(v):
            try:
                return max(0, min(65535, int(float(v))))
            except (TypeError, ValueError):
                return 0
        out = bytearray(cls.ANY_PAGES * 4)
        out[0:4] = cls.ANY_MAGIC + struct.pack('<H', cls.ANY_VERSION)
        out[4:24] = cls._any_str(sku)
        out[24:44] = cls._any_str(brand)
        out[44:64] = cls._any_str(typ)
        out[64:68] = bytes([0xFF, b, g, r])
        out[80:84] = struct.pack('<HH', _u16(min_temp), _u16(max_temp))
        out[104:108] = struct.pack('<HH', 175, 0)
        out[108:112] = struct.pack('<HH', _u16(weight_g), 0)
        return bytes(out)

    @classmethod
    def _anycubic_decode(cls, data):
        """Parse the Anycubic layout (for the write verify). Returns
        {'sku','brand','material','color'} or None."""
        import struct
        if len(data) < 68 or bytes(data[0:2]) != cls.ANY_MAGIC:
            return None
        def _s(a):
            return bytes(data[a:a + 20]).split(b'\x00', 1)[0].decode(
                'utf-8', 'replace')
        a_, b, g, r = data[64:68]
        return {'version': struct.unpack('<H', bytes(data[2:4]))[0],
                'sku': _s(4), 'brand': _s(24), 'material': _s(44),
                'color': '%02X%02X%02X' % (r, g, b)}

    # NTAG capability-container user-size (byte 2 of the CC * 8). Config /
    # lock pages sit just past the user area and must NEVER be written.
    def _rc_user_page_limit(self, idx, slot):
        """Highest writable user page from the CC (page 3, in the page-0
        READ). NTAG213 CC e1 10 12 00 -> 0x12*8=144 B = pages 4..39.
        Returns (last_user_page) or None if the CC is unreadable."""
        pg = self._rc_read_page(idx, slot, 0)
        if len(pg) < 16 or pg[12] != 0xE1:         # CC magic
            return None
        user_bytes = pg[14] * 8
        return 4 + (user_bytes // 4) - 1

    def _rc_write_page(self, idx, slot, page, four, respond=None,
                       readback=True):
        """NTAG WRITE (0xA2) of one 4-byte page, verified by READ-BACK.
        The 4-bit ACK through the op tunnel is UNRELIABLE - a page can be
        PROVEN written while the tunnel reports bits=0x00 / stale-buffer
        ack. The NTAG sends its ACK only after
        the ~4 ms programming time and the firmware's transceive does not
        wait that long. So the ACK is logged as diagnostics only and the
        page is READ BACK and compared - ground truth over handshake,
        via the HW-proven read path. One full write+readback retry (a
        field-edge read can drop a byte, same class as the UID flap).
        With readback=False the page is written BLIND (no read, no
        retry, returns True) - the caller then verifies a whole 4-page
        chunk with ONE read (2.5x faster than per-page readback) and
        falls back to this readback path only for a mismatching chunk.
        HARD guard: never page < 4 (UID/lock/OTP/CC) - the caller also
        caps at the user limit so config/lock pages past the data area
        are never touched."""
        if page < 4 or len(four) != 4:
            if respond:
                respond('rc522: refusing to write page %d (protected)' % page)
            return False
        want = list(four)
        for attempt in range(2):
            # The 4-bit ACK carries NO CRC: RX CRC off for the transceive
            # (TX CRC stays ON - the WRITE frame needs CRC_A appended),
            # back on afterwards for the read-back.
            rx = self._rc(idx, slot, 0, REG_RXMODE)
            if rx is not None and rx & 0x80:
                self._rc(idx, slot, 1, REG_RXMODE, rx & 0x7F)
            for i, b in enumerate([0xA2, page & 0xFF] + want):
                self._rc(idx, slot, 2, i, b)
            self._rc(idx, slot, 3, 6, PCD_TRANSCEIVE)    # 6 TX bytes
            bits = self._rc(idx, slot, 5, 0)
            ack = self._rc(idx, slot, 4, 0)
            if rx is not None and rx & 0x80:
                self._rc(idx, slot, 1, REG_RXMODE, rx)
            if not readback:
                if respond and self._debug:
                    respond('rc522[dbg] WRITE p%d %s -> bits=%s ack=%s '
                            '(chunk-verify)'
                            % (page, bytes(four).hex(), self._hx(bits),
                               self._hx(ack)))
                return True
            # Wait out the ~4 ms programming time and re-SELECT before the
            # verify read - a WRITE leaves the tag in a shifted read state
            # (a page-4 readback can return page-19 bytes).
            self._pause(0.02)
            self._rc(idx, slot, 6)
            back = self._rc_read_page(idx, slot, page)
            ok = (back[:4] == want)
            if respond and self._debug:
                respond('rc522[dbg] WRITE p%d %s -> bits=%s ack=%s '
                        'readback=%s %s'
                        % (page, bytes(four).hex(), self._hx(bits),
                           self._hx(ack), bytes(back[:4]).hex(),
                           'OK' if ok else 'FAIL'))
            if ok:
                return True
        return False

    def _fw_identify_busy(self, idx):
        """Is the FIRMWARE running its own tag identify on any slot of
        this unit (slot status 'identifying')? Its RC522 driver shares the
        bus with our tunnel ops, and corrupted UID reads (flipped bytes, a
        user-data page in place of page 0) fall inside a neighbour's
        'identifying' window."""
        try:
            info = self.ace._info_per_ace.get(idx) or {}
            for sl in info.get('slots') or []:
                if str(sl.get('status', '')).lower() == 'identifying':
                    return True
        except Exception:
            pass
        return False

    def _wait_fw_identify(self, idx, max_s=8.0):
        """Hold our reader ops while the firmware identify runs (bounded).
        Returns True when it waited."""
        t0 = self.ace.reactor.monotonic()
        waited = False
        while (self._fw_identify_busy(idx)
               and self.ace.reactor.monotonic() - t0 < max_s):
            self._pause(0.5)
            waited = True
        return waited

    def _rc_stable_uid(self, idx, slot, respond=None):
        """Anticollision confirm with tolerance for a single flaky read:
        require SELECT ok + two consecutive IDENTICAL non-empty UID reads,
        retrying up to 3 rounds. A read right after the tag enters the
        field can drop a byte to 0x00 - that is field-edge noise,
        not a neighbour tag (a neighbour never shares 6 of 7 UID bytes),
        and must not refuse the write. A REAL collision / flapping UID
        stays unstable across all rounds and is still refused. Returns
        (uid, None) on success or ('', (uid1, uid2, sel)) for the
        refusal message."""
        detail = ('?', '?', None)
        self._wait_fw_identify(idx)
        for attempt in range(4):
            if attempt:
                self._pause(0.3)
            uid1 = self._rc_read_uid(idx, slot, respond)
            sel = self._rc(idx, slot, 6)
            uid2 = self._rc_read_uid(idx, slot, respond)
            if respond and self._debug:
                respond('rc522[dbg] stable-uid round %d: uid1=%s sel=%s '
                        'uid2=%s' % (attempt + 1, uid1 or '-',
                                     self._hx(sel), uid2 or '-'))
            if sel == SELECT_OK and uid1 and uid1 == uid2:
                return uid1, None
            detail = (uid1 or '?', uid2 or '?', sel)
        return '', detail

    @staticmethod
    def uid_from_page0(data):
        """The 7-byte NTAG UID from a 16-byte page-0..3 read, hex, or ''.

        page0 = U0 U1 U2 BCC0, page1 = U3 U4 U5 U6, page2 byte0 = BCC1.
        BOTH check bytes are VERIFIED: a read in a shifted reader state came
        back as 88 04 1C AA 3A 51 C3 = the neighbour's page bytes with the
        cascade tag 0x88 pushed in front - deterministic, so it passed a
        two-identical-reads stability check three rounds running and was
        ingested as a "new MIFARE card"; a field-edge flip (...2A81 read as
        ...2A00) passed the same way. The BCCs are the tag's own checksum
        over exactly these bytes; a mismatch is a corrupted read, never a
        UID. An all-zero UID is rejected too. Shared by this reader and the
        Gen-1 tunnel client (ace_gen1_tunnel.uid_from_page0)."""
        try:
            if not data:
                return ''
            bcc0 = 0x88 ^ data[0] ^ data[1] ^ data[2]
            bcc1 = data[4] ^ data[5] ^ data[6] ^ data[7]
            if data[3] != bcc0 or data[8] != bcc1:
                return ''
            uid = bytes(data[0:3] + data[4:8])
            if not any(uid):
                return ''
            return uid.hex().upper()
        except (TypeError, IndexError):
            return ''

    def _rc_read_uid(self, idx, slot, respond=None):
        """Read an NTAG's UID from pages 0-1 after a SELECT. A READ (0x30)
        of page 0 returns 16 bytes = pages 0..3; the 7-byte UID is bytes
        0-2 (page 0, byte 3 is BCC0) + bytes 4-6 (page 1). Returns the UID
        hex string, or '' when the read looks empty/invalid. With
        `respond` set (DEBUG), logs each raw step."""
        dbg = respond if self._debug else None
        tx = self._rc(idx, slot, 0, REG_TXMODE)
        rx = self._rc(idx, slot, 0, REG_RXMODE)
        bf = self._rc(idx, slot, 0, REG_BITFRAMING)
        if dbg:
            dbg('rc522[dbg] reader=%d before: TxMode=%s RxMode=%s '
                'BitFraming=%s' % (1 if slot >= 2 else 0,
                                   self._hx(tx), self._hx(rx), self._hx(bf)))
        self._rc_setup_crc(idx, slot)
        if dbg:
            tx2 = self._rc(idx, slot, 0, REG_TXMODE)
            rx2 = self._rc(idx, slot, 0, REG_RXMODE)
            dbg('rc522[dbg] after CRC setup: TxMode=%s RxMode=%s'
                % (self._hx(tx2), self._hx(rx2)))
        pg = self._rc_read_page(idx, slot, 0, dbg)
        if not any(pg):
            return ''
        uid = self.uid_from_page0(pg)
        if not uid:
            logging.info('[multiACE] [rc522] UID read rejected (BCC '
                         'mismatch) ACE %d slot %d: %s',
                         idx, slot, bytes(pg[0:9]).hex())
        return uid

    @staticmethod
    def _hx(v):
        return '?' if v is None else '0x%02X' % (v & 0xFF)

    # -- the stage-1 routine ---------------------------------------------

    def read_slot(self, idx, slot, respond, max_mm=PARK_MAX_MM):
        """Bounded manual scan with positive shared-reader attribution.

        A neighbour is never moved. Every confirmed target move, including
        nested attribution moves, is accounted and reversed on exit. An
        uncertain motion acknowledgement prevents any guessed restore.
        """
        searched = 0
        self._manual_proof = True
        self._manual_motor_budget = 4 * int(max_mm) + 4 * START_PROBE_MM + 100
        try:
            # Reject mixed reader/protocol deployments before ANY motion.
            if (not self._target_motion_allowed(idx, slot) or
                    not self._wait_motion_ready(idx, slot) or
                    self._neighbour_signature(idx, slot) is None):
                respond('rc522: read failed - safe motion telemetry or '
                        'neighbour state unavailable; nothing moved')
                return
            while True:
                status = self._rc(idx, slot, 6)
                if status == SELECT_OK:
                    candidate = self._observed_uid(idx, slot)
                    if not candidate:
                        respond('rc522: read failed - UID unavailable or '
                                'collision; nothing stored')
                        return
                    # A spool can physically remain beside an empty inlet,
                    # so neither cached nor fresh inlet status proves its
                    # tag is absent. Attribute every card, not just cards
                    # for which the neighbour reports occupied.
                    if searched + START_PROBE_MM > max_mm:
                        respond('rc522: read failed - insufficient '
                                'movement budget to verify which slot '
                                'owns the tag; nothing stored')
                        return
                    if self._rotation_test(idx, slot, candidate, respond) != 'ours':
                        respond('rc522: read failed - cannot reliably '
                                'attribute card to this slot; nothing stored')
                        return
                    respond('rc522: card %s attributed to this slot' % candidate)
                    self._read_card(idx, slot, respond, expected_uid=candidate,
                                    allow_nudges=False)
                    return
                if status != 1:
                    respond('rc522: read failed - SELECT response missing '
                            'or ambiguous; nothing stored')
                    return
                if searched >= max_mm:
                    respond('rc522: no tag answered within %d mm' % max_mm)
                    return
                step = min(PARK_STEP_MM, max_mm - searched)
                if not self._move_and_confirm(idx, slot, step, SEARCH_MODE, respond):
                    return
                searched += step
        finally:
            try:
                self._restore_recorded_motion(respond)
            finally:
                self._manual_proof = False

    def _continuous_uid(self, idx, slot):
        """Fresh card observation; ambiguity is never evidence of absence."""
        status = self._rc(idx, slot, 6)
        if status == 1:
            return ''
        if status != SELECT_OK:
            return None
        return self._current_single_uid(idx, slot) or self._rc_read_uid(idx, slot) or None

    def _continuous_sample(self, idx, slot, length, baseline, samples):
        before = self._manual_sample(idx, slot)
        start_row = (getattr(self, '_encoder_diagnostic', {}) or {}).get('row') or {}
        if type(before) is int and abs(before) > length:
            self._motion_uncertain = 'continuous scan exceeded the measured forward limit'
            raise MotionUncertain(self._motion_uncertain)
        if (type(before) is not int or not 0 < abs(before) <= length
                or start_row.get('_decoder_present') is not True
                or dict(start_row) == baseline):
            # The short reset/zero frame before movement is not a missed RF
            # observation. A missing decoder after movement started is a gap.
            if len(samples) > 1 or before is None:
                self._continuous_sampling_error = 'decoder_sample_unavailable'
            return
        direction = 1 if before > 0 else -1
        observed_direction = getattr(self, '_continuous_direction', None)
        if observed_direction is not None and observed_direction != direction:
            self._continuous_direction = 0
            self._continuous_sampling_error = 'encoder_direction_changed'
            self._motion_uncertain = 'continuous scan direction changed during motion'
            raise MotionUncertain(self._motion_uncertain)
        uid = self._continuous_uid(idx, slot)
        after = self._manual_sample(idx, slot)
        end_row = (getattr(self, '_encoder_diagnostic', {}) or {}).get('row') or {}
        if type(after) is int and (abs(after) > length or before * after < 0):
            self._motion_uncertain = 'continuous scan encoder direction or forward limit changed'
            raise MotionUncertain(self._motion_uncertain)
        if (type(after) is not int or before * after <= 0
                or end_row.get('_decoder_present') is not True
                or not abs(before) <= abs(after) <= length
                or abs(after) - abs(before) > PARK_TOLERANCE_MM
                or abs(before) < samples[-1][1]):
            self._continuous_sampling_error = 'encoder_window_unconfirmed'
            return
        self._continuous_direction = direction
        samples.append((abs(before), abs(after), uid))

    @staticmethod
    def _continuous_card_window(samples):
        """A complete moving window is a candidate, never slot ownership."""
        from .ace_rfid_evidence import analyse_windows
        return analyse_windows(samples)['window']

    def _continuous_source_context(self, idx, slot):
        ace = self.ace
        return (ace._serials.get(idx), ace._location_identity(idx),
                getattr(ace, '_ace_mode', None), tuple(ace._rfid_route_heads(idx, slot)),
                getattr(ace, '_insert_epochs', {}).get((idx, slot)),
                getattr(ace, '_tag_op_seq', None))

    def _continuous_return_gate(self, idx, slot):
        """A final stable inlet proof is separate from the measured return."""
        for count in range(3):
            self._manual_guard()
            if self.ace._slot_actions._gate(idx, slot) is not True:
                raise MotionUncertain('识别结束后入口有料未确认，回停待核')
            self._manual_guard()
            if count != 2:
                self._pause(.1)

    def _commit_continuous_identity(self, idx, slot, uid, respond):
        pending = getattr(self, '_pending_identity', None)
        if (not isinstance(pending, dict) or (pending.get('idx'), pending.get('slot')) != (idx, slot)
                or pending.get('uid') != uid):
            return False
        self._manual_guard()
        if pending['kind'] == 'bambu':
            self._ingest_bambu(idx, slot, uid, pending['identity'], respond)
        elif pending['kind'] == 'ntag':
            self._ingest_attributed_ntag(idx, slot, uid, pending['data'], respond)
        elif pending['kind'] == 'uid_only':
            self._forget_identity(idx, slot, uid)
            self._note_fmt(idx, slot, 'mifare')
            self._note_uid(idx, slot, uid)
            self._bind_uid(idx, slot, uid, respond)
            respond('rc522: card UID %s (MIFARE identity not verified)' % uid)
        else:
            return False
        return True

    def read_slot_continuous(self, idx, slot, respond, max_mm=PARK_MAX_MM):
        """Bounded scan, explicit attribution, then commit after measured return."""
        from .ace_rfid_evidence import analyse_windows
        self._manual_proof = True
        self._park_tolerance_mm = PARK_TOLERANCE_MM
        self._continuous_direction = None
        self._continuous_sampling_error = None
        self._flow14_remaining = None
        self._pending_identity = None
        self._defer_identity_commit = False
        self._manual_return_confirmed = False
        self._scan_origin_reusable = False
        limit = min(int(max_mm), CONTINUOUS_SCAN_MM)
        # Position and total travel are different bounds. The scan may reach
        # the original 600 mm window; two attribution round trips reuse that
        # window, rather than taking 320 mm out of the search itself.
        self._manual_motor_budget = 2 * limit + 4 * START_PROBE_MM + 150
        key = (idx, slot)
        budget = self._continuous_budget = {'key': key, 'limit': limit, 'forward_commanded': 0,
            'reverse_commanded': 0, 'forward_limit': limit + 2 * START_PROBE_MM + 20,
            'reverse_limit': limit + 2 * START_PROBE_MM + 150,
            'maximum_position_mm': 0}
        logging.info('[multiACE] RFID scan budget ace=%s slot=%s max_position_mm=%s '
                     'forward_total_limit_mm=%s reverse_total_limit_mm=%s motor_total_limit_mm=%s',
                     idx, slot, limit, budget['forward_limit'], budget['reverse_limit'],
                     self._manual_motor_budget)
        returned = False
        commit_uid = None
        analysis = None
        source_context = self._continuous_source_context(idx, slot)
        length = limit
        policy = getattr(self.ace, '_insert_policy', None)
        origin_context = policy._scan_context(idx, slot) if policy is not None else None
        neighbour = None
        try:
            if length < PARK_STEP_MM:
                respond('rc522: result pending: 本次程限不足以完成归属验证和回停')
                return
            if not self._target_motion_allowed(idx, slot) or not self._wait_motion_ready(idx, slot):
                respond('rc522: read failed - target lane not ready; nothing moved')
                return
            neighbour = self._neighbour_signature(idx, slot)
            if neighbour is None:
                respond('rc522: result pending: 邻槽状态待确认，未转盘')
                return
            samples = [(0, 0, self._continuous_uid(idx, slot))]
            if not self._move_and_confirm(
                    idx, slot, length, SEARCH_MODE, respond,
                    observe=lambda baseline: self._continuous_sample(idx, slot, length, baseline, samples)):
                return
            if self._neighbour_signature(idx, slot) != neighbour:
                respond('rc522: result pending: 邻槽状态变化，未确认标签归属')
                return
            analysis = analyse_windows(samples)
            window = analysis['window'] if not self._continuous_sampling_error else None
            if window is None:
                respond('rc522: result pending: 已在 %d mm 窗口内扫描，未确认可归属标签；'
                        '不能据此认定无标签或完整转过一圈' % length)
                return
            uid, target, lower, upper = window
            direction = self._encoder_direction[key]
            # An empty neighbouring inlet does not prove its spool/tag absent.
            known_neighbour = (getattr(self.ace, '_rc_last_uid', {}) or {}).get((idx, slot ^ 1))
            if self._continuous_direction != direction or known_neighbour == uid:
                respond('rc522: result pending: 未确认标签属于目标槽')
                return
            position = direction * self._encoder_ledger[key]
            if target > position or position > length:
                raise MotionUncertain('连续识别终点超出已确认行程')
            if target == 0:
                self._restore_recorded_motion(respond)
                returned = True
            elif target < position:
                if not self._move_and_confirm(idx, slot, position-target, RESTORE_MODE, respond):
                    return
                position = direction * self._encoder_ledger[key]
                if not lower <= position <= upper:
                    respond('rc522: result pending: 回到读区的位置未确认')
                    return
            if (self._neighbour_signature(idx, slot) != neighbour
                    or self._observed_uid(idx, slot) != uid):
                respond('rc522: result pending: 返回读区时未复核到同一标签')
                return
            stamp = (getattr(self.ace, '_single_verified_uid', {}) or {}).get(key)
            expected_stamp = (source_context[0], source_context[1], source_context[4], uid)
            if stamp != expected_stamp:
                # Candidate-window correlation alone can be neighbour flicker.
                # Reuse the existing two round-trip attribution procedure, but
                # never advance beyond the already-authorized scan envelope.
                position = direction * self._encoder_ledger[key]
                # Prefer a retreat within the already-scanned interval. A tag
                # near its far end can then be proved without a deeper push.
                probe_mode = (RESTORE_MODE if position >= START_PROBE_MM else
                              SEARCH_MODE if position + START_PROBE_MM <= limit else None)
                if (probe_mode is None
                        or budget['forward_commanded'] + 2 * START_PROBE_MM > budget['forward_limit']
                        or budget['reverse_commanded'] + 2 * START_PROBE_MM + max(0, position) > budget['reverse_limit']
                        or self._manual_motor_budget < 4 * START_PROBE_MM + max(0, position) + 20):
                    respond('rc522: result pending: 当前安全程限不足以验证标签归属')
                    return
                returned = False
                if self._rotation_test(idx, slot, uid, respond, probe_mode=probe_mode) != 'ours':
                    respond('rc522: result pending: 标签归属尚未证明')
                    return
            if (self._continuous_source_context(idx, slot) != source_context
                    or self._neighbour_signature(idx, slot) != neighbour
                    or self._observed_uid(idx, slot) != uid):
                respond('rc522: result pending: 认证前来源或标签变化，未保存')
                return
            self._defer_identity_commit = True
            self._read_card(idx, slot, respond, expected_uid=uid, allow_nudges=False)
            commit_uid = uid
        finally:
            try:
                self._defer_identity_commit = False
                if not returned:
                    self._restore_recorded_motion(respond)
                if not self._motion_uncertain and key in self._motion_ledger:
                    stopped = self._req(idx, 'stop_feed_filament', {'index': slot})
                    if (not isinstance(stopped, dict) or stopped.get('_v2_frame_valid') is not True
                            or stopped.get('_v2_payload_valid') is not True or stopped.get('_v2_command') != 9
                            or type(stopped.get('code')) is not int or stopped['code'] != 0
                            or str(stopped.get('msg', '')).upper() == 'FORBIDDEN'):
                        raise MotionUncertain('识别回停 STOP 未确认')
                    self._manual_verify_stationary(idx, slot)
                    self._continuous_return_gate(idx, slot)
                    if self._continuous_source_context(idx, slot) != source_context:
                        raise MotionUncertain('识别回停后设备、插入代次或料路变化')
                    residual = self._encoder_ledger.get(key)
                    if type(residual) is not int or abs(residual) > PARK_TOLERANCE_MM:
                        raise MotionUncertain('识别回停残差未确认')
                    self._manual_return_confirmed = True
                    # A tolerated nonzero residual may still permit this
                    # identity read, but cannot silently become the next scan's
                    # zero. Repeated +10 mm returns must not ratchet the tip
                    # forward while each scan resets its 600 mm budget.
                    if policy is not None and residual == 0:
                        self._scan_origin_reusable = (
                            policy.restore_scan_origin(idx, slot, origin_context) is True)
                elif (policy is not None and not self._motion_uncertain
                      and key not in self._motion_ledger
                      and budget['forward_commanded'] == 0
                      and budget['reverse_commanded'] == 0):
                    # A rejected, too-small scan must not consume a known
                    # origin when no motor command was even attempted. This
                    # preserves the existing reference; it is not a measured
                    # return, a new origin, or permission to publish identity.
                    try:
                        if (self.ace._slot_actions.cancel is False
                                and self._continuous_source_context(idx, slot) == source_context
                                and self._target_motion_allowed(idx, slot) is True
                                and self._wait_motion_ready(idx, slot) is True):
                            self._continuous_return_gate(idx, slot)
                            if (self.ace._slot_actions.cancel is False
                                    and self._continuous_source_context(idx, slot) == source_context):
                                self._scan_origin_reusable = (
                                    policy.restore_scan_origin(idx, slot, origin_context) is True)
                    except Exception:
                        logging.info('[multiACE] RFID unmoved origin could not be reconfirmed',
                                     exc_info=True)
            finally:
                logging.info('[multiACE] RFID scan return ace=%s slot=%s forward_commanded_mm=%s '
                             'reverse_commanded_mm=%s max_confirmed_position_mm=%s '
                             'encoder_residual=%s return_confirmed=%s motion_uncertain=%s',
                             idx, slot, budget['forward_commanded'], budget['reverse_commanded'],
                             budget['maximum_position_mm'], self._encoder_ledger.get(key),
                             self._manual_return_confirmed, bool(self._motion_uncertain))
                self._manual_proof = False
                self._park_tolerance_mm = 2
                self._continuous_budget = None
        # No tag, inventory or colour mutation happens until the complete
        # return has succeeded. Exceptions/cancellation skip this commit.
        if (commit_uid and self._manual_return_confirmed and not self.ace._slot_actions.cancel
                and self._continuous_source_context(idx, slot) == source_context
                and self._neighbour_signature(idx, slot) == neighbour
                and self._commit_continuous_identity(idx, slot, commit_uid, respond)):
            if not hasattr(self.ace, '_single_verified_uid'):
                self.ace._single_verified_uid = {}
            self.ace._single_verified_uid[key] = (
                source_context[0], source_context[1], source_context[4], commit_uid)
            spacing = max((b[1] - a[0] for a, b in zip(samples, samples[1:])), default=0)
            self._flow14_remaining = dict(analysis, uid=commit_uid, ace=idx, slot=slot,
                insert_epoch=source_context[4], tag_op_seq=source_context[5],
                source_context=source_context, transport=source_context[0],
                device_uid=source_context[1][1], return_confirmed=True,
                completed_at=self.ace.reactor.monotonic(),
                return_residual_mm=self._encoder_ledger[key] * self._encoder_direction[key],
                maximum_observation_spacing_mm=spacing,
                forward_commanded_mm=budget['forward_commanded'],
                reverse_commanded_mm=budget['reverse_commanded'],
                maximum_position_mm=budget['maximum_position_mm'],
                forward_travel_limit_mm=budget['forward_limit'],
                forward_limit_mm=limit, reverse_limit_mm=budget['reverse_limit'],
                scan_origin_reusable=self._scan_origin_reusable,
                complete_turn_confirmed=False)

    def read_slot_single_checked(self, idx, slot, respond, guard, **kwargs):
        """One bounded scan and one UID/content read, without attribution guesses."""
        self._insert_checked_guard = guard
        self._insert_checked_uid = None
        self._single_auto = True
        token = object()
        sessions = getattr(self.ace,'_insert_reader_sessions',None)
        if sessions is None:
            sessions = self.ace._insert_reader_sessions = {}
        sessions[(idx,slot)] = token
        moving = False
        self._single_pending_stop = None
        def stop_if_active():
            if not moving and self._single_pending_stop is None:
                return
            stop_idx,stop_slot = self._single_pending_stop or (idx,slot)
            for _ in range(2):
                try:
                    response = self._req(stop_idx,'stop_feed_filament',{'index':stop_slot})
                except Exception:
                    response = None
                if (isinstance(response,dict) and response.get('code')==0
                        and response.get('_v2_command')==9 and response.get('_v2_frame_valid') is True
                        and response.get('_v2_payload_valid') is True):
                    return
                self._pause(.3)
        try:
            if not guard() or not self._target_motion_allowed(idx,slot):
                return {'kind':'transport_changed','parked':False,'uid':None}
            self._insert_checked_context = (idx,slot,self.ace._serials[idx],
                self.ace._insert_epochs[(idx,slot)],self.ace.printer.lookup_object('print_stats',None),
                self.ace._active_device_index,token)
            self._scan_measured=True
            self._scan_forward_limit=min(700,int(kwargs.get('sweep_mm',700)))
            self._scan_motor_position=0;self._scan_motor_total=0;self._scan_motor_total_limit=5600
            self._scan_motor_pending=None;self._scan_attempts=0;self._scan_zero_segments=0
            neighbour_before=self._neighbour_signature(idx,slot)
            status=self._rc(idx,slot,6)
            if status not in (SELECT_OK,1):
                return {'kind':'unread','parked':False,'uid':None}
            found=status == SELECT_OK
            if not found:
                baseline=self._fresh_encoder(idx,slot)
                self._sweep_baseline=getattr(self,'_encoder_diagnostic',None) if baseline is not None else None
                self._sweep_identity=self.ace._location_identity(idx)
                self._sweep_values=[];self._sweep_reset_seen=False
                leg=self._scan_forward_limit
                self._relative_sweep=True
                try:
                    moving=True
                    if not self._move(idx,slot,leg,SEARCH_MODE,respond):
                        moving=False
                        return {'kind':'unread','parked':False,'uid':None}
                finally:
                    self._relative_sweep=False
                deadline=self.ace.reactor.monotonic()+leg/float(PARK_SPEED)+1.5
                while self.ace.reactor.monotonic()<deadline:
                    self._pause(.1);self._insert_guard()
                    value=self._fresh_encoder(idx,slot)
                    current=(getattr(self,'_encoder_diagnostic',{}) or {}).get('row') or {}
                    old=(self._sweep_baseline or {}).get('row') or {}
                    if value is not None and all(type(row.get(k)) is int for row in (old,current) for k in ('steps','length')):
                        if (current['steps']<old['steps'] or current['length']<old['length']
                                or (value==0 and old.get('decoder') not in (None,0))):
                            self._sweep_reset_seen=True
                        if self._sweep_reset_seen and current.get('_decoder_present') is True and value:
                            self._sweep_values.append(value)
                    status=self._rc(idx,slot,6)
                    if status not in (SELECT_OK,1):
                        self._stop_and_measure(idx,slot,leg,respond)
                        moving=False
                        self._rewind_scan_origin(idx,slot,respond)
                        return {'kind':'unread','parked':False,'uid':None}
                    if status == SELECT_OK:
                        self._stop_and_measure(idx,slot,leg,respond)
                        moving=False
                        found=True
                        break
                if not found:
                    enc=self._natural_sweep_terminal(idx,slot,leg)
                    samples=self._sweep_values
                    if (not self._sweep_reset_seen or not samples or any(v*enc<=0 for v in samples)
                            or any(abs(b)<abs(a) for a,b in zip(samples,samples[1:])) or abs(enc)<abs(samples[-1])):
                        raise MotionUncertain('single scan movement unconfirmed')
                    direction=self._encoder_direction.get((idx,slot)) or (1 if enc>0 else -1)
                    if enc*direction<=0:
                        raise MotionUncertain('single scan direction changed')
                    self._encoder_direction[(idx,slot)]=direction
                    row=(getattr(self,'_encoder_diagnostic',{}) or {}).get('row') or {}
                    self._commit_scan_motor(idx,slot,SEARCH_MODE,leg,row.get('length'),True)
                    self._encoder_ledger[(idx,slot)]=self._encoder_ledger.get((idx,slot),0)+enc
                    moving=False
            uid=''; content=None; single_uid=False
            if found:
                self._insert_guard()
                uid=self._current_single_uid(idx,slot)
                single_uid=bool(uid)
                if not uid:
                    uid=self._rc_read_uid(idx,slot)
                if uid:
                    content=(self._read_bambu_classic(idx,slot,uid,single=True) if single_uid
                             else self._read_openspool(idx,slot,respond))
            self._rewind_scan_origin(idx,slot,respond)
            self._insert_guard()
            # A cached UID alone does not prove which spool is in a shared field.
            # Only an identity explicitly bound to this connection and slot may authorize a bind.
            stamp=(getattr(self.ace,'_single_verified_uid',{}) or {}).get((idx,slot))
            identity=self.ace._location_identity(idx)
            proven=(uid and stamp == (self.ace._serials.get(idx),identity,
                                     self.ace._insert_epochs.get((idx,slot)),uid))
            neighbour=slot^1
            neighbour_stamp=(getattr(self.ace,'_single_verified_uid',{}) or {}).get((idx,neighbour))
            if (not proven and uid and neighbour_before is not None and neighbour_stamp
                    and neighbour_stamp[:3] == (self.ace._serials.get(idx),identity,
                                               self.ace._insert_epochs.get((idx,neighbour)))
                    and neighbour_stamp[3] != uid and self._neighbour_signature(idx,slot)==neighbour_before):
                proven=True
            if not proven or not content:
                return {'kind':'unread','parked':False,'uid':None,'candidate_uid':uid or None}
            if single_uid:
                self._ingest_bambu(idx,slot,uid,content,respond)
            else:
                self._finish_present(idx,slot,uid,content,respond)
            if not hasattr(self.ace,'_single_verified_uid'):
                self.ace._single_verified_uid={}
            self.ace._single_verified_uid[(idx,slot)]=(self.ace._serials.get(idx),identity,
                                                       self.ace._insert_epochs.get((idx,slot)),uid)
            parked=kwargs.get('start_depth') is None
            return {'kind':'success','parked':parked,'uid':uid}
        except ScanBudgetExhausted:
            moving=False
            try:
                self._rewind_scan_origin(idx,slot,respond)
                return {'kind':'unread','parked':False,'uid':None}
            except Exception as exc:
                self.ace._latch_stop_fault(idx,slot,str(exc))
                return {'kind':'stop_unconfirmed','parked':False,'uid':None}
        except MotionUncertain as exc:
            stop_if_active()
            self.ace._latch_stop_fault(idx,slot,str(exc))
            return {'kind':'stop_unconfirmed','parked':False,'uid':None}
        except Exception:
            stop_if_active()
            logging.exception('[multiACE] single insert read failed')
            self.ace._latch_stop_fault(idx,slot,'single insert read exception; motion unconfirmed')
            return {'kind':'error','parked':False,'uid':None}
        finally:
            if sessions.get((idx,slot)) is token:
                sessions.pop((idx,slot),None)
            self._single_auto=False;self._scan_measured=False
            self._insert_checked_guard=None;self._insert_checked_context=None

    def read_slot_transport_checked(self, idx, slot, respond, guard, **kwargs):
        """Checked insert read. guard() must confirm epoch/transport/route.

        A non-preload result is returned on any uncertain or rejected motion.
        The old read_slot_transport entry point remains available unchanged in
        its return contract. The caller must recheck guard after this returns.
        """
        self._insert_checked_guard = guard
        self._insert_checked_uid = None
        self._insert_checked_park_confirmed = False
        token = object()
        sessions = getattr(self.ace, '_insert_reader_sessions', None)
        if sessions is None:
            sessions = self.ace._insert_reader_sessions = {}
        sessions[(idx, slot)] = token
        try:
            if not guard():
                return {'kind': 'transport_changed', 'parked': False, 'uid': None}
            self._insert_checked_context = (idx, slot, self.ace._serials[idx],
                self.ace._insert_epochs[(idx, slot)],
                self.ace.printer.lookup_object('print_stats', None),
                self.ace._active_device_index, token)
            self._scan_measured = True
            try:
                result = self.read_slot_transport(idx, slot, respond, **kwargs)
            except ScanBudgetExhausted:
                self._rewind_scan_origin(idx,slot,respond)
                self._insert_guard()
                respond('rc522: scan bound reached; returned to scan origin')
                return {'kind':'unread','parked':False,'uid':None}
            if not guard():
                return {'kind': 'transport_changed', 'parked': False, 'uid': None}
            return result
        except MotionUncertain as e:
            try:
                self.ace._latch_stop_fault(idx, slot, str(e))
            except Exception:
                logging.exception('[multiACE] failed to latch uncertain insert motion')
            return {'kind': 'stop_unconfirmed', 'parked': False, 'uid': None}
        except Exception:
            logging.exception('[multiACE] [rc522] checked insert failed')
            try:
                self.ace._latch_stop_fault(idx, slot, 'checked insert exception; motion unconfirmed')
            except Exception:
                logging.exception('[multiACE] failed to latch insert exception')
            return {'kind': 'error', 'parked': False, 'uid': None}
        finally:
            if sessions.get((idx, slot)) is token:
                sessions.pop((idx, slot), None)
            self._insert_checked_guard = None
            self._insert_checked_context = None
            self._scan_measured = False

    def _insert_result(self, kind):
        if getattr(self, '_insert_checked_guard', None) is None:
            return kind if kind in ('deferred', 'unread') else None
        return {'kind': kind,
                'parked': (kind in ('success', 'no_tag', 'unread') and
                           getattr(self, '_insert_checked_park_confirmed', False)),
                'uid': getattr(self, '_insert_checked_uid', None)}

    def _insert_guard(self):
        guard = getattr(self, '_insert_checked_guard', None)
        if guard is not None and not guard():
            raise MotionUncertain('insert route or transport changed')

    def _natural_sweep_terminal(self, idx, slot, leg):
        self._insert_guard()
        if not self._wait_motion_ready(idx,slot):
            raise MotionUncertain('sweep completion unconfirmed')
        enc=self._fresh_encoder(idx,slot)
        first=dict((getattr(self,'_encoder_diagnostic',{}) or {}).get('row') or {})
        self._pause(0.3)
        second=self._fresh_encoder(idx,slot)
        last=dict((getattr(self,'_encoder_diagnostic',{}) or {}).get('row') or {})
        if (not self._wait_motion_ready(idx,slot) or enc != second
                or first.get('_decoder_present') is not True or last.get('_decoder_present') is not True
                or any(type(first.get(k)) is not int or first.get(k)!=last.get(k) for k in ('steps','length'))
                or first['length'] < leg-5):
            raise MotionUncertain('sweep terminal stability or commanded length unconfirmed')
        if not isinstance(enc,int) or isinstance(enc,bool) or not (leg*.5 <= abs(enc) <= leg*1.5+2):
            raise MotionUncertain('sweep encoder unconfirmed')
        return enc

    def read_slot_transport(self, idx, slot, respond, start_depth=0,
                            net_target=390, sweep_mm=700):
        """The INSERT read: ONE long forward sweep of sweep_mm - that is more than
        a full spool revolution (~600 units on a full 1kg spool, fewer
        units per revolution as it empties), so the tag MUST pass the
        antenna - listening the whole way. Hit -> brake, read, then ONE
        correction move to net_target (forward or back, wherever the stop
        landed). No hit -> one rollback to net_target. No stop-and-go
        poking. start_depth = the verified abort's pulled-in amount (None
        = the firmware procedure completed, spool already AT net_target -
        the sweep then runs from there and rolls back further)."""
        ace = self.ace
        neighbour = slot ^ 1
        # The neighbour's tag, when we read it earlier this boot: a
        # blocker from the first unit on, whether or not it answers at
        # sweep start (a parked tag at the field edge flickers).
        blocking_uid = self._known_neighbour_uid(idx, slot)
        depth = start_depth if start_depth is not None else net_target
        origin_depth = depth
        if getattr(self, '_scan_measured', False):
            self._scan_origin_depth = origin_depth
            self._scan_target_depth = net_target
            self._scan_forward_limit = min(700, int(sweep_mm))
            self._scan_motor_position = 0
            self._scan_motor_total = 0
            self._scan_motor_total_limit = 5600
            self._scan_motor_pending = None
            self._scan_attempts = 0
            self._scan_zero_segments = 0
        # UIDs the neighbour was rotated away from this sweep: the same
        # card answering AGAIN later can only have come back with OUR
        # lane (the neighbour stays parked) - it is ours, no re-test.
        cleared_uids = set()
        if depth > MOVE_MAX_MM:
            respond('rc522: start depth %d is implausible - assuming the '
                    'park depth %d' % (depth, net_target))
            depth = net_target
        self._wait_neighbour_settled(idx, slot, respond)
        if self._rc_select(idx, slot):
            if self._neighbour_empty(idx, slot):
                respond('rc522: card already in the field - reading, then '
                        'transporting to park depth')
                depth += self._read_card(idx, slot, respond)
                self._correct_to(idx, slot, net_target - depth, respond)
                return self._insert_result('unread' if getattr(self, '_read_unread', False) else ('success' if getattr(self, '_insert_checked_uid', None) else 'unread'))
            # Shared antenna, neighbour occupied - but the answering card
            # can just as well be OUR OWN tag sitting in the field at
            # insert. Decide by the known neighbour UID, else by the
            # rotation test. A neighbour's card is ROTATED OUT of the
            # field so the
            # sweep runs blocker-free; only when the neighbour may not be
            # moved does the UID stay a blocker.
            uid_a, _bd = self._rc_stable_uid(idx, slot)
            verdict = None
            if not uid_a:
                # Unreadable (collision / corrupted) - with a neighbour
                # around, clearing it is the only way to find out.
                if not self._clear_neighbour(idx, slot, respond):
                    # Neighbour in ITS OWN firmware insert (two spools
                    # into one antenna pair seconds apart): not a dead end - our queued abort stops that
                    # procedure moments later and the neighbour becomes
                    # rotatable. Hand the read back to the queue instead
                    # of failing it.
                    try:
                        n_busy = (ace._v2_get_slot_status(idx, neighbour)
                                  in V2_ACTIVE_MOTION_STATES)
                    except Exception:
                        n_busy = False
                    self._correct_to(idx, slot, net_target - depth, respond)
                    if n_busy:
                        respond('rc522: neighbour slot %d is being inserted '
                                'right now - this read is queued behind it'
                                % ace._disp(neighbour))
                        return self._insert_result('deferred')
                    respond('rc522: a card already answers but its UID is '
                            'unreadable - cannot tell it from the '
                            'neighbour. Remove or rotate the neighbour '
                            'spool a bit, then retry.')
                    # An ambiguous shared-field return is not evidence of
                    # a safely attributable spool, even after correction.
                    self._insert_checked_park_confirmed = False
                    return self._insert_result('unread')
                if self._rc_select(idx, slot):
                    uid_a, _bd = self._rc_stable_uid(idx, slot)
                    verdict = 'ours' if uid_a else None
            else:
                # Registry match or unknown neighbour alike: physics decides
                # (the registry can be stale, see write_slot).
                verdict, lane_moved = self._whose_card(idx, slot, uid_a,
                                                       respond)
                if verdict is None:
                    if getattr(self,'_scan_measured',False):
                        self._rewind_scan_origin(idx,slot,respond)
                        self._insert_checked_park_confirmed = False
                        return self._insert_result('unread')
                    return self._insert_result('stop_unconfirmed')
                if lane_moved:
                    depth += START_PROBE_MM
            if verdict == 'cleared':
                blocking_uid = ''
                cleared_uids.add(uid_a)
            if verdict == 'neighbour':
                if self._clear_neighbour(idx, slot, respond):
                    blocking_uid = ''
                    if uid_a:
                        cleared_uids.add(uid_a)
                elif self._own_after_clear(idx, slot, uid_a, respond):
                    verdict = 'ours'
                else:
                    blocking_uid = uid_a
                    respond('rc522: card %s is the neighbour\'s - '
                            'listening for a different one' % uid_a)
            if verdict == 'ours':
                respond('rc522: card %s is this slot\'s tag - reading it'
                        % uid_a)
                depth += self._read_card(idx, slot, respond)
                self._correct_to(idx, slot, net_target - depth, respond)
                return self._insert_result('unread' if getattr(self, '_read_unread', False) else ('success' if getattr(self, '_insert_checked_uid', None) else 'unread'))
        swept = 0
        found = False
        speed = PARK_SPEED
        slow_pass = False
        while swept < sweep_mm and not found:
            leg = sweep_mm - swept
            if getattr(self,'_scan_measured',False):
                if self._scan_motor_pending is not None:
                    raise MotionUncertain('previous scan motor movement unconfirmed')
                available=self._scan_forward_limit-self._scan_motor_position
                if available<=0:
                    break
                leg=min(leg,available)
            _baseline_value = self._fresh_encoder(idx,slot)
            self._sweep_baseline = (getattr(self, '_encoder_diagnostic', None)
                                    if _baseline_value is not None else None)
            self._sweep_identity = self.ace._location_identity(idx)
            self._sweep_values = []
            self._sweep_reset_seen = False
            self._relative_sweep = True
            try:
                moved = self._move(idx, slot, leg, SEARCH_MODE, respond, speed=speed)
            finally:
                self._relative_sweep = False
            if not moved:
                if getattr(self, '_insert_checked_guard', None) is not None:
                    raise MotionUncertain('sweep rejected')
                break
            deadline = (self.ace.reactor.monotonic()
                        + leg / float(speed) + 1.5)
            stopped = False
            while self.ace.reactor.monotonic() < deadline:
                # Poll as fast as the tunnel allows (~0.16 s per SELECT):
                # at 40 units/s the old 0.3 s pause left ~19 units between
                # polls, more than a small NTAG sticker's read window.
                self._pause(0.1)
                guard = getattr(self, '_insert_checked_guard', None)
                if guard is not None and not guard():
                    # A running sweep must be stopped even when its ownership
                    # is revoked; a failed stop remains motion-uncertain.
                    self._stop_and_measure(idx, slot, leg, respond)
                    raise MotionUncertain('insert ownership changed during sweep')
                value = self._fresh_encoder(idx,slot)
                diagnostic = getattr(self,'_encoder_diagnostic',{})
                current = diagnostic.get('row') or {}
                old = (self._sweep_baseline or {}).get('row') or {}
                if (value is not None and type(old.get('steps')) is int and type(old.get('length')) is int
                        and type(current.get('steps')) is int and type(current.get('length')) is int):
                    if (current['steps'] < old['steps'] or current['length'] < old['length']
                            or (value == 0 and old.get('decoder') not in (None,0))):
                        self._sweep_reset_seen = True
                    if self._sweep_reset_seen and current.get('_decoder_present') is True and value:
                        self._sweep_values.append(value)
                if not self._rc_select(idx, slot):
                    continue
                if blocking_uid:
                    # Quick in-motion check, no stop: the parked neighbour
                    # reads stably; only a different/unreadable UID is
                    # worth braking for.
                    if self._rc_read_uid(idx, slot) == blocking_uid:
                        continue
                d = self._stop_and_measure(idx, slot, leg, respond)
                stopped = True
                swept += d
                depth += d
                uid_now, _d2 = self._rc_stable_uid(idx, slot)
                if blocking_uid and uid_now == blocking_uid:
                    break                    # false alarm - sweep on
                # CENTRE before reading: the brake lands the tag at the
                # field EDGE (detection = field entry) and edge reads are
                # unreliable - identify/NDEF fail and even the double-read
                # UID can pass with a flipped byte. READ-guided
                # (PROBE_OFFSETS note); net is signed.
                # NOT for a MIFARE card (single-size UID): it has no pages
                # the probe could ever read clean, so the centering walks
                # ALL nine offsets (the +-40 dance) for nothing - and on a
                # Bambu spool the chip is in the FLANGE, answering at any
                # filament depth: a hit right after the bite plus that
                # dance can pull the tip back out of the gate. The UID is
                # the whole read: take it here. Our tunnel never reads a
                # MIFARE's pages (NAK), so uid_now is EMPTY for exactly the
                # card this is about - the firmware sentinel is the only UID
                # source there: one identify decides.
                mifare_uid = uid_now if self._is_single_uid(uid_now) else ''
                if not uid_now:
                    res0 = self._identify(idx, slot)
                    if self._answered(res0) and self._is_uid_read(res0):
                        s0 = self._sentinel_uid(res0)
                        if self._is_single_uid(s0):
                            mifare_uid = s0
                if mifare_uid:
                    respond('rc522: card %s has a single-size UID (MIFARE, '
                            'no pages) - reading it here, no centering'
                            % mifare_uid)
                    _c = 0
                else:
                    _c = self._centre_on_read(idx, slot, respond)
                swept += _c
                depth += _c
                if self._neighbour_occupied(idx, slot):
                    # A parked neighbour tag at its field edge FLICKERS:
                    # silent at sweep start, answering mid-sweep. Decide: known neighbour UID or
                    # unreadable -> rotate the neighbour out and sweep on;
                    # unknown UID -> rotation test.
                    # A neighbour that went in DURING our sweep is still
                    # in its firmware insert - its tag moves through the
                    # field, nothing can be attributed until it settles.
                    self._wait_neighbour_settled(idx, slot, respond)
                    uid_c, _d3 = self._rc_stable_uid(idx, slot)
                    # Our tunnel reads no MIFARE UID; the firmware sentinel
                    # gave it. A known neighbour UID that differs then
                    # settles it without rotating anything.
                    uid_c = uid_c or mifare_uid
                    # Ask the identify only while the card being judged IS
                    # that MIFARE: once it is HALTed, op 6 can reach another
                    # card, which is then judged by op 6 as before.
                    self._halted_uid = (mifare_uid if mifare_uid
                                        and uid_c == mifare_uid else '')
                    n_uid = self._known_neighbour_uid(idx, slot)
                    verdict = None
                    probed = False
                    if uid_c and uid_c in cleared_uids:
                        # The neighbour was rotated out of the field and
                        # this card is back: it came with OUR lane.
                        verdict = 'ours'
                        respond('rc522: card %s answers again after the '
                                'neighbour was rotated away - it is this '
                                'slot\'s own tag' % uid_c)
                    elif uid_c and blocking_uid and uid_c == blocking_uid:
                        # Confirmed by an earlier rotation test this sweep.
                        verdict = 'neighbour'
                    elif not uid_c or not n_uid or uid_c == n_uid:
                        # Unreadable, unknown neighbour, or a registry
                        # match (possibly stale): let physics decide.
                        verdict, lane_moved = self._whose_card(
                            idx, slot, uid_c, respond)
                        if verdict is None:
                            if getattr(self,'_scan_measured',False):
                                self._rewind_scan_origin(idx,slot,respond)
                                self._insert_checked_park_confirmed = False
                                respond('rc522: card ownership unresolved; returned to scan origin')
                                return self._insert_result('unread')
                            break
                        probed = lane_moved
                        if lane_moved:
                            swept += START_PROBE_MM
                            depth += START_PROBE_MM
                    if verdict == 'cleared':
                        # Rotated out already: sweep on, and the same card
                        # answering again later came with OUR lane.
                        if uid_c:
                            cleared_uids.add(uid_c)
                        if blocking_uid == uid_c:
                            blocking_uid = ''
                        sweep_mm += max(_c, 0)
                        break                # sweep on
                    if verdict == 'neighbour':
                        respond('rc522: %s at ~%d units is the parked '
                                'neighbour tag'
                                % ('unreadable card' if not uid_c
                                   else 'card %s' % uid_c, depth))
                        if self._clear_neighbour(idx, slot, respond):
                            if uid_c:
                                cleared_uids.add(uid_c)
                        elif self._own_after_clear(idx, slot, uid_c,
                                                   respond):
                            # Second opinion overrules the rotation
                            # test: read it, never chase it again.
                            verdict = 'ours'
                            if blocking_uid == uid_c:
                                blocking_uid = ''
                        else:
                            blocking_uid = uid_c or blocking_uid
                    if verdict == 'neighbour':
                        # The stop/centre/probe ate sweep budget that
                        # was meant for OUR tag - give it back so the
                        # sweep still covers a full revolution (08:45:
                        # the tag appeared at 625 of 700, 75 units left).
                        sweep_mm += max(_c, 0) + (START_PROBE_MM
                                                  if probed else 0)
                        break                # sweep on
                self._halted_uid = ''
                respond('rc522: tag found during transport (~%d units in)'
                        % depth)
                depth += self._read_card(idx, slot, respond,
                                         reject_uid=blocking_uid)
                found = True
                break
            if not stopped:
                if getattr(self, '_insert_checked_guard', None) is not None:
                    enc = self._natural_sweep_terminal(idx,slot,leg)
                    if getattr(self,'_relative_unknown',False) or getattr(self,'_scan_measured',False):
                        direction=self._encoder_direction.get((idx,slot))
                        if (not self._sweep_reset_seen or not self._sweep_values
                                or any(v*enc<=0 for v in self._sweep_values)
                                or any(abs(b)<abs(a) for a,b in zip(self._sweep_values,self._sweep_values[1:]))
                                or abs(enc)<abs(self._sweep_values[-1])):
                            raise MotionUncertain('relative sweep reset or movement unconfirmed')
                        if direction is None and self._sweep_reset_seen and self._sweep_values:
                            candidate=1 if enc>0 else -1
                            samples=self._sweep_values
                            if (all(v*candidate>0 for v in samples)
                                    and all(abs(b)>=abs(a) for a,b in zip(samples,samples[1:]))
                                    and abs(enc)>=abs(samples[-1])):
                                direction=candidate
                                self._encoder_direction[(idx,slot)]=candidate
                                self._remember_encoder_direction(idx,slot,candidate)
                        if direction not in (-1,1) or enc*direction<=0:
                            raise MotionUncertain('sweep direction unconfirmed')
                        row=(getattr(self,'_encoder_diagnostic',{}) or {}).get('row') or {}
                        self._commit_scan_motor(idx,slot,SEARCH_MODE,leg,row.get('length'),self._sweep_reset_seen)
                        self._encoder_ledger[(idx,slot)]=self._encoder_ledger.get((idx,slot),0)+enc
                        logging.info('[multiACE] RFID natural sweep ace=%s slot=%s command=%s raw=%s normalized=%s final=%s ledger=%s',
                                     idx,slot,leg,enc,enc*direction,getattr(self,'_encoder_diagnostic',None),self._encoder_ledger[(idx,slot)])
                swept += leg
                depth += (enc * direction if getattr(self,'_scan_measured',False) else leg)
            if swept >= sweep_mm and not found and not slow_pass:
                # Second pass at half speed before giving up: costs ~35 s,
                # only for a spool whose tag the fast pass missed (or that
                # has none).
                slow_pass = True
                speed = max(10, PARK_SPEED // 2)
                if getattr(self,'_scan_measured',False):
                    self._rewind_scan_origin(idx,slot,respond)
                    depth = origin_depth
                swept = 0
                respond('rc522: nothing in the fast pass - sweeping again '
                        'at %d units/s' % speed)
        self._halted_uid = ''
        if not found:
            if blocking_uid:
                respond('rc522: only the neighbour card (UID %s) answered '
                        'over %d units - this slot\'s tag could not be '
                        'told apart.' % (blocking_uid, sweep_mm))
                self._neighbour_blocked_notice(idx, slot, blocking_uid)
            else:
                respond('rc522: no tag answered over %d units - the spool '
                        'may carry no (working) tag.' % sweep_mm)
        self._correct_to(idx, slot, net_target - depth, respond)
        if found and getattr(self, '_read_unread', False):
            # A card answered the sweep, the read got nothing: the caller
            # retries once from the parked position (see _read_card).
            return self._insert_result('unread')
        if found:
            return self._insert_result('success' if getattr(self, '_insert_checked_uid', None) else 'unread')
        return self._insert_result('unread' if blocking_uid else 'no_tag')

    def _rewind_scan_origin(self, idx, slot, respond):
        """Return measured net motion before starting the second pass."""
        key = (idx,slot)
        raw = self._encoder_ledger.get(key,0)
        direction = self._encoder_direction.get(key)
        if raw and direction not in (-1,1):
            raise MotionUncertain('scan origin direction unconfirmed')
        direction = direction or 1
        budget = 2 * abs(raw) + 20
        commanded = 0
        tolerance = getattr(self, '_park_tolerance_mm', 2)
        for _ in range(8):
            remaining = self._encoder_ledger.get(key,0) * direction
            if abs(remaining) <= tolerance:
                self._insert_guard()
                if not self._wait_motion_ready(idx,slot):
                    raise MotionUncertain('scan origin standstill unconfirmed')
                logging.info('[multiACE] RFID scan origin restored ace=%s slot=%s raw_ledger=%s',idx,slot,self._encoder_ledger.get(key,0))
                return
            commanded += abs(remaining)
            if commanded > budget:
                raise MotionUncertain('scan origin motor budget exceeded')
            mode = RESTORE_MODE if remaining > 0 else SEARCH_MODE
            if not self._move_and_confirm(idx,slot,abs(remaining),mode,respond):
                raise MotionUncertain('scan origin correction rejected')
            if abs(self._encoder_ledger.get(key,0) * direction) >= abs(remaining):
                raise MotionUncertain('scan origin correction made no progress')
        if abs(self._encoder_ledger.get(key,0)*direction)<=2:
            self._insert_guard()
            if self._wait_motion_ready(idx,slot):
                self._insert_guard()
                return
        raise MotionUncertain('scan origin encoder residual unconfirmed')

    def _correct_to(self, idx, slot, delta, respond):
        """One paced correction move: positive delta feeds forward,
        negative rolls back. Small offsets (<=20) are left alone."""
        if getattr(self, "_relative_unknown", False):
            key = (idx, slot)
            direction = self._encoder_direction.get(key)
            initial = self._encoder_ledger.get(key, 0)
            if direction not in (-1, 1) and initial:
                raise MotionUncertain("relative direction unconfirmed")
            direction = direction or 1
            motor_budget = int(abs(initial) * 2) + 20
            motor_total = 0
            for _ in range(8):
                residual = self._encoder_ledger.get(key, 0) * direction
                if abs(residual) <= 2:
                    self._insert_guard()
                    if not self._wait_motion_ready(idx, slot):
                        raise MotionUncertain("relative standstill unconfirmed")
                    self._insert_guard()
                    self._insert_checked_park_confirmed = True
                    return
                motor_total += abs(residual)
                if motor_total > motor_budget:
                    raise MotionUncertain("relative correction motor budget exceeded")
                if not self._move_and_confirm(
                    idx,
                    slot,
                    abs(residual),
                    RESTORE_MODE if residual > 0 else SEARCH_MODE,
                    respond,
                ):
                    raise MotionUncertain("relative correction rejected")
                if abs(self._encoder_ledger.get(key,0) * direction) >= abs(residual):
                    raise MotionUncertain('relative correction made no progress')
            if abs(self._encoder_ledger.get(key,0)*direction)<=2:
                self._insert_guard()
                if self._wait_motion_ready(idx,slot):
                    self._insert_guard()
                    self._insert_checked_park_confirmed=True
                    return
            raise MotionUncertain("relative encoder residual unconfirmed")
        if getattr(self, '_scan_measured', False):
            raw = self._encoder_ledger.get((idx, slot), 0)
            direction = self._encoder_direction.get((idx, slot))
            if raw and direction not in (-1, 1):
                raise MotionUncertain('scan correction direction unconfirmed')
            direction = direction or 1
            target_delta = self._scan_target_depth - self._scan_origin_depth
            initial = target_delta - raw * direction
            motor_budget = int(abs(initial) * 2) + 20
            commanded = 0
            for _ in range(8):
                remaining = target_delta - self._encoder_ledger.get((idx, slot), 0) * direction
                if abs(remaining) <= 2:
                    self._insert_guard()
                    if not self._wait_motion_ready(idx, slot):
                        raise MotionUncertain('scan park standstill unconfirmed')
                    self._insert_guard()
                    self._insert_checked_park_confirmed = True
                    return
                commanded += abs(remaining)
                if commanded > motor_budget:
                    raise MotionUncertain('scan park motor budget exceeded')
                if not self._move_and_confirm(idx, slot, abs(remaining),
                                              SEARCH_MODE if remaining > 0 else RESTORE_MODE, respond):
                    raise MotionUncertain('scan park correction rejected')
                new_remaining=target_delta-self._encoder_ledger.get((idx,slot),0)*direction
                if abs(new_remaining)>=abs(remaining):
                    raise MotionUncertain('scan park correction made no progress')
            if abs(target_delta-self._encoder_ledger.get((idx,slot),0)*direction)<=2:
                self._insert_guard()
                if self._wait_motion_ready(idx,slot):
                    self._insert_guard()
                    self._insert_checked_park_confirmed=True
                    return
            raise MotionUncertain('scan park encoder residual unconfirmed')
        delta = int(delta)
        if abs(delta) <= 20:
            if getattr(self, "_insert_checked_guard", None) is not None:
                self._insert_guard()
                if not self._wait_motion_ready(idx, slot):
                    raise MotionUncertain("park standstill unconfirmed")
                self._insert_guard()
                self._insert_checked_park_confirmed = True
            return
        mode = SEARCH_MODE if delta > 0 else RESTORE_MODE
        length = abs(delta)
        if getattr(self, "_insert_checked_guard", None) is not None:
            if not self._move_and_confirm(idx, slot, length, mode, respond):
                raise MotionUncertain("park correction rejected")
            self._insert_guard()
            self._insert_checked_park_confirmed = True
            return
        if self._move(idx, slot, length, mode, respond):
            self._pause(length / float(PARK_SPEED) + 1.0)
            respond(
                "rc522: parked at target depth (%s %d)"
                % ("fed" if delta > 0 else "rolled back", length)
            )

    # -- stage 3: write (OpenSpool NTAG only) -----------------------------

    def write_slot(self, idx, slot, ident, respond, max_mm=PARK_MAX_MM,
                   fmt='openspool'):
        """Park the slot's tag, then write an OpenSpool NDEF onto it.
        Greenlet context (reactor.pause OK). Safety, in order of regret:
        (1) only ever writes user pages (>=4), capped at the CC user limit -
        UID/lock/OTP/CC/config pages are never touched; (2) refuses on an
        ambiguous field (shared-antenna collision / unstable UID) so the
        WRONG tag is never written (Simon-CR rule); (3) reads a backup
        first; (4) verifies by read-back. Only NTAG (OpenSpool/blank) - a
        MIFARE (Bambu/Snapmaker) never SELECTs cleanly here and is refused
        upstream. Lane restored in finally."""
        ace = self.ace
        rolled = 0
        try:
            # Park: forward-search until any card SELECTs (write targets are
            # blank/OpenSpool NTAG that the firmware identify may not catch).
            # Park OUR tag: a parked NEIGHBOUR tag in the shared field is a
            # clean single card too, and a write must never land on it -
            # so every answering card is attributed first (known neighbour
            # UID, else the rotation test) and a neighbour's is rotated out
            # of the field before the search goes on.
            uid1 = ''
            announced = False
            while True:
                if self._rc_select(idx, slot):
                    uid1, detail = self._rc_stable_uid(idx, slot)
                    n_occ = self._neighbour_occupied(idx, slot)
                    if not uid1:
                        # Collision / corrupted read: with a neighbour,
                        # clear it and look again; alone it is a real
                        # ambiguity.
                        if n_occ and self._clear_neighbour(idx, slot,
                                                           respond):
                            continue
                        respond('rc522: ambiguous field (SELECT=%s UID '
                                '%s/%s after 3 tries) - a neighbour tag '
                                'may be in range. Rotate the neighbour '
                                'spool away and retry; not writing.'
                                % (self._hx(detail[2]), detail[0],
                                   detail[1]))
                        return
                    if n_occ:
                        n_uid = self._known_neighbour_uid(idx, slot)
                        if n_uid and uid1 != n_uid:
                            verdict = 'ours'
                        else:
                            # Unknown neighbour, OR the registry says this
                            # UID is the neighbour's - the registry can be
                            # stale (a roll moved while Klipper was off),
                            # so physics decides either way.
                            verdict, lane_moved = self._whose_card(
                                idx, slot, uid1, respond)
                            if verdict is None:
                                return
                            if lane_moved:
                                rolled += START_PROBE_MM
                            if verdict == 'cleared':
                                uid1 = ''
                                continue
                        if verdict == 'neighbour':
                            respond('rc522: card %s is the neighbour\'s '
                                    'tag' % uid1)
                            if not self._clear_neighbour(idx, slot,
                                                         respond):
                                respond('rc522: cannot clear the field - '
                                        'not writing.')
                                return
                            uid1 = ''
                            continue
                    break                    # ours, stable
                if rolled >= max_mm:
                    break
                if not announced:
                    respond('rc522: searching for the tag to write (up to '
                            '%d mm)' % max_mm)
                    announced = True
                if not self._move(idx, slot, PARK_STEP_MM, SEARCH_MODE,
                                  respond):
                    return
                rolled += PARK_STEP_MM
                self._pause(PARK_STEP_MM / float(PARK_SPEED) + 0.6)
                if rolled % 100 == 0:
                    respond('rc522: ... %d mm' % rolled)
            if not uid1:
                respond('rc522: no tag found within %d mm - cannot write.'
                        % max_mm)
                return
            # CENTRE before writing: the search stops at the field EDGE, and
            # edge reads flip bytes - chunk-verify mismatches and their
            # per-page retries come from writing there. READ-guided
            # (PROBE_OFFSETS note); the
            # restore in finally covers it via `rolled`.
            rolled += self._centre_on_read(idx, slot, respond)
            uid_c = ''
            for _try in range(3):
                # One bad read is not a changed field (a single
                # BCC-rejected read after centering must not refuse the
                # write).
                # Re-read, and nudge once more when it stays unreadable.
                if _try:
                    self._pause(1.0)
                    if self._move(idx, slot, CENTER_MM, SEARCH_MODE,
                                  respond):
                        self._pause(CENTER_MM / float(PARK_SPEED) + 0.5)
                        rolled += CENTER_MM
                self._rc_select(idx, slot)
                uid_c, _dc = self._rc_stable_uid(idx, slot)
                if uid_c:
                    break
            if uid_c != uid1:
                respond('rc522: tag %s changed to %s after centering - '
                        'ambiguous field, not writing.'
                        % (uid1, uid_c or '?'))
                return
            # Encode + capacity check against the CC user limit.
            if fmt == 'anycubic':
                # sku = the card UID unless the caller insists on another.
                data = self._anycubic_encode(
                    (ident.get('sku') or '').strip() or uid1,
                    ident.get('material', ''), ident.get('subtype', ''),
                    ident.get('vendor', ''), ident.get('color', ''),
                    ident.get('min_temp', ''), ident.get('max_temp', ''),
                    ident.get('weight_g', 0))
            else:
                data = self._openspool_encode(
                    ident.get('material', ''), ident.get('color', ''),
                    ident.get('vendor', ''), ident.get('min_temp', ''),
                    ident.get('max_temp', ''))
            npages = len(data) // 4
            last_user = self._rc_user_page_limit(idx, slot)
            if last_user is not None and 4 + npages - 1 > last_user:
                respond('rc522: needs %d pages but the tag ends at page %d - '
                        'refusing (would overflow into config/lock pages).'
                        % (npages, last_user))
                return
            # Read-before-write backup (Simon-CR rule 3) - logged so it is
            # recoverable from the console if a write goes wrong.
            backup = self._rc_read_userdata(idx, slot, respond=None,
                                            first=4, last=4 + npages - 1)
            respond('rc522: writing %s to UID %s (%d pages). backup=%s'
                    % ('Anycubic' if fmt == 'anycubic' else 'OpenSpool',
                       uid1, npages, backup.hex()))
            # Write-through in 4-page chunks: pages written BLIND, then ONE
            # READ verifies the whole chunk (16 bytes = 4 pages) - ~2.5x
            # faster than per-page readback, same ground truth. Only a
            # mismatching chunk falls back to the per-page readback+retry
            # path. The verified chunk bytes double as the final decode
            # input, so no separate full read-back pass is needed.
            rb = b''
            for base in range(0, npages, 4):
                want = data[base * 4:min((base + 4) * 4, len(data))]
                for j in range(len(want) // 4):
                    k = base + j
                    self._rc_write_page(idx, slot, 4 + k,
                                        data[k*4:k*4+4], respond,
                                        readback=False)
                # After a WRITE batch the tag/reader is left in a shifted
                # read state - a verify READ of page 4 came back as page-19
                # bytes ("bran"), deterministically, twice;
                # the second full run started clean and read correctly. So
                # re-SELECT before verifying: re-activates the tag and
                # resets its read pointer, after the ~4 ms programming time.
                self._pause(0.02)
                self._rc(idx, slot, 6)
                pg = self._rc_read_page(idx, slot, 4 + base)
                got = bytes(pg[:len(want)])
                if got != want:
                    respond('rc522: pages %d-%d mismatch after write - '
                            'retrying per page'
                            % (4 + base, 4 + base + len(want) // 4 - 1))
                    for j in range(len(want) // 4):
                        k = base + j
                        if not self._rc_write_page(idx, slot, 4 + k,
                                                   data[k*4:k*4+4], respond):
                            respond('rc522: WRITE failed at page %d - '
                                    'aborting. The tag may be partly '
                                    'written; re-run to finish.' % (4 + k))
                            return
                    got = want   # every page passed its own readback
                rb += got
            if fmt == 'anycubic':
                dec = self._anycubic_decode(rb)
                want_mat = (ident.get('material') or '').strip()
                if (ident.get('subtype') or '').strip():
                    want_mat = '%s %s' % (want_mat, ident['subtype'].strip())
            else:
                dec = self._openspool_decode(rb)
                want_mat = ident.get('material')
            want_col = (ident.get('color', '') or '').lstrip('#').upper()[:6]
            if (dec and dec.get('material') == want_mat
                    and (dec.get('color') or '').upper() == want_col):
                respond('rc522: write VERIFIED - %s #%s (UID %s%s)'
                        % (dec['material'], dec.get('color') or '------',
                           uid1, (', sku %s' % dec.get('sku'))
                           if fmt == 'anycubic' else ''))
                # Ingest what was just written as a HOST read, so the slot
                # shows the RFID identity and binds at once instead of at
                # the next insert/ACE_TAG_READ.
                # Same shape a read of this tag would produce: Anycubic ->
                # the written sku, OpenSpool -> sku = the card UID.
                try:
                    col = dec.get('color') or ''
                    rgb = ([int(col[0:2], 16), int(col[2:4], 16),
                            int(col[4:6], 16)] if len(col) == 6
                           else [0, 0, 0])
                    res = {'type': dec['material'], 'color': rgb,
                           'brand': (dec.get('brand') if fmt == 'anycubic'
                                     else ident.get('vendor', '')) or '',
                           'sku': (dec.get('sku') if fmt == 'anycubic'
                                   else uid1) or uid1,
                           'subtype': ''}
                    self._note_fmt(idx, slot, fmt)
                    ace._v2_store_filament_read(idx, slot, res, uid=uid1,
                                                fmt=fmt)
                    self._note_uid(idx, slot, uid1)
                    self._bind_uid(idx, slot, uid1, respond)
                except Exception as e:
                    respond('rc522: post-write ingest failed (tag is '
                            'written): %s' % e)
            else:
                respond('rc522: write done but VERIFY MISMATCH - read back %s. '
                        'Check the tag / re-run.' % dec)
        finally:
            if rolled:
                if self._move(idx, slot, rolled, RESTORE_MODE, respond):
                    self._pause(rolled / float(PARK_SPEED) + 1.0)
                    respond('rc522: lane restored (%d mm)' % rolled)
                else:
                    respond('rc522: WARNING - restore feed rejected, lane is '
                            '%d mm short. Re-seat the spool or run a load.'
                            % rolled)

    def _read_attributed_ntag(self, idx, slot, uid, respond):
        """Read the attributed NTAG's pages while its selected UID is stable.

        Firmware identify can describe another card on the shared reader.
        Read the raw pages once, with a UID check on either side, instead.
        """
        self._read_unread = False
        before, _ = self._rc_stable_uid(idx, slot, respond)
        if before != uid:
            respond('rc522: result pending: 标签已变化或暂未读到，可重试识别；未保存')
            return 0
        data = self._rc_read_userdata(idx, slot, respond=None)
        if not data:
            self._read_unread = True
            respond('rc522: result pending: 标签数据暂未读到，可重试识别或手动选择耗材')
            return 0
        if self._rc_read_uid(idx, slot, respond) != uid:
            respond('rc522: result pending: 读取时标签已变化，可重试识别；未保存')
            return 0
        if getattr(self, '_defer_identity_commit', False):
            self._pending_identity = {'kind': 'ntag', 'idx': idx, 'slot': slot,
                                      'uid': uid, 'data': data}
            respond('rc522: NTAG content read; waiting for measured return')
            return 0
        return self._ingest_attributed_ntag(idx, slot, uid, data, respond)

    def _ingest_attributed_ntag(self, idx, slot, uid, data, respond):
        own = self._anycubic_decode(data)
        if own and (own.get('material') or '').strip():
            color = own['color']
            res = {'type': own['material'], 'brand': own['brand'],
                   'sku': own['sku'], 'color': [int(color[i:i + 2], 16)
                                               for i in (0, 2, 4)],
                   'subtype': '', 'code': 0}
            self._note_fmt(idx, slot, 'anycubic')
            _, prebound = self.ace._spool_by_sku(uid)
            self.ace._v2_store_filament_read(idx, slot, res, uid=uid,
                                            prebound=prebound,
                                            fmt='anycubic')
            self._note_uid(idx, slot, uid)
            self._bind_uid(idx, slot, uid, respond, unbind=prebound is not None)
            respond('rc522: anycubic tag: %s #%s (UID %s)'
                    % (own['material'], color, uid))
        else:
            identity = self._openspool_decode(data)
            self._note_fmt(idx, slot, 'openspool' if identity else 'unknown')
            self._note_uid(idx, slot, uid)
            self._finish_present(idx, slot, uid, identity, respond)
        return 0

    def _read_card(self, idx, slot, respond, reject_uid='', expected_uid='',
                   allow_nudges=True):
        """UID-first ingest of a card sitting in the field: stable UID ->
        identify harvest (Anycubic identity, or the Bambu UID sentinel) ->
        NDEF (OpenSpool). The UID bind runs LAST so it wins over the
        identity ingest's own sku-bind attempt - a factory Anycubic sku is
        per-ARTICLE and must never be the binding key.
        reject_uid = the neighbour's known card: a read that resolves to
        it stores nothing (the shared antenna pair can hand us the
        neighbour's tag; a stored one would be a wrong identity).
        Returns the units the lane was nudged forward (callers add them to
        their depth/restore accounting)."""
        if expected_uid and len(expected_uid) == 14:
            if expected_uid == reject_uid:
                self._neighbour_blocked_notice(idx, slot, reject_uid)
                return 0
            return self._read_attributed_ntag(idx, slot, expected_uid, respond)
        ace = self.ace
        uid = ''
        os = None
        moved = 0
        # Cleared here, set by _finish_present when a card answered the
        # sweep but yielded neither UID nor identity - the transport sweep
        # then reports 'unread' so the insert handler can retry ONCE from
        # the parked position (such a read typically succeeds on a second
        # pass).
        self._read_unread = False
        for attempt in range(3):
            # Retry the whole read while a card still answers: the reads
            # come back corrupted in bursts (a neighbour's firmware
            # identify on the shared reader bus) and a second try seconds
            # later reads clean.
            if attempt:
                if not allow_nudges:
                    break
                self._pause(1.5)
                self._wait_fw_identify(idx)
                # A retry means the reads were bad HERE: change the
                # position first (a SELECT that still answers proves
                # nothing about page reads). Retry 2 goes one step DEEPER; retry 3 goes BACK
                # to just before the SELECT hit point: a weakly coupled
                # tag answers its first SELECT already near the field
                # centre, so the centering + a deeper step only carry it
                # away. `moved` is signed; callers add it.
                if attempt == 1:
                    if self._move(idx, slot, CENTER_MM, SEARCH_MODE, respond):
                        self._pause(CENTER_MM / float(PARK_SPEED) + 0.5)
                        moved += CENTER_MM
                else:
                    back = moved + CENTER_MM * 2
                    if back > 0 and self._move(idx, slot, back, 1, respond):
                        self._pause(back / float(PARK_SPEED) + 0.5)
                        moved -= back
                sel = self._rc_select(idx, slot)
                respond('rc522: read retry %d%s'
                        % (attempt + 1, '' if sel else ' (identify only)'))
                if not sel:
                    # No SELECT is not "no card": a card the firmware
                    # already talked to sits HALTed and ignores our REQA,
                    # only the firmware identify (its own activation) wakes
                    # it - and for MIFARE that identify IS the read (UID
                    # sentinel).
                    res = self._identify(idx, slot)
                    if self._answered(res):
                        break
                    res = None
                    continue
            # DEBUG=1 used to stop at the sweep: the read path never
            # handed `respond` down, so every raw step of a failing read
            # stayed invisible.
            uid, _detail = self._rc_stable_uid(idx, slot, respond)
            # Read a single-size UID and authenticate before identify: the
            # firmware's identify can HALT a MIFARE card, after which its
            # REQA-based SELECT will not wake it. Never accept the known
            # neighbour's UID on the shared antenna.
            if not uid:
                try:
                    candidate = self._selected_single_uid(idx, slot)
                    if expected_uid and candidate and candidate != expected_uid:
                        respond('rc522: read failed - card UID changed before '
                                'authentication; nothing stored')
                        return moved
                    if candidate and candidate != reject_uid:
                        verified = self._read_bambu_classic(idx, slot, candidate)
                        if verified:
                            self._ingest_bambu(idx, slot, candidate, verified,
                                               respond)
                            return moved
                except Exception as e:
                    logging.info('[multiACE] [rc522] Bambu pre-identify read '
                                 'failed: %s', e)
            res = self._identify(idx, slot)
            if respond and self._debug:
                respond('rc522[dbg] identify: %s'
                        % ('answered' if self._answered(res) else 'nothing'))
            if self._answered(res):
                break
            os = self._read_openspool(idx, slot, respond)
            if os and os.get('material') and uid:
                break
            res = None
        if expected_uid:
            sent_expected = (self._sentinel_uid(res)
                             if self._answered(res) and self._is_uid_read(res)
                             else '')
            if ((uid and uid != expected_uid) or
                    (sent_expected and sent_expected != expected_uid) or
                    (uid or sent_expected) != expected_uid):
                respond('rc522: read failed - card UID changed or is '
                        'unavailable; nothing stored')
                return moved
            # A generic firmware identity cannot be cryptographically tied
            # to our earlier UID in a shared field. Only authenticated Bambu
            # data (returned above) or the UID-only sentinel is safe here.
            if not self._is_uid_read(res):
                respond('rc522: read failed - generic tag identity is not '
                        'verified against this UID; nothing stored')
                return moved
        if reject_uid:
            _sent = (self._sentinel_uid(res)
                     if self._answered(res) and self._is_uid_read(res)
                     else '')
            if reject_uid in (uid, _sent):
                respond('rc522: the read resolved to the neighbour\'s card '
                        '%s - not this slot\'s tag, nothing stored'
                        % reject_uid)
                self._neighbour_blocked_notice(idx, slot, reject_uid)
                return moved
        if self._answered(res) and self._is_uid_read(res):
            sent = self._sentinel_uid(res)
            if uid and len(uid) == 14:
                # OUR tunnel read a full 7-byte UID with both BCCs
                # verified: this is an NTAG, not a MIFARE card, whatever
                # the firmware says. Its sentinel means it failed to
                # decode the pages (the cascade artefact above). Decode
                # the pages ourselves.
                if sent and sent != uid:
                    logging.info('[multiACE] [rc522] ACE %d slot %d: '
                                 'firmware sentinel UID %s disagrees with '
                                 'the tunnel UID %s - tunnel wins (7 bytes, '
                                 'BCC-verified)', idx, slot, sent, uid)
                respond('rc522: firmware read only a UID for card %s - '
                        'decoding the pages here' % uid)
                own = self._read_own_anycubic(idx, slot, respond)
                if own is not None:
                    res = own            # the Anycubic branch below ingests
                else:
                    if os is None:
                        os = self._read_openspool(idx, slot, respond)
                    res = None           # UID-only / OpenSpool tail below
            else:
                # MIFARE (Bambu): raw page reads fail (NAK junk), so the
                # firmware sentinel is the only UID source; our own read
                # stays first whenever it exists.
                uid = uid or sent
                if self._is_truncated_uid(uid):
                    respond('rc522: firmware reported a truncated 7-byte '
                            'UID (%s, cascade level 1 only) - kept as a '
                            'partial key' % uid)
                    logging.info('[multiACE] [rc522] ACE %d slot %d: '
                                 'truncated cascade UID %s from the '
                                 'firmware sentinel, no own read possible '
                                 '(MIFARE) - partial key', idx, slot, uid)
                # 60O's identify may contain defaults even when auth failed,
                # and has incorrect colour offsets. Only raw authenticated
                # blocks can establish Bambu material and colour.
                verified = None
                if uid and not self._is_truncated_uid(uid):
                    try:
                        verified = self._read_bambu_classic(idx, slot, uid)
                    except Exception as e:
                        logging.info('[multiACE] [rc522] Bambu read failed: %s', e)
                if verified:
                    self._ingest_bambu(idx, slot, uid, verified, respond)
                    return moved
                if getattr(self, '_defer_identity_commit', False):
                    self._pending_identity = {'kind': 'uid_only', 'idx': idx, 'slot': slot, 'uid': uid}
                    respond('rc522: UID read; waiting for measured return')
                    return moved
                respond('rc522: card UID %s (MIFARE identity not verified)'
                        % (uid or '?'))
                self._forget_identity(idx, slot, uid)
                self._note_fmt(idx, slot, 'mifare')
                self._note_uid(idx, slot, uid)
                self._bind_uid(idx, slot, uid, respond)
                return moved
        if self._answered(res):
            if (res.get('type') or '').strip():
                _nat = getattr(ace, '_native_tag_format',
                               lambda _s: '')(res.get('sku'))
                respond('rc522: %s tag: %s %s (sku %s, UID %s)'
                        % (_nat or 'anycubic',
                           res.get('type', ''), res.get('brand', ''),
                           ('-' if _nat else (res.get('sku') or '-')),
                           uid or '?'))
                if self._dump:
                    # Layout collection: raw pages next to the parsed
                    # identity (the Anycubic decoder/writer reference).
                    try:
                        self._rc(idx, slot, 6)       # re-SELECT for raw ops
                        self._rc_read_userdata(idx, slot, respond=respond)
                    except Exception as e:
                        respond('rc522: raw dump failed: %s' % e)
                # TWO codes on one tag. UID first, side-effect-free: a
                # table entry carrying the UID as its sku wins (OpenSpool /
                # MIFARE / UID-as-SKU spools - a V1 Pro reads those as sku
                # too, so both worlds bind the same entry). Otherwise the
                # sku binds (V1-compatible path) and the UID only LOGS its
                # no-match line for the web's card_uids adoption - it must
                # not release the binding the sku just made.
                _sid, _sp = (ace._spool_by_sku(uid) if uid
                             else (None, None))
                # Not necessarily an Anycubic tag since ACE2-Open V1.1.46O:
                # its on-chip decoder answers OpenSpool and friends through
                # this same reply, with the format name in the sku field
                # (ace.NATIVE_TAG_FORMATS). getattr so an older ace.py keeps
                # today's behaviour - neither file is in the bundle, so they
                # can skew.
                _fmt = (getattr(ace, '_native_tag_format', lambda _s: '')(
                    res.get('sku')) or 'anycubic')
                self._note_fmt(idx, slot, _fmt)
                if _sp is not None:
                    ace._v2_store_filament_read(idx, slot, res, uid=uid,
                                                prebound=_sp, fmt=_fmt)
                    self._note_uid(idx, slot, uid)
                    self._bind_uid(idx, slot, uid, respond)
                else:
                    ace._v2_store_filament_read(idx, slot, res, uid=uid,
                                                fmt=_fmt)
                    self._note_uid(idx, slot, uid)
                    self._bind_uid(idx, slot, uid, respond, unbind=False)
                return moved
        self._note_fmt(idx, slot, 'openspool' if (os and os.get('material'))
                       else ('unknown' if uid else ''))
        self._note_uid(idx, slot, uid)
        self._finish_present(idx, slot, uid, os, respond)
        return moved

    def _note_fmt(self, idx, slot, fmt):
        """Tag format of the last read at (idx, slot) - anycubic /
        openspool / mifare / unknown - for the web."""
        if not fmt:
            return
        reg = getattr(self.ace, '_rc_last_fmt', None)
        if reg is None:
            reg = self.ace._rc_last_fmt = {}
        reg[(idx, slot)] = fmt

    def _forget_identity(self, idx, slot, uid):
        """A read found card `uid` without an identity: drop the identity
        an earlier read cached for this slot when it belongs to ANOTHER
        card, or the previous spool keeps showing. The insert sweep drops
        it before it starts, a manual read did not. The same card (a page
        decode that failed this time) and a cached read without a UID keep
        their identity; so does a truncated cascade UID, which cannot be
        compared."""
        try:
            if not uid or self._is_truncated_uid(uid):
                return
            cache = (getattr(self.ace, '_v2_filament_info_per_ace', None)
                     or {}).get(idx)
            old = (cache or {}).get(slot)
            old_uid = str((old or {}).get('uid') or '').replace(
                ':', '').strip().upper()
            if old_uid and old_uid != str(uid).strip().upper():
                cache.pop(slot, None)
                self.ace._persist_tag_reads()
        except Exception:
            pass

    def _note_uid(self, idx, slot, uid):
        """Remember the UID last read at (idx, slot) on the ace object
        (this reader is created per op). The insert sweep of the
        NEIGHBOUR slot uses it: the two slots of a pair share one antenna,
        and a parked neighbour tag at the field edge answers
        intermittently - it did not answer at sweep start, then stopped
        the sweep mid-way as a "found" tag. Knowing the neighbour's UID up front makes it
        a blocker from the first unit on."""
        context = getattr(self, '_insert_checked_context', None)
        if (getattr(self, '_insert_checked_guard', None) is not None and uid
                and context is not None and (idx, slot) == context[:2]):
            old = (getattr(self.ace, '_rc_last_uid', None) or {}).get((idx, slot))
            if old and str(old).upper() != str(uid).upper():
                # This reader has just attributed the new UID to this lane.
                # Keep its epoch; old queued requests still carry the old UID.
                (getattr(self.ace, '_v2_filament_info_per_ace', None) or {}).get(idx, {}).pop(slot, None)
            self._insert_checked_uid = uid
        if not uid:
            return
        reg = getattr(self.ace, '_rc_last_uid', None)
        if reg is None:
            reg = self.ace._rc_last_uid = {}
        reg[(idx, slot)] = uid
        if (context is not None and (idx,slot)==context[:2]
                and getattr(self,'_insert_checked_guard',None) is not None
                and not getattr(self,'_single_auto',False)):
            if not hasattr(self.ace,'_single_verified_uid'):
                self.ace._single_verified_uid={}
            self.ace._single_verified_uid[(idx,slot)]=(context[2],self.ace._location_identity(idx),context[3],uid)
        try:
            self.ace._persist_tag_reads()
        except Exception:
            pass

    def _slot_presence(self, idx, slot):
        """The device's PRESENCE word for (idx, slot) - the 'status' field
        (FILAMENT_STATES: empty / unknown / ready / identifying), or None
        when unknown. NOT _v2_get_slot_status: that is 'slot_status', the
        LANE MOTOR state (ready / feeding / preloading ...), and an empty
        lane idles at 'ready' there. Every neighbour test in this file
        used to read the motor word, so an EMPTY neighbour never counted
        as empty: the sweep ran the rotation test against nobody, rotated
        an empty lane 200 units and only the second opinion saved the
        read."""
        try:
            slot = int(slot)
            info = self.ace._info_per_ace.get(idx) or {}
            for s in info.get('slots') or []:
                if s.get('index') == slot:
                    return s.get('status')
        except Exception:
            pass
        return None

    def _neighbour_empty(self, idx, slot):
        """True when the neighbour bay is KNOWN empty (device presence
        word); False when occupied or unknown - unknown stays conservative,
        a card in the field could then still be the neighbour's."""
        st = self._slot_presence(idx, slot ^ 1)
        return st is not None and self.ace._is_empty_status(str(st))

    def _neighbour_blocked_notice(self, idx, slot, uid):
        """The slot's tag could not be told from the neighbour's card and
        the neighbour could not be rotated away: tell the user (web +
        log) what to do. Lives in ace.py (i18n); getattr = deploy skew."""
        fn = getattr(self.ace, '_tag_read_blocked_by_neighbour', None)
        if fn is None:
            return
        try:
            fn(idx, slot, uid)
        except Exception:
            logging.exception('[multiACE] [rc522] neighbour notice')

    def _known_neighbour_uid(self, idx, slot):
        """UID of the tag last read in the neighbour slot, '' when the
        neighbour is empty or was never read this boot."""
        ace = self.ace
        neighbour = slot ^ 1
        if self._neighbour_empty(idx, slot):
            return ''
        return (getattr(ace, '_rc_last_uid', None) or {}).get(
            (idx, neighbour), '')

    def _neighbour_occupied(self, idx, slot):
        return not self._neighbour_empty(idx, slot)

    def _rotation_test(self, idx, slot, uid, respond, probe_mode=SEARCH_MODE):
        """Attribute only repeated UID departures correlated with this lane.

        The paired lane must stay ready with unchanged move counters. Each
        target test move is reversed and encoder residual checked. Unknown,
        collision, changed UID or failed rollback never proves ownership.
        """
        if probe_mode not in (SEARCH_MODE, RESTORE_MODE):
            raise ValueError('invalid attribution direction')
        return_mode = RESTORE_MODE if probe_mode == SEARCH_MODE else SEARCH_MODE
        def trace(stage, **data):
            try:
                logging.info('[multiACE] attribution diagnostic %s %s', stage, data)
            except Exception:
                pass
        def stable(stage, cycle, expected, residual=None):
            # No additional motion: retain existing attribution comparisons.
            reactor = self.ace.reactor
            deadline = reactor.monotonic() + 12.0
            count = 0
            original_pause = self._pause
            class StaticTimeout(Exception):
                pass
            def check():
                self._insert_guard()
                owner = getattr(self, '_manual_auth_owner', None)
                if owner is not None and not owner.route_valid():
                    raise MotionUncertain('attribution owner changed or cancelled')
                actions = getattr(self.ace, '_slot_actions', None)
                if (actions is not None and getattr(actions, 'cancel', False) and
                    getattr(self.ace, '_tag_read_busy', False) and
                    getattr(self.ace, '_tag_op_kind', None) == 'read' and
                    (getattr(self.ace, '_tag_op_ace', None), getattr(self.ace, '_tag_op_slot', None)) == (idx, slot)):
                    raise MotionUncertain('attribution cancelled')
                if reactor.monotonic() >= deadline:
                    raise StaticTimeout()
            def bounded_pause(seconds):
                end = min(deadline, reactor.monotonic() + seconds)
                while reactor.monotonic() < end:
                    check()
                    original_pause(min(.05, end - reactor.monotonic()))
                check()
            self._pause = bounded_pause
            try:
                while True:
                    check()
                    neighbour_started = reactor.monotonic()
                    current = self._neighbour_signature(idx, slot)
                    neighbour_elapsed = reactor.monotonic() - neighbour_started
                    check()
                    started = reactor.monotonic()
                    observed = self._observed_expected_uid(idx, slot, uid) if current == expected else None
                    elapsed = reactor.monotonic() - started
                    trace(stage, cycle=cycle, expected_neighbour=expected, neighbour=current,
                          expected_uid=uid, observed_uid=observed,
                          residual=residual, encoder=self._encoder_ledger.get((idx, slot), 0),
                          neighbour_seconds=neighbour_elapsed, observation_seconds=elapsed)
                    check()
                    if current != expected or (observed and observed != uid):
                        return False
                    count = count + 1 if observed == uid else 0
                    if count >= 2:
                        return True
                    self._pause(.15)
            except StaticTimeout:
                trace(stage + '_timeout', cycle=cycle, expected_neighbour=expected,
                      expected_uid=uid, residual=residual, consecutive=count)
                return False
            finally:
                self._pause = original_pause
        if not uid:
            respond('rc522: card UID unavailable; attribution is ambiguous')
            return None
        neighbour = self._neighbour_signature(idx, slot)
        if neighbour is None:
            respond('rc522: neighbour status unavailable or moving; '
                    'attribution is ambiguous')
            return None
        trace('baseline', ace=idx, slot=slot, expected_uid=uid, neighbour=neighbour)
        start_encoder = self._encoder_ledger.get((idx, slot), 0)
        for cycle in range(2):
            if not stable('before', cycle, neighbour):
                respond('rc522: card or neighbour unstable before attribution')
                return None
            before = self._encoder_ledger.get((idx, slot), 0)
            if not self._move_and_confirm(idx, slot, START_PROBE_MM,
                                          probe_mode, respond):
                return None
            forward_delta = self._encoder_ledger.get((idx, slot), 0) - before
            trace('forward', cycle=cycle, encoder_delta=forward_delta)
            seen = []
            try:
                for _ in range(3):
                    current = self._neighbour_signature(idx, slot)
                    trace('during', cycle=cycle, expected_neighbour=neighbour, neighbour=current)
                    if current != neighbour:
                        self._motion_uncertain = ('ACE %d slot %d: neighbour '
                            'changed during attribution; target may be '
                            'displaced; inspect both lanes' % (idx, slot))
                        respond('rc522: WARNING - ' + self._motion_uncertain)
                        raise MotionUncertain(self._motion_uncertain)
                    seen.append(self._observed_uid(idx, slot))
                    trace('during_uid', cycle=cycle, observed_uid=seen[-1])
                    self._pause(0.15)
            finally:
                if not self._motion_uncertain:
                    if not self._move_and_confirm(idx, slot, START_PROBE_MM,
                                                  return_mode, respond):
                        self._motion_uncertain = ('ACE %d slot %d: attribution '
                            'return rejected after %d mm probe; inspect lane'
                            % (idx, slot, START_PROBE_MM))
                        respond('rc522: WARNING - ' + self._motion_uncertain)
                        raise MotionUncertain(self._motion_uncertain)
            residual = self._encoder_ledger.get((idx, slot), 0) - before
            trace('reverse', cycle=cycle, encoder_delta=residual - forward_delta, residual=residual, encoder=self._encoder_ledger.get((idx, slot), 0), seen=seen)
            if abs(residual) > MAX_ENCODER_RESIDUAL_MM:
                self._motion_uncertain = ('ACE %d slot %d: attribution '
                    'round-trip encoder residual %+d mm; inspect lane'
                    % (idx, slot, residual))
                respond('rc522: WARNING - ' + self._motion_uncertain)
                raise MotionUncertain(self._motion_uncertain)
            if not all(x == '' for x in seen):
                respond('rc522: card did not leave cleanly; attribution ambiguous')
                return None
            if not stable('after', cycle, neighbour, residual):
                respond('rc522: card or neighbour did not return stably; '
                        'attribution ambiguous')
                return None
        total = self._encoder_ledger.get((idx, slot), 0) - start_encoder
        if abs(total) > MAX_ENCODER_RESIDUAL_MM:
            self._motion_uncertain = ('ACE %d slot %d: aggregate attribution '
                'encoder residual %+d mm; inspect lane' % (idx, slot, total))
            respond('rc522: WARNING - ' + self._motion_uncertain)
            raise MotionUncertain(self._motion_uncertain)
        return 'ours'

    def _whose_card(self, idx, slot, uid, respond):
        """Attribute a card that answers while the neighbour bay is
        occupied. Returns (verdict, lane_moved): 'ours'; 'cleared' = it was
        the neighbour's and has been rotated out of the field; 'neighbour'
        = the rotation test says so, field still occupied; None = motor
        refused. lane_moved = our lane was left START_PROBE_MM further in.

        Positive evidence first: when the neighbour may be rotated, rotate
        IT - a neighbour's tag leaves with its lane, ours stays in the
        field. The rotation test moves OUR lane and reads any missed SELECT
        as 'ours', but a parked neighbour tag at the field edge flickers,
        so it could hand the neighbour's card to this slot. It stays only
        as the fallback for a neighbour that may not be moved."""
        if self._neighbour_movable(idx, slot):
            if self._clear_neighbour(idx, slot, respond):
                respond('rc522: card %s left the field with the neighbour '
                        'lane - it was the neighbour\'s tag' % (uid or '?'))
                return 'cleared', False
            if self._own_after_clear(idx, slot, uid, respond):
                return 'ours', False
        verdict = self._rotation_test(idx, slot, uid, respond)
        return verdict, False

    def _neighbour_movable(self, idx, slot):
        """May the neighbour lane be rotated? Only an IDLE, unloaded
        neighbour: never one feeding a head (its filament sits in the
        bowden/toolhead), never one in motion, never during a print."""
        ace = self.ace
        neighbour = slot ^ 1
        try:
            ps = ace.printer.lookup_object('print_stats', None)
            if ps is not None and (getattr(ps, 'state', '') or '').lower() \
                    in ('printing', 'paused'):
                return False
            if ace._get_heads_for_ace_slot(idx, neighbour):
                return False
            st = ace._v2_get_slot_status(idx, neighbour)
            if st in V2_ACTIVE_MOTION_STATES:
                return False
        except Exception:
            return False
        return True

    def _clear_neighbour(self, idx, slot, respond):
        """Fail closed until neighbour displacement can be restored safely."""
        self._clear_last_moved = 0
        respond('rc522: neighbour cannot be moved safely by this operation; '
                'leaving it untouched')
        return False

    def _own_after_clear(self, idx, slot, uid, respond):
        """The second opinion after a FAILED clear: the neighbour lane was
        rotated its whole CLEAR_MAX_MM budget and the SAME card still
        answers. A neighbour's tag cannot survive that - its read window
        is ~40 units wide (PROBE_OFFSETS) and it just moved 200 - so the
        card sits on OUR spool and moved nowhere because our lane stood
        still. The rotation test alone gets this wrong on a flange tag
        with a field wider than START_PROBE_MM. Only
        a clear that could not rotate at all (loaded/busy neighbour, 0
        units) leaves the question open. Returns True for 'ours'."""
        if getattr(self, '_clear_last_moved', 0) < CLEAR_MAX_MM:
            return False
        if self._card_answers(idx, slot) is not True:
            return False
        uid_now = self._halted_uid or self._rc_stable_uid(idx, slot)[0]
        if not uid_now or (uid and uid_now != uid):
            return False
        respond('rc522: card %s still answers after the neighbour moved %d '
                'units - it is this slot\'s own tag' % (uid_now, CLEAR_MAX_MM))
        return True

    def _neighbour_inserting(self, idx, slot):
        """Is the neighbour lane in its own firmware insert procedure
        (identifying / preloading / feeding)? Its tag is then moving
        through the shared field."""
        try:
            pres = str(self._slot_presence(idx, slot ^ 1) or '').lower()
            st = str(self.ace._v2_get_slot_status(idx, slot ^ 1) or '')
        except Exception:
            return False
        return pres == 'identifying' or st in V2_ACTIVE_MOTION_STATES

    def _wait_neighbour_settled(self, idx, slot, respond):
        """Hold (bounded) while the neighbour is mid-insert. Its own
        procedure (pull-in + identify) completes within seconds; mid-sweep
        nothing of ours interrupts it - the abort we queue for that slot
        only runs after THIS read finishes. Once it has settled, a card
        in the field can be attributed. Returns True when it waited."""
        if not self._neighbour_inserting(idx, slot):
            return False
        respond('rc522: neighbour slot %d is being inserted - waiting for '
                'it to settle before deciding whose card answers'
                % self.ace._disp(slot ^ 1))
        t0 = self.ace.reactor.monotonic()
        while (self._neighbour_inserting(idx, slot)
               and self.ace.reactor.monotonic() - t0 < NEIGHBOUR_SETTLE_S):
            self._pause(0.5)
        return True

    def _bind_uid(self, idx, slot, uid, respond, unbind=True):
        """Bind the slot by card UID - the uniform per-chip key. On no
        table hit, _spool_bind_by_tag emits the load-bearing 'matches no
        table entry' line the web backend adopts from via Spoolman
        card_uids. unbind=False when another code of the same tag
        already decided the binding."""
        if not uid:
            return
        ace = self.ace
        try:
            bound = ace._spool_bind_by_tag(idx, slot, uid, unbind=unbind)
        except TypeError:
            bound = ace._spool_bind_by_tag(idx, slot, uid)   # older ace.py
        if bound is not None:
            respond('rc522: bound to spool %s' % ace._spool_label(bound))

    def _read_own_anycubic(self, idx, slot, respond):
        """Decode the Anycubic layout from the NTAG user pages with OUR
        reader (the writer's own verify decoder), for a tag the firmware
        identify could not decode. Returns an identify-shaped dict
        (type / brand / sku / color / subtype) or None."""
        try:
            self._rc(idx, slot, 6)               # re-SELECT for raw ops
            pages = self._rc_read_userdata(idx, slot, respond=None)
            dec = self._anycubic_decode(pages)
        except Exception as e:
            respond('rc522: own page decode failed: %s' % e)
            return None
        if not dec or not (dec.get('material') or '').strip():
            return None
        col = dec.get('color') or ''
        try:
            rgb = [int(col[0:2], 16), int(col[2:4], 16),
                   int(col[4:6], 16)] if len(col) == 6 else [0, 0, 0]
        except ValueError:
            rgb = [0, 0, 0]
        return {'type': dec['material'], 'brand': dec.get('brand', ''),
                'sku': dec.get('sku', ''), 'color': rgb, 'subtype': '',
                'code': 0}

    def _read_openspool(self, idx, slot, respond):
        """Read the NTAG user pages of a present foreign card and decode
        OpenSpool. Logs the raw pages when DUMP is on (schema work).
        Returns the identity dict or None (not OpenSpool / MIFARE / empty)."""
        pages = self._rc_read_userdata(
            idx, slot,
            respond=(respond if (self._dump or self._debug) else None))
        return self._openspool_decode(pages)

    def _finish_present(self, idx, slot, uid, os, respond):
        """A foreign card was SELECTed (stage 2). With OpenSpool data decoded
        (material/color/brand from the NDEF), feed the FULL identity through
        the normal ingest (identity + display + bind + Spoolman kick), sku =
        the card UID so card_uids binding still works. Without OpenSpool,
        bind by UID alone (_spool_bind_by_tag: local table match or the
        'matches no table entry' line the web adopts from). No identity is
        invented when neither is available."""
        ace = self.ace
        if os and os.get('material'):
            col = os.get('color') or ''
            try:
                rgb = [int(col[0:2], 16), int(col[2:4], 16),
                       int(col[4:6], 16)] if len(col) == 6 else [0, 0, 0]
            except ValueError:
                rgb = [0, 0, 0]
            res = {'type': os['material'], 'color': rgb,
                   'brand': os.get('vendor', ''), 'sku': uid or '',
                   'subtype': ''}
            respond('rc522: OpenSpool tag: %s %s #%s (UID %s)'
                    % (os['material'], os.get('vendor') or '-',
                       col or '------', uid or '?'))
            ace._v2_store_filament_read(idx, slot, res, uid=uid,
                                        fmt='openspool')
            return
        if not uid:
            respond('rc522: a card is present but its UID could not be '
                    'read (raw RC522 read returned nothing) - the register '
                    'setup may need tuning on this firmware.')
            self._read_unread = True
            return
        self._forget_identity(idx, slot, uid)
        bound = ace._spool_bind_by_tag(idx, slot, uid)
        if bound is not None:
            respond('rc522: bound to spool %s' % ace._spool_label(bound))
        else:
            respond('rc522: UID %s not in the table (a Spoolman spool with '
                    'this card_uid will adopt on the next sweep)' % uid)
