"""Daily bounded motion using fixed bindings and current-operation ownership."""

import copy
import hashlib
import logging
import uuid
from types import MappingProxyType

from .ace_controlled_motion import (ASSIST, CLOSE, EJECT_TO_EMPTY, MOVE, OPEN, QUERY, RENEW, SEEK_REFERENCE,
                                    STOP, TARGET, MotionError,
                                    request_operation)
from .ace_path_store import PathStateError, REFERENCE, action_fingerprint, finite_mm
from .ace_protocol_v2 import pb_decode


ACTIVE_PHASES = frozenset(('queued', 'claiming', 'binding', 'starting', 'running',
                           'stopping', 'rebasing', 'committing'))
TERMINAL_PHASES = frozenset(('complete', 'cancelled', 'error'))
MAX_REFERENCE_MM = 5000
RECENT_RECEIPTS = 128
SPEED = 80


class _ConsumedPermitView:
    """Read live owner state while retaining one already-written permit."""
    def __init__(self, owner, overrides):
        self._owner = owner
        self._overrides = overrides

    def __getattr__(self, name):
        return self._overrides[name] if name in self._overrides else getattr(self._owner, name)


class OriginLease:
    RULES = (
        ('_manual_measure_token', '_manual_measure_owner', 'allows', 'consumed', True),
        ('_eject_token', '_slot_actions', 'eject_allows', 'eject_consumed', True),
        ('_normal_load_token', '_normal_load_speed', 'allows_feed', 'wrote', False),
        ('_normal_speed_token', '_normal_load_speed', 'allows_speed', 'wrote', False),
        ('_y_recovery_token', '_y_recovery', 'allows', 'consumed', True),
        ('_hot_token', '_hot_session', 'allows', 'consumed', True),
    )

    def __init__(self, ace, idx, request):
        self.ace, self.idx = ace, idx
        self.transport = ace._serials.get(idx)
        original = dict(request)
        original['params'] = MappingProxyType(copy.deepcopy(request.get('params') or {}))
        if isinstance(original.get('_insert_wire'), (tuple, list)):
            wire = list(original['_insert_wire'])
            if len(wire) == 7:
                wire[4] = copy.deepcopy(wire[4])
            original['_insert_wire'] = tuple(wire)
        self.request = MappingProxyType(original)
        self.rules = []
        for token_key, owner_key, checker, consumer, single_use in self.RULES:
            if token_key not in original:
                continue
            owner = getattr(ace, owner_key, None)
            token = original[token_key]
            permits = getattr(owner, 'permits', {})
            self.rules.append({
                'token_key': token_key, 'owner_key': owner_key, 'owner': owner,
                'token': token, 'entry': copy.deepcopy(permits.get(token)),
                'checker': checker, 'consumer': consumer, 'single_use': single_use,
                'actor': getattr(owner, 'owner', None),
                'eject_lease': getattr(owner, '_eject_lease', None),
                'eject_seq': (getattr(owner, 'state', {}) or {}).get('seq'),
            })
        self.operation = None
        self.parent = None
        self.consumed = False

    def allows(self, idx, serial):
        if (idx != self.idx or serial is not self.transport
                or self.ace._serials.get(idx) is not self.transport
                or self.request.get('_insert_stop_transport', self.transport) is not self.transport):
            return False
        if self.parent is not None and not self.parent.allows(idx, serial):
            return False
        wire = self.request.get('_insert_wire')
        if wire is not None and not self.ace._insert_wire_valid(idx, serial, wire):
            return False
        for rule in self.rules:
            owner = rule['owner']
            if owner is None or getattr(self.ace, rule['owner_key'], None) is not owner:
                return False
            checker = getattr(owner, rule['checker'], None)
            if not callable(checker):
                return False
            args = ((idx, self.request) if rule['owner_key'] in ('_y_recovery', '_hot_session')
                    else (idx, self.request, serial))
            if self.consumed and rule['single_use']:
                if rule['owner_key'] in ('_y_recovery', '_hot_session') and getattr(owner, 'owner', None) is not rule['actor']:
                    return False
                if rule['token_key'] == '_eject_token':
                    if (owner._eject_pending is not None or owner._eject_lease is not rule['eject_lease']
                            or owner.state.get('seq') != rule['eject_seq']):
                        return False
                    overrides = {'_eject_pending': rule['token']}
                else:
                    if rule['token'] in owner.permits or rule['entry'] is None:
                        return False
                    permits = dict(owner.permits)
                    permits[rule['token']] = rule['entry']
                    overrides = {'permits': permits}
                function = getattr(checker, '__func__', None)
                if function is None or not function(_ConsumedPermitView(owner, overrides), *args):
                    return False
            elif not checker(*args):
                return False
        return True

    def written(self, operation_code):
        if self.parent is not None:
            self.parent.written(operation_code)
        speed_update = self.request['method'] == 'update_feeding_speed'
        if self.consumed or (operation_code not in (MOVE, ASSIST, EJECT_TO_EMPTY, SEEK_REFERENCE)
                             and not (speed_update and operation_code == RENEW)):
            return
        self.consumed = True
        for rule in self.rules:
            consumer = getattr(rule['owner'], rule['consumer'], None)
            if callable(consumer):
                if rule['consumer'] == 'wrote':
                    consumer(self.idx, self.request)
                else:
                    consumer(self.request)


