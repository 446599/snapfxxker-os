"""Offline specification, NOT firmware, a hardware driver, or an enforcement hook.

All events are serialized and atomic in this deterministic model. Hardware work
is represented by effects for an external executor; it never runs in an event.
A logical RF lease is deliberately distinct from the short metadata/command
lock. No serial/reader wait or hardware callback runs while that lock is held.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum


class Owner(str, Enum):
    BACKGROUND = "background"
    CMD68 = "cmd68"
    HOST_TUNNEL = "host_tunnel"


class State(str, Enum):
    IDLE = "idle"
    ACTIVE = "active"
    DRAINING = "draining"
    CLEANING = "cleaning"
    UNKNOWN = "unknown"
    RECOVERING = "recovering"


class Refused(RuntimeError):
    """A fail-closed request rejection; it never transfers ownership."""


@dataclass(frozen=True)
class Lease:
    boot_epoch: str
    generation: int
    owner: Owner
    connection_generation: int | None
    session_generation: int


@dataclass(frozen=True)
class Ticket:
    lease: Lease
    sequence: int
    kind: str
    label: str


@dataclass(frozen=True)
class Effect:
    kind: str
    ticket: Ticket


@dataclass(frozen=True)
class Result:
    lease: Lease
    successful: bool
    reason: str
    released_at: int


# Semantic obligations only. These are NOT RC522 register names or addresses.
CLEANUP_STEPS = (
    "stop_reader_engine_and_timer",
    "clear_crypto_authentication",
    "flush_fifo_and_pending_events",
    "restore_reader_configuration",
    "restore_antenna_selection_and_rf_baseline",
    "verify_quiescent_baseline",
)


class Arbiter:
    def __init__(self, *, boot_epoch="offline-test-only", lease_limit=20,
                 lifetime_limit=60, operation_timeout=5, drain_timeout=5,
                 cleanup_timeout=5):
        limits = (lease_limit, lifetime_limit, operation_timeout,
                  drain_timeout, cleanup_timeout)
        if any(type(x) is not int or x <= 0 for x in limits):
            raise ValueError("timeouts must be positive integer model ticks")
        if not isinstance(boot_epoch, str) or not boot_epoch:
            raise ValueError("boot_epoch is required")
        self.boot_epoch = boot_epoch
        self.lease_limit = lease_limit
        self.lifetime_limit = lifetime_limit
        self.operation_timeout = operation_timeout
        self.drain_timeout = drain_timeout
        self.cleanup_timeout = cleanup_timeout
        self.now = 0
        self.state = State.IDLE
        self.lease = None
        self.pending = None
        self.acquired_at = None
        self.deadline = None
        self.work_deadline = None
        self.close_reason = None
        self.cleanup_index = None
        self.connected = False
        self.connection_generation = 0
        self._generation = 0
        self._session_generation = 0
        self._sequence = 0
        self._locked = False
        self.irq_masked = False
        self.effects = []
        self.results = []
        self.audit = []

    @contextmanager
    def _atomic(self):
        if self._locked or self.irq_masked:
            raise AssertionError("nested command lock or IRQ masking forbidden")
        self._locked = True
        try:
            yield
            self.check_invariants()
        finally:
            self._locked = False

    def check_invariants(self):
        assert not self.irq_masked, "IRQ masking is forbidden in this model"
        assert (self.state == State.IDLE) == (self.lease is None)
        if self.state == State.IDLE:
            assert self.pending is None and self.deadline is None
            assert self.work_deadline is None and self.close_reason is None
        else:
            assert self.lease.generation == self._generation
        if self.pending is not None:
            assert self.pending.lease == self.lease
        if self.state == State.ACTIVE:
            assert self.close_reason is None and self.now < self.deadline
            assert self.pending is None or self.pending.kind == "operation"
        if self.state == State.DRAINING:
            assert self.pending is not None and self.pending.kind == "operation"
            assert self.close_reason is not None
        if self.state == State.CLEANING:
            assert self.pending is not None and self.pending.kind == "cleanup"
            assert self.pending.label == CLEANUP_STEPS[self.cleanup_index]
        if self.state == State.RECOVERING:
            assert self.pending is not None and self.pending.kind == "recovery"
        assert len({r.lease for r in self.results}) == len(self.results)
        assert all(r.lease != self.lease for r in self.results)

    def _event(self, name, detail=None):
        self.audit.append((self.now, name, self.lease, detail))

    def _ticket(self, kind, label, timeout):
        self._sequence += 1
        self.pending = Ticket(self.lease, self._sequence, kind, label)
        self.work_deadline = self.now + timeout
        self.effects.append(Effect("execute_" + kind, self.pending))
        self._event("issued", self.pending)
        return self.pending

    def take_effects(self):
        # Dispatch must occur after the atomic event has returned.
        self.assert_unlocked()
        effects, self.effects = tuple(self.effects), []
        return effects

    def effect_is_current(self, effect):
        """Fence a queued effect in the serialized model before dispatch.

        The real executor must additionally serialize dispatch with revocation
        and drain already-claimed work. A check alone is not a hardware lock.
        """
        with self._atomic():
            self._expire()
            if not isinstance(effect, Effect) or effect.ticket != self.pending:
                return False
            allowed = {
                "execute_operation": State.ACTIVE,
                "cancel_and_drain": State.DRAINING,
                "execute_cleanup": State.CLEANING,
                "execute_recovery": State.RECOVERING,
            }
            return allowed.get(effect.kind) == self.state

    def assert_unlocked(self):
        assert not self._locked and not self.irq_masked

    def external_wait(self, reason="hardware_or_serial_wait"):
        """Test instrumentation only; does not block or access any device."""
        self.assert_unlocked()
        self._event("wait_outside_lock", reason)

    def _validate_ttl(self, ttl):
        if type(ttl) is not int or not 1 <= ttl <= self.lease_limit:
            raise Refused("invalid lease duration")

    def _authorize(self, lease, *, active=True):
        if lease != self.lease or self.lease is None:
            raise Refused("stale or foreign lease")
        if lease.connection_generation is not None:
            if (not self.connected or
                    lease.connection_generation != self.connection_generation):
                raise Refused("disconnected or superseded connection")
        if active and self.state != State.ACTIVE:
            raise Refused("lease is no longer active")

    def connect(self):
        with self._atomic():
            self._expire()
            if self.lease and self.lease.owner != Owner.BACKGROUND:
                self._revoke("connection_replaced")
            self.connection_generation += 1
            self.connected = True
            self._event("connected", self.connection_generation)
            return self.connection_generation

    def disconnect(self, connection_generation):
        with self._atomic():
            self._expire()
            if (not self.connected or
                    connection_generation != self.connection_generation):
                return False
            self.connected = False
            self.connection_generation += 1
            if self.lease and self.lease.owner != Owner.BACKGROUND:
                self._revoke("disconnected")
            return True

    def begin(self, owner, *, ttl=10, connection_generation=None):
        with self._atomic():
            self._expire()
            self._validate_ttl(ttl)
            if not isinstance(owner, Owner):
                raise Refused("unknown owner")
            if self.state != State.IDLE:
                raise Refused("RF busy or quarantined")
            if owner == Owner.BACKGROUND:
                if connection_generation is not None:
                    raise Refused("background owner cannot use a host connection")
            elif (type(connection_generation) is not int or
                  not self.connected or
                  connection_generation != self.connection_generation):
                raise Refused("current connected host generation is required")
            self._generation += 1
            self._session_generation += 1
            self.lease = Lease(self.boot_epoch, self._generation, owner,
                               connection_generation, self._session_generation)
            self.state = State.ACTIVE
            self.acquired_at = self.now
            self.deadline = min(self.now + ttl, self.now + self.lifetime_limit)
            self._event("acquired")
            return self.lease

    def renew(self, lease, *, ttl):
        with self._atomic():
            self._expire()
            self._authorize(lease)
            self._validate_ttl(ttl)
            if lease.owner != Owner.HOST_TUNNEL:
                raise Refused("only multi-command host leases may renew")
            self.deadline = min(self.now + ttl,
                                self.acquired_at + self.lifetime_limit)
            self._event("renewed", self.deadline)
            return self.deadline

    def start_operation(self, lease, label):
        with self._atomic():
            self._expire()
            self._authorize(lease)
            if self.pending is not None:
                raise Refused("operation already in flight")
            if not isinstance(label, str) or not label:
                raise Refused("operation label required")
            return self._ticket("operation", label, self.operation_timeout)

    def operation_done(self, ticket, outcome="success"):
        """Success/failure both attest that THIS operation is quiescent.

        Unknown does not attest quiescence. Exact pending tickets can drain an
        old disconnected owner, but cannot publish data or affect a new owner.
        """
        with self._atomic():
            self._expire()
            if (not isinstance(ticket, Ticket) or ticket != self.pending or
                    ticket.kind != "operation" or
                    self.state not in (State.ACTIVE, State.DRAINING)):
                return False
            if outcome not in ("success", "failure", "unknown"):
                raise Refused("invalid operation outcome")
            if outcome == "unknown":
                self._unknown("operation_outcome_unknown")
            else:
                self.pending = None
                self.work_deadline = None
                self._event("operation_quiescent", ticket)
                if self.state == State.DRAINING or outcome == "failure":
                    self.close_reason = self.close_reason or "operation_failed"
                    self._begin_cleanup()
            return True

    def finish(self, lease):
        with self._atomic():
            self._expire()
            self._authorize(lease)
            if self.pending is not None:
                raise Refused("cannot finish with an operation in flight")
            self.close_reason = "completed"
            self._begin_cleanup()

    def abort(self, lease):
        with self._atomic():
            self._expire()
            self._authorize(lease, active=False)
            self._revoke("aborted")

    def _revoke(self, reason):
        if self.state == State.ACTIVE:
            self.close_reason = reason
            self._event("revoked", reason)
            if self.pending is not None:
                self.state = State.DRAINING
                self.work_deadline = self.now + self.drain_timeout
                self.effects.append(Effect("cancel_and_drain", self.pending))
            else:
                self._begin_cleanup()
        elif self.state == State.CLEANING and self.close_reason == "completed":
            # Disconnect/abort during cleanup suppresses successful publication.
            self.close_reason = reason

    def _begin_cleanup(self):
        assert self.pending is None, "never clean up before operation quiescence"
        self.state = State.CLEANING
        self.cleanup_index = 0
        self._ticket("cleanup", CLEANUP_STEPS[0], self.cleanup_timeout)

    def cleanup_done(self, ticket, *, verified=True):
        with self._atomic():
            self._expire()
            if (not isinstance(ticket, Ticket) or
                    self.state != State.CLEANING or ticket != self.pending or
                    ticket.kind != "cleanup"):
                return False
            if verified is not True:
                self._unknown("cleanup_failed_or_unverified")
                return True
            self._event("cleanup_verified", ticket.label)
            self.pending = None
            self.work_deadline = None
            self.cleanup_index += 1
            if self.cleanup_index < len(CLEANUP_STEPS):
                self._ticket("cleanup", CLEANUP_STEPS[self.cleanup_index],
                             self.cleanup_timeout)
            else:
                self._release()
            return True

    def _release(self):
        assert self.pending is None and self.cleanup_index == len(CLEANUP_STEPS)
        result = Result(self.lease, self.close_reason == "completed",
                        self.close_reason, self.now)
        self.results.append(result)
        self._event("released", result)
        self.state = State.IDLE
        self.lease = self.pending = self.deadline = self.work_deadline = None
        self.acquired_at = self.cleanup_index = self.close_reason = None

    def _unknown(self, reason):
        self.state = State.UNKNOWN
        self.close_reason = reason
        self.work_deadline = None
        self._event("quarantined", reason)

    def request_recovery(self):
        """Emit a reset obligation, not an actual hardware reset."""
        with self._atomic():
            if self.state != State.UNKNOWN:
                raise Refused("recovery requires a quarantined resource")
            self.state = State.RECOVERING
            return self._ticket("recovery", "reset_and_verify_all_rf_work_quiescent",
                                self.cleanup_timeout)

    def recovery_done(self, ticket, *, verified_quiescent=False):
        with self._atomic():
            self._expire()
            if (not isinstance(ticket, Ticket) or
                    self.state != State.RECOVERING or ticket != self.pending or
                    ticket.kind != "recovery"):
                return False
            if verified_quiescent is not True:
                self._unknown("recovery_failed_or_unverified")
                return True
            self._event("recovery_quiescent", ticket)
            self.pending = None
            self.work_deadline = None
            self.close_reason = "recovered_after_unknown"
            self._begin_cleanup()
            return True

    def advance(self, ticks=1):
        if type(ticks) is not int or ticks < 0:
            raise ValueError("monotonic integer time only")
        with self._atomic():
            self.now += ticks
            self._expire()

    def _expire(self):
        if self.state == State.ACTIVE and self.now >= self.deadline:
            self._revoke("lease_expired")
        if self.work_deadline is not None and self.now >= self.work_deadline:
            if self.state == State.ACTIVE:
                self._revoke("operation_timeout")
            elif self.state in (State.DRAINING, State.CLEANING, State.RECOVERING):
                self._unknown(self.state.value + "_timeout")
