import itertools
import random
import unittest
from dataclasses import replace

from rf_model import Arbiter, CLEANUP_STEPS, Owner, Refused, State


def connected(**kwargs):
    a = Arbiter(**kwargs)
    a.connect()
    return a


def begin(a, owner=Owner.HOST_TUNNEL, ttl=10):
    return a.begin(owner, ttl=ttl,
                   connection_generation=(None if owner == Owner.BACKGROUND
                                          else a.connection_generation))


def complete_cleanup(a):
    count = 0
    while a.state == State.CLEANING:
        assert a.cleanup_done(a.pending, verified=True)
        count += 1
    return count


def successful_operation(a, lease):
    ticket = a.start_operation(lease, "select_authenticate_read")
    assert a.operation_done(ticket)
    return ticket


def assert_history(test, a):
    current = None
    cleanup = []
    inflight = False
    for _, name, lease, detail in a.audit:
        if name == "acquired":
            test.assertIsNone(current)
            current, cleanup, inflight = lease, [], False
        elif name == "issued":
            test.assertEqual(lease, current)
            if detail.kind == "operation":
                test.assertFalse(inflight)
                inflight = True
            elif detail.kind == "cleanup":
                test.assertFalse(inflight)
        elif name in ("operation_quiescent", "recovery_quiescent"):
            inflight = False
            if name == "recovery_quiescent":
                cleanup = []
        elif name == "cleanup_verified":
            cleanup.append(detail)
        elif name == "released":
            test.assertEqual(lease, current)
            test.assertFalse(inflight)
            test.assertEqual(tuple(cleanup), CLEANUP_STEPS)
            current = None
    test.assertEqual(current, a.lease)
    a.check_invariants()
    a.assert_unlocked()


