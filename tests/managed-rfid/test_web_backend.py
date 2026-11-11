import ast, json, os, pathlib, shutil, subprocess, tarfile, tempfile, time, typing, unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
ARCHIVE = (
    REPO
    / "overlays/233-dev.2-perms.1/root/usr/local/share/233-firmware/multiace-managed-1.27b-233.20261111-single.3.tar.gz"
)


class BackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tarfile.open(ARCHIVE) as t:
            m = next(x for x in t if x.name.endswith("/web/backend/main.py"))
            source = t.extractfile(m).read()
        names = {
            "_rfid_hold_state",
            "_parse_state",
            "_resolve_head_source",
            "_slot_state_name",
            "_color_to_hex",
            "_tag_read_possible",
            "_community_fw_kind",
            "_int_or",
            "_rfid_position_flags",
            "_rfid_head_position_flag",
        }
        nodes = [
            x
            for x in ast.parse(source).body
            if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))
            and x.name in names
        ]
        ns = {
            "Any": typing.Any,
            "os": os,
            "time": time,
            "_reload_overrides_if_changed": lambda: None,
            "_override_for": lambda *a: None,
            "_drop_override_if_present": lambda *a: False,
            "_save_overrides_to_disk": lambda: None,
            "_eject_pending_since": {},
            "EJECT_DEBOUNCE_S": 2,
            "_read_display_index_base": lambda: 1,
            "_inbox_status": lambda: {},
        }
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
                "packaged-backend",
                "exec",
            ),
            ns,
        )
        cls.parse = staticmethod(ns["_parse_state"])

    def test_complete_state_preserves_hold_and_bad_data_fails_closed(self):
        route = {"slot": 0, "ace_index": 0}
        ace = {
            "device_count": 1,
            "aces": [
                {
                    "idx": 0,
                    "connected": True,
                    "slots": [
                        {
                            "index": i,
                            "status": "ready",
                            "position_unconfirmed": i == 0,
                            "eject_recovery_allowed": i == 0,
                        }
                        for i in range(4)
                    ],
                }
            ],
            "location_hold": {"rfid_routes": [route], "rfid_routes_valid": True},
        }
        result = self.parse({"ace": ace})
        self.assertEqual(result["location_hold"]["rfid_routes"], [route])
        self.assertTrue(result["aces"][0]["slots"][0]["position_unconfirmed"])
        self.assertTrue(result["aces"][0]["slots"][0]["eject_recovery_allowed"])
        ace["location_hold"] = {"rfid_routes": [{"slot": 9}], "rfid_routes_valid": True}
        self.assertFalse(self.parse({"ace": ace})["location_hold"]["rfid_routes_valid"])

    def test_backend_to_actual_packaged_ui(self):
        if not shutil.which("node"):
            self.skipTest("node is required for web behavior tests")
        slots = lambda: [
            {
                "index": i,
                "status": "ready",
                "position_unconfirmed": False,
                "eject_recovery_allowed": False,
            }
            for i in range(4)
        ]
        devices = [
            {
                "idx": i,
                "connected": True,
                "protocol": "v2",
                "gate_status": [1] * 4,
                "slots": slots(),
            }
            for i in range(3)
        ]
        devices[1]["slots"][0].update(
            position_unconfirmed=True, eject_recovery_allowed=True
        )
        devices[1]["gate_status"][0] = 0
        devices[2]["slots"][0].update(
            position_unconfirmed=True, eject_recovery_allowed=False
        )
        ace = {
            "device_count": 3,
            "aces": devices,
            "location_hold": {
                "rfid_routes": [{"slot": 0, "ace_index": 1}],
                "rfid_routes_valid": True,
            },
            "head_position_unconfirmed": {
                "0": True,
                "1": False,
                "2": False,
                "3": False,
            },
        }
        status = {
            "ace": ace,
            "print_stats": {"state": "standby"},
            "filament_feed left": {
                "extruder0": {"filament_at_extruder": False},
                "extruder1": {"filament_at_extruder": False},
            },
            "filament_feed right": {
                "extruder2": {"filament_at_extruder": False},
                "extruder3": {"filament_at_extruder": False},
            },
        }
        parsed = self.parse(status)
        self.assertEqual(parsed["aces"][1]["slots"][0]["state"], "empty")
        with tempfile.TemporaryDirectory() as td, tarfile.open(ARCHIVE) as t:
            state = pathlib.Path(td) / "state.json"
            state.write_text(json.dumps(parsed))
            member = next(x for x in t if x.name.endswith("/web/frontend/app.js"))
            app = pathlib.Path(td) / "app.js"
            app.write_bytes(t.extractfile(member).read())
            subprocess.run(
                [
                    "node",
                    str(pathlib.Path(__file__).with_name("js-mapper-to-ui.js")),
                    str(state),
                    str(app),
                ],
                check=True,
                timeout=15,
                text=True,
            )
