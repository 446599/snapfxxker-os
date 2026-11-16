"""Original-slot recovery and source confirmation without clearing physical paths."""
import copy
import logging
import secrets
from .ace_stop_guard import controlled_cleanup
from .ace_recovery_policy import RecoveryBudget


FAULT_STATES = frozenset(('feed_error', 'rollback_error', 'assist_error',
    'preload_error', 'stuck_error', 'tangled_error', 'motor_error'))
NORMAL_STATES = frozenset(('ready', 'assisting', 'rollback_assisting'))
IDLE_PRINT_STATES = frozenset(('standby', 'complete', 'cancelled', 'error'))


class RecoveryContextChanged(RuntimeError):
    pass


def _motion_request(request):
    if not isinstance(request, dict) or controlled_cleanup(request):
        return False
    method = request.get('method')
    if method in ('drying', 'drying_raw'):
        return (request.get('params') or {}).get('auto_roll', False) is not False
    if method == 'insert_policy_set':
        return (request.get('params') or {}).get('enabled') is not False
    return method in ('feed_filament', 'unwind_filament', 'feed_or_rollback_raw',
                      'start_feed_assist', 'update_feeding_speed', 'insert_policy_start',
                      'raw')


class ErrorRecovery:
    def __init__(self, ace):
        self.ace = ace
        self.active = False
        self.generation = 0
        self.owner = None
        self.target = None
        self._tickets = {}
        self._automatic = RecoveryBudget()
        self._timer = None
        self._disconnected = False
        hooks = ace.printer.lookup_object('webhooks')
        hooks.register_endpoint('multiace/slot_recover', self.endpoint)
        hooks.register_endpoint('multiace/head_source', self.source_endpoint)
        ace.printer.register_event_handler('klippy:ready', self._handle_ready)
        ace.printer.register_event_handler('klippy:disconnect', self._handle_disconnect)

    def _handle_ready(self):
        self._disconnected = False
        if self._timer is None:
            self._timer = self.ace.reactor.register_timer(
                self._automatic_tick, self.ace.reactor.monotonic() + .75)

    def _handle_disconnect(self):
        self._disconnected = True
        if self._timer is not None:
            self.ace.reactor.unregister_timer(self._timer)
            self._timer = None
        for key in self._automatic.episodes:
            self._automatic.block(key, '控制器已断开，请重新检查原设备和料路')

    def _physical_context(self, idx, slot):
        a = self.ace
        return (id(a._serials.get(idx)), copy.deepcopy(a._location_identity(idx)),
                (getattr(a, '_insert_epochs', {}) or {}).get((idx, slot)),
                getattr(a, '_ace_mode', None), tuple(a._rfid_route_heads(idx, slot) or ()),
                tuple(sorted((head, repr(source)) for head, source in a._head_source.items())))

    def _is_busy(self):
        a = self.ace
        path = getattr(a, '_path_calibration', None)
        operations = getattr(a, '_path_operations', None)
        bg = a.printer.lookup_object('ace_bg_swap', None)
        guard = a.printer.lookup_object('ace_native_guard', None)
        return (self.active or a._slot_actions.state.get('state') in ('preparing', 'running')
                or getattr(a, '_swap_in_progress', False)
                or getattr(a, '_unload_all_active', False)
                or getattr(a, '_in_internal_load_head', False)
                or getattr(a, '_tag_read_busy', False)
                or getattr(a, '_gen1_manual_busy', False)
                or path is not None and path.task.get('active')
                or getattr(a, '_calibration_unload', {}).get('active')
                or (getattr(a, '_calibration', {}) or {}).get('state', 'idle')
                   not in ('idle', 'cancelled', 'complete', 'completed', 'error')
                or bg is not None and getattr(bg, '_busy', False)
                or guard is not None and getattr(guard, 'state', None) != 'idle'
                or operations is not None and any(item.get('state') not in ('complete', 'cancelled', 'error')
                                                 for item in operations.status())
                or any(getattr(task, 'active', False) for task in
                       getattr(a, '_feed_supervisors', {}).values()))

    def _lane_state(self, idx, slot):
        states = self.ace._info_per_ace.get(idx, {}).get('slots') or []
        lane = next((row for row in states if isinstance(row, dict)
                     and row.get('index') == slot), {})
        return lane.get('slot_status', 'unknown')

    def _faults_for(self, idx, slot):
        _generation, faults = self.ace._stop_guard.recovery_snapshot()
        return [fault for fault in faults
                if (fault.get('ace_idx'), fault.get('slot')) == (idx, slot)]

    def _original_source(self, idx, slot):
        """A reconnect/USB enumeration must never relabel the old route."""
        a = self.ace
        identity = a._rfid_identity_status(idx)
        if identity is None:
            raise RuntimeError('原 ACE 身份尚未确认，请等待连接恢复后重新检查')
        for fault in self._faults_for(idx, slot):
            if fault.get('port') != identity['port'] or fault.get('uid') != identity['uid']:
                raise RecoveryContextChanged('停止故障属于另一原设备，不能替换原料路')
        for head in a._rfid_route_heads(idx, slot) or ():
            source = a._head_source.get(head)
            if not isinstance(source, dict):
                continue
            if (source.get('ace_index'), source.get('slot')) != (idx, slot):
                raise RecoveryContextChanged('共用管仍由另一来源占用，请重新检查原料路')
            if not isinstance(source.get('connection_identity'), dict) or 'insert_epoch' not in source:
                raise RecoveryContextChanged('旧来源缺少设备身份或插料代次，请先确认原来源')
            if source['connection_identity'] != identity:
                raise RecoveryContextChanged('原送料设备或端口已变化，保留原来源，请重新检查')
            epoch = getattr(a, '_insert_epochs', {}).get((idx, slot))
            if type(source['insert_epoch']) is not type(epoch) or source['insert_epoch'] != epoch:
                raise RecoveryContextChanged('原来源的插料代次已变化，请检查管内耗材')

    def _automatic_tick(self, eventtime):
        """Only schedule STOP/read reconciliation; never execute motion in a timer."""
        if self._disconnected:
            return self.ace.reactor.NEVER
        a = self.ace
        try:
            ps = a.printer.lookup_object('print_stats', None)
            idle = getattr(ps, 'state', '').lower() in IDLE_PRINT_STATES
            busy = self._is_busy()
            if busy or not idle:
                return eventtime + .75
            for idx in sorted(a._connected_per_ace):
                for slot in range(4):
                    key = (idx, slot)
                    actual = self._lane_state(idx, slot)
                    needed = bool(self._faults_for(idx, slot)) or actual in FAULT_STATES
                    # Do not treat a gap in telemetry as recovery; the same
                    # physical incident retains its budget across STOP relatches.
                    if not needed and actual not in NORMAL_STATES:
                        continue
                    context = (self._physical_context(idx, slot)
                               if a._rfid_identity_status(idx) is not None else None)
                    due = self._automatic.observe(key, context,
                                                  needed, eventtime)
                    if not due:
                        continue
                    if a._connected_per_ace.get(idx) is not True or a._serials.get(idx) is None:
                        continue
                    try:
                        self._original_source(idx, slot)
                        self._start(idx, slot, 'auto', automatic=True)
                    except RecoveryContextChanged as exc:
                        self._automatic.block(key, str(exc))
                        continue
                    except RuntimeError as exc:
                        # A transient sensor/identity read can recover without
                        # spending the two STOP attempts or sending any motor move.
                        self._automatic.defer(key, eventtime, str(exc))
                        continue
                    except Exception as exc:
                        self._automatic.block(key, str(exc))
                        logging.exception('[multiACE] automatic recovery could not start')
                        continue
                    return eventtime + .75
        except Exception:
            logging.exception('[multiACE] automatic recovery scheduling failed')
        return eventtime + .75

    def stamp(self, request):
        if not _motion_request(request):
            return True
        if request.get('_recovery_generation', self.generation) != self.generation:
            return False
        if self.active and request.get('_recovery_owner') is not self.owner:
            return False
        request['_recovery_generation'] = self.generation
        return True

    def allows_wire(self, request):
        if not _motion_request(request):
            return True  # STOP and telemetry stay available even while faulted.
        return (request.get('_recovery_generation') == self.generation
                and (not self.active or request.get('_recovery_owner') is self.owner))

    def begin(self, idx, slot):
        if self.active:
            raise RuntimeError('已有故障复核正在进行')
        self.generation += 1  # revoke motor requests queued before this recovery
        self.owner = object()
        self.target = (idx, slot)
        self.active = True
        return self.owner

    def end(self, owner):
        if owner is self.owner:
            self.generation += 1  # a delayed owned move cannot run after cleanup
            self.active = False
            self.owner = None
            self.target = None

    def _fingerprint(self, idx, slot):
        a = self.ace
        generation, _faults = a._stop_guard.recovery_snapshot()
        return (id(a._serials.get(idx)), a._location_identity(idx),
                (getattr(a, '_insert_epochs', {}) or {}).get((idx, slot)),
                generation, a._slot_actions.seq, getattr(a, '_ace_mode', None),
                tuple(a._rfid_route_heads(idx, slot) or ()),
                tuple(sorted((head, repr(source)) for head, source in a._head_source.items())))

    def _ticket(self, idx, slot):
        key = (idx, slot)
        fingerprint = self._fingerprint(idx, slot)
        saved = self._tickets.get(key)
        if saved is None or saved[0] != fingerprint:
            saved = (fingerprint, secrets.token_hex(16))
            self._tickets[key] = saved
        return saved[1]

    def task_status(self):
        return self.ace._slot_actions.status()

    def _retained_confirmation_head(self, idx, slot):
        a = self.ace
        identity = a._rfid_identity_status(idx)
        if identity is None:
            return None
        epoch = getattr(a, '_insert_epochs', {}).get((idx, slot))
        candidates = []
        for head in a._rfid_route_heads(idx, slot) or ():
            source = a._head_source.get(head)
            if not isinstance(source, dict) or (source.get('ace_index'), source.get('slot')) != (idx, slot):
                continue
            if source.get('connection_identity') is not None and source['connection_identity'] != identity:
                continue
            if 'insert_epoch' in source and (type(source['insert_epoch']) is not type(epoch)
                                             or source['insert_epoch'] != epoch):
                continue
            if source.get('connection_identity') is None or 'insert_epoch' not in source:
                candidates.append(head)
        return candidates[0] if len(candidates) == 1 else None

    def status(self):
        a = self.ace
        fault = a._stop_guard.snapshot()
        rows = []
        busy = self._is_busy()
        ps = a.printer.lookup_object('print_stats', None)
        idle = getattr(ps, 'state', '').lower() in IDLE_PRINT_STATES
        for idx in sorted(a._connected_per_ace):
            for slot in range(4):
                online = a._connected_per_ace.get(idx) is True and a._serials.get(idx) is not None
                actual = self._lane_state(idx, slot)
                needs = actual in FAULT_STATES or bool(self._faults_for(idx, slot))
                available = online and not busy and idle
                automatic = self._automatic.snapshot((idx, slot))
                source_head = self._retained_confirmation_head(idx, slot)
                rows.append({'ace': idx, 'slot': slot,
                             'can_check': available, 'can_clear': available,
                             'can_auto': available and source_head is None,
                             'can_probe': available and actual == 'feed_error',
                             'source_head': source_head,
                             'needs_source_confirmation': source_head is not None,
                             'can_confirm_source': available and source_head is not None,
                             'needs_clear': needs, 'detail': actual,
                             'automatic': automatic,
                             'prompt': ('送料受阻仍需检查喷头前端；原料路保留，可自行选择短退一次'
                                        if actual == 'feed_error' else
                                        '自动检查停止状态；可确认的故障自动解除，原料路仍保留'),
                             'ticket': self._ticket(idx, slot) if online else ''})
        return {'active': self.active, 'stop_fault': fault,
                'automatic_enabled': True, 'automatic_attempt_limit': self._automatic.limit,
                'queued_reads': len(getattr(self.ace, '_insert_read_queue', [])),
                'slots': rows}

    def _start(self, idx, slot, action, automatic=False):
        a = self.ace
        actions = a._slot_actions
        if self._is_busy():
            raise RuntimeError('请等待当前耗材操作结束')
        self._original_source(idx, slot)
        actions._check(idx, slot, for_eject=True, allow_target_loaded=True,
                       allow_stop_fault=True)
        context = self._physical_context(idx, slot)
        owner = self.begin(idx, slot)
        actions.seq += 1
        actions.cancel = False
        actions.state = {'seq': actions.seq, 'state': 'preparing', 'kind': 'recover',
                         'ace': idx, 'slot': slot, 'automatic': automatic,
                         'message': '正在自动检查原槽停止状态' if action == 'auto'
                                    else '正在复核原槽停止状态'}
        self._tickets.pop((idx, slot), None)
        key = (idx, slot)
        if automatic:
            self._automatic.start(key)

        def run(_event):
            try:
                if self._disconnected or self._physical_context(idx, slot) != context:
                    raise RuntimeError('原设备连接或料路来源已变化，请重新检查')
                actions.recover_slot(idx, slot, allow_probe=action == 'clear',
                                     clear_latch=action != 'check', owner_token=owner)
                if action == 'auto':
                    actual = actions.state.get('slot_status')
                    attention = actual != 'ready' or actions.state.get('requires_position_review') is True
                    actions.state.update(automatic=automatic, needs_attention=attention)
                    if actual == 'feed_error':
                        actions.state.update(recovered=False,
                            message='已确认停止；送料受阻仍存在，请检查喷头前端。原料路保留，可自行选择短退一次')
                    elif actual != 'ready':
                        actions.state.update(recovered=False,
                            message='已确认停止；料槽仍报 %s，原料路保留，请检查设备' % actual)
                    elif not actions.state.get('requires_hot_unload'):
                        actions.state.update(recovered=True,
                            message='本槽停止状态已确认，可恢复的停止故障已解除；原料路仍保留，打印保持当前状态')
            except Exception as exc:
                actions.state.update(state='cancelled' if actions.cancel else 'error', message=str(exc))
            finally:
                try:
                    actions.state['automatic'] = automatic
                    try:
                        same_context = not self._disconnected and self._physical_context(idx, slot) == context
                    except Exception:
                        same_context = False
                    if not same_context:
                        actions.state.update(state='error', recovered=False, needs_attention=True,
                                             message='设备连接或料路来源已变化，请重新检查')
                    if automatic:
                        # A failed validation is never reported as success merely
                        # because an earlier STOP ACK was received.
                        stopped = actions.state.get('stopped') is True and actions.state.get('state') == 'complete'
                        self._automatic.finish(key, a.reactor.monotonic(), stopped,
                            actions.state.get('message', ''),
                            needs_attention=actions.state.get('needs_attention') is True,
                            retry=not actions.cancel and same_context)
                        if not same_context:
                            self._automatic.block(key, '设备连接或料路来源已变化，请重新检查')
                finally:
                    self.end(owner)
        try:
            a.reactor.register_async_callback(run)
        except Exception:
            self.end(owner)
            actions.state.update(state='error', message='恢复任务未能启动，请重新检查')
            raise
        return actions.seq

    def endpoint(self, request):
        try:
            idx, slot = request.get_int('ace'), request.get_int('slot')
            action, ticket = request.get_str('action'), request.get_str('ticket')
            confirmed = request.get_int('confirmed', 0)
            if (type(idx) is not int or type(slot) is not int or not 0 <= idx < 4
                    or not 0 <= slot < 4 or action not in ('check', 'clear', 'auto')
                    or type(confirmed) is not int or confirmed != 1):
                raise RuntimeError('恢复目标或操作无效')
            saved = self._tickets.get((idx, slot))
            if (saved is None or not isinstance(ticket, str) or len(ticket) != 32
                    or saved != (self._fingerprint(idx, slot), ticket)):
                raise RuntimeError('原设备或任务已变化，请刷新恢复状态')
            seq = self._start(idx, slot, action)
            request.send({'started': True, 'seq': seq, 'kind': 'recover'})
        except Exception as exc:
            request.send({'started': False, 'message': str(exc)})

    def confirm_source(self, head, idx, slot, retained=False, ticket=None):
        a = self.ace
        if (type(head) is not int or type(idx) is not int or type(slot) is not int
                or not 0 <= head < 4 or not 0 <= slot < 4 or not 0 <= idx < len(a._ace_devices)):
            raise ValueError('供料槽选择无效')
        if retained:
            saved = self._tickets.get((idx, slot))
            if (saved is None or saved != (self._fingerprint(idx, slot), ticket)
                    or self._retained_confirmation_head(idx, slot) != head):
                raise RuntimeError('原来源或设备已变化，请刷新状态后重新确认')
        ps = a.printer.lookup_object('print_stats', None)
        if getattr(ps, 'state', '') not in ('standby', 'complete', 'cancelled', 'paused', 'error'):
            raise RuntimeError('请先暂停打印或等待设备空闲')
        module, channel = a.EXTRUDER_MAP[head]
        feed = a.printer.lookup_object('filament_feed %s' % module, None)
        if (getattr(a, '_in_internal_load_head', False)
                or any(owner.active for owner in list(getattr(a, '_feed_supervisors', {}).values()))
                or feed is not None and getattr(feed, 'channel_active', None) == channel):
            raise RuntimeError('送料或热端夹持尚未结束，不能改写本次原来源')
        path = a._path_calibration
        if (a._swap_in_progress or a._unload_all_active or getattr(a, '_tag_read_busy', False)
                or path.task.get('active') or a._slot_actions.state.get('state') in ('preparing', 'running')
                or any(op['state'] not in ('complete', 'cancelled', 'error')
                       for op in a._path_operations.status())):
            raise RuntimeError('请等待当前耗材操作结束')
        if not a._connected_per_ace.get(idx) or idx in a._fw_update_hold:
            raise RuntimeError('所选 ACE 尚未在线')
        if path._head(idx, slot, head) != head:
            raise RuntimeError('所选料槽不属于该喷头的 Multi／Head 绑定')
        present = path.head_sensor(head)
        if type(present) is not bool or not present and not retained:
            raise RuntimeError('喷头当前无料或状态未知；仅可为已有管内来源补充身份')
        gates = a._gate_status_per_ace.get(idx) or []
        if slot >= len(gates) or gates[slot] != 1:
            raise RuntimeError('所选 ACE 槽入口未检测到耗材')
        existing = a._head_source.get(head)
        if retained and (not isinstance(existing, dict)
                         or (existing.get('ace_index'), existing.get('slot')) != (idx, slot)):
            raise RuntimeError('只能补充原有来源的身份，不能创建或替换管内来源')
        if isinstance(existing, dict) and not existing.get('load_failed'):
            if (existing.get('ace_index'), existing.get('slot')) != (idx, slot):
                raise RuntimeError('已有当前供料记录，请先核对所选料槽')
        identity = a._rfid_identity_status(idx)
        if a._is_v2_idx(idx) and identity is None:
            raise RuntimeError('ACE 原设备 UID 尚未确认，不能确认供料来源')
        if (isinstance(existing, dict)
                and (existing.get('ace_index'), existing.get('slot')) == (idx, slot)
                and existing.get('connection_identity') is not None
                and existing['connection_identity'] != identity):
            raise RuntimeError('原送料设备的身份或物理端口已变化；不能改写原来源')
        current_epoch = getattr(a, '_insert_epochs', {}).get((idx, slot))
        if (isinstance(existing, dict) and 'insert_epoch' in existing
                and (type(existing['insert_epoch']) is not type(current_epoch)
                     or existing['insert_epoch'] != current_epoch)):
            raise RuntimeError('原来源的插料代次已变化，不能用来源确认覆盖')
        old_sources = copy.deepcopy(a._head_source)
        rows = a._info_per_ace.get(idx, {}).get('slots') or []
        material = next((row for n, row in enumerate(rows)
                         if isinstance(row, dict) and row.get('index', n) == slot), {})
        source = copy.deepcopy(existing) if retained else {'ace_index': idx, 'slot': slot, 'type': material.get('type', ''),
                  'brand': material.get('brand', ''), 'color': a._device_color_hex(material),
                  'load_failed': False}
        if not retained:
            source = a._overlay_override(idx, slot, source)
        if identity is not None:
            source['connection_identity'] = copy.deepcopy(identity)
            source['insert_epoch'] = current_epoch
        a._head_source[head] = source
        try:
            a._save_head_source()
        except Exception:
            a._head_source = old_sources
            raise
        if not retained:
            a._ghost_heads.discard(head)
        self._tickets.pop((idx, slot), None)
        self._automatic.episodes.pop((idx, slot), None)
        return {'saved': True, 'head': head, 'ace': idx, 'slot': slot,
                'source_retained': retained, 'load_failed': source.get('load_failed') is True}

    def source_endpoint(self, request):
        try:
            retained = request.get_int('retained', 0)
            if type(retained) is not int or retained not in (0, 1):
                raise ValueError('原来源确认参数无效')
            if retained:
                confirmed = request.get_int('confirmed', 0)
                if type(confirmed) is not int or confirmed != 1:
                    raise ValueError('请先确认耗材确实来自原料槽')
            result = self.confirm_source(request.get_int('head'), request.get_int('ace'),
                                         request.get_int('slot'), retained=retained == 1,
                                         ticket=request.get_str('ticket', '') if retained else None)
        except (RuntimeError, ValueError) as exc:
            result = {'saved': False, 'message': str(exc)}
        request.send(result)
