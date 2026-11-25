"""Regression tests for ACE2K support in the managed slot-eject gate."""
import importlib
import sys
import types
import unittest

from packaged_runtime import runtime_root


ROOT = runtime_root() / "klipper" / "extras"
PKG = "packaged_ace2k_slot_eject_test"
pkg = types.ModuleType(PKG)
pkg.__path__ = [str(ROOT)]
pkg.__package__ = PKG
sys.modules[PKG] = pkg
slot_actions = importlib.import_module(PKG + ".ace_slot_actions")


class Sensor:
    runout_buttun_state = False


class Printer:
    def lookup_object(self, name, default=None):
        if name == "print_stats":
            return types.SimpleNamespace(state="standby")
        if name == "filament_motion_sensor e0_filament":
            return Sensor()
        return default


class Ace:
    def __init__(self, protocol="ace2k", connected=True, fw_hold=False):
        self.printer = Printer()
        self.protocol = protocol
        self._connected_per_ace = {0: connected}
        self._fw_update_hold = {0} if fw_hold else set()
        self._serials = {0: object()}
        self._path_calibration = None
        self._swap_in_progress = False
        self._unload_all_active = False
        self._tag_read_busy = False
        self._gen1_manual_busy = False
        self._calibration = {"state": "idle"}
        self._calibration_unload = {}
        self._head_source = {}

    def _require_no_stop_fault(self, **kwargs):
        pass

    def _is_v2_idx(self, idx):
        return self.protocol == "v2"

    def _is_ace2k_idx(self, idx):
        return self.protocol == "ace2k"

    def _get_heads_for_ace_slot(self, idx, slot):
        return []

    def _rfid_route_heads(self, idx, slot):
        return [0]


class SlotEjectAce2kTests(unittest.TestCase):
    def check(self, ace):
        action = object.__new__(slot_actions.SlotActions)
        action.ace = ace
        action.state = {"state": "idle", "kind": ""}
        action.cancel = False
        action._check(0, 0, for_eject=True)

    def test_connected_ace2k_is_allowed(self):
        self.check(Ace("ace2k"))

    def test_connected_v2_remains_allowed(self):
        self.check(Ace("v2"))

    def test_v1_remains_unsupported(self):
        with self.assertRaisesRegex(RuntimeError, "ACE 离线或不支持此操作"):
            self.check(Ace("v1"))

    def test_ace2k_offline_is_still_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "ACE 离线或不支持此操作"):
            self.check(Ace("ace2k", connected=False))

    def test_ace2k_firmware_update_hold_is_still_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "ACE 离线或不支持此操作"):
            self.check(Ace("ace2k", fw_hold=True))

    def test_ace2k_discover_response_is_accepted_for_location_identity(self):
        source = (runtime_root() / "klipper" / "extras" / "ace.py").read_text()
        self.assertIn("and (self._is_ace2k_idx(idx) or", source)
        self.assertIn("self._location_uid_by_idx[idx] = (request_transport, uid)", source)
        self.assertIn("not (self._is_v2_idx(idx) or self._is_ace2k_idx(idx))", source)


if __name__ == "__main__":
    unittest.main()
