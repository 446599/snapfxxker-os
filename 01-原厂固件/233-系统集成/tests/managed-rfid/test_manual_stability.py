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


module = mod


class Clock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def pause(self, seconds):
        self.value += seconds


class Fixture:
    def __init__(self, mode):
        self.reader = module.AceTagReader(None)
        self.reader._manual_proof = True
        self.reader._target_motion_allowed = lambda *args: True
        self.reader._wait_motion_ready = lambda *args, **kwargs: True
        self.reader._remember_encoder_direction = lambda *args: None
        self.reader._encoder_direction = {(1, 1): -1}
        self.reader._encoder_ledger = {(1, 1): 0}
        self.clock = Clock()
        self.reader.ace = types.SimpleNamespace(reactor=self.clock)
        self.reader._pause = self.clock.pause
        self.reader._move = self.move
        self.reader._fresh_encoder = self.fresh
        self.mode = mode
        self.requests = []
        self.samples = []
        self.last = (-5, {"steps": 100, "length": 10, "_decoder_present": True})
        self.disconnect = lambda: None

    def move(self, idx, slot, length, mode, respond):
        self.requests.append((length, mode))
        initial = (0, {"steps": 7, "length": 0, "_decoder_present": False})
        early = (-1, {"steps": 20, "length": 1, "_decoder_present": True})
        terminal = (-10, {"steps": 121, "length": 9, "_decoder_present": True})
        if self.mode == "stable_late":
            self.samples = [initial, early, terminal, terminal]
        elif self.mode == "missing":
            self.samples = [
                initial,
                (0, {"steps": 7, "length": 0, "_decoder_present": False}),
            ]
        elif self.mode == "changing":
            self.samples = [initial] + [
                (-8 - i, {"steps": 100 + i, "length": 9, "_decoder_present": True})
                for i in range(20)
            ]
        elif self.mode == "reconnect":
            self.samples = [initial, early, terminal, terminal]
        return True

    def fresh(self, *args):
        value, row = self.samples.pop(0) if self.samples else self.last
        self.last = (value, row)
        self.reader._encoder_diagnostic = {"row": row.copy()}
        if self.mode == "reconnect" and value == -10:
            self.disconnect()
        return value


class IndependentStability(unittest.TestCase):
    def test_valid_changing_terminal_then_stable_is_accepted(self):
        f = Fixture("stable_late")
        self.assertTrue(
            f.reader._move_and_confirm(1, 1, 10, module.SEARCH_MODE, lambda msg: None)
        )
        self.assertEqual(f.reader._encoder_ledger[(1, 1)], -10)
        self.assertEqual(f.reader._manual_last_terminal[(1, 1)], (-10, 121, 9))
        self.assertEqual(f.requests, [(10, module.SEARCH_MODE)])

    def test_continued_change_and_absent_decoder_rejected(self):
        for scenario in ("changing", "missing"):
            with self.subTest(scenario=scenario):
                f = Fixture(scenario)
                with self.assertRaises(module.MotionUncertain):
                    f.reader._move_and_confirm(
                        1, 1, 10, module.SEARCH_MODE, lambda msg: None
                    )
                self.assertIsNotNone(f.reader._motion_uncertain)
                self.assertEqual(f.requests, [(10, module.SEARCH_MODE)])

    def test_shared_deadline_rejects_late_valid_sample(self):
        f = Fixture("stable_late")
        original = f.reader._fresh_encoder

        def delayed(*args):
            value = original(*args)
            if value == -10:
                f.clock.value += 7
            return value

        f.reader._fresh_encoder = delayed
        with self.assertRaises(module.MotionUncertain):
            f.reader._move_and_confirm(1, 1, 10, module.SEARCH_MODE, lambda msg: None)
        self.assertEqual(f.requests, [(10, module.SEARCH_MODE)])

    def test_identity_change_during_final_sampling_latches_real_supervisor(self):
        with tempfile.TemporaryDirectory() as tmp, tarfile.open(ARCHIVE) as archive:
            directory = pathlib.Path(tmp)
            for member in archive:
                if (
                    member.isfile()
                    and "/klipper/extras/" in member.name
                    and member.name.endswith(".py")
                ):
                    (directory / member.name.rsplit("/", 1)[-1]).write_bytes(
                        archive.extractfile(member).read()
                    )
            package = types.ModuleType("stability_supervisor")
            package.__path__ = [str(directory)]
            sys.modules[package.__name__] = package
            actions_module = importlib.import_module(
                "stability_supervisor.ace_slot_actions"
            )
            f = Fixture("reconnect")
            events = []
            transport = object()
            hold = types.SimpleNamespace(
                hold_memory=lambda *args: (events.append("memory") or {"target": True}),
                persist_route=lambda route: events.append("persist"),
            )
            ace = types.SimpleNamespace(
                reactor=f.clock,
                _serials={1: transport},
                _location_hold=hold,
                _normal_load_speed=types.SimpleNamespace(park=None),
                _location_identity=lambda idx: ("port", (1, 2, 3)),
                _rfid_route_heads=lambda idx, slot: [slot],
                _latch_stop_fault=lambda *args: events.append("fault"),
                _ace_mode="multi",
            )
            f.disconnect = lambda: ace._serials.update({1: object()})
            f.reader.read_slot = lambda *args: f.reader._move_and_confirm(
                1, 1, 10, module.SEARCH_MODE, lambda msg: None
            )
            actions = types.SimpleNamespace(
                ace=ace,
                _check=lambda *args, **kwargs: None,
                _eject_stop=lambda *args: events.append("stop"),
                _stable=lambda *args, **kwargs: events.append("stable"),
            )
            actions_module.supervise_rfid_motion(
                f.reader, actions, 1, 1, transport, module.MotionUncertain
            )
            with self.assertRaises(module.MotionUncertain):
                f.reader.read_slot(1, 1, lambda msg: None)
            self.assertEqual(events, ["memory", "fault", "persist"])
            self.assertEqual(len(f.requests), 1)
            for name in list(sys.modules):
                if name == package.__name__ or name.startswith(package.__name__ + "."):
                    del sys.modules[name]


if __name__ == "__main__":
    unittest.main()
