import atexit, hashlib, json, os, pathlib, sys, tarfile, tempfile, types, unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
ARCHIVE = (
    REPO
    / "overlays/233-dev.2-perms.1/root/usr/local/share/233-firmware/multiace-managed-1.27b-233.20261111-single.5.tar.gz"
)
TMP = tempfile.TemporaryDirectory(prefix="park-package-")
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
pkg = types.ModuleType("parkcheck")
pkg.__path__ = [str(root)]
sys.modules[pkg.__name__] = pkg
from parkcheck import ace_slot_actions as mod


class MotionUncertain(Exception):
    pass


class TestParkScope(unittest.TestCase):
    def make(self, idx, slot):
        transport = object()
        events = []
        park = object()

        class Hold:
            def hold_memory(self, *args):
                events.append("memory")
                return args

            def persist_route(self, *args):
                events.append("persist")

            def hold_route(self, *args):
                events.append("direct-persist")

        ace = types.SimpleNamespace(
            reactor=types.SimpleNamespace(monotonic=lambda: 0),
            _serials={idx: transport},
            _location_hold=Hold(),
            _normal_load_speed=types.SimpleNamespace(park=park),
            _location_identity=lambda i: ("port", (1, 2, 3)),
            _rfid_route_heads=lambda i, s: [s],
            _latch_stop_fault=lambda *a: events.append("fault"),
        )
        act = types.SimpleNamespace(
            ace=ace,
            _eject_stop=lambda *a: events.append("stop"),
            _stable=lambda *a, **k: events.append("stable"),
        )

        def fail(*a, **kw):
            raise MotionUncertain("uncertain")

        reader = types.SimpleNamespace(
            _pause=lambda *a: None,
            _move_and_confirm=fail,
            read_slot=fail,
            _motion_uncertain=None,
        )
        mod.supervise_rfid_motion(reader, act, idx, slot, transport, MotionUncertain)
        return ace, reader, park, events

    def test_supervisor_unrelated_preserves_calibrated_park(self):
        ace, reader, park, events = self.make(1, 0)
        with self.assertRaises(MotionUncertain):
            reader.read_slot(1, 0, lambda x: None)
        self.assertIs(ace._normal_load_speed.park, park)
        self.assertEqual(events, ["memory", "stop", "stable", "persist"])

    def test_supervisor_calibration_route_invalidates_park(self):
        ace, reader, park, events = self.make(0, 3)
        with self.assertRaises(MotionUncertain):
            reader.read_slot(0, 3, lambda x: None)
        self.assertIsNone(ace._normal_load_speed.park)
        self.assertEqual(events, ["memory", "stop", "stable", "persist"])

    def test_actual_eject_commit_only_invalidates_calibrated_route(self):
        for idx, slot, uid, port in (
            (1, 0, (1, 2, 3), "port"),
            (0, 3, mod.UID, mod.PORT),
        ):
            ace, reader, park, events = self.make(idx, slot)
            ace._head_source = {}
            ace._save_head_source = lambda: None
            ace._ace_devices = [mod.PORT, "port"]
            actor = object.__new__(mod.SlotActions)
            actor.ace = ace
            actor._commit_ejected(idx, slot, object(), uid, None, {})
            if (idx, slot) == (0, 3):
                self.assertIsNone(ace._normal_load_speed.park)
            else:
                self.assertIs(ace._normal_load_speed.park, park)

    def test_target_eject_with_valid_legacy_marker(self):
        ace, reader, park, events = self.make(0, 3)
        ace._head_source = {}
        ace._save_head_source = lambda: None
        ace._ace_devices = [mod.PORT]
        actor = object.__new__(mod.SlotActions)
        actor.ace = ace
        with tempfile.TemporaryDirectory() as d:
            marker = Path(d) / "legacy.json"
            routes = Path(d) / "routes.json"
            marker.write_text(
                json.dumps(
                    {
                        "reason": "position_unconfirmed_after_seek",
                        "state": "verified_ejected",
                        "ace_uid": list(mod.UID),
                        "slot": 3,
                        "head": 3,
                        "port": mod.PORT,
                        "gate_empty": True,
                        "heads_clear": True,
                        "stopped": True,
                        "uid_verified": True,
                    }
                )
            )
            os.chmod(marker, 0o600)
            real = mod.LocationHold
            ace._location_hold = real(str(marker), str(routes))
            digest = hashlib.sha256(marker.read_bytes()).hexdigest()

            def construct(path=None, **kw):
                return real(str(path or marker), str(routes))

            with patch.object(mod, "PATH", str(marker)), patch.object(
                mod, "LocationHold", side_effect=construct
            ):
                actor._commit_ejected(0, 3, object(), mod.UID, digest, {})
            self.assertIsNone(ace._normal_load_speed.park)
            self.assertFalse(ace._location_hold.active)
            self.assertEqual(
                json.loads(marker.read_text())["state"], "verified_ejected"
            )

    def test_direct_persist_respects_route(self):
        for idx, slot in ((1, 0), (0, 3)):
            ace, reader, park, events = self.make(idx, slot)
            mod.persist_rfid_position_uncertain(
                ace, idx, slot, ("port", (1, 2, 3)), [slot], "route"
            )
            if (idx, slot) == (0, 3):
                self.assertIsNone(ace._normal_load_speed.park)
            else:
                self.assertIs(ace._normal_load_speed.park, park)


if __name__ == "__main__":
    unittest.main(verbosity=2)
