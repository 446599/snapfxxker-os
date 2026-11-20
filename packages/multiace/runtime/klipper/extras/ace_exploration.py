"""One-shot, lower-assurance 50 mm / 10 mm/s exploratory trial.

Uses real cached Klipper pin/public states and MCU/ACE connection data. Does
NOT assert guaranteed clearance, fresh physical pin sampling, validated buffer
polarity or measured worst-case braking. If the tip is already near the head
and STOP fails, the remaining part of the 50 mm command can still advance.
No default authorization is shipped. A short-lived, single-use authorization
file must be created by the supervising task for this exact trial.
"""
from dataclasses import dataclass
import json
import math
import os
import stat
import time

from .ace_guarded_scan import GuardedContinuousScan, KlipperScanIO
from .ace_async_uid import Context

AUTHORIZATION_PATH = '/home/lava/printer_data/controlled-exploration/authorization.json'
SLOT3_AUTHORIZATION_PATH = '/home/lava/printer_data/controlled-exploration/authorization-slot2.json'
HISTORY = {
    'date': '2026-11-06', 'ace_index': 0, 'slot': 3, 'head': 3,
    'commands_after_sensor_became_empty_mm': [500, 199],
    'last_reported_decoder_mm': -185,
    'confirmation_source': 'Current-session confirmation of unchanged filament since previous retraction',
    'limit': 'Historical commands and last-command encoder do not establish exact current tip position.'}


def finite(x):
    return type(x) in (int, float) and math.isfinite(x)


def bit(value):
    # Existing firmware API represents logical states as bool or integer 0/1.
    # Do not guess strings, other integers or electrical pin polarity.
    if type(value) is bool or type(value) is int and value in (0, 1):
        return bool(value)
    return None


class OneShotAuthorization:
    """An operation permission, not a claim that sensor/travel facts are true.

    Durable O_EXCL consumption prevents reuse after a process restart. No
    G-code argument can manufacture an authorization or extend its expiry.
    """
    def __init__(self, path=AUTHORIZATION_PATH, wall_clock=time.time):
        self.path, self.clock = path, wall_clock
        self.consumed = False
        info = os.lstat(path)
        directory = os.lstat(os.path.dirname(os.path.abspath(path)))
        if (not stat.S_ISREG(info.st_mode) or info.st_size > 4096
                or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600
                or not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.geteuid()
                or stat.S_IMODE(directory.st_mode) != 0o700):
            raise ValueError('authorization requires runtime-owned 0700 directory and 0600 regular file')
        with open(path, 'r', encoding='utf-8') as handle:
            self.record = json.load(handle)
        self.check()

    def check(self):
        r, now = self.record, self.clock()
        if (not isinstance(r, dict) or r.get('kind') != 'one_shot_ace_exploration'
                or any(type(r.get(k)) is not int for k in ('ace_index','slot','length_mm','speed_mm_s'))
                or (r['ace_index'],r['slot'],r['length_mm'],r['speed_mm_s']) not in ((0,3,50,10),(0,2,50,10))
                or not isinstance(r.get('authorization_id'), str) or not r['authorization_id'].strip()
                or not finite(r.get('expires_at_utc')) or not 0 < r['expires_at_utc']-now <= 600):
            raise ValueError('missing, expired or wrong-scope one-shot authorization')
        if not self.consumed and os.path.exists(self.path+'.consumed'):
            raise ValueError('this one-shot authorization was already consumed')

    def consume(self):
        self.check()
        if self.consumed:
            raise ValueError('one-shot exploration cannot send another movement')
        fd = os.open(self.path+'.consumed', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, (self.record['authorization_id']+'\n').encode('utf-8'))
            os.fsync(fd)
        finally:
            os.close(fd)
        # Persist the directory entry too; failure blocks movement even if a
        # marker was created. A process restart must not silently grant reuse.
        parent_fd = os.open(os.path.dirname(os.path.abspath(self.path)), os.O_RDONLY | getattr(os,'O_DIRECTORY',0))
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        self.consumed = True  # Burn before any possibly accepted motion send.


