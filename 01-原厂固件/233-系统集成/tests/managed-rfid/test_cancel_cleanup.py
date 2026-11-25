import atexit, importlib, pathlib, sys, tarfile, tempfile, types, unittest
from unittest.mock import patch

REPO = pathlib.Path(__file__).resolve().parents[2]
ARCHIVE = (
    REPO
    / "overlays/233-dev.2-perms.1/root/usr/local/share/233-firmware/multiace-managed-1.27b-233.20261111-single.5.tar.gz"
)
TMP = tempfile.TemporaryDirectory(prefix="cancel-package-")
atexit.register(TMP.cleanup)
root = pathlib.Path(TMP.name)
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
pkg = types.ModuleType("cancelpkg")
pkg.__path__ = [str(root)]
sys.modules["cancelpkg"] = pkg
serial = types.ModuleType("serial")
serial.SerialException = Exception
sys.modules.setdefault("serial", serial)
sys.modules["cancelpkg.pulse_counter"] = types.ModuleType("cancelpkg.pulse_counter")
with patch("os.path.isdir", return_value=True), patch(
    "os.path.isfile", return_value=True
), patch("os.path.lexists", return_value=True):
    am = importlib.import_module("cancelpkg.ace")
    fm = importlib.import_module("cancelpkg.filament_feed_ace")


class G:
    def __init__(self, **kw):
        self.data = kw

    def get_int(self, k, default=None, **kw):
        return self.data.get(k, default)

    def get(self, k, default=None):
        return self.data.get(k, default)

    def error(self, *args, **kw):
        return RuntimeError(str(args or kw))


