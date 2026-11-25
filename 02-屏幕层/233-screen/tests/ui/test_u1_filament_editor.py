# SPDX-License-Identifier: GPL-3.0-or-later

import json
import time

import pytest
from helix.app import HelixApp, HelixCtlError

from conftest import BINARY


def test_u1_filament_selection_and_quick_colors(tmp_path):
    if not BINARY.exists():
        pytest.skip(f"{BINARY} not built")
    # Keep the mock's first-use top-cover notice from covering the filament panel.
    (tmp_path / "settings-test.json").write_text(json.dumps({
        "printers": {"default": {"chamber_air": {"first_fit_shown": True}}},
        "display": {"animations_enabled": False, "theme": "u1-warm"},
        "language": "zh",
    }))
    with HelixApp(
        BINARY,
        tmp_path / "control.sock",
        tmp_path / "app.log",
        extra_args=["-s", "480x320", "--display", "0", "--light"],
        extra_env={
            "HELIX_CONFIG_DIR": str(tmp_path),
            "HELIX_MOCK_PRINTER": "snapmaker_u1",
            "HELIX_MOCK_AMS": "multiace",
            "HELIX_MOCK_MULTIACE_MODE": "multi",
            "HELIX_MOCK_MULTIACE_UNITS": "2",
            "HELIX_MOCK_AUTO_PRINT": "0",
            "SDL_RENDER_DRIVER": "software",
        },
    ) as app:
        def geom(name):
            return app.geom(name)["widgets"][0]

        def tap_at_center(w):
            app.ctl("tap", w["x"] + w["w"] // 2, w["y"] + w["h"] // 2)

        def tap(name):
            tap_at_center(geom(name))

        app.navigate("filament")
        # Wait for the first ACE snapshot, not the recovery view with four feeder cards.
        app.wait_for("ams_slot_4_color", 0xFF0000, timeout=10)
        deadline = time.monotonic() + 5
        cards = []
        while time.monotonic() < deadline:
            try:
                cards = [w for w in app.geom("u1_filament_cards", 1)["widgets"]
                         if w.get("name") == "u1_filament_card" and w["w"] > 20]
                if len(cards) == 4:
                    break
            except HelixCtlError as err:
                if "Widget not found" not in err.message:
                    raise
            time.sleep(0.05)
        assert len(cards) == 4

        def card_colors(image, card):
            x, y = card["x"], card["y"] + card["h"] // 2
            return image.getpixel((x + 4, y))[:3], image.getpixel((x + 1, y))[:3]

        # The mock routes slot 1 to head 1 at boot. Selection moves from slot 1
        # to slot 2, while the confirmed source keeps its distinct background.
        before = app.capture()
        before.save(tmp_path / "before.png")
        app.ctl("tap", cards[1]["x"] + 32, cards[1]["y"] + cards[1]["h"] // 2)
        after = app.capture()
        after.save(tmp_path / "after.png")
        loaded_before, selected_border = card_colors(before, cards[0])
        loaded_after, idle_border = card_colors(after, cards[0])
        idle_card, new_selected_border = card_colors(after, cards[1])
        assert loaded_before == loaded_after
        assert loaded_after != idle_card
        assert selected_border == new_selected_border
        assert selected_border != idle_border

        pencil = next(w for w in app.geom("u1_filament_cards", 2)["widgets"]
                      if w.get("name") == "u1_filament_card_pencil"
                      and cards[0]["x"] <= w["x"] < cards[0]["x"] + cards[0]["w"])
        tap_at_center(pencil)
        tap("u1_filament_edit_slot")
        app.wait_for("u1_material_edit_visible", 1)
        title, back = geom("material_edit_title"), geom("material_edit_back")
        assert abs(2 * title["x"] + title["w"] - 480) <= 2
        assert title["x"] > back["x"] + back["w"]
        row, dropdown = geom("material_edit_type_row"), geom("material_edit_dropdown")
        assert abs((dropdown["y"] - row["y"]) * 2 + dropdown["h"] - row["h"]) <= 1
        footer = geom("material_edit_save")
        chips = [geom(f"material_edit_quick_color_{i}") for i in range(8)]
        assert all(c["w"] >= 44 and c["h"] >= 44 and c["y"] + c["h"] < footer["y"]
                   for c in chips)
        assert all(a["x"] + a["w"] <= b["x"] for a, b in zip(chips, chips[1:]))
        editor = app.capture()
        editor.save(tmp_path / "editor.png")
        assert [editor.getpixel((c["x"] + c["w"] // 2, c["y"] + c["h"] // 2))[:3]
                for c in chips] == [(245, 245, 245), (28, 28, 28), (158, 158, 158),
                                    (211, 47, 47), (240, 117, 26), (245, 196, 0),
                                    (46, 125, 50), (21, 101, 192)]

        original = app.get("u1_material_edit_color")["value"]
        tap("material_edit_quick_color_3")
        app.wait_for("u1_material_edit_color", "#D32F2F")
        app.wait_for("u1_material_edit_quick_color", 3)
        tap("material_edit_open_color")
        app.wait_for("u1_material_edit_visible", 0)
        app.screenshot(str(tmp_path / "full-palette.png"))
        tap("u1_filament_color_cancel")
        app.wait_for("u1_material_edit_visible", 1)
        assert app.get("u1_material_edit_color")["value"] == "#D32F2F"

        tap("material_edit_cancel")
        tap_at_center(pencil)
        tap("u1_filament_edit_slot")
        app.wait_for("u1_material_edit_visible", 1)
        assert app.get("u1_material_edit_color")["value"] == original

        tap("material_edit_quick_color_3")
        tap("material_edit_save")
        app.wait_for("u1_material_edit_visible", 0)
        app.wait_for("ams_slot_4_color", 0xD32F2F, timeout=5)
