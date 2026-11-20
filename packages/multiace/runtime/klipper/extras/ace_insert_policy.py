"""ACE insertion policy: short grip, then explicitly enabled bounded RFID.

The MCU captures a fresh input edge. The host serializes it with existing slot
operations; START carries both connection session and insertion epoch. No timed
motor retry, automatic scan, or additional head preload follows the short mode.
"""
import logging
import secrets
import struct
import threading
from .ace_filament_options import FilamentOptions, SCAN_MM, GRIP_MM, GRIP_SPEED
from contextlib import contextmanager

FIRMWARE = 'V1.2.00O'
METHODS = {'insert_policy_query': 0, 'insert_policy_set': 1,
           'insert_policy_start': 2, 'insert_policy_discard': 3}

class NoMotion(RuntimeError):
    """The writer or a validated MCU response proved START was not accepted."""


def encode(method, params):
    op = METHODS[method]
    slot, enabled = params.get('index', 0), params.get('enabled', True)
    session, epoch = params.get('session', 0), params.get('epoch', 0)
    expected, revision = params.get('expected_session', 0), params.get('revision', 0)
    if type(slot) is not int or not 0 <= slot < 4 or type(enabled) is not bool:
        raise ValueError('invalid insertion policy')
    if any(type(v) is not int or not 0 <= v <= 0xffffffff for v in (session, epoch, expected, revision)):
        raise ValueError('invalid insertion identity')
    return struct.pack('<4sBBBBIIII', b'AIP1', 1, op, slot, int(enabled), session, epoch, expected, revision)


def decode(payload):
    if not isinstance(payload, bytes) or len(payload) != 48:
        raise ValueError('invalid insertion reply length')
    magic, version, code, configured, enabled, session, park = struct.unpack_from('<4sBBBBII', payload)
    epochs = list(struct.unpack_from('<4I', payload, 16))
    pending, results = list(payload[32:36]), list(payload[36:40])
    if (magic != b'AIR1' or version != 1 or code > 6 or configured > 1 or enabled > 1
            or park != 400 or any(v > 1 for v in pending) or any(v > 6 for v in results)
            or struct.unpack_from('<I', payload, 44)[0] != 0x20261114
            or (configured and not session)):
        raise ValueError('invalid insertion reply')
    return {'code': code, 'configured': bool(configured), 'enabled': bool(enabled),
            'session': session, 'park_mm': park, 'epochs': epochs, 'pending': pending, 'results': results,
            'revision': struct.unpack_from('<I', payload, 40)[0]}