class TestCancel(unittest.TestCase):
    def setup_machine(self, head=0, blocked="fault"):
        a = am.MultiAce.__new__(am.MultiAce)
        f = fm.FilamentFeed.__new__(fm.FilamentFeed)
        calls = []
        a._stop_guard = types.SimpleNamespace(
            message=lambda: "locked" if blocked == "fault" else None
        )
        a._slot_actions = types.SimpleNamespace(
            state={
                "state": "running" if blocked == "eject" else "idle",
                "kind": "eject",
            }
        )
        a._head_source = {0: None, 1: None, 2: None, 3: None}
        a.head_uses_ace = lambda h: False
        a._active_device_index = 0
        f.ace = a
        f.module_name = "left"
        f.filament_ch = [1, 0]
        f.channel_active = None
        f.channel_state = [fm.FEED_STA_UNLOAD_HEAT_FINISH] * 2
        f.channel_error = ["x", "x"]
        f.module_exist = [True, True]
        f.config = {"auto_mode": [True, True]}
        f._port = [
            types.SimpleNamespace(get_filament_detected=lambda: False),
            types.SimpleNamespace(get_filament_detected=lambda: True),
        ]
        f._set_channel_state = lambda c, s, *args: (
            calls.append(("state", c, s)),
            f.channel_state.__setitem__(c, s),
        )
        f.hide_heat_finish = [True, True]
        heaters = [
            types.SimpleNamespace(set_temp=lambda t, i=i: calls.append(("heat", i, t)))
            for i in range(4)
        ]
        state = types.SimpleNamespace(state="standby")

        class Printer:
            def lookup_object(self, name, default=None):
                if name == "print_stats":
                    return state
                if name == "extruder":
                    return types.SimpleNamespace(get_heater=lambda: heaters[0])
                if name.startswith("extruder"):
                    return types.SimpleNamespace(
                        get_heater=lambda: heaters[int(name[-1])]
                    )
                if name == "filament_feed left":
                    return f
                if name == "filament_feed right":
                    return None
                return default

        sensor = types.SimpleNamespace(
            runout_helper=types.SimpleNamespace(
                cmd_SET_FILAMENT_SENSOR=lambda g: calls.append(
                    ("sensor", g.get_int("ENABLE"))
                )
            )
        )
        base_lookup = Printer.lookup_object
        Printer.lookup_object = lambda self, n, d=None: (
            sensor
            if n.startswith("filament_motion_sensor")
            else base_lookup(self, n, d)
        )
        f.printer = a.printer = Printer()

        def command(text):
            calls.append(("cmd", text))
            if text.startswith("FEED_AUTO"):
                vals = dict(x.split("=", 1) for x in text.split()[1:])
                vals = {k: (int(v) if v.isdigit() else v) for k, v in vals.items()}
                f.cmd_FEED_AUTO(G(**vals))

        proxy_guard = types.SimpleNamespace(
            snapshot=lambda: {"fault": 1} if blocked == "fault" else None,
            require_clear=lambda: (_ for _ in ()).throw(
                am.StopUnconfirmedError("locked")
            ),
        )
        raw = types.SimpleNamespace(run_script_from_command=command)
        a.gcode = am.StopGuardGCode(raw, lambda: proxy_guard)
        f.gcode = am.StopGuardGCode(raw, lambda: proxy_guard)
        return a, f, calls, state

    def test_two_channels_and_two_locks(self):
        for head in (0, 1):
            for blocked in ("fault", "eject"):
                a, f, calls, state = self.setup_machine(head, blocked)
                a.cmd_ACE_UNLOAD_HEAD(G(HEAD=head, STAGE="cancel"))
                self.assertIn(("heat", head, 0.0), calls)
                self.assertEqual(
                    [x for x in calls if x[0] == "heat"], [("heat", head, 0.0)]
                )
                self.assertEqual(a._head_source, {0: None, 1: None, 2: None, 3: None})
                self.assertFalse(f.hide_heat_finish[1 - head])
                self.assertFalse(any("G1 " in str(x) for x in calls))

    def test_active_channel_or_print_refuses_without_mutation(self):
        for cond in ("active", "printing", "unprepared"):
            a, f, calls, state = self.setup_machine()
            f.channel_active = 1 if cond == "active" else None
            state.state = "printing" if cond == "printing" else "standby"
            f.channel_state[1] = (
                fm.FEED_STA_LOAD_PREPARE
                if cond == "unprepared"
                else fm.FEED_STA_UNLOAD_HEAT_FINISH
            )
            with self.assertRaises(RuntimeError):
                a.cmd_ACE_UNLOAD_HEAD(G(HEAD=0, STAGE="cancel"))
            self.assertFalse(any(x[0] == "heat" for x in calls))

    def test_real_proxy_blocks_script_but_direct_cleanup_works(self):
        a, f, calls, state = self.setup_machine()
        with self.assertRaises(am.StopUnconfirmedError):
            a.gcode.run_script_from_command(
                "FEED_AUTO MODULE=left CHANNEL=1 EXTRUDER=0 UNLOAD=1 STAGE=cancel"
            )
        a.cmd_ACE_UNLOAD_HEAD(G(HEAD=0, STAGE="cancel"))
        self.assertIn(("heat", 0, 0.0), calls)
        self.assertIn(("sensor", 1), calls)

    def test_real_feed_auto_cancel_under_stop_guard(self):
        a, f, calls, state = self.setup_machine()
        f.cmd_FEED_AUTO(G(CHANNEL=1, EXTRUDER=0, UNLOAD=1, STAGE="cancel"))
        self.assertIn(("heat", 0, 0.0), calls)
        self.assertIn(("sensor", 1), calls)
        self.assertEqual(a._head_source, {0: None, 1: None, 2: None, 3: None})

    def test_other_module_busy_does_not_change_global_or_heater(self):
        a, f, calls, state = self.setup_machine()
        old = a.printer.lookup_object
        a.printer.lookup_object = lambda n, d=None: (
            types.SimpleNamespace(channel_active=0)
            if n == "filament_feed right"
            else old(n, d)
        )
        with self.assertRaises(RuntimeError):
            a.cmd_ACE_UNLOAD_HEAD(G(HEAD=0, STAGE="cancel"))
        self.assertFalse(any(x[0] == "heat" for x in calls))

    def test_bad_direct_cancel_and_normal_load_rejected(self):
        a, f, calls, state = self.setup_machine()
        for kwargs in (
            dict(CHANNEL=0, EXTRUDER=0, UNLOAD=1, STAGE="cancel"),
            dict(CHANNEL=1, EXTRUDER=0, UNLOAD=1, LOAD=1, STAGE="cancel"),
            dict(CHANNEL=0, EXTRUDER=0, LOAD=1),
        ):
            with self.assertRaises(Exception):
                f.cmd_FEED_AUTO(G(**kwargs))
        self.assertFalse(any(x[0] == "heat" for x in calls))


if __name__ == "__main__":
    unittest.main()