class Operation:
    def __init__(self, manager, kind, route, controller, action_id, parameters):
        self.manager = manager
        self.kind, self.route, self.controller = kind, route, controller
        self.action_id = action_id
        self.task_id = 'op-' + hashlib.sha256(action_id.encode()).hexdigest()[:32]
        self.parameters = parameters
        self.parent_task_id = parameters.get('parent_task_id')
        self.phase = 'queued'
        self.error = None
        self.started = False
        self.created = manager.reactor.monotonic()
        self.stage = None
        self.ledger = []
        self.edge = None
        self.initial = None
        self.target = None
        self.geometry = None
        self.basic = None
        self.permission = False
        self.callbacks = []
        self.stop_callbacks = []
        self.stop_ack_pending = False
        self.stop_requested = False
        self.cancel_before_start = False
        self.prior_owner = None
        self.result = None
        self.origin = None

    def snapshot(self):
        if self.result is not None and self.phase in TERMINAL_PHASES:
            return copy.deepcopy(self.result)
        result = {'action_id': self.action_id, 'task_id': self.task_id,
                  'kind': self.kind, 'state': self.phase, 'error': self.error,
                  'ace': self.route['ace'], 'slot': self.route['slot'],
                  'head': self.route['head'], 'started': self.started,
                  'ledger': copy.deepcopy(self.ledger)}
        result['motion'] = self.controller.snapshot()
        return result

    def wait_started(self, timeout=10):
        return self.manager._wait(self, timeout, started=True)

    def wait_finished(self, timeout=None):
        if timeout is None:
            length = self.parameters.get('length', MAX_REFERENCE_MM)
            speed = self.parameters.get('speed', SPEED)
            timeout = max(30, length / speed + 30)
            if self.kind == 'unload':
                saved = self.manager.calibration.store.state['routes'].get(self.route['route_id'], {})
                reference = saved.get('geometry') or saved.get('basic') or {}
                length = reference.get('length_mm', MAX_REFERENCE_MM)
                search_speed = reference.get('reference_speed_mm_s', SPEED)
                timeout = max(timeout, length / search_speed + length / speed + 30)
        return self.manager._wait(self, timeout, started=False)


