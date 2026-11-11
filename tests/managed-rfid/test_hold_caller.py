import atexit, importlib, os, pathlib, sys, tarfile, tempfile, types, unittest, json
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
ARCHIVE = (
    REPO
    / "overlays/233-dev.2-perms.1/root/usr/local/share/233-firmware/multiace-managed-1.27b-233.20261111-single.5.tar.gz"
)
TMP = tempfile.TemporaryDirectory(prefix="hold-package-")
atexit.register(TMP.cleanup)
root = Path(TMP.name)
with tarfile.open(ARCHIVE) as archive:
    for member in archive:
        if (
            member.isfile()
            and "/klipper/extras/" in member.name
            and member.name.endswith(".py")
        ):
            (root / member.name.rsplit("/", 1)[-1]).write_bytes(
                archive.extractfile(member).read()
            )
pkg = types.ModuleType("holdtest")
pkg.__path__ = [str(root)]
sys.modules["holdtest"] = pkg
from holdtest.ace_location_hold import LocationHold, UID, PORT
from holdtest.ace_slot_actions import supervise_rfid_motion, SlotActions


class Error(Exception):
    pass


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.marker = d / "old"
        self.routes = d / "routes"
        self.marker.write_text(
            json.dumps(
                {
                    "reason": "position_unconfirmed_after_seek",
                    "state": "verified_ejected",
                    "ace_uid": list(UID),
                    "slot": 3,
                    "head": 3,
                    "port": PORT,
                    "gate_empty": True,
                    "heads_clear": True,
                    "stopped": True,
                    "uid_verified": True,
                }
            )
        )
        os.chmod(self.marker, 0o600)
        self.h = LocationHold(str(self.marker), str(self.routes))
        self.assertFalse(self.h.active)

    def tearDown(self):
        self.tmp.cleanup()

    def test_two_routes_restart_guards_and_release(self):
        a = ("p1", (1, 2, 3), 0)
        b = ("p2", (4, 5, 6), 2)
        self.h.hold_route(*a, [0])
        self.h.hold_route(*b, [2])
        h = LocationHold(str(self.marker), str(self.routes))
        self.assertFalse(h.active)
        self.assertEqual(len(h.routes), 2)
        self.assertTrue(h.blocks_route(idx=1, slot=0, port="p1", ace_uid=(1, 2, 3)))
        self.assertTrue(h.blocks_route(head=0))
        self.assertFalse(h.blocks_route(head=1))
        self.assertTrue(
            h.blocks_route(idx=3, slot=0, port="p3", ace_uid=(7, 8, 9), heads=[0])
        )
        self.assertFalse(
            h.blocks_route(idx=3, slot=1, port="p3", ace_uid=(7, 8, 9), heads=[1])
        )
        self.assertTrue(h.blocks_route(idx=3, slot=0, port="new", ace_uid=(1, 2, 3)))
        self.assertTrue(h.blocks_route(idx=1, slot=0, port="p1", ace_uid=None))
        self.assertTrue(
            h.blocks_wire(
                1, {"method": "feed_filament", "params": {"index": 0}}, "p1", (1, 2, 3)
            )
        )
        self.assertFalse(
            h.blocks_wire(
                1,
                {"method": "stop_feed_filament", "params": {"index": 0}},
                "p1",
                (1, 2, 3),
            )
        )
        token = h.route_token(*a)
        h.release_route(*a, token)
        reloaded = LocationHold(str(self.marker), str(self.routes))
        self.assertEqual(len(reloaded.routes), 1)
        self.assertEqual(reloaded.routes[0]["port"], "p2")
        self.assertFalse(reloaded.blocks_route(head=0))
        self.assertTrue(reloaded.blocks_route(head=2))

    def test_actual_eject_caller_clears_only_matching_route(self):
        self.h.hold_route("p1", (1, 2, 3), 0, [0])
        self.h.hold_route("p2", (4, 5, 6), 2, [2])
        transport = object()
        events = []
        ace = types.SimpleNamespace(
            reactor=types.SimpleNamespace(monotonic=lambda: 0),
            _serials={1: transport},
            _ace_devices=["x", "p1"],
            _head_source={},
            _location_hold=self.h,
            _rfid_route_heads=lambda idx, slot: [slot],
            _swap_in_progress=False,
            _latch_stop_fault=lambda *x: events.append(x),
        )
        a = object.__new__(SlotActions)
        a.ace = ace
        a.cancel = False
        a.state = {"seq": 1, "state": "running", "kind": "eject"}
        a._check = lambda *x, **k: None
        a._request = lambda *x, **k: {"uid1": 1, "uid2": 2, "uid3": 3}
        a._eject_stop_ready = lambda *x: False
        a._eject_stable = lambda *x: False
        a._commit_ejected = lambda *x: None
        a._run(1, 0, transport)
        self.assertEqual(a.state["state"], "complete")
        self.assertEqual([x["port"] for x in self.h.routes], ["p2"])
        self.assertFalse(events)

    def test_eject_wrong_identity_cannot_release(self):
        self.h.hold_route("p1", (1, 2, 3), 0, [0])
        transport = object()
        ace = types.SimpleNamespace(
            reactor=types.SimpleNamespace(monotonic=lambda: 0),
            _serials={1: transport},
            _ace_devices=["x", "p1"],
            _head_source={},
            _location_hold=self.h,
            _rfid_route_heads=lambda idx, slot: [slot],
            _swap_in_progress=False,
        )
        a = object.__new__(SlotActions)
        a.ace = ace
        a.cancel = False
        a.state = {"seq": 1, "state": "running", "kind": "eject"}
        a._check = lambda *x, **k: None
        a._request = lambda *x, **k: {"uid1": 9, "uid2": 9, "uid3": 9}
        a._run(1, 0, transport)
        self.assertEqual(a.state["state"], "error")
        self.assertEqual(len(self.h.routes), 1)

    def test_real_wire_guard_rejects_other_lease_bypass(self):
        import importlib

        serial = types.ModuleType("serial")
        serial.SerialException = Exception
        sys.modules["serial"] = serial
        original = os.path.isdir, os.path.isfile, os.path.lexists
        os.path.isdir = lambda x: (
            True
            if str(x) == "/oem/apps/multiace/latest/klipper/extras"
            else original[0](x)
        )
        os.path.isfile = lambda x: (
            True
            if str(x).startswith("/oem/apps/multiace/latest/klipper/extras/")
            else original[1](x)
        )
        os.path.lexists = lambda x: (
            True if str(x).endswith("/ace_native_guard.py") else original[2](x)
        )
        try:
            m = importlib.import_module("holdtest.ace")
        finally:
            os.path.isdir, os.path.isfile, os.path.lexists = original
        self.h.hold_route("p1", (1, 2, 3), 0, [0])
        lease = types.SimpleNamespace(allows=lambda *x: True)
        eject = types.SimpleNamespace(eject_allows=lambda *x: False)
        fake = types.SimpleNamespace(
            _location_identity=lambda idx: ("p1", (1, 2, 3)),
            _rfid_route_heads=lambda idx, slot: [slot],
            _location_hold=self.h,
            _manual_measure_owner=lease,
            _serials={1: object()},
            _y_recovery=lease,
            _hot_session=lease,
            _slot_actions=eject,
        )
        fake._ace_devices = ["p0", "p1"]
        fake._connected_per_ace = {1: True}
        fake._location_identity = lambda idx: (
            ("p1", (1, 2, 3)) if idx == 1 else ("p0", None)
        )
        fake._rfid_identity_status = types.MethodType(
            m.MultiAce._rfid_identity_status, fake
        )
        self.assertTrue(m.MultiAce._rfid_identity_known(fake, 1))
        result = m.MultiAce._rfid_route_status(fake)
        self.assertEqual(result[0]["ace_index"], 1)
        self.assertEqual(m.MultiAce._rfid_slot_flags(fake, 1, 0), (True, True))
        fake._location_identity = lambda idx: ("p1", None)
        self.assertFalse(m.MultiAce._rfid_identity_known(fake, 1))
        result = m.MultiAce._rfid_route_status(fake)
        self.assertIsNone(result[0]["ace_index"])
        self.assertEqual(m.MultiAce._rfid_slot_flags(fake, 1, 0), (True, False))
        fake._location_identity = lambda idx: ("p1", (1, 2, 3))
        req = {"method": "feed_filament", "params": {"index": 0}}
        self.assertTrue(m.MultiAce._location_blocks_wire(fake, 1, req))
        self.assertFalse(
            m.MultiAce._location_blocks_wire(
                fake, 1, {"method": "stop_feed_filament", "params": {"index": 0}}
            )
        )
        eject.eject_allows = lambda *x: True
        self.assertFalse(m.MultiAce._location_blocks_wire(fake, 1, req))
        # Head mode: ACE 1's four slots feed head 0, ACE 2 feeds head 1.
        token = self.h.route_token("p1", (1, 2, 3), 0)
        self.h.release_route("p1", (1, 2, 3), 0, token)
        fake._ace_mode = "head"
        fake._head_source = {}
        fake.head_uses_ace = lambda h: h in (0, 1)
        mapping = {0: 1, 1: 2}
        fake.head_ace_for = lambda h: mapping[h]
        fake._rfid_route_heads = types.MethodType(m.MultiAce._rfid_route_heads, fake)
        fake._serials = {1: object(), 2: object()}
        fake._location_identity = lambda idx: (
            ("p1", (1, 2, 3)) if idx == 1 else ("p2", (4, 5, 6))
        )
        eject.eject_allows = lambda *x: False
        self.h.hold_route("p1", (1, 2, 3), 2, fake._rfid_route_heads(1, 2), "ace")
        self.assertEqual(self.h.routes[0]["heads"], [0])
        req3 = {"method": "feed_filament", "params": {"index": 3}}
        self.assertTrue(m.MultiAce._location_blocks_wire(fake, 1, req3))
        self.assertFalse(m.MultiAce._location_blocks_wire(fake, 2, req3))
        fake._head_source = {}
        fake.head_uses_ace = lambda h: False if h == 0 else h == 1
        self.assertEqual(
            m.MultiAce._rfid_head_status(fake),
            {"0": True, "1": False, "2": False, "3": False},
        )
        fake.head_uses_ace = lambda h: h in (0, 1)
        mapping[0] = 2
        mapping[1] = 1
        self.assertTrue(m.MultiAce._location_blocks_wire(fake, 2, req3))
        self.assertTrue(m.MultiAce._location_blocks_wire(fake, 1, req3))
        self.assertTrue(
            m.MultiAce._location_blocks_wire(
                fake, 1, {"method": "feed_filament", "params": {"index": 2}}
            )
        )
        self.assertEqual(self.h.routes[0]["heads"], [0])
        # The reader does not take the writer lock: live head/slot flags and STOP remain responsive.
        import threading

        done = threading.Event()
        results = []
        self.h.hold_memory("p2", (4, 5, 6), 1, [1], "route")

        def reads():
            results.append(m.MultiAce._rfid_head_status(fake))
            results.append(m.MultiAce._location_blocks_wire(fake, 2, req3))
            results.append(
                m.MultiAce._location_blocks_wire(
                    fake, 2, {"method": "stop_feed_filament", "params": {"index": 3}}
                )
            )
            done.set()

        with self.h._locked():
            t = threading.Thread(target=reads)
            t.start()
            self.assertTrue(done.wait(1), "status/guard blocked by writer lock")
        t.join()
        self.assertTrue(results[0]["1"])
        self.assertTrue(results[1])
        self.assertFalse(results[2])

    def test_two_instances_add_and_release_keeps_other(self):
        x = LocationHold(str(self.marker), str(self.routes))
        y = LocationHold(str(self.marker), str(self.routes))
        a = ("p1", (1, 2, 3), 0)
        b = ("p2", (4, 5, 6), 1)
        x.hold_route(*a, [0])
        y.hold_route(*b, [1])
        self.assertEqual(
            len(LocationHold(str(self.marker), str(self.routes)).routes), 2
        )
        token = x.route_token(*a)
        x.release_route(*a, token)
        z = LocationHold(str(self.marker), str(self.routes))
        self.assertEqual([r["port"] for r in z.routes], ["p2"])

    def test_concurrent_instances_merge_and_stale_token_fails(self):
        import threading

        x = LocationHold(str(self.marker), str(self.routes))
        y = LocationHold(str(self.marker), str(self.routes))
        barrier = threading.Barrier(2)
        errors = []

        def write(obj, args):
            try:
                barrier.wait()
                obj.hold_route(*args, [args[2]])
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=write, args=(x, ("p1", (1, 2, 3), 0))),
            threading.Thread(target=write, args=(y, ("p2", (4, 5, 6), 1))),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertFalse(errors)
        self.assertEqual(
            len(LocationHold(str(self.marker), str(self.routes)).routes), 2
        )
        token = x.route_token("p1", (1, 2, 3), 0)
        y.hold_route("p3", (7, 8, 9), 2, [2])
        with self.assertRaises(ValueError):
            x.release_route("p1", (1, 2, 3), 0, token)
        self.assertEqual(
            len(LocationHold(str(self.marker), str(self.routes)).routes), 3
        )

    def test_missing_parent_created_and_generic_message(self):
        from pathlib import Path

        h = LocationHold(
            str(self.marker), str(Path(self.tmp.name) / "new-parent" / "holds.json")
        )
        h.hold_route("p1", (1, 2, 3), 0, [0])
        self.assertNotIn("槽4", h.message())
        self.assertIn("退出对应ACE料槽", h.message())

    def test_same_instance_snapshot_does_not_overwrite_writer(self):
        import threading

        self.h.hold_route("p1", (1, 2, 3), 0, [0])
        begin = threading.Event()
        proceed = threading.Event()
        orig = self.h._write_routes

        def slow(routes):
            begin.set()
            self.assertTrue(proceed.wait(2))
            orig(routes)

        errors = []

        def write():
            try:
                self.h.hold_route("p2", (4, 5, 6), 1, [1])
            except Exception as exc:
                errors.append(exc)

        with patch.object(self.h, "_write_routes", side_effect=slow):
            t = threading.Thread(target=write)
            t.start()
            self.assertTrue(begin.wait(1))
            self.assertTrue(self.h.blocks_route(head=1))
            proceed.set()
            t.join(2)
        self.assertFalse(t.is_alive())
        self.assertFalse(errors)
        self.assertEqual(
            len(LocationHold(str(self.marker), str(self.routes)).routes), 2
        )

    def test_write_failure_preserves_memory_and_raises(self):
        with patch.object(self.h, "_write_routes", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.h.hold_route("p1", (1, 2, 3), 0, [0])
        self.assertTrue(self.h.blocks_route(head=0))

    def test_release_cas_and_corrupt_file_fail_closed(self):
        self.h.hold_route("p1", (1, 2, 3), 0, [0])
        token = self.h.route_token("p1", (1, 2, 3), 0)
        self.h.hold_route("p2", (4, 5, 6), 2, [2])
        with self.assertRaises(ValueError):
            self.h.release_route("p1", (1, 2, 3), 0, token)
        self.assertEqual(len(self.h.routes), 2)
        self.routes.write_text("{broken")
        os.chmod(self.routes, 0o600)
        bad = LocationHold(str(self.marker), str(self.routes))
        self.assertFalse(bad.routes_valid)
        self.assertTrue(bad.blocks_route(head=1))

    def test_read_top_level_uncertain_and_read_failure_without_motion(self):
        transport = object()
        events = []
        h = self.h
        ace = types.SimpleNamespace(
            reactor=types.SimpleNamespace(monotonic=lambda: 0),
            _serials={1: transport},
            _location_hold=h,
            _normal_load_speed=types.SimpleNamespace(park=1),
            _location_identity=lambda idx: ("p1", (1, 2, 3)),
            _rfid_route_heads=lambda idx, slot: [slot],
            _latch_stop_fault=lambda *x: events.append(("fault",)),
        )
        actions = types.SimpleNamespace(
            ace=ace,
            _eject_stop=lambda *x: events.append(("stop",)),
            _stable=lambda *x, **k: events.append(("stable",)),
        )

        def fail(*a, **k):
            raise Error("neighbour changed")

        r = types.SimpleNamespace(
            _pause=lambda x: None,
            _move_and_confirm=lambda *x: None,
            read_slot=fail,
            _motion_uncertain=None,
        )
        supervise_rfid_motion(r, actions, 1, 0, transport, Error)
        with self.assertRaises(Error):
            r.read_slot(1, 0, lambda x: None)
        self.assertEqual(events, [("stop",), ("stable",)])
        self.assertTrue(h.blocks_route(head=0))
        h.release_route("p1", (1, 2, 3), 0, h.route_token("p1", (1, 2, 3), 0))
        events.clear()
        r = types.SimpleNamespace(
            _pause=lambda x: None,
            _move_and_confirm=lambda *x: None,
            read_slot=lambda *x: None,
            _motion_uncertain=None,
        )
        supervise_rfid_motion(r, actions, 1, 0, transport, Error)
        r.read_slot(1, 0, lambda x: None)
        self.assertEqual(events, [])
        self.assertFalse(h.routes)

    def test_stop_precedes_wait_for_disk_lock(self):
        import threading

        transport = object()
        events = []
        stop = threading.Event()
        h = self.h
        ace = types.SimpleNamespace(
            reactor=types.SimpleNamespace(monotonic=lambda: 0),
            _serials={1: transport},
            _location_hold=h,
            _normal_load_speed=types.SimpleNamespace(park=1),
            _location_identity=lambda idx: ("p1", (1, 2, 3)),
            _rfid_route_heads=lambda idx, slot: [slot],
            _latch_stop_fault=lambda *x: events.append(("fault",)),
        )

        def emit(*a):
            events.append(("stop",))
            stop.set()

        actions = types.SimpleNamespace(
            ace=ace,
            _eject_stop=emit,
            _stable=lambda *a, **k: events.append(("stable",)),
        )

        def fail(*a, **k):
            raise Error("uncertain")

        r = types.SimpleNamespace(
            _pause=lambda x: None,
            _move_and_confirm=fail,
            read_slot=fail,
            _motion_uncertain=None,
        )
        supervise_rfid_motion(r, actions, 1, 0, transport, Error)

        def call():
            try:
                r._move_and_confirm(1, 0, 160, 0, lambda x: None)
            except Error:
                pass

        with h._locked():
            t = threading.Thread(target=call)
            t.start()
            self.assertTrue(stop.wait(1), "STOP delayed by held file lock")
            self.assertTrue(h._pending)
            self.assertTrue(t.is_alive())
        t.join(2)
        self.assertFalse(t.is_alive())
        self.assertEqual(events, [("stop",), ("stable",)])
        self.assertTrue(h.routes)

    def test_persist_failure_after_stop_latches_and_keeps_ram(self):
        transport = object()
        events = []
        h = self.h
        ace = types.SimpleNamespace(
            reactor=types.SimpleNamespace(monotonic=lambda: 0),
            _serials={1: transport},
            _location_hold=h,
            _normal_load_speed=types.SimpleNamespace(park=1),
            _location_identity=lambda idx: ("p1", (1, 2, 3)),
            _rfid_route_heads=lambda idx, slot: [slot],
            _latch_stop_fault=lambda *x: events.append(("fault",)),
        )
        actions = types.SimpleNamespace(
            ace=ace,
            _eject_stop=lambda *x: events.append(("stop",)),
            _stable=lambda *x, **k: events.append(("stable",)),
        )

        def fail(*a, **k):
            raise Error("uncertain")

        r = types.SimpleNamespace(
            _pause=lambda x: None,
            _move_and_confirm=fail,
            read_slot=fail,
            _motion_uncertain=None,
        )
        supervise_rfid_motion(r, actions, 1, 0, transport, Error)
        with patch.object(h, "_write_routes", side_effect=OSError("disk")):
            with self.assertRaises(Error):
                r._move_and_confirm(1, 0, 160, 0, lambda x: None)
        self.assertEqual(events, [("stop",), ("stable",), ("fault",)])
        self.assertTrue(h._pending)
        self.assertTrue(h.blocks_route(head=0))

    def test_stop_failure_latches_global_but_keeps_route(self):
        transport = object()
        fault = []
        h = self.h
        ace = types.SimpleNamespace(
            reactor=types.SimpleNamespace(monotonic=lambda: 0),
            _serials={1: transport},
            _location_hold=h,
            _normal_load_speed=types.SimpleNamespace(park=1),
            _location_identity=lambda idx: ("p1", (1, 2, 3)),
            _rfid_route_heads=lambda idx, slot: [slot],
            _latch_stop_fault=lambda *x: fault.append(x),
        )

        def fail(*a, **kw):
            raise Error("uncertain")

        actions = types.SimpleNamespace(
            ace=ace, _eject_stop=fail, _stable=lambda *a, **k: None
        )
        r = types.SimpleNamespace(
            _pause=lambda x: None,
            _move_and_confirm=fail,
            read_slot=fail,
            _motion_uncertain=None,
        )
        supervise_rfid_motion(r, actions, 1, 0, transport, Error)
        with self.assertRaises(Error):
            r._move_and_confirm(1, 0, 160, 0, lambda x: None)
        self.assertEqual(len(fault), 1)
        self.assertTrue(h.blocks_route(head=0))

    def test_supervised_uncertain_stops_and_holds_original_identity(self):
        h = self.h
        transport = object()
        events = []

        class Reactor:
            def monotonic(self):
                return 0

        ace = types.SimpleNamespace(
            reactor=Reactor(),
            _serials={1: transport},
            _location_hold=h,
            _normal_load_speed=types.SimpleNamespace(park=1),
            _location_identity=lambda idx: ("p1", (1, 2, 3)),
            _rfid_route_heads=lambda idx, slot: [slot],
            _latch_stop_fault=lambda *x: events.append(("fault", x)),
        )
        actions = types.SimpleNamespace(
            ace=ace,
            _eject_stop=lambda *a: events.append(("stop",)),
            _stable=lambda *a, **k: events.append(("stable",)),
        )

        def fail(*a, **kw):
            raise Error("uncertain")

        r = types.SimpleNamespace(
            _pause=lambda x: None,
            _move_and_confirm=fail,
            read_slot=fail,
            _motion_uncertain=None,
        )
        supervise_rfid_motion(r, actions, 1, 0, transport, Error)
        with self.assertRaises(Error):
            r._move_and_confirm(1, 0, 160, 0, lambda x: None)
        self.assertEqual(events, [("stop",), ("stable",)])
        self.assertTrue(h.blocks_route(head=0))
        self.assertEqual(
            len(LocationHold(str(self.marker), str(self.routes)).routes), 1
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