@dataclass(frozen=True)
class ExplorationPlan:
    # Experiment limits and watchdogs, NOT verified safety distances/timings.
    session: str
    ace_index: int = 0
    slot: int = 3
    head: int = 3
    length_mm: int = 50
    speed_mm_s: int = 10
    enable_rfid: bool = True
    allow_default_code_zero: bool = True
    max_reply_s: float = 1.0
    poll_interval_s: float = .05
    telemetry_max_age_s: float = 1.5
    stop_timeout_s: float = 1.0
    rest_timeout_s: float = 5.0
    minimum_rest_interval_s: float = .2
    move_ack_timeout_s: float = 1.0
    completion_grace_s: float = 2.0
    mcu_progress_timeout_s: float = 3.0

    def validate(self, now):
        if (not finite(now) or (self.ace_index,self.slot,self.head,self.length_mm,self.speed_mm_s) not in ((0,3,3,50,10),(0,2,2,50,10))
                or not isinstance(self.session,str) or not self.session):
            raise ValueError('only this ACE0/slot4, 50 mm at 10 mm/s one-shot trial is supported')


class ExistingMachineIO(KlipperScanIO):
    """Concrete current APIs, explicitly lower assurance than fresh-pin proof."""
    def __init__(self, reader, owner):
        super().__init__(reader)
        self.owner = owner
        self.mcu_counters = None
        self.mcu_progress_observed_at = None
        self.latest_observation = None

    def require_environment(self, plan, now):
        ace, printer = self.ace, self.printer
        if getattr(ace, '_ace_mode', None) != 'multi':
            raise ValueError('exploration supports only current multi mode')
        guard = getattr(ace,'_stop_guard',None)
        if guard is None or not callable(getattr(ace,'_latch_stop_fault',None)):
            raise ValueError('compatible STOP fault latch is required')
        guard.require_clear()
        if not isinstance(getattr(ace,'_connected_per_ace',None),dict) or ace._connected_per_ace.get(0) is not True:
            raise ValueError('ACE0 is not reported connected')
        current = getattr(ace,'_serials',{}).get(0)
        if current is None or getattr(current,'is_open',False) is not True:
            raise ValueError('ACE0 serial connection is unavailable')
        if self.transport_handle is None:
            self.transport_handle = current
        elif self.transport_handle is not current:
            raise ValueError('ACE connection changed during exploration')
        ps = printer.lookup_object('print_stats',None)
        if ps is None or getattr(ps,'state',None) not in ('standby','complete','cancelled'):
            raise ValueError('printer is not idle')
        web = printer.lookup_object('webhooks',None)
        web_status = web.get_status(now) if web is not None else None
        if not isinstance(web_status,dict) or web_status.get('state') != 'ready':
            raise ValueError('Klipper is not ready')
        if ace._get_heads_for_ace_slot(plan.ace_index,plan.slot):
            raise ValueError('target slot already feeds a loaded head')
        if (getattr(ace,'_exploration_owner',None) is not self.owner
                or getattr(ace,'_tag_read_busy',False) is not True):
            raise ValueError('exclusive host tag-operation ownership lost')
        if getattr(ace,'_insert_read_running',None):
            raise ValueError('insertion routine is active')
        if getattr(ace,'_insert_read_queue',None):
            raise ValueError('queued insertion routine would overlap exploration')

    def head(self, plan, now):
        sensor = self.printer.lookup_object('filament_motion_sensor e%d_filament' % plan.head,None)
        mcu = self.printer.lookup_object('mcu e%d' % plan.head,None)
        if sensor is None or mcu is None:
            return 'unknown', None
        raw = bit(getattr(sensor,'runout_buttun_state',None))
        public_data = sensor.get_status(now)
        public = bit(public_data.get('filament_detected')) if isinstance(public_data,dict) else None
        # Trigger is never suppressed for disagreement or missing telemetry.
        if raw is True or public is True:
            return 'triggered', None
        if raw is not False or public is not False:
            return 'unknown', None
        if getattr(mcu,'_is_shutdown',False) is True:
            return 'unknown', None
        state = mcu.get_status(now)
        stats = state.get('last_stats') if isinstance(state,dict) else None
        if not isinstance(stats,dict):
            return 'unknown', None
        counters = tuple(stats.get(k) for k in ('bytes_read','receive_seq'))
        if not all(type(x) is int and x >= 0 for x in counters) or not any(counters):
            return 'unknown', None
        if self.mcu_counters is not None and any(a < b for a,b in zip(counters,self.mcu_counters)):
            return 'unknown', None
        if self.mcu_counters is None or counters != self.mcu_counters:
            self.mcu_progress_observed_at = now
        self.mcu_counters = counters
        if now-self.mcu_progress_observed_at > plan.mcu_progress_timeout_s:
            return 'unknown', None
        self.latest_observation = {
            'raw_cached':raw, 'public_cached':public, 'host_observed_at':now,
            'mcu_counters':counters, 'mcu_progress_observed_at':self.mcu_progress_observed_at,
            'assurance':'Klipper cached raw/public plus MCU communication; not independent fresh pin sampling'}
        return 'clear', self.latest_observation