class InsertPolicy:
    def __init__(self, ace, config):
        self.ace = ace
        # Consume the legacy Klipper option without treating it as consent.
        config.getboolean('auto_insert_rfid', False)
        self.options = FilamentOptions(ace, ace.paths['config_dir'] +
                                      '/persistent/multiace-advanced-filament.json')
        self.units = {}
        self.changing = False
        self.polling = False
        self.active = None
        self.owner = None
        self.scan_origins = {}
        self.wire_lock = threading.Lock()
        self.timer = None
        ace.gcode.register_command('ACE_SET_AUTO_INSERT_RFID', self.set_command,
            desc='Enable optional RFID after explicit Y-clearance confirmation; default is a 100 mm grip.')
        ace.gcode.register_command('ACE_SET_INSERT_RFID_AUTO', self.set_auto_command,
            desc='Toggle only RFID on the next insertion; manual RFID remains available.')
        ace.gcode.register_command('ACE_SET_REMAINING_ESTIMATE', self.remaining_command,
            desc='Enable optional remaining estimate after Y-clearance confirmation.')
        ace.printer.register_event_handler('klippy:ready', self.ready)

    @property
    def enabled(self):
        return self.options.enabled('rfid')

    def ready(self):
        self.timer = self.ace.reactor.register_timer(self.tick, self.ace.reactor.monotonic() + 1)

    def capable(self, idx):
        return str((self.ace._ace_models.get(idx) or ('', ''))[1]).upper() in (FIRMWARE, 'V1.2.01O')

    def bypass(self, idx):
        return self.changing or self.capable(idx)

    def busy(self):
        a = self.ace
        ps = a.printer.lookup_object('print_stats', None)
        stop_guard = getattr(a, '_stop_guard', None)
        return (stop_guard is not None and stop_guard.snapshot() is not None
            or ps is None or getattr(ps, 'state', '').lower() not in ('standby', 'complete', 'cancelled')
            or a._swap_in_progress or a._unload_all_active or getattr(a, '_tag_read_busy', False)
            or a._slot_actions.state.get('state') in ('preparing', 'running')
            or bool(getattr(a, '_insert_read_running', set()))
            or bool(getattr(a, '_insert_abort_running', set()))
            or getattr(getattr(a, '_path_calibration', None), 'task', {}).get('active', False))

    @contextmanager
    def claim(self, kind, idx=None, slot=None):
        """Claim the same gate as manual RFID/eject before the first reactor yield."""
        a, actions = self.ace, self.ace._slot_actions
        if self.owner is not None or self.busy():
            raise RuntimeError('请等待当前打印或料槽动作结束')
        actions.seq += 1
        token = {'seq': actions.seq, 'kind': kind, 'idx': idx, 'slot': slot}
        self.owner = token
        actions.cancel = False
        actions.state = {'seq': actions.seq, 'state': 'running', 'kind': kind,
                         'ace': idx, 'slot': slot, 'progress': 0, 'limit': GRIP_MM,
                         'message': '正在同步插料设置' if kind == 'insert_policy' else '正在抓取耗材'}
        a._swap_in_progress = True
        try:
            yield token
            if actions.state.get('state') == 'running':
                actions.state.update(state='complete', progress=100)
        except Exception as exc:
            cancelled = actions.cancel and (kind != 'insert' or actions.state.get('stop_confirmed'))
            actions.state.update(state='cancelled' if cancelled else 'error',
                                 message='已停止插料' if cancelled and kind == 'insert' else str(exc))
            raise
        finally:
            self.owner = None
            a._swap_in_progress = False

    def connected(self, idx, entry):
        a = self.ace
        if (not a._connected_per_ace.get(idx) or a._serials.get(idx) is not entry['transport']
                or a._location_identity(idx) != entry['identity']):
            raise RuntimeError('ACE 连接或身份已变化')

    def owned(self, idx, entry, allow_cancel=False):
        self.connected(idx, entry)
        actions = self.ace._slot_actions
        if (self.owner is None or actions.state.get('seq') != self.owner['seq']
                or actions.state.get('kind') != self.owner['kind'] or (actions.cancel and not allow_cancel)):
            raise RuntimeError('插料操作已停止或发生变化')

    def unit(self, idx):
        a = self.ace
        transport, identity = a._serials.get(idx), a._location_identity(idx)
        if transport is None or identity[1] is None or not a._connected_per_ace.get(idx):
            raise RuntimeError('ACE 身份或连接未确认')
        entry = self.units.get(idx)
        if entry is None or entry['transport'] is not transport or entry['identity'] != identity:
            entry = {'transport': transport, 'identity': identity, 'session': secrets.randbits(32) or 1,
                     'snapshot': None, 'epochs': None, 'applied': None, 'error': ''}
            self.units[idx] = entry
        return entry

    def request(self, idx, method, entry, slot=0, epoch=0, enabled=None, token=None,
                expected_session=0, revision=0, cleanup=False):
        a = self.ace
        if cleanup and method != 'insert_policy_discard': raise ValueError('invalid cleanup operation')
        check = (lambda: self.connected(idx, entry)) if method == 'insert_policy_query' else (lambda: self.owned(idx, entry, cleanup))
        check()
        request = {'method': method, 'params': {'index': slot, 'epoch': epoch,
            'session': entry['session'], 'enabled': False,
            'expected_session': expected_session, 'revision': revision},
            '_insert_stop_transport': entry['transport']}
        box = []
        def callback(self, response):
            box.append(response)
        request['_insert_reject'] = callback
        if method != 'insert_policy_query': request['_insert_policy_owner'] = self.owner
        if cleanup: request['_insert_policy_cleanup'] = True
        if token is not None: request['_insert_policy_token'] = token
        try:
            a.send_request_to(idx, request, callback)
            deadline = a.reactor.monotonic() + 2.0
            while not box and a.reactor.monotonic() < deadline:
                check()
                a.reactor.pause(min(deadline, a.reactor.monotonic() + .05))
            check()
        except Exception:
            if token is not None:
                with self.wire_lock:
                    token['revoked'] = True
                    if not token.get('wire_attempted'): raise NoMotion('插料在发送前取消')
            raise
        reply = box[0] if box else None
        if reply is None and token is not None:
            with self.wire_lock:
                token['revoked'] = True
                if not token.get('wire_attempted'): raise NoMotion('插料排队已超时，未发送送料指令')
        if method == 'insert_policy_start' and isinstance(reply, dict):
            if (reply.get('_insert_invalidated') is True or reply.get('_host_stop_fault') is True
                    or reply.get('_location_hold') is True):
                raise NoMotion('插料在发送前被拦截')
            if (reply.get('_v2_frame_valid') is True and reply.get('_v2_payload_valid') is True
                    and reply.get('_v2_command') == 93 and type(reply.get('code')) is int
                    and 1 <= reply['code'] <= 5):
                raise NoMotion('插料事件已过期或设备未接受，请重新插料')
        if (not isinstance(reply, dict) or reply.get('_v2_frame_valid') is not True
                or reply.get('_v2_payload_valid') is not True or reply.get('_v2_command') != 93
                or type(reply.get('code')) is not int or reply['code'] != 0):
            raise RuntimeError('ACE 插料设置响应未确认')
        result = reply.get('result', {}).get('insert_policy')
        if not isinstance(result, dict): raise RuntimeError('ACE 插料设置数据缺失')
        entry['snapshot'] = result
        return result

    def apply_unit(self, idx, enabled=False):
        # Always suppress native long pull-in; optional scans are host-bounded.
        enabled = False
        a, entry = self.ace, self.unit(idx)
        snapshot = self.request(idx, 'insert_policy_query', entry)
        self.observe(idx, entry, snapshot)
        if (snapshot['configured'] and snapshot['session'] == entry['session']
                and snapshot['enabled'] == enabled and entry['applied'] == enabled):
            entry['error'] = ''
            return snapshot
        entry['applied'] = None
        monitor = lambda: self.owned(idx, entry)
        lanes = a._slot_actions._lane_states(idx, monitor=monitor)
        if any(value != 'ready' for value in lanes.values()):
            raise RuntimeError('ACE 正在运动，请等待结束')
        # RF enable belongs to the two paired readers. Raw manual RC522 access
        # remains available while the native background readers are disabled.
        for slot in (0, 2):
            a._slot_actions._request(idx, 'set_rfid_enable', {'index': slot, 'enable': enabled}, 14,
                                     monitor=monitor, stop_transport=entry['transport'], policy_token=self.owner)
        result = self.request(idx, 'insert_policy_set', entry, enabled=enabled,
                              expected_session=snapshot['session'], revision=snapshot['revision'])
        if not result['configured'] or result['session'] != entry['session'] or result['enabled'] != enabled:
            raise RuntimeError('ACE 未应用插料设置')
        self.observe(idx, entry, result)
        entry['applied'], entry['error'] = enabled, ''
        return result

    def set_command(self, gcmd):
        self._set_option(gcmd, 'rfid')

    def remaining_command(self, gcmd):
        self._set_option(gcmd, 'remaining')

    def set_auto_command(self, gcmd):
        enabled = bool(gcmd.get_int('ENABLE', minval=0, maxval=1))
        try:
            self.options.set_auto(enabled)
        except (OSError, ValueError) as exc:
            raise gcmd.error(str(exc))
        gcmd.respond_info('插料后自动识别设置已保存；下次插料生效')

    def _set_option(self, gcmd, feature):
        enabled = bool(gcmd.get_int('ENABLE', minval=0, maxval=1))
        confirmed = gcmd.get_int('CONFIRM_Y', 0, minval=0, maxval=1) == 1
        clearance = gcmd.get_int('CLEARANCE_MM', 0, minval=0, maxval=20000)
        if self.changing or self.busy():
            raise gcmd.error('请等待当前打印或料槽动作结束')
        indices = [i for i, connected in self.ace._connected_per_ace.items() if connected]
        if enabled and not any(self.capable(i) for i in indices):
            raise gcmd.error('至少需要一台支持短吸模式的在线 ACE')
        self.changing = True
        try:
            with self.claim('insert_policy'):
                # Synchronization never enables the MCU's native long movement.
                for idx in indices:
                    if self.capable(idx):
                        self.apply_unit(idx)
                self.options.update(feature, enabled, clearance, confirmed)
                self.ace._insert_read_queue = []
            gcmd.respond_info('耗材高级选项已保存；下次插料生效')
        except Exception as exc:
            raise gcmd.error(str(exc))
        finally:
            self.changing = False

    def invalidate(self, idx, slot):
        a, key = self.ace, (idx, slot)
        with self.wire_lock:
            self.scan_origins.pop(key, None)
        a._v2_filament_info_per_ace.get(idx, {}).pop(slot, None)
        for name in ('_rc_last_uid', '_rc_last_fmt', '_insert_native_pending', '_single_verified_uid'):
            getattr(a, name, {}).pop(key, None)
        for name in ('_v2_filament_info_pending', '_v2_filament_info_empty'):
            getattr(a, name, {}).get(idx, {}).pop(slot, None)
        epochs = getattr(a, '_insert_epochs', None)
        if epochs is None: epochs = a._insert_epochs = {}
        epochs[key] = epochs.get(key, 0) + 1
        a._persist_tag_reads()

    def observe(self, idx, entry, snapshot):
        previous = entry['epochs']
        for slot in range(4):
            if previous is not None and previous[slot] != snapshot['epochs'][slot]:
                travel = getattr(self.ace, '_head_travel', None)
                if travel is not None:
                    travel.reset_route(idx, slot)
            if previous is None or previous[slot] != snapshot['epochs'][slot]:
                self.invalidate(idx, slot)
        entry['epochs'] = list(snapshot['epochs'])

    def prepare_manual(self, idx, slot):
        if not self.capable(idx): return
        if self.changing: raise RuntimeError('正在切换插料设置')
        with self.claim('insert_policy', idx, slot):
            entry = self.unit(idx)
            snapshot = self.apply_unit(idx)
            self.request(idx, 'insert_policy_discard', entry, slot, snapshot['epochs'][slot])

    def tick(self, eventtime):
        if not self.polling and not self.changing and not self.busy():
            self.polling = True
            self.ace.reactor.register_async_callback(self.poll)
        return eventtime + .5

    def poll(self, eventtime):
        try:
            for idx, connected in list(self.ace._connected_per_ace.items()):
                if not connected or not self.capable(idx) or self.changing or self.busy(): continue
                try:
                    entry = self.unit(idx)
                    snapshot = self.request(idx, 'insert_policy_query', entry)
                    if self.changing or self.busy(): continue
                    if (snapshot['configured'] and snapshot['session'] == entry['session']
                            and snapshot['enabled'] is False and entry['applied'] is False):
                        self.observe(idx, entry, snapshot)
                        entry['error'] = ''
                    else:
                        with self.claim('insert_policy', idx):
                            snapshot = self.apply_unit(idx)
                    for slot, pending in enumerate(snapshot['pending']):
                        if pending:
                            self.run(idx, slot, entry, snapshot['epochs'][slot])
                except Exception as exc:
                    entry = self.units.get(idx)
                    if entry is not None: entry['error'] = str(exc)
        finally:
            self.polling = False

    def permits_start(self, idx, request, transport):
        if '_insert_policy_owner' in request:
            if request['_insert_policy_owner'] is None or request['_insert_policy_owner'] is not self.owner:
                return False
            entry = self.units.get(idx)
            try:
                self.owned(idx, entry, request.get('_insert_policy_cleanup') is True
                           and request.get('method') == 'insert_policy_discard')
                if transport is not entry['transport']: return False
            except Exception:
                return False
        if request.get('method') != 'insert_policy_start': return True
        token = request.get('_insert_policy_token')
        try:
            if token is None or token is not self.active or token['idx'] != idx or token.get('revoked'):
                return False
            self.monitor(token)
            return (transport is token['entry']['transport'] and
                    request['params']['epoch'] == token['epoch'] and
                    request['params']['session'] == token['entry']['session'])
        except Exception:
            return False

    def _scan_context(self, idx, slot):
        ace = self.ace
        return (ace._serials.get(idx), ace._location_identity(idx),
                getattr(ace, '_ace_mode', None), ace._rfid_route_heads(idx, slot),
                getattr(ace, '_insert_epochs', {}).get((idx, slot)))

    def drop_changed_scan_origins(self):
        with self.wire_lock:
            for key, context in list(self.scan_origins.items()):
                if context != self._scan_context(*key):
                    self.scan_origins.pop(key, None)

    def take_scan_origin(self, idx, slot):
        with self.wire_lock:
            origin = self.scan_origins.pop((idx, slot), None)
        return origin is not None and origin == self._scan_context(idx, slot)

    def restore_scan_origin(self, idx, slot, original_context):
        """Restore a consumed origin only after the reader proved its return.

        The reader invokes this after fresh stationary encoder and inlet checks.
        This is not a calibration or a new Y-clearance claim. A replug, new
        insertion or topology change cannot renew a previous origin.
        """
        with self.wire_lock:
            if (original_context is None
                    or original_context != self._scan_context(idx, slot)
                    or self.ace._connected_per_ace.get(idx) is not True
                    or self.ace._serial_failed_per_ace.get(idx, False)):
                return False
            self.scan_origins[(idx, slot)] = original_context
        return True

    def before_write(self, idx, request, transport):
        method = request.get('method')
        params = request.get('params') or {}
        with self.wire_lock:
            if method == 'insert_policy_start':
                if not self.permits_start(idx, request, transport):
                    return False
                request['_insert_policy_token']['wire_attempted'] = True
            if method in ('insert_policy_start', 'feed_filament', 'unwind_filament',
                          'feed_or_rollback_raw', 'start_feed_assist', 'update_feeding_speed'):
                slot = params.get('index')
                if type(slot) is int and 0 <= slot < 4:
                    self.scan_origins.pop((idx, slot), None)
                else:
                    for lane in range(4):
                        self.scan_origins.pop((idx, lane), None)
            elif ((method == 'raw' and params.get('cmd') in (8, 10, 77))
                  or (method in ('drying', 'drying_raw') and params.get('auto_roll') is True)
                  or '_controlled_owner' in request):
                for lane in range(4):
                    self.scan_origins.pop((idx, lane), None)
            return True

    def monitor(self, token):
        a, actions = self.ace, self.ace._slot_actions
        idx, slot, entry = token['idx'], token['slot'], token['entry']
        self.owned(idx, entry)
        if (self.changing or actions.cancel or self.active is not token
                or actions.state.get('seq') != token['seq'] or actions.state.get('kind') != 'insert'
                or a._location_identity(idx) != entry['identity']):
            raise RuntimeError('插料已停止或任务发生变化')
        actions._check(idx, slot, transport=entry['transport'], own=True)

    def run(self, idx, slot, entry, epoch):
        a, actions = self.ace, self.ace._slot_actions
        auto_at_start = self.options.enabled('auto_rfid', idx)
        try:
            actions._check(idx, slot)
            with self.claim('insert', idx, slot) as token:
                token.update(entry=entry, epoch=epoch)
                self.active = token
                monitor = lambda: self.monitor(token)
                try:
                    monitor()
                    current = self.request(idx, 'insert_policy_query', entry)
                    if current['epochs'][slot] != epoch or not current['pending'][slot]:
                        return
                    baseline = actions._sample(idx, slot, allow_initial_zero=True, monitor=monitor)
                    if not baseline[0]:
                        raise RuntimeError('料槽入口无料')
                    # Consume the fresh insertion event BEFORE the short move.
                    # A reboot or a lost response never replays a previous insert.
                    self.request(idx, 'insert_policy_discard', entry, slot, epoch)
                    monitor()
                    actions._request(idx, 'feed_filament',
                        {'index': slot, 'length': GRIP_MM, 'speed': GRIP_SPEED}, 8,
                        monitor=monitor, stop_transport=entry['transport'], policy_token=self.owner)
                    # Wait for the finite feed while remaining responsive to cancel.
                    started = a.reactor.monotonic()
                    duration = GRIP_MM / float(GRIP_SPEED)
                    earliest = started + duration
                    deadline = earliest + 3.0
                    actions.state.update(progress_percent=0, progress_estimated=True,
                                         message='正在插入 10 cm')
                    while a.reactor.monotonic() < earliest:
                        monitor()
                        percent = min(95, int(100 * (a.reactor.monotonic() - started) / duration))
                        actions.state.update(progress_percent=percent)
                        a.reactor.pause(min(earliest, a.reactor.monotonic() + .1))
                    actions.state.update(progress_percent=95, message='正在确认送料停止')
                    while True:
                        monitor()
                        if actions._lane_states(idx, monitor=monitor).get(slot) == 'ready':
                            break
                        if a.reactor.monotonic() >= deadline:
                            raise RuntimeError('插料未在预期时间内结束，已停止')
                        a.reactor.pause(min(deadline, a.reactor.monotonic() + .1))
                    monitor()
                    actions._eject_stop(idx, slot, entry['transport'], monitor)
                    a.wait_ace_ready_on(idx)
                    try:
                        stopped = actions._sample(idx, slot, allow_initial_zero=True, monitor=monitor)
                        present, (decoder_ok, delta), (_steps, measured_length) = stopped
                        if (present and decoder_ok and 0 < abs(delta) <= GRIP_MM + 10
                                and 0 < measured_length <= GRIP_MM and stopped[1:] != baseline[1:]):
                            context = self._scan_context(idx, slot)
                            with self.wire_lock:
                                self.scan_origins[(idx, slot)] = context
                    except RuntimeError as exc:
                        logging.info('[multiACE] RFID short-grip origin not confirmed: %s', exc)
                    actions.state.update(state='complete', progress=100,
                                         progress_percent=100, progress_estimated=False,
                                         stop_confirmed=True,
                                         message='已插入 ACE，等待装料到喷头')
                    entry['error'] = ''
                except Exception:
                    connected = lambda: self.connected(idx, entry)
                    try:
                        connected()
                        actions._eject_stop(idx, slot, entry['transport'], connected)
                        stop_deadline = a.reactor.monotonic() + 2.0
                        while actions._lane_states(idx, monitor=connected).get(slot) != 'ready':
                            if a.reactor.monotonic() >= stop_deadline:
                                raise RuntimeError('目标槽仍未确认停止')
                            a.reactor.pause(min(stop_deadline, a.reactor.monotonic() + .1))
                        actions.state['stop_confirmed'] = True
                    except Exception as stop_error:
                        logging.warning('[multiACE] short insertion STOP unconfirmed')
                        raise RuntimeError('停止未确认，请检查 ACE：%s' % stop_error) from stop_error
                    raise
                finally:
                    self.active = None
            if (auto_at_start and not actions.cancel
                    and self.options.enabled('auto_rfid', idx)):
                self.connected(idx, entry)
                gcmd = a.gcode.create_gcode_command('ACE_SLOT_RFID', 'ACE_SLOT_RFID',
                    {'ACE': str(idx), 'SLOT': str(slot), 'MAX_MM': str(SCAN_MM)})
                gcmd._short_insert_context = (entry['transport'], entry['identity'],
                                             a._insert_epochs.get((idx, slot)))
                a.cmd_ACE_SLOT_RFID(gcmd)
        except Exception as exc:
            entry['error'] = str(exc)
            logging.info('[multiACE] insertion %d/%d: %s', idx, slot, exc)

    def status(self):
        units = []
        for idx, connected in self.ace._connected_per_ace.items():
            entry = self.units.get(idx, {})
            units.append({'ace': idx, 'connected': bool(connected), 'supported': self.capable(idx),
                          'applied': entry.get('applied') if connected else None,
                          'rfid_enabled': self.options.enabled('rfid', idx),
                          'remaining_enabled': self.options.enabled('remaining', idx),
                          'error': entry.get('error', '')})
        return {'enabled': self.enabled, 'persisted': self.options.saved['rfid'], 'park_mm': GRIP_MM,
                'advanced': self.options.status(),
                'changing': self.changing, 'units': units}