class PathOperations:
    def __init__(self, calibration):
        self.calibration = calibration
        self.ace = calibration.ace
        self.reactor = calibration.reactor
        self.operations = {}
        self.pending = {}
        self.by_action = {}
        self.idle_stops = {}
        self.host_epoch = uuid.uuid4().hex
        self.issued_counter = 0
        self._next_insert_scan = 0.0
        calibration.operations = self

    def handles(self, idx, slot=None):
        return any(ace == idx and (slot is None or lane == slot)
                   and controller.snapshot()['available']
                   for (ace, lane), controller in self.calibration.controllers.items())

    def _route(self, idx, slot, head):
        port = self.ace._ace_devices[idx]
        _port, uid = self.ace._location_identity(idx)
        return {'route_id': 'daily/%d/%d' % (idx, slot), 'uid': list(uid or ()),
                'port': port, 'ace': idx, 'slot': slot, 'head': head,
                'topology_version': self.calibration._topology_version()}

    def _new_action_id(self):
        self.issued_counter += 1
        return 'ordinary-%s-%d' % (self.host_epoch, self.issued_counter)

    def _expired_action(self, action_id):
        parts = action_id.split('-')
        if len(parts) == 3 and parts[0] == 'ordinary':
            if parts[1] != self.host_epoch:
                raise PathStateError('旧主机会话动作回执已过期')
            try:
                counter = int(parts[2])
            except ValueError:
                counter = 0
            if 0 < counter <= self.issued_counter:
                raise PathStateError('动作回执已过期，请查询当前管路状态')
        raise PathStateError('动作标识尚未由当前主机会话签发')

    @staticmethod
    def _action_record(operation, state):
        return {'task_id': operation.task_id,
                'fingerprint': action_fingerprint(operation.parameters),
                'state': state, 'ordinary': True,
                'parent_task_id': operation.parent_task_id,
                'parameters': copy.deepcopy(operation.parameters),
                'route': copy.deepcopy(operation.route)}

    @staticmethod
    def _ordinary_record(action_id, record):
        expected = 'op-' + hashlib.sha256(action_id.encode()).hexdigest()[:32]
        return record.get('ordinary') is True or record.get('task_id') == expected

    def prune_receipts(self):
        """Bound daily history while the entry layer rejects retired IDs."""
        actions = self.calibration.actions
        protected = {operation.action_id for operation in self.operations.values()
                     if operation.phase in ACTIVE_PHASES}
        protected.update(operation.action_id for operation in self.pending.values())
        for route in self.calibration.store.state['routes'].values():
            token = route.get('last_insert_action')
            if isinstance(token, dict) and isinstance(token.get('action_id'), str):
                protected.add(token['action_id'])
        task = self.calibration.task
        protected.update(key for key, record in actions.items()
                         if record.get('task_id') == task.get('task_id')
                         or task.get('active') and record.get('parent_task_id') == task.get('task_id'))
        completed = [key for key, record in actions.items()
                     if self._ordinary_record(key, record) and key not in protected
                     and record.get('state') in ('complete', 'error')]
        for key in completed[:-RECENT_RECEIPTS]:
            del actions[key]
        retained = protected | set(actions)
        for key, operation in list(self.by_action.items()):
            if key not in retained and operation.phase in TERMINAL_PHASES:
                operation.controller.retire_actions(key + '/')
                del self.by_action[key]
        for controller in self.calibration.controllers.values():
            prefixes = {action_id.split('/', 1)[0] for action_id in controller.receipts
                        if action_id.startswith(('ordinary-', 'insert-')) and '/' in action_id}
            for prefix in prefixes - retained:
                controller.retire_actions(prefix + '/')

    def _create(self, kind, idx, slot, head, parameters, action_id=None, automatic=False):
        issued_here = action_id is None
        action_id = self._new_action_id() if issued_here else action_id
        if not isinstance(action_id, str) or not 1 <= len(action_id) <= 96:
            raise PathStateError('动作标识无效')
        head = self.calibration._head(idx, slot, head)
        route = self._route(idx, slot, head)
        parameters = dict(parameters, kind=kind, route_id=route['route_id'],
                          topology_version=route['topology_version'], head=head)
        if self.calibration.thermal_active and self.calibration.task.get('active'):
            parameters['parent_task_id'] = self.calibration.task['task_id']
        existing = self.by_action.get(action_id)
        if existing is not None:
            if existing.parameters != parameters:
                raise PathStateError('动作标识已用于另一请求')
            return existing
        controller = self.calibration.controller(idx, slot)
        operation = Operation(self, kind, route, controller, action_id, parameters)
        previous = self.calibration.store.check_action(action_id, operation.task_id, parameters)
        if previous is not None:
            if previous['state'] not in ('complete', 'error'):
                raise PathStateError('上次动作在稳定提交前中断，请先核对管路')
            operation.result = previous.get('result')
            result_state = (operation.result or {}).get('state')
            operation.phase = ('cancelled' if result_state == 'cancelled'
                               else previous['state'])
            operation.error = previous.get('error')
            self.by_action[action_id] = operation
            return operation
        if not issued_here and not automatic:
            self._expired_action(action_id)
        key = idx, slot
        current = self.operations.get(key)
        if current is not None and current.phase not in TERMINAL_PHASES:
            if current.kind != 'assist' or self.pending.get(key):
                raise PathStateError('本槽已有受控进退料动作')
            self._validate(operation)
            self.by_action[action_id] = operation
            self.pending[key] = operation
            self._request_stop(current, 'switching controlled motion mode')
            return operation
        self._validate(operation)
        self.operations[key] = operation
        self.by_action[action_id] = operation
        return operation

    def feed(self, idx, slot, head, length, speed, action_id=None, to_head=True):
        length = finite_mm(length, '送料行程', minimum=0.001)
        if type(to_head) is not bool:
            raise PathStateError('送料终点模式无效')
        return self._create('feed', idx, slot, head,
                            {'length': length, 'speed': self._speed(speed), 'to_head': to_head}, action_id)

    def retract(self, idx, slot, head, length, speed, action_id=None, bulk=False):
        length = finite_mm(length, '退料行程', minimum=0.001)
        if type(bulk) is not bool:
            raise PathStateError('退料终点模式无效')
        return self._create('unload' if bulk else 'retract', idx, slot, head,
                            {'length': length, 'speed': self._speed(speed)}, action_id)

    def assist(self, idx, slot, head, reverse=False, action_id=None):
        if type(reverse) is not bool:
            raise PathStateError('助力方向无效')
        current = self.operations.get((idx, slot))
        if (current is not None and current.kind == 'assist'
                and current.phase == 'running'
                and current.parameters['reverse'] == reverse):
            return current
        return self._create('assist', idx, slot, head,
                            {'reverse': reverse, 'speed': 50}, action_id)

    def eject(self, idx, slot, head=None, length=MAX_REFERENCE_MM, speed=80, action_id=None):
        length = finite_mm(length, '退出行程上限', minimum=1)
        return self._create('eject', idx, slot, head,
                            {'length': length, 'speed': self._speed(speed)}, action_id)

    @staticmethod
    def _speed(speed):
        if type(speed) is not int or not 1 <= speed <= 100:
            raise PathStateError('受控运动速度需要在1至100mm/s内')
        return speed

    def _same_source(self, operation):
        source = self.ace._head_source.get(operation.route['head'])
        return isinstance(source, dict) and not source.get('load_failed') and (source.get('ace_index'), source.get('slot')) == (
            operation.route['ace'], operation.route['slot'])

    def _validate(self, operation):
        self.ace._require_no_stop_fault()
        if operation.origin is not None and not operation.origin.allows(
                operation.route['ace'], operation.controller.transport.original):
            raise PathStateError('原进退料授权已失效')
        route = self._route(operation.route['ace'], operation.route['slot'], operation.route['head'])
        if any(route[key] != operation.route[key] for key in ('ace', 'slot', 'head', 'port', 'topology_version')):
            raise PathStateError('受控动作的设备或接管方式已改变')
        sample = operation.controller.snapshot()
        if not sample['available']:
            raise PathStateError(sample.get('capability_reason') or '受控固件尚未握手')
        if operation.phase not in TERMINAL_PHASES and not sample['inlet'] and operation.kind != 'eject':
            raise PathStateError('ACE入口耗材状态已改变')
        task = self.calibration.task
        if operation.parent_task_id is not None and (
                task.get('task_id') != operation.parent_task_id or not task.get('active')
                or getattr(self.calibration, 'stop_requested', False)
                or getattr(self.calibration, 'cancel_requested', False)):
            raise PathStateError('原校准准备任务已停止或被替换')
        quiescing = (getattr(self.calibration, 'quiescing_assists', False)
                     and task.get('stage') == 'preparing' and operation.kind == 'assist'
                     and operation.phase in ('running', 'stopping', 'committing'))
        if task.get('active') and not quiescing and not (self.calibration.thermal_active
                                       and task.get('head') == route['head']):
            raise PathStateError('请等待当前料管校准结束')
        if operation.kind == 'park_insert':
            return
        head_present = self.calibration.head_sensor(route['head'])
        if operation.kind == 'eject':
            source = self.ace._head_source.get(route['head'])
            if head_present and (source is None or self._same_source(operation)):
                raise PathStateError('请先完成目标喷头热卸载，再退出ACE')
            return
        source = self.ace._head_source.get(route['head'])
        observed_feed = (operation.kind == 'feed' and operation.permission
                         and operation.initial is not None
                         and operation.initial.get('head_present') is False
                         and (source is None or source.get('load_failed')))
        if head_present and not self._same_source(operation) and not observed_feed:
            raise PathStateError('喷头实际供料来源尚未确认')
        if operation.kind == 'assist' and not self._same_source(operation):
            raise PathStateError('助力必须绑定已确认的喷头供料源')
        if operation.kind == 'assist' and not operation.parameters['reverse'] and not head_present:
            raise PathStateError('正向助力需要喷头物理到料确认')

    def _peer_routes_clear(self, operation):
        # A terminal task may still be waiting for its asynchronous STOP.
        # Retain only that live motion exclusion, never historical position locks.
        for other in self.operations.values():
            if other is operation or other.route['head'] != operation.route['head']:
                continue
            controller = other.controller
            stopping = (controller.device.motion_owners.get(other.route['slot']) is controller
                        or controller.snapshot()['state'] in ('starting', 'running', 'stopping'))
            if (other.phase in ACTIVE_PHASES and other.permission) or stopping:
                raise PathStateError('同一喷头已有进退料任务，请等待结束')

    def _claim(self, operation):
        self._validate(operation)
        self._peer_routes_clear(operation)
        operation.initial = copy.deepcopy(operation.controller.snapshot())
        operation.initial['head_present'] = self.calibration.head_sensor(operation.route['head'])
        if operation.kind == 'unload' and operation.initial['head_present']:
            raise PathStateError('请先完成喷头热卸载与清空确认')
        if operation.kind == 'park_insert':
            saved = self.calibration.store.state['routes'].get(operation.route['route_id'], {})
            operation.geometry = copy.deepcopy(saved.get('geometry'))
            if not operation.geometry:
                raise PathStateError('自动停车距离尚未设置')
        self.calibration.actions[operation.action_id] = self._action_record(operation, 'running')
        self.prune_receipts()
        operation.permission = True
        self._begin(operation)

    def _bind(self, operation, lower, upper, stage):
        operation.stage = stage
        operation.phase = 'binding'
        # The configured ACE/head mapping is authoritative. The controller still
        # binds the current transport and refreshes its own telemetry as needed.
        operation.controller.bind(lower_mm=lower, upper_mm=upper,
                                  callback=lambda _snapshot, error: self._binding_reply(operation, error),
                                  origin=operation.origin)

    def _binding_reply(self, operation, error):
        if operation.cancel_before_start:
            return
        if error is not None:
            self._fail(operation, str(error))

    def _begin(self, operation):
        sample = operation.controller.snapshot()
        parameters = operation.parameters
        if operation.kind == 'assist':
            self._bind(operation, -MAX_REFERENCE_MM, MAX_REFERENCE_MM, 'assist')
        elif operation.kind == 'eject':
            self._bind(operation, -parameters['length'], 0,
                       'eject' if sample['inlet'] else 'already_empty')
        elif operation.kind in ('retract', 'unload'):
            self._bind(operation, -parameters['length'], 1, 'relative_reverse')
        elif operation.kind == 'feed':
            self._bind(operation, 0, parameters['length'], 'relative_forward')
        else:
            if sample['position_mm'] is None or not sample['trusted']:
                raise PathStateError('自动停车需要当前插料基准')
            operation.target = operation.geometry['park_mm']
            self._bind(operation, 0, operation.target + operation.geometry['uncertainty_mm'], 'absolute')

    def _start(self, operation):
        self._validate(operation)
        operation.phase = 'starting'
        operation.stop_requested = False
        operation.stop_ack_pending = False
        controller = operation.controller
        parameters = operation.parameters
        action = operation.action_id + '/' + operation.stage
        travel = getattr(self.ace, '_head_travel', None)
        if travel is not None:
            travel.motion_started(action, operation.route['head'], operation.route['ace'],
                                  operation.route['slot'], operation.kind)

        def accepted(_snapshot, error):
            if error is not None:
                self._fail(operation, str(error))
                return
            operation.started = True
            operation.phase = 'running'
            callbacks, operation.callbacks = operation.callbacks, []
            for callback in callbacks:
                callback(operation, None)

        if operation.stage == 'assist':
            controller.assist(action, reverse=parameters['reverse'], callback=accepted)
        elif operation.stage == 'eject':
            controller.eject_to_empty(action, parameters['length'], parameters['speed'], accepted)
        elif operation.stage == 'already_empty':
            controller.confirm_stopped(action, accepted)
        elif operation.stage == 'relative_reverse':
            controller.reference_move(action, -parameters['length'], parameters['speed'], accepted)
        elif operation.stage == 'relative_forward':
            controller.reference_move(action, parameters['length'], parameters['speed'], accepted)
        elif operation.stage == 'seek_unload':
            reference_speed = (operation.geometry or operation.basic).get('reference_speed_mm_s', 80)
            reference = operation.geometry or operation.basic
            budget = min(MAX_REFERENCE_MM, reference['length_mm'] + reference.get('uncertainty_mm', 0))
            controller.seek_reference(action, budget,
                                      reference_speed, accepted)
        else:
            controller.start(action, operation.target, parameters['speed'], accepted)

    def _capture_edge(self, operation, sample, present):
        operation.edge = {'present': present, 'position_mm': sample['position_mm'],
                          'sample_sequence': sample['sample_sequence'],
                          'observed_at': self.reactor.monotonic()}
        self._request_stop(operation, 'head sensor reference reached')

    def _observe(self, operation):
        self._validate(operation)
        sample = operation.controller.snapshot()
        if sample['state'] in ('unknown', 'unavailable'):
            raise PathStateError(sample.get('error') or '受控运动位置证据不足')
        if operation.phase == 'binding':
            if sample['state'] == 'ready':
                self._start(operation)
            return
        if operation.phase not in ('running', 'stopping'):
            return
        present = self.calibration.head_sensor(operation.route['head'])
        if operation.phase == 'running' and sample['state'] in ('starting', 'running'):
            if operation.stage == 'seek_unload' and present:
                self._capture_edge(operation, sample, True)
            elif operation.kind == 'feed' and present and not operation.initial.get('head_present', False):
                self._capture_edge(operation, sample, True)
            elif operation.kind == 'retract' and operation.initial.get('head_present') and not present:
                self._capture_edge(operation, sample, False)
        if sample['state'] in ('complete', 'cancelled'):
            self._terminal(operation, sample, present)

    def _terminal(self, operation, sample, head_present):
        travel = getattr(self.ace, '_head_travel', None)
        if travel is not None:
            travel.motion_finished(operation.action_id + '/' + operation.stage,
                                   sample['travelled_mm'])
        if not operation.ledger or operation.ledger[-1]['motion_id'] != sample['motion_id']:
            operation.ledger.append({'stage': operation.stage, 'travelled_mm': sample['travelled_mm'],
                                     'position_mm': sample['position_mm'], 'edge': copy.deepcopy(operation.edge),
                                     'motion_id': sample['motion_id']})
        if operation.kind == 'unload' and head_present:
            raise PathStateError('退料结束后喷头仍有耗材')
        if operation.kind == 'eject' and sample['inlet'] and not operation.stop_requested:
            raise PathStateError('退出动作未确认入口退空')
        # A feed that exhausts its distance without reaching the head returns to
        # the original loader's four-attempt / 50 mm retry loop.
        self._commit_terminal(operation, sample, head_present)

    def _position(self, operation, sample, state, trusted=None):
        return {'state': state, 'position_mm': sample['position_mm'],
                'trusted': sample['trusted'] if trusted is None else trusted,
                'boot_epoch': sample['boot_epoch'], 'insertion_epoch': sample['insertion_epoch'],
                'motion_id': sample['motion_id'], 'task_id': operation.task_id}

    def _commit_terminal(self, operation, sample, head_present):
        sensor_stop = operation.edge is not None
        final_phase = ('cancelled' if sample['state'] == 'cancelled' and not sensor_stop
                       else 'complete')
        if operation.kind == 'feed' and head_present and sensor_stop:
            self._publish_source(operation)
        operation.permission = False
        operation.phase = final_phase
        operation.result = dict(operation.snapshot(), state=final_phase,
                                head_reached=head_present)
        self.calibration.actions[operation.action_id].update(state='complete',
                                                             result=copy.deepcopy(operation.result))
        if operation.kind == 'park_insert':
            saved = self.calibration.store.state['routes'].get(operation.route['route_id'])
            if saved is not None:
                saved['last_insert_action'] = {'action_id': operation.action_id,
                    'boot_epoch': sample['boot_epoch'], 'insertion_epoch': sample['insertion_epoch']}
        self.prune_receipts()

    def _publish_source(self, operation):
        route = operation.route
        existing = self.ace._head_source.get(route['head'])
        if existing is not None and not existing.get('load_failed'):
            if not self._same_source(operation):
                raise PathStateError('喷头来源在到料确认后改变')
            return
        info = self.ace._info_per_ace.get(route['ace'], {})
        slots = info.get('slots') or []
        slot = next((item for index, item in enumerate(slots)
                     if isinstance(item, dict) and item.get('index', index) == route['slot']), {})
        source = {'ace_index': route['ace'], 'slot': route['slot'],
                  'type': slot.get('type', ''), 'brand': slot.get('brand', ''),
                  'color': self.ace._device_color_hex(slot)}
        source = self.ace._overlay_override(route['ace'], route['slot'], source)
        source = self.ace._inherit_prev_capture(route['head'], route['ace'], route['slot'], source)
        self.ace._head_source[route['head']] = source
        try:
            self.ace._save_head_source()
        except Exception:
            self.ace._head_source[route['head']] = existing
            raise

    def _fail(self, operation, message):
        travel = getattr(self.ace, '_head_travel', None)
        if travel is not None:
            travel.motion_finished(operation.action_id + '/' + operation.stage, None, False)
        operation.error = message
        operation.permission = False
        operation.phase = 'error'
        sample = operation.controller.snapshot()
        if (operation.controller.device.motion_owners.get(operation.route['slot']) is operation.controller
                or sample['state'] in ('starting', 'running', 'stopping')):
            try:
                operation.controller.stop(reason=message)
            except MotionError:
                self.ace._latch_stop_fault(operation.route['ace'], operation.route['slot'], message,
                                           identity=(operation.route['port'], tuple(operation.route['uid']) or None))
        record = self.calibration.actions.setdefault(operation.action_id, self._action_record(operation, 'error'))
        record.update(state='error', error=message)
        self.prune_receipts()
        callbacks, operation.callbacks = operation.callbacks, []
        for callback in callbacks:
            callback(operation, PathStateError(message))
        callbacks, operation.stop_callbacks = operation.stop_callbacks, []
        for callback in callbacks:
            callback(operation, PathStateError(message))
        printing = getattr(self.ace, '_is_actively_printing', lambda: False)()
        if printing:
            active = self.ace.toolhead.get_extruder().get_name()
            head = 0 if active == 'extruder' else int(active.removeprefix('extruder'))
            if head == operation.route['head'] or self.calibration.store.error:
                self.ace.error_msg = message
                self.reactor.register_async_callback(
                    lambda eventtime: self.ace.gcode.run_script('PAUSE'))

    def persistence_failed(self, message):
        for operation in list(self.operations.values()):
            if operation.phase in ACTIVE_PHASES:
                self._fail(operation, message)

    def _request_stop(self, operation, reason, callback=None):
        if callback is not None:
            operation.stop_callbacks.append(callback)
        if not operation.started and operation.phase in ('queued', 'claiming', 'binding'):
            operation.cancel_before_start = True
            if operation.phase != 'claiming':
                self._cancel_unstarted(operation)
            return
        if operation.controller.snapshot()['state'] in ('complete', 'cancelled'):
            callbacks, operation.stop_callbacks = operation.stop_callbacks, []
            for finished in callbacks:
                finished(operation, None)
            return
        if operation.stop_requested:
            if not operation.stop_ack_pending:
                callbacks, operation.stop_callbacks = operation.stop_callbacks, []
                for finished in callbacks:
                    finished(operation, None)
            return
        operation.stop_requested = True
        operation.stop_ack_pending = True
        operation.phase = 'stopping'

        def acknowledged(_snapshot, error):
            operation.stop_ack_pending = False
            callbacks, operation.stop_callbacks = operation.stop_callbacks, []
            for finished in callbacks:
                finished(operation, error)
            if error is not None:
                self._fail(operation, str(error))

        operation.controller.stop(reason=reason, callback=acknowledged)

    def _cancel_unstarted(self, operation):
        operation.permission = False
        operation.phase = 'cancelled'
        record = self.calibration.actions.get(operation.action_id)
        if record is not None:
            record.update(state='complete', result={'state': 'cancelled'})
        self._cancel_callbacks(operation)

    def _cancel_callbacks(self, operation):
        callbacks, operation.stop_callbacks = operation.stop_callbacks, []
        for finished in callbacks:
            finished(operation, None)
        callbacks, operation.callbacks = operation.callbacks, []
        for finished in callbacks:
            finished(operation, PathStateError('动作已在启动前取消'))

    def stop(self, idx, slot, reason='stopped', callback=None):
        operation = self.operations.get((idx, slot))
        if operation is not None and operation.phase in ACTIVE_PHASES:
            self._request_stop(operation, reason, callback)
            return operation
        return None

    def is_ready(self, idx):
        self.ace._require_no_stop_fault()
        idle_stops = [entry for (ace, _slot), entry in self.idle_stops.items() if ace == idx]
        failed_stop = next((entry for entry in idle_stops if entry['error']), None)
        if failed_stop is not None:
            raise PathStateError(failed_stop['error'])
        if idle_stops:
            return False
        active = [op for (ace, _slot), op in self.operations.items() if ace == idx]
        if any(op.phase == 'error' and op.controller.device.motion_owners.get(op.route['slot']) is op.controller
               for op in active):
            return False
        return all(op.phase in TERMINAL_PHASES or (op.kind == 'assist' and op.phase == 'running')
                   for op in active)

    def wait_ready(self, idx, timeout=130):
        deadline = self.reactor.monotonic() + timeout
        while not self.is_ready(idx):
            if self.reactor.monotonic() >= deadline:
                for (ace, slot), operation in self.operations.items():
                    if ace == idx and operation.phase in ACTIVE_PHASES:
                        self._fail(operation, '受控动作等待超过时限')
                raise PathStateError('受控动作等待超过时限')
            self.reactor.pause(min(deadline, self.reactor.monotonic() + 0.02))

    def _wait(self, operation, timeout, started):
        deadline = self.reactor.monotonic() + timeout
        while operation.phase not in TERMINAL_PHASES and not (started and operation.started):
            if self.reactor.monotonic() >= deadline:
                self._fail(operation, '受控动作等待超过时限')
                break
            self.reactor.pause(min(deadline, self.reactor.monotonic() + 0.02))
        if operation.phase == 'error':
            raise PathStateError(operation.error)
        return operation.snapshot()

    def status(self):
        return [operation.snapshot() for operation in self.operations.values()]

    def allows_wire(self, idx, request, serial=None):
        try:
            operation_code = request_operation(request)
        except MotionError:
            return False
        if operation_code is None:
            return False
        owner = request.get('_controlled_owner')
        original = request.get('_controlled_transport')
        if (original is None or original is not self.ace._serials.get(idx)
                or serial is not None and serial is not original):
            return False
        controllers = [controller for (ace, _slot), controller in self.calibration.controllers.items()
                       if ace == idx and controller.token is owner
                       and controller.transport.original is original]
        if not controllers:
            return False
        if operation_code in (QUERY, STOP, CLOSE):
            return True
        if not self.origin_allows_wire(idx, request, serial or original):
            return False
        operation = next((item for item in self.operations.values()
                          if item.controller is controllers[0] and item.permission
                          and item.phase in ACTIVE_PHASES), None)
        if operation is None:
            return False
        try:
            self._validate(operation)
        except (MotionError, PathStateError):
            return False
        return True

    def origin_allows_wire(self, idx, request, serial):
        try:
            code = request_operation(request)
            if code in (QUERY, STOP, CLOSE) or code is None:
                return True
            origin = request.get('_controlled_origin')
            operation = next((item for item in self.operations.values()
                              if item.controller.token is request.get('_controlled_owner')
                              and item.route['ace'] == idx and item.permission
                              and item.phase in ACTIVE_PHASES), None)
            if origin is None:
                return operation is None or operation.origin is None
            return (isinstance(origin, OriginLease) and origin.operation is operation
                    and operation is not None and origin.allows(idx, serial))
        except Exception:
            return False

    def origin_written(self, request):
        code = request_operation(request)
        if code in (None, QUERY, STOP, CLOSE):
            return
        origin = request.get('_controlled_origin')
        if (isinstance(origin, OriginLease) and origin.operation is not None
                and origin.operation.controller.token is request.get('_controlled_owner')):
            origin.written(code)

    def allow_location_recovery(self, idx, request):
        try:
            code = request_operation(request)
        except MotionError:
            return False
        if code not in (OPEN, EJECT_TO_EMPTY, RENEW):
            return False
        operation = next((item for item in self.operations.values()
                          if item.route['ace'] == idx and item.kind == 'eject'
                          and item.controller.token is request.get('_controlled_owner')
                          and item.phase in ACTIVE_PHASES), None)
        return operation is not None and self.allows_wire(idx, request)

    allows_location_recovery = allow_location_recovery

    def _compat(self, callback, command, operation=None, error=None):
        if callback is None:
            return
        response = {
            'code': 1 if error else 0, 'msg': 'FORBIDDEN' if error else 'success',
            'result': {'controlled_action': operation.action_id if operation else None,
                       'error': str(error) if error else None},
            '_v2_frame_valid': error is None, '_v2_payload_valid': error is None,
            '_v2_command': command, '_controlled_command': 92,
        }
        try:
            callback(self=self.ace, response=response)
        except Exception:
            logging.exception('[multiACE] controlled compatibility callback failed')

    def _acknowledge(self, operation, callback, command):
        if operation.phase == 'error':
            self._compat(callback, command, operation, operation.error)
        elif operation.started or operation.phase in TERMINAL_PHASES:
            self._compat(callback, command, operation)
        elif callback is not None:
            operation.callbacks.append(lambda item, error: self._compat(callback, command, item, error))

    def _raw_parameters(self, params):
        if not isinstance(params.get('hex'), str):
            raise PathStateError('原始运动参数无效')
        try:
            fields = pb_decode(bytes.fromhex(params['hex']))
        except (ValueError, IndexError) as error:
            raise PathStateError('原始运动载荷无效') from error
        if any(field not in (1, 2, 3, 4) or len(values) != 1
               or values[0][0] != 0 or type(values[0][1]) is not int
               for field, values in fields.items()):
            raise PathStateError('原始运动字段无效')
        return {'index': fields.get(1, [(0, 0)])[0][1],
                'speed': fields.get(2, [(0, 50)])[0][1],
                'length': fields.get(3, [(0, 0)])[0][1],
                'mode': fields.get(4, [(0, 0)])[0][1]}

    def _stop_unowned(self, idx, slot, callback, command):
        key = idx, slot
        previous = self.idle_stops.get(key)
        if previous is not None:
            if previous['acknowledged']:
                self._compat(callback, command, error=previous['error'])
            else:
                previous['callbacks'].append(callback)
            return True
        controller = self.calibration.controller(idx, slot)
        sample = controller.snapshot()
        active = sample['state'] in ('starting', 'running', 'stopping')
        if not active and not controller.device.opened:
            return False
        entry = {'controller': controller, 'callbacks': [callback],
                 'acknowledged': False, 'error': None}
        self.idle_stops[key] = entry

        def acknowledged(_sample, error):
            entry['acknowledged'] = True
            entry['error'] = str(error) if error is not None else None
            callbacks, entry['callbacks'] = entry['callbacks'], []
            for finished in callbacks:
                self._compat(finished, command, error=error)

        if active:
            controller.stop(reason='explicit slot stop', callback=acknowledged)
        else:
            def bound(_sample, error):
                if error is not None:
                    acknowledged(_sample, error)
                    return
                controller.confirm_stopped('idle-stop-%d' % slot, acknowledged)
            controller.bind(lower_mm=-MAX_REFERENCE_MM, upper_mm=MAX_REFERENCE_MM, callback=bound)
        return True

    def intercept(self, idx, request, callback):
        if not isinstance(request, dict):
            return False
        method = request.get('method')
        params = request.get('params') or {}
        if not isinstance(params, dict):
            return False
        command = 8
        if method == 'raw':
            command = params.get('cmd')
            if command not in (8, 9, 10, 77):
                return False
            try:
                params = self._raw_parameters(params)
            except PathStateError as error:
                self._compat(callback, command, error=error)
                return True
            method = {8: 'feed_or_rollback_raw', 77: 'feed_or_rollback_raw',
                      9: 'stop_feed_filament', 10: 'update_feeding_speed'}[command]
        elif method not in ('feed_filament', 'unwind_filament', 'feed_or_rollback_raw',
                             'start_feed_assist', 'stop_feed_filament',
                             'stop_feed_assist', 'update_feeding_speed'):
            return False
        try:
            slot = params.get('index', 0)
            if type(slot) is not int or not 0 <= slot < 4:
                raise PathStateError('运动槽位无效')
            if not self.handles(idx, slot):
                return False
            if method in ('stop_feed_filament', 'stop_feed_assist'):
                command = 9
                if request.get('_insert_stop_transport', self.ace._serials.get(idx)) is not self.ace._serials.get(idx):
                    raise PathStateError('原停止连接已改变')
                operation = self.stop(idx, slot, callback=lambda item, error: self._compat(
                    callback, command, item, error))
                if operation is None:
                    return self._stop_unowned(idx, slot, callback, command)
                return True
            origin = OriginLease(self.ace, idx, request)
            if not origin.allows(idx, self.ace._serials.get(idx)):
                raise PathStateError('原进退料授权已失效')
            head = self.calibration._head(idx, slot, request.get('_path_head'))
            if method == 'update_feeding_speed':
                command = 10
                operation = self.operations.get((idx, slot))
                if operation is None or operation.phase != 'running':
                    raise PathStateError('本槽没有可更新的受控运动')
                origin.operation = operation
                origin.parent = operation.origin
                if operation.kind != 'assist':
                    operation.controller.update(operation.controller.action['id'], speed_mm_s=params.get('speed'),
                                                callback=lambda _sample, error: self._compat(
                                                    callback, command, operation, error), origin=origin)
                else:
                    operation.controller.renew(callback=lambda _sample, error: self._compat(
                        callback, command, operation, error), origin=origin)
                return True
            mode = params.get('mode', 0)
            if method == 'unwind_filament':
                mode = 1
            elif method == 'start_feed_assist':
                mode = 2
            if type(mode) is not int or mode not in (0, 1, 2, 3):
                raise PathStateError('运动模式无效')
            if mode in (2, 3):
                operation = self.assist(idx, slot, head, reverse=mode == 3)
            elif mode == 1:
                operation = self.retract(idx, slot, head, params.get('length'), params.get('speed', 80),
                                         bulk=request.get('_path_bulk_unload', False))
            else:
                operation = self.feed(idx, slot, head, params.get('length'), params.get('speed', 80),
                                      to_head=request.get('_path_to_head', False))
            origin.operation = operation
            if operation.origin is None:
                operation.origin = origin
            elif operation.started and operation.kind == 'assist':
                origin.parent = operation.origin
                operation.controller.renew(callback=lambda _sample, error: self._compat(
                    callback, command, operation, error), origin=origin)
                return True
            self._acknowledge(operation, callback, command)
        except (MotionError, PathStateError, TypeError, ValueError) as error:
            self._compat(callback, command, error=error)
        return True


    def tick(self, now):
        for key, entry in list(self.idle_stops.items()):
            sample = entry['controller'].snapshot()
            if sample['state'] in ('complete', 'cancelled'):
                if not entry['acknowledged']:
                    callbacks, entry['callbacks'] = entry['callbacks'], []
                    for finished in callbacks:
                        self._compat(finished, 9)
                del self.idle_stops[key]
            elif sample['state'] in ('unknown', 'unavailable'):
                error = sample.get('error') or '料槽停止未确认'
                for finished in entry['callbacks']:
                    self._compat(finished, 9, error=error)
                del self.idle_stops[key]
        for key, operation in list(self.pending.items()):
            current = self.operations.get(key)
            if current is None or current.phase in TERMINAL_PHASES:
                self.operations[key] = operation
                del self.pending[key]
        for operation in list(self.operations.values()):
            if operation.phase not in ACTIVE_PHASES:
                continue
            try:
                if operation.phase == 'queued':
                    if operation.controller.device.motion_owners.get(operation.route['slot']) is None:
                        operation.initial = operation.controller.snapshot()
                        operation.initial['head_present'] = self.calibration.head_sensor(operation.route['head'])
                        self._claim(operation)
                elif operation.phase not in ('claiming', 'committing', 'rebasing'):
                    self._observe(operation)
            except (PathStateError, MotionError) as error:
                self._fail(operation, str(error))
        return now + 0.05
