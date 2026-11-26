import concurrent.futures
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from cloud_route_owner import Route, RouteOwner, RouteBusy


class RouteOwnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "claims.json"
        self.owner = RouteOwner(self.path)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.owner.close)

    def route(self, lane=0, uid="unit1", port="usb1", reader="reader0", heads=(0,)):
        return Route(uid, port, lane, reader, heads)

    def test_same_group_and_shared_head_conflict_other_group_parallel(self):
        a = self.owner.reserve(self.route())
        with self.assertRaises(RouteBusy):
            self.owner.reserve(self.route(1, heads=(1,)))
        with self.assertRaises(RouteBusy):
            self.owner.reserve(self.route(2, reader="reader1", heads=(0,)))
        b = self.owner.reserve(self.route(2, reader="reader1", heads=(2,)))
        self.assertEqual(set(self.owner.snapshot()), {a, b})
        with self.assertRaises(RouteBusy):
            self.owner.reserve(self.route(3, "unit2", "usb2", "reader1", (2,)))

    def test_atomic_reservation_race(self):
        barrier = threading.Barrier(2)
        def attempt():
            barrier.wait()
            try:
                return self.owner.reserve(self.route())
            except RouteBusy:
                return None
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(sum(r is not None for r in results), 1)

    def test_dispatch_is_persistent_and_not_replayable(self):
        a = self.owner.reserve(self.route())
        running = self.owner.mark_dispatched(a)
        with self.assertRaises(RouteBusy):
            self.owner.mark_dispatched(a)
        with self.assertRaises(RouteBusy):
            self.owner.abandon_before_dispatch(running)
        with self.assertRaises(RouteBusy):
            self.owner.release_verified(running, lambda _: False)
        self.owner.release_verified(running, lambda c: c == running)
        self.assertEqual(self.owner.snapshot(), ())

    def test_crash_restore_holds_even_reserved_and_replaced_unit(self):
        a = self.owner.reserve(self.route())
        self.owner.close()
        with RouteOwner(self.path) as restarted:
            current, = restarted.snapshot()
            self.assertEqual((current.token, current.state), (a.token, "recovery"))
            with self.assertRaises(RouteBusy):
                restarted.reserve(self.route(3, "replacement", "usb1", "reader1", (3,)))
            with self.assertRaises(RouteBusy):
                restarted.reserve(self.route(3, "unit1", "usb9", "reader1", (3,)))
            with self.assertRaises(RouteBusy):
                restarted.abandon_before_dispatch(current)
            restarted.release_verified(current, lambda _: True)
            self.assertEqual(restarted.snapshot(), ())

    def test_exclusive_process_owner(self):
        with self.assertRaises(BlockingIOError):
            RouteOwner(self.path)

    def test_failed_persistence_prevents_dispatch_and_new_claims(self):
        a = self.owner.reserve(self.route())
        with patch("cloud_route_owner.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.owner.mark_dispatched(a)
        with self.assertRaises(RuntimeError):
            self.owner.reserve(self.route(2, reader="reader1", heads=(2,)))

    def test_corrupt_journal_fails_closed(self):
        self.owner.close()
        self.path.write_text("{invalid")
        with self.assertRaises(ValueError):
            RouteOwner(self.path)
        self.assertEqual(self.path.read_text(), "{invalid")

    def test_stale_claim_cannot_release_new_claim(self):
        a = self.owner.reserve(self.route())
        self.owner.abandon_before_dispatch(a)
        b = self.owner.reserve(self.route())
        with self.assertRaises(RouteBusy):
            self.owner.abandon_before_dispatch(a)
        self.assertEqual(self.owner.snapshot(), (b,))


if __name__ == "__main__":
    unittest.main()
