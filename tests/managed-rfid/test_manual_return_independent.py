import atexit, importlib.util, pathlib, sys, tarfile, tempfile, types, unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
ARCHIVE = (
    REPO
    / "overlays/233-dev.2-perms.1/root/usr/local/share/233-firmware/multiace-managed-1.27b-233.20261111-single.5.tar.gz"
)
TMP = tempfile.TemporaryDirectory(prefix="manual-return-package-")
atexit.register(TMP.cleanup)
ROOT = pathlib.Path(TMP.name)
with tarfile.open(ARCHIVE) as archive:
    for member in archive:
        if (
            member.isfile()
            and "/klipper/extras/" in member.name
            and member.name.endswith(".py")
        ):
            (ROOT / member.name.rsplit("/", 1)[-1]).write_bytes(
                archive.extractfile(member).read()
            )
SOURCE = ROOT / "ace_rc522.py"
spec = importlib.util.spec_from_file_location("manual_return_candidate", SOURCE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class Fixture:
    def __init__(self, raw, polarity, outcome="ok"):
        self.reader = mod.AceTagReader(None)
        self.reader._motion_ledger = {(1, 1): 0}
        self.reader._encoder_ledger = {(1, 1): raw}
        self.reader._encoder_direction = {(1, 1): polarity}
        self.reader._manual_proof = True
        self.reader._target_motion_allowed = lambda *args: True
        self.reader._wait_motion_ready = lambda *args, **kwargs: True
        self.clock = types.SimpleNamespace(value=0.0)
        self.reader.ace = types.SimpleNamespace(
            reactor=types.SimpleNamespace(monotonic=lambda: self.clock.value)
        )
        self.reader._pause = lambda seconds: setattr(
            self.clock, "value", self.clock.value + seconds
        )
        self.reader._remember_encoder_direction = lambda *args: None
        self.outcome = outcome
        self.telemetry = []
        self.last = (36, {"steps": 500, "length": 50, "_decoder_present": True})
        self.calls = []
        self.reader._move = self.move
        self.reader._fresh_encoder = self.fresh

    def move(self, idx, slot, length, mode, respond):
        self.calls.append((length, mode))
        self.reader._motion_ledger[(idx, slot)] += (
            length if mode == mod.SEARCH_MODE else -length
        )
        sign = self.reader._encoder_direction[(idx, slot)] * (
            1 if mode == mod.SEARCH_MODE else -1
        )
        value = sign * max(1, round(length * 0.8))
        initial = (0, {"steps": 7, "length": 0, "_decoder_present": False})
        if self.outcome == "reset_missing":
            initial = self.last
        if self.outcome == "wrong_direction":
            value = -value
        end = (value, {"steps": 100, "length": length, "_decoder_present": True})
        if self.outcome == "unstable":
            confirm = (value + 1, end[1])
        else:
            confirm = end
        self.telemetry = (
            [initial] * 7 if self.outcome == "reset_missing" else [initial]
        ) + [end, confirm]
        self.last = confirm
        return True

    def fresh(self, *args):
        value, row = self.telemetry.pop(0) if self.telemetry else self.last
        self.reader._encoder_diagnostic = {"row": row.copy()}
        return value


class IndependentManualReturn(unittest.TestCase):
    def test_recorded_27_encoder_forward_returns_by_measured_motion(self):
        for polarity in (-1, 1):
            f = Fixture(27 * polarity, polarity)
            f.reader._motion_ledger[(1, 1)] = 50
            f.reader._restore_recorded_motion(lambda msg: None)
            self.assertEqual(f.calls[0], (27, mod.RESTORE_MODE))
            self.assertLessEqual(abs(f.reader._encoder_ledger[(1, 1)]), 2)
            self.assertLessEqual(sum(length for length, _ in f.calls), 74)

    def test_original_9_residual_correction_both_polarities(self):
        for polarity in (-1, 1):
            f = Fixture(-9 * polarity, polarity)
            f.reader._restore_recorded_motion(lambda msg: None)
            self.assertEqual(f.calls, [(9, mod.SEARCH_MODE)])
            self.assertLessEqual(abs(f.reader._encoder_ledger[(1, 1)]), 2)

    def test_reset_failure_and_wrong_direction_do_not_report_success(self):
        for outcome in ("reset_missing", "wrong_direction", "unstable"):
            f = Fixture(9, -1, outcome)
            with self.assertRaises(mod.MotionUncertain):
                f.reader._restore_recorded_motion(lambda msg: None)

    def test_uncertain_telemetry_triggers_stop_before_persistent_route_hold(self):
        archive_path = ARCHIVE
        with tempfile.TemporaryDirectory() as td, tarfile.open(archive_path) as archive:
            tmp = pathlib.Path(td)
            for member in archive:
                if (
                    member.isfile()
                    and "/klipper/extras/" in member.name
                    and member.name.endswith(".py")
                ):
                    (tmp / member.name.rsplit("/", 1)[-1]).write_bytes(
                        archive.extractfile(member).read()
                    )
            pkg = types.ModuleType("manual_hold_test")
            pkg.__path__ = [str(tmp)]
            sys.modules[pkg.__name__] = pkg
            from manual_hold_test.ace_slot_actions import supervise_rfid_motion

            f = Fixture(9, -1, "reset_missing")
            events = []
            transport = object()

            class Clock:
                t = 0.0

                def monotonic(self):
                    return self.t

            clock = Clock()
            f.reader._pause = lambda seconds: setattr(clock, "t", clock.t + seconds)
            f.reader.read_slot = lambda *args: f.reader._restore_recorded_motion(
                lambda msg: None
            )
            hold = types.SimpleNamespace(
                hold_memory=lambda *args: (
                    events.append("memory") or {"route": "target"}
                ),
                persist_route=lambda route: events.append("persist"),
            )
            ace = types.SimpleNamespace(
                reactor=clock,
                _serials={1: transport},
                _location_hold=hold,
                _normal_load_speed=types.SimpleNamespace(park=None),
                _location_identity=lambda idx: ("port", (1, 2, 3)),
                _rfid_route_heads=lambda idx, slot: [slot],
                _latch_stop_fault=lambda *args: events.append("fault"),
                _ace_mode="multi",
            )
            actions = types.SimpleNamespace(
                ace=ace,
                _check=lambda *args, **kwargs: None,
                _eject_stop=lambda *args: events.append("stop"),
                _stable=lambda *args, **kwargs: events.append("stable"),
            )
            supervise_rfid_motion(
                f.reader, actions, 1, 1, transport, mod.MotionUncertain
            )
            with self.assertRaises(mod.MotionUncertain):
                f.reader.read_slot(1, 1, lambda msg: None)
            self.assertEqual(events, ["memory", "stop", "stable", "persist"])
            del sys.modules["manual_hold_test"]

    def test_stationary_missing_scalar_or_stale_terminal_is_not_proof(self):
        for reading, row, terminal in (
            (0, {"steps": 7, "length": 0, "_decoder_present": False}, (36, 500, 50)),
            (35, {"steps": 500, "length": 50, "_decoder_present": True}, (36, 500, 50)),
        ):
            f = Fixture(0, -1)
            f.reader._manual_last_terminal = {(1, 1): terminal}
            f.last = (reading, row)
            with self.assertRaises(mod.MotionUncertain):
                f.reader._manual_verify_stationary(1, 1)

    def test_real_read_slot_with_no_motion_needs_no_terminal(self):
        reader = mod.AceTagReader(None)
        reader._target_motion_allowed = lambda *args: True
        reader._wait_motion_ready = lambda *args: True
        reader._neighbour_signature = lambda *args: ("empty",)
        reader._rc = lambda *args: 1
        reader._move = lambda *args: self.fail("no movement allowed")
        messages = []
        reader.read_slot(1, 1, messages.append, max_mm=0)
        self.assertEqual(reader._motion_ledger, {})
        self.assertFalse(reader._manual_proof)
        self.assertTrue(any("no tag" in message for message in messages))

    def test_reconnect_during_final_stability_must_not_succeed(self):
        archive_path = ARCHIVE
        with tempfile.TemporaryDirectory() as td, tarfile.open(archive_path) as archive:
            tmp = pathlib.Path(td)
            for member in archive:
                if (
                    member.isfile()
                    and "/klipper/extras/" in member.name
                    and member.name.endswith(".py")
                ):
                    (tmp / member.name.rsplit("/", 1)[-1]).write_bytes(
                        archive.extractfile(member).read()
                    )
            pkg = types.ModuleType("manual_reconnect_test")
            pkg.__path__ = [str(tmp)]
            sys.modules[pkg.__name__] = pkg
            from manual_reconnect_test.ace_slot_actions import supervise_rfid_motion

            f = Fixture(0, -1)
            f.reader._manual_last_terminal = {(1, 1): (36, 500, 50)}
            transport = object()
            events = []

            class Clock:
                t = 0.0

                def monotonic(self):
                    return self.t

            clock = Clock()
            ace = types.SimpleNamespace(
                reactor=clock,
                _serials={1: transport},
                _location_hold=types.SimpleNamespace(
                    hold_memory=lambda *a: events.append("memory"),
                    persist_route=lambda *a: events.append("persist"),
                ),
                _normal_load_speed=types.SimpleNamespace(park=None),
                _location_identity=lambda idx: ("port", (1, 2, 3)),
                _rfid_route_heads=lambda idx, slot: [slot],
                _latch_stop_fault=lambda *args: events.append("fault"),
                _ace_mode="multi",
            )

            def swap(seconds):
                clock.t += seconds
                ace._serials[1] = object()

            f.reader._pause = swap
            f.reader.read_slot = lambda *args: f.reader._restore_recorded_motion(
                lambda msg: None
            )
            actions = types.SimpleNamespace(
                ace=ace,
                _check=lambda *args, **kwargs: None,
                _eject_stop=lambda *args: events.append("stop"),
                _stable=lambda *args, **kwargs: events.append("stable"),
            )
            supervise_rfid_motion(
                f.reader, actions, 1, 1, transport, mod.MotionUncertain
            )
            with self.assertRaises(mod.MotionUncertain):
                f.reader.read_slot(1, 1, lambda msg: None)
            self.assertIn("fault", events)
            del sys.modules["manual_reconnect_test"]

    def test_unknown_direction_never_moves(self):
        f = Fixture(9, -1)
        f.reader._encoder_direction.clear()
        with self.assertRaises(mod.MotionUncertain):
            f.reader._restore_recorded_motion(lambda msg: None)
        self.assertEqual(f.calls, [])


if __name__ == "__main__":
    unittest.main()