class ControlledExploration(GuardedContinuousScan):
    """One known-authorized experiment; no synthetic high-assurance proof."""
    def __init__(self, reader, authorization):
        plan = ExplorationPlan(authorization.record['authorization_id'], slot=authorization.record['slot'], head=authorization.record['slot'])
        super().__init__(reader,plan)
        self.authorization = authorization
        self.io = ExistingMachineIO(reader,self)
        self.buffer_baseline = None
        self.rf_context_provider = self._existing_rf_context
        self.rf_generation = 0
        self.cleanup_timer = None

    def _existing_rf_context(self, plan, now):
        serial = getattr(self.io.ace,'_serials',{}).get(0)
        # This is the cooperating host tag-operation claim, not a proof of
        # device-wide RF isolation: raw commands/firmware activity can bypass it.
        # report() explicitly keeps every UID an unconfirmed observation.
        return Context(plan.session,0,active=self.state=='scanning',stop_requested=self.stop_sent,
            connected=(serial is self.io.transport_handle and serial is not None
                       and getattr(serial,'is_open',False) is True),
            rf_exclusive=(getattr(self.io.ace,'_exploration_owner',None) is self
                          and getattr(self.io.ace,'_tag_read_busy',False) is True))

    def _head_ok(self, now):
        verdict, observation = self.io.head(self.plan,now)
        if verdict != 'clear':
            raise ValueError('toolhead cached raw/public or MCU state '+verdict)

    def _check(self, now):
        self.plan.validate(now)
        if not self.move_sent:
            self.authorization.check()
        self.io.require_environment(self.plan,now)
        self._head_ok(now)
        self._rf_context(now)

    def _check_buffer(self, result, now):
        fields = result.get('fields')
        # Field 1 is the observed GET_KEY_STATE container. No bit's polarity
        # or meaning is asserted. The whole raw word is a change-only trip.
        word = fields.get('1') if isinstance(fields,dict) else None
        if type(word) is not int or not 0 <= word < 1 << 32:
            raise ValueError('GET_KEY_STATE raw baseline unavailable')
        if self.buffer_baseline is None:
            self.buffer_baseline = word
        elif word != self.buffer_baseline:
            raise ValueError('raw GET_KEY_STATE changed; supplementary stop, polarity unknown')

    def _command_window_seconds(self):
        return self.plan.length_mm/self.plan.speed_mm_s+self.plan.move_ack_timeout_s+self.plan.completion_grace_s

    def _move_ack_seconds(self):
        return self.plan.move_ack_timeout_s

    def _launch(self, now):
        try:
            self.authorization.consume()
        except Exception as exc:
            self._block('one-shot authorization cannot be consumed: '+str(exc))
            return
        super()._launch(now)
        if self.move_sent and not self.stop_sent:
            # At most two RF requests between safety queries. This allows a
            # six-request BCC pass inside a short trial without extending it.
            self.cycle = ['get_key_state','__rf__','__rf__','get_status',
                          '__rf__','__rf__','get_key_state','get_feed_info',
                          '__rf__','__rf__']
            self.cycle_index = 0

    def start(self):
        ace = self.io.ace
        if getattr(ace,'_tag_read_busy',False) or getattr(ace,'_exploration_owner',None) is not None:
            self._block('another tag operation or exploration is active')
            return self.report()
        ace._tag_read_busy = True
        ace._exploration_owner = self
        result = super().start()
        self.cleanup_timer = self.io.schedule(self._finish_watch,self.io.now()+.05)
        return result

    def _finish_watch(self, eventtime):
        if self.state not in self.TERMINAL:
            return eventtime+.05
        ace = self.io.ace
        if getattr(ace,'_exploration_owner',None) is self:
            ace._exploration_owner = None
            ace._tag_read_busy = False
        ace._exploration_last_result = self.report()
        return self.io.reactor.NEVER

    def report(self):
        out = super().report()
        for row in out['uid_observations']:
            row['integrity_verified'] = False
            row['host_rf_isolation'] = 'cooperative only; other raw RF paths not excluded'
            row['label'] = 'unconfirmed unassigned BCC observation' + ('; assumes omitted field12 is zero' if row.get('protocol_default_zero_assumption') else '')
        out.update(rf_isolation_verified=False, mode='one-shot lower-assurance exploration',history=(dict(HISTORY) if self.plan.slot == 3 else dict(HISTORY, slot=2, head=2, commands_after_sensor_became_empty_mm=[500,285], last_reported_decoder_mm=-271)),
            estimated_clearance_mm=None, guaranteed_clearance_mm=None,
            pin_observation=self.io.latest_observation if isinstance(self.io,ExistingMachineIO) else None,
            buffer_baseline=getattr(self,'buffer_baseline',None),buffer_polarity_verified=False,
            authorization_consumed=self.authorization.consumed,
            residual_risk='If the tip is near the head and STOP fails, up to the remaining 50 mm command can still advance.',
            full_turn_verified=False)
        return out


