"""Reactor-owned batch dispatcher. No G-code execution or hardware by itself.

The backend must derive plans from verified live routing, atomically claim the
same resources as all other motion entrances, and make begin() nonblocking.
Its poll() may report a released terminal state only after actual motion
verification and durable claim release. All public methods run on the same
reactor, without yielding inside the scheduler. Never trust UI parking flags.
"""
from dataclasses import dataclass
from uuid import uuid4
from multi_mode_route import ExitRoute

ACTIONS = ('load', 'unload', 'eject')
ACTIVE = ('running', 'stopping', 'recovery')


def index(value):
    return type(value) is int and 0 <= value < 4


@dataclass(frozen=True)
class Bay:
    ace: int
    slot: int

    def __post_init__(self):
        if not index(self.ace) or not index(self.slot):
            raise ValueError('invalid ACE/slot')


@dataclass(frozen=True)
class Plan:
    bay: Bay
    # 'parked' means backend verified the tip is in its own branch, not
    # inferred from an empty head switch. Other routes use the common head.
    parked: bool = False

    def resources(self, action):
        independent = action == 'eject' and self.parked is True
        route = ExitRoute(self.bay.ace, self.bay.slot, independent)
        keys = {('lane', ace, slot) for ace in range(4) for slot in range(4)
                if route.blocks_motion(ace, slot)}
        if not independent:
            keys.add(('head', self.bay.slot))
            keys.add(('tool_motion',))  # existing shared trapq/extruder path
        return frozenset(keys)


def parse_batch(body):
    if not isinstance(body, dict) or set(body) != {'action', 'slots'}:
        raise ValueError('expected action and slots only')
    action, rows = body['action'], body['slots']
    if action not in ACTIONS or not isinstance(rows, list) or not 1 <= len(rows) <= 16:
        raise ValueError('invalid material batch')
    bays = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'ace','slot'}:
            raise ValueError('expected explicit ACE and slot')
        bay = Bay(row['ace'],row['slot'])
        if bay in bays:
            raise ValueError('duplicate slot')
        bays.append(bay)
    if action == 'load' and len({b.slot for b in bays}) != len(bays):
        raise ValueError('select at most one spool per head for loading')
    return action, bays


@dataclass
class Task:
    id: str
    batch: str
    action: str
    bay: Bay
    state: str = 'queued'
    resources: frozenset = frozenset()
    handle: object = None
    message: str = ''

    def public(self):
        return dict(id=self.id, batch=self.batch, action=self.action,
                    ace=self.bay.ace, slot=self.bay.slot, state=self.state,
                    message=self.message)


class BatchScheduler:
    def __init__(self, backend):
        self.backend = backend
        self.tasks = {}

    def submit(self, body):
        action, bays = parse_batch(body)
        active = {t.bay for t in self.tasks.values() if t.state in ACTIVE+('queued',)}
        if active.intersection(bays):
            raise ValueError('selected slot already has a task')
        batch = uuid4().hex
        for bay in bays:
            ident = uuid4().hex
            self.tasks[ident] = Task(ident, batch, action, bay)
        return batch

    def status(self, batch):
        return [t.public() for t in self.tasks.values() if t.batch == batch]

    def _used(self):
        return frozenset(k for t in self.tasks.values() if t.state in ACTIVE for k in t.resources)

    def advance(self):
        # Poll every running task first, then start ALL compatible queued
        # tasks in one pass. A pending/failed bay never blocks unrelated bays.
        for task in tuple(self.tasks.values()):
            if task.state not in ('running','stopping'):
                continue
            try:
                result = self.backend.poll(task.handle)
                if result in ('complete','cancelled','failed'):
                    # Backend contract: exact verified terminal + lease release.
                    task.state = result
                elif result == 'recovery':
                    task.state = 'recovery'
                elif result != 'running':
                    raise ValueError('invalid backend result')
            except Exception as e:
                task.state, task.message = 'recovery', str(e)
        for task in tuple(self.tasks.values()):
            if task.state != 'queued':
                continue
            try:
                plan = self.backend.plan(task.action, task.bay)
                if plan is None:  # known transient conflict; try next tick
                    continue
                if not isinstance(plan, Plan) or plan.bay != task.bay:
                    raise ValueError('route not confirmed')
                resources = plan.resources(task.action)
            except Exception as e:
                task.state, task.message = 'rejected', str(e)
                continue
            if resources & self._used():
                continue
            try:
                # Atomic backend lease across actual global motion entrances.
                # No dispatch before claim; busy means no motion occurred.
                handle = self.backend.claim(task.id, task.action, plan, resources)
            except Exception as e:
                task.state, task.message = 'rejected', str(e)
                continue
            if handle is None:
                continue
            task.resources, task.handle, task.state = resources, handle, 'running'
            try:
                self.backend.begin(handle)
            except Exception as e:
                # A write could already have happened. Retain the claim.
                task.state, task.message = 'recovery', str(e)

    def cancel(self, task_id):
        task = self.tasks.get(task_id)
        if task is None:
            return False
        if task.state == 'queued':
            task.state = 'cancelled'
            return True
        if task.state not in ('running','stopping','recovery'):
            return False
        try:
            if self.backend.stop(task.handle) is not True:
                return False
            task.state = 'stopping'  # ACK is not a verified stop
            return True
        except Exception as e:
            task.state, task.message = 'recovery', str(e)
            return False
