"""Offline source regression checks; no serial port or heater is used."""
import ast
import importlib.util
import struct
import types
import unittest
from pathlib import Path
from unittest.mock import Mock

HERE = Path(__file__).resolve().parent
EXTRAS = HERE.parent.parent / "providers" / "multiACE" / "multiace" / "klipper" / "extras"
spec = importlib.util.spec_from_file_location("identity_patch", HERE / "patch_flow116.py")
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


def extract(path, name, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    matches = [n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == name]
    if len(matches) != 1:
        raise AssertionError((name, len(matches)))
    exec(compile(ast.Module(body=matches, type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


PROTOCOL = EXTRAS / "ace_protocol_ace2k.py"
HOST = EXTRAS / "ace.py"


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.ns = {"struct": struct, "UID_LEN": 12, "FAILED": -1,
                   "DRYER_METHODS": (), "MOTION_METHODS": ()}
        self.chip = extract(PROTOCOL, "chip_words", self.ns)
        self.execute = extract(PROTOCOL, "_execute", self.ns)
        self.verify = extract(HOST, "_discovery_uid", {})
        self.raw = struct.pack("<3I", 1, 2, 0xffffffff)
        self.host = types.SimpleNamespace(
            _is_ace2k_idx=lambda idx: True, _is_v2_idx=lambda idx: False)
        self.link = types.SimpleNamespace(
            _ready=True, _shutdown=None, _call=Mock(), _pump=Mock(),
            _tick=Mock(), _tag_tick=Mock(), _dryer_tick=Mock(),
            version="0.16.0-233b", uid=self.raw)

    def query(self, raw):
        self.link._call.side_effect = lambda name, response: (
            {"uid": raw} if name == "ace2k_uid_query" and raw is not None else None)
        return self.execute(self.link, {"method": "discover_device", "id": 9})

    def test_native_live_uid(self):
        response = self.query(self.raw)
        self.assertIs(response["_ace2k_uid_verified"], True)
        self.assertNotIn("_v2_frame_valid", response)
        self.assertEqual(self.verify(self.host, 3, response), (1, 2, 0xffffffff))
        self.link._call.assert_any_call("ace2k_uid_query", "ace2k_uid_response")

    def test_no_uid_never_reuses_cache(self):
        for raw in (None, b"", bytes(12), bytes(11), bytes(13)):
            with self.subTest(raw=raw):
                self.link.uid = self.raw
                response = self.query(raw)
                self.assertNotEqual(response["code"], 0)
                self.assertEqual(self.link.uid, b"")
                self.assertIsNone(self.verify(self.host, 3, response))

    def test_protocol_proof_cannot_be_substituted(self):
        response = self.query(self.raw)
        response.pop("_ace2k_uid_verified")
        response.update(_v2_frame_valid=True, _v2_payload_valid=True, _v2_command=0)
        self.assertIsNone(self.verify(self.host, 3, response))
        self.host._is_ace2k_idx = lambda idx: False
        self.host._is_v2_idx = lambda idx: True
        self.assertEqual(self.verify(self.host, 3, response), (1, 2, 0xffffffff))
        for bad in (False, -1, 0x100000000):
            response["result"]["uid1"] = bad
            self.assertIsNone(self.verify(self.host, 3, response))

    def test_queued_stop_does_not_start_or_stop_other_lane(self):
        motion = extract(PROTOCOL, "_motion", {})
        pending = {"waiting": True}
        other = {"waiting": True}
        jobs = {1: pending, 2: other}
        link = types.SimpleNamespace(
            _jobs=jobs, _yield_auto=Mock(), _stop_lane=Mock(),
            _pump=Mock(), _work_events=Mock())
        link._end_job = Mock(side_effect=lambda lane, *args, **kw: jobs.pop(lane))
        response = motion(link, "stop_feed_filament", {"index": 1}, 1)
        self.assertEqual(response["code"], 0)
        self.assertIs(pending["stopped"], True)
        self.assertEqual(jobs, {2: other})
        link._stop_lane.assert_not_called()
        link._end_job.assert_called_once_with(1, "cancelled before start", stop="stopped")

    def test_slot_guard_preserves_legacy_and_other_methods(self):
        source = """class SlotActions:
    def _request(self, idx, method, r, command):
        if not isinstance(r, dict) or r.get('_v2_frame_valid') is not True or r.get('_v2_payload_valid') is not True or r.get('_v2_command') != command:
            raise RuntimeError('%s 响应帧无效' % method)
        return r
"""
        changed = patcher.patch_slot_guard(source)
        self.assertEqual(patcher.patch_slot_guard(changed), changed)
        ns = {}
        exec(changed, ns)
        slot = ns["SlotActions"]()
        slot.ace = self.host
        self.host._discovery_uid = lambda idx, r: self.verify(self.host, idx, r)
        response = self.query(self.raw)
        self.assertIs(slot._request(3, "discover_device", response, 0), response)
        with self.assertRaises(RuntimeError):
            slot._request(3, "get_status", response, 6)
        with self.assertRaises(RuntimeError):
            slot._request(3, "discover_device", {"code": 0}, 0)
        self.host._is_ace2k_idx = lambda idx: False
        self.host._is_v2_idx = lambda idx: True
        with self.assertRaises(RuntimeError):
            slot._request(3, "discover_device", response, 0)
        legacy = dict(response, _v2_frame_valid=True, _v2_payload_valid=True, _v2_command=0)
        self.assertIs(slot._request(3, "discover_device", legacy, 0), legacy)

    def test_source_anchors_idempotent(self):
        for name, pairs in patcher.REPLACEMENTS.items():
            source = (EXTRAS / name).read_text(encoding="utf-8")
            self.assertEqual(patcher.apply_pairs(source, pairs), source)


if __name__ == "__main__":
    unittest.main()