def start_exploration_command(ace,gcmd):
    if gcmd.get_int('RECOVER',0,minval=0,maxval=1) or gcmd.get_int('RECOVERY_STATUS',0,minval=0,maxval=1):
        from .ace_latch_recovery import command
        return command(ace,gcmd)
    if gcmd.get_int('STATUS',0,minval=0,maxval=1):
        current = getattr(ace,'_exploration_scan',None)
        result = current.report() if current is not None else getattr(ace,'_exploration_last_result',{'state':'no trial result in this process'})
        gcmd.respond_info(json.dumps(result,ensure_ascii=False,sort_keys=True))
        return result
    if gcmd.get_int('CANCEL',0,minval=0,maxval=1):
        current = getattr(ace,'_exploration_scan',None)
        if current is not None:
            current.cancel()
            gcmd.respond_info('Exploration cancellation requested; STOP is not proof of standstill')
        return current
    # Only operating limits can be specified, never fake safety booleans.
    args = (gcmd.get_int('ACE',0),gcmd.get_int('SLOT',3),
            gcmd.get_int('MAX_MM',50),gcmd.get_int('SPEED',10))
    if args not in ((0,3,50,10),(0,2,50,10)):
        raise gcmd.error('This one-shot authorization is only ACE0 SLOT3, 50 mm at 10 mm/s')
    try:
        authorization = OneShotAuthorization(SLOT3_AUTHORIZATION_PATH if args[1] == 2 else AUTHORIZATION_PATH)
        if (authorization.record['ace_index'], authorization.record['slot'], authorization.record['length_mm'], authorization.record['speed_mm_s']) != args:
            raise ValueError('authorization does not match requested trial')
        from .ace_rc522 import AceTagReader
        scan = ControlledExploration(AceTagReader(ace),authorization)
        ace._exploration_scan = scan
        gcmd.respond_info('One-shot 50 mm exploration: cached pin/MCU supervision, raw buffer change-stop; not a full turn or guaranteed clearance')
        scan.start()
        if scan.state == 'blocked':
            raise ValueError(scan.reason)
        return scan
    except Exception as exc:
        raise gcmd.error('Exploration refused: '+str(exc))
