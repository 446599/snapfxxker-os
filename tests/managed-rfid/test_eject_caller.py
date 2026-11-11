import atexit, pathlib, sys, tarfile, tempfile, types, unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
ARCHIVE = (
    REPO
    / "overlays/233-dev.2-perms.1/root/usr/local/share/233-firmware/multiace-managed-1.27b-233.20261111-single.5.tar.gz"
)
TMP = tempfile.TemporaryDirectory(prefix="eject-caller-")
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
pkg = types.ModuleType("flow")
pkg.__path__ = [str(root)]
sys.modules["flow"] = pkg
from flow.ace_slot_actions import SlotActions


class Reactor:
    def __init__(self):
        self.t = 0
        self.callback = None
        self.tick = None

    def monotonic(self):
        return self.t

    def pause(self, t):
        self.t = t
        self.tick and self.tick()

    def register_async_callback(self, fn):
        self.callback = fn

    def run(self):
        self.callback(self.t)


class G:
    def __init__(self, slot=0):
        self.slot = slot

    def get_int(self, key, **kw):
        return 0 if key == "ACE" else self.slot

    def error(self, msg):
        return RuntimeError(msg)

    def respond_info(self, msg):
        pass


class Machine:
    def __init__(self, gate=True):
        self.reactor = Reactor()
        self.transport = object()
        self._serials = {0: self.transport}
        self._connected_per_ace = {0: True}
        self._serial_failed_per_ace = {}
        self._fw_update_hold = set()
        self._ace_devices = ["fake"]
        self._swap_in_progress = False
        self._unload_all_active = False
        self._tag_read_busy = False
        self._gen1_manual_busy = False
        self._calibration = {"state": "idle"}
        self._calibration_unload = {}
        self._head_source = {i: None for i in range(4)}
        self._location_hold = types.SimpleNamespace(
            active=False,
            valid_marker=True,
            blocks_route=lambda **kw: False,
            route_token=lambda *a: None,
        )
        self._normal_load_speed = types.SimpleNamespace(park=None)
        self.gate = gate
        self.lane = "ready"
        self.sent = []
        self.sensors = [
            types.SimpleNamespace(runout_buttun_state=False) for _ in range(4)
        ]
        self.printer = self
        self.fault = []
        self.drop = None
        self.onmove = None
        self.gates = None

    def lookup_object(self, name, default=None):
        if name == "webhooks":
            return types.SimpleNamespace(register_endpoint=lambda *x: None)
        if name == "print_stats":
            return types.SimpleNamespace(state="standby")
        if name.startswith("filament_motion_sensor e"):
            return self.sensors[int(name.split(" e")[1][0])]
        return default

    def _rfid_route_heads(self, idx, slot):
        return {slot}

    def _require_no_stop_fault(self):
        pass

    def _is_v2_idx(self, idx):
        return True

    def _save_head_source(self):
        pass

    def _latch_stop_fault(self, *x):
        self.fault.append(x)

    def send_request_to(self, idx, req, cb):
        name = req["method"]
        self.sent.append(name)
        if name == self.drop:
            return
        if name == "discover_device":
            result = {"uid1": 1, "uid2": 2, "uid3": 3}
            cmd = 0
        elif name == "get_status":
            result = {
                "_status_wire_valid": True,
                "slots": [
                    {
                        "index": i,
                        "slot_status": self.lane if i == 0 else "ready",
                        "_wire_valid": True,
                    }
                    for i in range(4)
                ],
            }
            cmd = 6
        elif name == "get_key_state":
            if self.gates is not None:
                self.gate = next(self.gates, self.gate)
            result = {"fields": {"1": int(self.gate)}}
            cmd = 73
        elif name == "unwind_filament":
            self.lane = "rollback"
            self.onmove and self.onmove()
            result = {}
            cmd = 8
        elif name == "stop_feed_filament":
            self.lane = "ready"
            result = {}
            cmd = 9
        else:
            raise AssertionError(name)
        cb(
            None,
            {
                "_v2_frame_valid": True,
                "_v2_payload_valid": True,
                "_v2_command": cmd,
                "code": 0,
                "result": result,
            },
        )


class Flow(unittest.TestCase):
    def run_op(self, m):
        x = SlotActions(m)
        m._slot_actions = x
        x.start(G())
        m.reactor.run()
        return x

    def test_empty_has_stop_no_motor_and_repeat(self):
        m = Machine(False)
        x = self.run_op(m)
        self.assertEqual(x.state["state"], "complete")
        self.assertNotIn("unwind_filament", m.sent)
        x.start(G())
        m.reactor.run()
        self.assertEqual(x.state["state"], "complete")

    def test_gate_empty_after_motor(self):
        m = Machine()
        m.onmove = lambda: setattr(m, "gate", False)
        x = self.run_op(m)
        self.assertEqual(x.state["state"], "complete")
        self.assertEqual(m.sent.count("unwind_filament"), 1)
        self.assertGreaterEqual(m.sent.count("stop_feed_filament"), 2)
        self.assertNotIn("get_feed_info", m.sent)

    def test_lost_motor_ack_still_stops_once(self):
        m = Machine()
        m.drop = "unwind_filament"
        x = self.run_op(m)
        self.assertEqual(x.state["state"], "error")
        self.assertEqual(m.sent.count("unwind_filament"), 1)
        self.assertGreaterEqual(m.sent.count("stop_feed_filament"), 2)

    def test_reconnect_during_motor_fails_closed(self):
        m = Machine()
        m.onmove = lambda: m._serials.update({0: object()})
        x = self.run_op(m)
        self.assertEqual(x.state["state"], "error")
        self.assertTrue(m.fault)

    def test_cancel_during_motor_stops(self):
        m = Machine()
        x = SlotActions(m)
        m._slot_actions = x
        m.onmove = lambda: setattr(x, "cancel", True)
        x.start(G())
        m.reactor.run()
        self.assertEqual(x.state["state"], "error")
        self.assertGreaterEqual(m.sent.count("stop_feed_filament"), 2)

    def test_loaded_target_rejected(self):
        m = Machine()
        m.sensors[0].runout_buttun_state = True
        m._head_source[0] = {"ace_index": 0, "slot": 0}
        x = SlotActions(m)
        with self.assertRaises(RuntimeError):
            x.start(G())
        self.assertEqual(m.sent, [])

    def test_limit_always_present_stops_at_bounded_timeout(self):
        m = Machine()
        x = self.run_op(m)
        self.assertEqual(x.state["state"], "error")
        self.assertIn("时限", x.state["message"])
        self.assertGreaterEqual(m.sent.count("stop_feed_filament"), 2)
        self.assertEqual(m.sent.count("unwind_filament"), 1)

    def test_other_ace_same_head_is_allowed_by_admission(self):
        m = Machine()
        m._ace_devices = ["fake", "other"]
        m.sensors[0].runout_buttun_state = True
        m._head_source[0] = {"ace_index": 1, "slot": 0}
        m._get_heads_for_ace_slot = lambda i, s: [0]
        x = SlotActions(m)
        x.start(G())
        self.assertEqual(x.state["state"], "running")

    def test_gate_bounce_at_initial_stop_rejected(self):
        m = Machine()
        m.gates = iter([True, False, True])
        x = self.run_op(m)
        self.assertEqual(x.state["state"], "error")
        self.assertNotIn("unwind_filament", m.sent)


if __name__ == "__main__":
    unittest.main()