class OwnershipTests(unittest.TestCase):
    def test_all_owner_pairs_exclude_whole_transactions(self):
        for first, other in itertools.product(Owner, repeat=2):
            with self.subTest(first=first, other=other):
                a = connected()
                lease = begin(a, first)
                for label in ("select", "authenticate", "read", "decode"):
                    ticket = a.start_operation(lease, label)
                    with self.assertRaises(Refused):
                        begin(a, other)
                    a.operation_done(ticket)
                    with self.assertRaises(Refused):
                        begin(a, other)
                a.finish(lease)
                for _ in CLEANUP_STEPS:
                    with self.assertRaises(Refused):
                        begin(a, other)
                    a.cleanup_done(a.pending)
                self.assertTrue(a.results[-1].successful)
                begin(a, other)
                assert_history(self, a)

    def test_tunnel_retains_lease_between_frames_and_serial_wait(self):
        a = connected()
        lease = begin(a)
        successful_operation(a, lease)
        a.external_wait("host inter-frame delay")
        self.assertEqual(a.lease, lease)
        self.assertIsNone(a.pending)
        with self.assertRaises(Refused):
            begin(a, Owner.BACKGROUND)
        a.advance(3)
        a.renew(lease, ttl=10)
        successful_operation(a, lease)
        self.assertEqual(a.state, State.ACTIVE)
        a.finish(lease)
        self.assertEqual(a.results, [])
        self.assertEqual(complete_cleanup(a), len(CLEANUP_STEPS))
        assert_history(self, a)

    def test_failure_requires_cleanup_and_yields_no_success(self):
        for owner in Owner:
            with self.subTest(owner=owner):
                a = connected()
                lease = begin(a, owner)
                t = a.start_operation(lease, "read")
                a.operation_done(t, "failure")
                self.assertEqual(a.state, State.CLEANING)
                self.assertEqual(a.results, [])
                complete_cleanup(a)
                self.assertFalse(a.results[-1].successful)
                assert_history(self, a)

    def test_abort_cancels_then_waits_for_quiescence_before_cleanup(self):
        a = connected()
        lease = begin(a)
        t = a.start_operation(lease, "read")
        a.abort(lease)
        self.assertEqual(a.state, State.DRAINING)
        self.assertEqual([e.kind for e in a.take_effects()],
                         ["execute_operation", "cancel_and_drain"])
        a.external_wait("cancel completion")
        with self.assertRaises(Refused):
            begin(a, Owner.CMD68)
        self.assertFalse(a.cleanup_done(t))
        a.operation_done(t)
        complete_cleanup(a)
        self.assertEqual(a.results[-1].reason, "aborted")
        self.assertFalse(a.results[-1].successful)
        assert_history(self, a)

    def test_disconnect_reconnect_cannot_steal_draining_lease(self):
        a = connected()
        old_connection = a.connection_generation
        lease = begin(a)
        t = a.start_operation(lease, "read")
        a.disconnect(old_connection)
        new_connection = a.connect()
        self.assertGreater(new_connection, old_connection)
        with self.assertRaises(Refused):
            begin(a)
        with self.assertRaises(Refused):
            a.renew(lease, ttl=10)
        # Exact old operation may acknowledge quiescence, but never success.
        self.assertTrue(a.operation_done(t))
        complete_cleanup(a)
        self.assertFalse(a.results[-1].successful)
        new_lease = begin(a)
        self.assertGreater(new_lease.session_generation, lease.session_generation)
        self.assertFalse(a.operation_done(t))
        self.assertEqual(a.lease, new_lease)
        assert_history(self, a)

    def test_disconnect_during_cleanup_suppresses_publication(self):
        a = connected()
        lease = begin(a, Owner.CMD68)
        successful_operation(a, lease)
        a.finish(lease)
        a.disconnect(a.connection_generation)
        complete_cleanup(a)
        self.assertFalse(a.results[-1].successful)
        self.assertEqual(a.results[-1].reason, "disconnected")

    def test_connection_replacement_revokes_cmd68(self):
        a = connected()
        lease = begin(a, Owner.CMD68)
        a.connect()
        self.assertEqual(a.state, State.CLEANING)
        with self.assertRaises(Refused):
            a.finish(lease)
        complete_cleanup(a)
        self.assertEqual(a.results[-1].reason, "connection_replaced")

    def test_disconnect_does_not_revoke_background_or_new_connection(self):
        a = connected()
        old = a.connection_generation
        a.connect()
        lease = begin(a, Owner.BACKGROUND)
        self.assertFalse(a.disconnect(old))
        a.disconnect(a.connection_generation)
        self.assertEqual(a.state, State.ACTIVE)
        successful_operation(a, lease)
        a.finish(lease)
        complete_cleanup(a)
        self.assertTrue(a.results[-1].successful)

    def test_expiry_between_frames_requires_cleanup(self):
        a = connected()
        lease = begin(a, ttl=2)
        successful_operation(a, lease)
        a.advance(2)
        self.assertEqual(a.state, State.CLEANING)
        with self.assertRaises(Refused):
            a.renew(lease, ttl=10)
        with self.assertRaises(Refused):
            a.start_operation(lease, "late frame")
        complete_cleanup(a)
        self.assertEqual(a.results[-1].reason, "lease_expired")
        assert_history(self, a)

    def test_expiry_inflight_does_not_release_and_lost_cancel_quarantines(self):
        a = connected()
        lease = begin(a, ttl=2)
        t = a.start_operation(lease, "read")
        a.advance(2)
        self.assertEqual(a.state, State.DRAINING)
        a.advance(a.drain_timeout)
        self.assertEqual(a.state, State.UNKNOWN)
        self.assertEqual(a.lease, lease)
        self.assertFalse(a.operation_done(t))
        self.assertEqual(a.results, [])
        with self.assertRaises(Refused):
            begin(a, Owner.BACKGROUND)
        assert_history(self, a)

    def test_expiry_callback_at_boundary_cannot_succeed_transaction(self):
        a = connected()
        lease = begin(a, ttl=2)
        t = a.start_operation(lease, "read")
        a.advance(2)
        self.assertTrue(a.operation_done(t))
        complete_cleanup(a)
        self.assertFalse(a.results[-1].successful)

    def test_operation_timeout_revokes_before_lease_expires(self):
        a = connected(operation_timeout=2)
        lease = begin(a, ttl=20)
        t = a.start_operation(lease, "hung reader")
        a.advance(2)
        self.assertEqual(a.state, State.DRAINING)
        a.operation_done(t, "failure")
        complete_cleanup(a)
        self.assertEqual(a.results[-1].reason, "operation_timeout")

    def test_renewal_has_hard_lifetime_limit_and_no_resurrection(self):
        a = connected(lease_limit=10, lifetime_limit=12)
        lease = begin(a, ttl=10)
        a.advance(8)
        self.assertEqual(a.renew(lease, ttl=10), 12)
        a.advance(3)
        self.assertEqual(a.renew(lease, ttl=10), 12)
        a.advance(1)
        with self.assertRaises(Refused):
            a.renew(lease, ttl=10)
        self.assertEqual(a.state, State.CLEANING)

    def test_only_host_tunnel_can_renew(self):
        for owner in (Owner.CMD68, Owner.BACKGROUND):
            a = connected()
            lease = begin(a, owner)
            with self.assertRaises(Refused):
                a.renew(lease, ttl=10)

    def test_cleanup_failure_at_every_stage_preserves_owner(self):
        for fail_at in range(len(CLEANUP_STEPS)):
            with self.subTest(fail_at=fail_at):
                a = connected()
                lease = begin(a)
                successful_operation(a, lease)
                a.finish(lease)
                for _ in range(fail_at):
                    a.cleanup_done(a.pending)
                a.cleanup_done(a.pending, verified=False)
                self.assertEqual(a.state, State.UNKNOWN)
                self.assertEqual(a.lease, lease)
                self.assertEqual(a.results, [])
                with self.assertRaises(Refused):
                    begin(a, Owner.BACKGROUND)
                assert_history(self, a)

    def test_cleanup_timeout_and_late_callback_fail_closed(self):
        a = connected()
        lease = begin(a)
        successful_operation(a, lease)
        a.finish(lease)
        stale = a.pending
        a.advance(a.cleanup_timeout)
        self.assertEqual(a.state, State.UNKNOWN)
        self.assertFalse(a.cleanup_done(stale))
        self.assertEqual(a.results, [])

    def test_unknown_requires_verified_recovery_and_entire_cleanup(self):
        a = connected()
        lease = begin(a)
        stale = a.start_operation(lease, "unknown read")
        a.operation_done(stale, "unknown")
        recovery = a.request_recovery()
        a.recovery_done(recovery)  # Default refuses unverified reset.
        self.assertEqual(a.state, State.UNKNOWN)
        recovery = a.request_recovery()
        a.recovery_done(recovery, verified_quiescent=True)
        self.assertEqual(a.state, State.CLEANING)
        self.assertFalse(a.operation_done(stale))
        complete_cleanup(a)
        self.assertFalse(a.results[-1].successful)
        self.assertEqual(a.results[-1].reason, "recovered_after_unknown")
        assert_history(self, a)

    def test_recovery_timeout_and_stale_recovery_callback(self):
        a = connected()
        lease = begin(a)
        t = a.start_operation(lease, "read")
        a.operation_done(t, "unknown")
        first = a.request_recovery()
        a.advance(a.cleanup_timeout)
        self.assertEqual(a.state, State.UNKNOWN)
        second = a.request_recovery()
        self.assertFalse(a.recovery_done(first, verified_quiescent=True))
        a.recovery_done(second, verified_quiescent=True)
        complete_cleanup(a)
        begin(a)
        self.assertFalse(a.recovery_done(second, verified_quiescent=True))
        assert_history(self, a)

    def test_stale_callbacks_and_lease_cannot_mutate_new_owner(self):
        a = connected()
        old_lease = begin(a)
        old_op = successful_operation(a, old_lease)
        a.finish(old_lease)
        old_cleanup = a.pending
        complete_cleanup(a)
        new = begin(a, Owner.CMD68)
        t = a.start_operation(new, "read")
        snapshot = (a.lease, a.pending, a.state, len(a.results))
        self.assertFalse(a.operation_done(old_op))
        self.assertFalse(a.cleanup_done(old_cleanup))
        with self.assertRaises(Refused):
            a.abort(old_lease)
        with self.assertRaises(Refused):
            a.start_operation(replace(new, session_generation=99999), "forged")
        with self.assertRaises(Refused):
            a.start_operation(replace(new, boot_epoch="other-boot"), "stale")
        self.assertEqual(snapshot, (a.lease, a.pending, a.state, len(a.results)))
        self.assertEqual(a.pending, t)
        assert_history(self, a)

    def test_duplicate_callback_cannot_complete_next_operation(self):
        a = connected()
        lease = begin(a)
        first = successful_operation(a, lease)
        second = a.start_operation(lease, "next frame")
        self.assertFalse(a.operation_done(first))
        self.assertEqual(a.pending, second)

    def test_command_conflict_and_finish_with_inflight_are_rejected(self):
        a = connected()
        lease = begin(a)
        t = a.start_operation(lease, "first")
        with self.assertRaises(Refused):
            a.start_operation(lease, "second")
        with self.assertRaises(Refused):
            a.finish(lease)
        self.assertEqual(a.pending, t)
        self.assertEqual(a.state, State.ACTIVE)

    def test_wait_and_effect_dispatch_must_be_outside_command_lock(self):
        a = connected()
        lease = begin(a)
        a.start_operation(lease, "read")
        a.external_wait()
        with a._atomic():
            with self.assertRaises(AssertionError):
                a.external_wait()
            with self.assertRaises(AssertionError):
                a.take_effects()
        self.assertEqual(len(a.take_effects()), 1)
        a.irq_masked = True
        with self.assertRaises(AssertionError):
            a.external_wait()
        a.irq_masked = False
        a.check_invariants()

    def test_invalid_time_ttl_and_connections_are_rejected(self):
        a = Arbiter()
        with self.assertRaises(Refused):
            begin(a)
        a.connect()
        for ttl in (0, -1, 21, True, 0.5):
            with self.assertRaises(Refused):
                begin(a, ttl=ttl)
        for ticks in (-1, 0.5, True):
            with self.assertRaises(ValueError):
                a.advance(ticks)
        self.assertEqual(a.state, State.IDLE)

    def test_late_queued_effects_are_fenced_after_revoke_or_release(self):
        a = connected()
        lease = begin(a)
        t = a.start_operation(lease, "read")
        operation_effect = a.take_effects()[0]
        self.assertTrue(a.effect_is_current(operation_effect))
        a.abort(lease)
        cancel_effect = a.take_effects()[0]
        self.assertFalse(a.effect_is_current(operation_effect))
        self.assertTrue(a.effect_is_current(cancel_effect))
        a.operation_done(t)
        cleanup_effect = a.take_effects()[0]
        self.assertFalse(a.effect_is_current(cancel_effect))
        self.assertTrue(a.effect_is_current(cleanup_effect))
        complete_cleanup(a)
        begin(a)
        self.assertFalse(a.effect_is_current(cleanup_effect))
        self.assertFalse(a.effect_is_current(operation_effect))

    def test_malformed_callbacks_and_boolean_connection_are_rejected(self):
        a = connected()
        with self.assertRaises(Refused):
            a.begin(Owner.CMD68, connection_generation=True)
        begin(a)
        for bad in (None, object(), {}, "callback"):
            self.assertFalse(a.operation_done(bad))
            self.assertFalse(a.cleanup_done(bad))
            self.assertFalse(a.recovery_done(bad))
            self.assertFalse(a.effect_is_current(bad))
        self.assertEqual(a.state, State.ACTIVE)

    def test_all_5040_orderings_of_racing_events(self):
        # A finite interleaving test, NOT a proof over all firmware schedules.
        schedules = itertools.permutations(("callback", "expire", "abort",
                                           "disconnect", "reconnect",
                                           "background", "cmd68"))
        count = 0
        for schedule in schedules:
            a = connected()
            old_conn = a.connection_generation
            old_lease = begin(a, ttl=2)
            old_ticket = a.start_operation(old_lease, "read")
            for event in schedule:
                try:
                    if event == "callback":
                        a.operation_done(old_ticket)
                    elif event == "expire":
                        a.advance(2)
                    elif event == "abort":
                        a.abort(old_lease)
                    elif event == "disconnect":
                        a.disconnect(old_conn)
                    elif event == "reconnect":
                        a.connect()
                    elif event == "background":
                        begin(a, Owner.BACKGROUND)
                    elif event == "cmd68":
                        begin(a, Owner.CMD68)
                except Refused:
                    pass
                a.check_invariants()
                a.assert_unlocked()
            if a.state == State.CLEANING:
                complete_cleanup(a)
            assert_history(self, a)
            self.assertFalse(any(r.successful for r in a.results))
            count += 1
        self.assertEqual(count, 5040)

    def test_seeded_fault_and_stale_event_sequences(self):
        # 30 fixed seeds x 200 events. Include valid and intentionally stale
        # callbacks, malformed owners, repeated controls, expiry, and recovery.
        for seed in range(30):
            rng = random.Random(seed)
            a = connected()
            leases, tickets = [], []
            for _ in range(200):
                event = rng.randrange(15)
                lease = rng.choice(leases) if leases else None
                ticket = rng.choice(tickets) if tickets else None
                try:
                    if event == 0:
                        leases.append(begin(a, rng.choice(list(Owner)), ttl=rng.randint(1, 10)))
                    elif event == 1 and lease:
                        tickets.append(a.start_operation(lease, "random frame"))
                    elif event == 2 and ticket:
                        a.operation_done(ticket, rng.choice(("success", "failure", "unknown")))
                    elif event == 3 and lease:
                        a.finish(lease)
                    elif event == 4 and lease:
                        a.abort(lease)
                    elif event == 5:
                        a.advance(rng.randint(0, 7))
                    elif event == 6:
                        a.disconnect(a.connection_generation)
                    elif event == 7:
                        a.connect()
                    elif event == 8 and a.state == State.CLEANING:
                        tickets.append(a.pending)
                        a.cleanup_done(a.pending, verified=rng.choice((True, True, False)))
                    elif event == 9 and a.state == State.UNKNOWN:
                        tickets.append(a.request_recovery())
                    elif event == 10 and a.state == State.RECOVERING:
                        a.recovery_done(a.pending, verified_quiescent=rng.choice((True, False)))
                    elif event == 11 and lease:
                        a.renew(lease, ttl=10)
                    elif event == 12 and ticket:
                        a.cleanup_done(ticket)
                    elif event == 13 and ticket:
                        a.recovery_done(ticket, verified_quiescent=True)
                    else:
                        a.external_wait("scheduler interleaving")
                except Refused:
                    pass
                assert_history(self, a)


if __name__ == "__main__":
    unittest.main(verbosity=2)
