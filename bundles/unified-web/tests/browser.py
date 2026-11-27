"""Exercise the packaged console in Chrome against same-origin printer fixtures."""
import json
import mimetypes
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


ASSETS = Path(__file__).resolve().parents[1]
HEATERS = ["extruder", "extruder1", "extruder2", "extruder3"]


class Printer:
    def __init__(self):
        self.calls = []
        self.tasks = []
        self.selected = []
        self.setting = "off"
        self.fail_save = True
        self.config = "[ace]\nfeed_speed: 80  # printer setting\n"
        self.units = [
            dict(idx=a, unit_key=f"uid:ace{a}", connected=True, protocol="ace2k",
                 slots=[dict(idx=s, state="ready", material=["PETG", "PLA", "PLA", "ASA"][a],
                             color=["#e3b230", "#216f87", "#e3978a", "#4f585b"][(a + s) % 4])
                        for s in range(4)])
            for a in range(4)
        ]

    def respond(self, method, url, body):
        path = urlsplit(url).path
        self.calls.append((method, path, body))
        if path == "/multiace/api/state":
            return 200, dict(aces=self.units, toolheads=[], mode="multi", printer_state="standby")
        if path == "/printer/objects/query":
            if "material_batch_tasks" in url:
                status = {"ace": {"material_batch_tasks": self.tasks}}
            elif "configfile" in url:
                status = {"configfile": {"settings": {h: {"max_temp": 300} for h in HEATERS + ["heater_bed"]}}}
            else:
                status = dict(webhooks={"state": "ready"}, print_stats={"state": "standby"},
                              toolhead={"extruder": "extruder", "position": [20, 30, 40, 0]},
                              **{h: {"temperature": 35 + i, "target": 0} for i, h in enumerate(HEATERS + ["heater_bed"])})
            return 200, {"result": {"status": status}}
        if path == "/multiace/api/print-control":
            return 200, {"state": "standby", "speed_factor": 100, "extrude_factor": 100, "fan": 0}
        if path == "/multiace/api/materials":
            return 200, {"materials": ["PLA", "PETG"]}
        if path == "/multiace/api/material/batch/preview":
            self.selected = body["slots"]
            return 200, {"ticket": "fixture", "needs_parking_confirmation": True,
                         "slots": [{**s, "state": "inspect_parked"} for s in self.selected]}
        if path == "/multiace/api/material/batch/start":
            self.tasks = [{**s, "action": "eject", "id": f"task{i}", "batch": "batch1",
                           "state": "native", "cancel_mode": "targeted"} for i, s in enumerate(self.selected)]
            return 200, {"batch": "batch1", "tasks": [t["id"] for t in self.tasks]}
        if path == "/multiace/api/material/batch/stop":
            task = next(t for t in self.tasks if t["id"] == body["task"])
            task.update(state="cancelled", cancel_mode="none")
            return 200, {"accepted": True, "state": "cancelled"}
        if path == "/multiace/api/slot-override/sync":
            if self.fail_save:
                return 500, {"detail": "耗材保存失败（测试）"}
            self.units[body["ace"]]["slots"][body["slot"]].update(material=body["material"])
            return 200, {"ok": True, "override": body}
        if path == "/u1/device/firmware/settings":
            if method == "POST":
                self.setting = body["value"]
                return 200, {"ok": True, "state": "applied", "current": self.setting}
            return 200, {"available": True, "groups": [{"id": "general", "label": "设备"}],
                         "settings": [{"id": "bed_mesh", "group": "general", "label": "打印前调平",
                                       "current": self.setting, "options": [
                                           {"value": "off", "label": "关"}, {"value": "on", "label": "开"}]}]}
        if path == "/multiace/api/config":
            if method == "PUT":
                return 409, {"detail": "another client updated ace.cfg"}
            return 200, {"content": self.config, "sha1": "initial-revision"}
        if path == "/multiace/api/history":
            return 200, {"jobs": [{"filename": "dual-color.gcode", "status": "completed"}]}
        if path == "/multiace/api/queue":
            return 200, {"queue": []}
        return 404, {"detail": "Unknown fixture route " + path}

    def writes(self, path):
        return [b for method, p, b in self.calls if method != "GET" and p == path]


def serve(printer):
    assets = {"/u1/" + p.name: p for p in ASSETS.iterdir() if p.suffix in {".html", ".js", ".css"}}
    assets["/u1/"] = assets["/u1/index.html"]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if self.command == "GET" and path in assets:
                asset = assets[path]
                self.send(200, mimetypes.guess_type(asset)[0], asset.read_bytes())
                return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else None
            status, data = printer.respond(self.command, self.path, body)
            self.send(status, "application/json", json.dumps(data).encode())

        do_POST = do_PUT = do_GET

        def send(self, status, media, data):
            self.send_response(status)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def preview(page, name):
    root = os.getenv("RUNNER_TEMP")
    if not root:
        return
    folder = Path(root) / "u1-web-preview"
    folder.mkdir(parents=True, exist_ok=True)
    page.evaluate("""() => {
        const badge = document.createElement('span');
        badge.id = 'fixture-label';
        badge.textContent = '模拟设备数据';
        badge.style.cssText = 'border:1px solid #c8c9be;padding:2px 7px;border-radius:5px;font-size:12px;margin-right:6px';
        document.querySelector('#origin').replaceChildren(badge);
    }""")
    try:
        page.screenshot(path=folder / name)
    finally:
        page.evaluate("document.querySelector('#origin').textContent = location.host")


def exercise(page, origin, printer):
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
    page.goto(origin + "/u1/")
    btn = lambda label: page.get_by_role("button", name=label, exact=True)
    nav = lambda view: page.locator(f'#nav a[href="#{view}"]').click()
    confirm = lambda label: page.get_by_role("dialog").get_by_role("button", name=label, exact=True).click()
    page.get_by_text("打印机已连接", exact=False).wait_for()
    preview(page, "mobile-overview-simulated.png")
    page.locator(".op-heads button").nth(2).click()
    expect(page.locator(".op-heads button").nth(2)).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_role("spinbutton", name=re.compile(r"^喷头 3"))).to_be_enabled()
    assert not [v for v in printer.calls if v[0] != "GET"], "Opening a tool must be read-only"

    nav("material")
    slots = page.locator(".mat-slot")
    expect(slots).to_have_count(16)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    if os.getenv("RUNNER_TEMP"):
        page.set_viewport_size({"width": 1280, "height": 900})
        preview(page, "desktop-material-simulated.png")
        page.set_viewport_size({"width": 390, "height": 844})
    slots.nth(0).click()
    slots.nth(4).click()
    expect(btn("装入喷头")).to_be_disabled()
    btn("退出 ACE").click()
    expect(page.get_by_role("dialog")).to_contain_text("检查")
    confirm("取消")
    start = "/multiace/api/material/batch/start"
    assert not printer.writes(start)
    btn("退出 ACE").click()
    confirm("确认")
    expect(btn("停止此槽")).to_have_count(2)
    assert printer.writes("/multiace/api/material/batch/preview")[-1] == {
        "action": "eject", "slots": [{"ace": 0, "slot": 0, "serial": "uid:ace0"},
                                     {"ace": 1, "slot": 0, "serial": "uid:ace1"}]}
    assert printer.writes(start) == [{"ticket": "fixture", "confirm_parked": True}]
    btn("停止此槽").nth(1).click()
    confirm("确认")
    expect(page.locator(".mat-task").filter(has_text="已取消")).to_have_count(1)
    assert printer.writes("/multiace/api/material/batch/stop") == [{"task": "task1"}]

    slots.nth(5).click()
    btn("编辑耗材").click()
    material = page.get_by_label("材料", exact=True)
    material.fill("PETG")
    btn("保存耗材").click()
    expect(page.locator("#notice")).to_contain_text("耗材保存失败")
    expect(material).to_have_value("PETG")
    printer.fail_save = False
    btn("保存耗材").click()
    expect(slots.nth(5)).to_contain_text("PETG")
    assert printer.writes("/multiace/api/slot-override/sync")[-1] == {"ace": 1, "slot": 1, "material": "PETG"}

    nav("settings")
    page.get_by_role("searchbox").fill("调平")
    setting = page.get_by_role("combobox", name="打印前调平")
    setting.select_option("on")
    btn("应用").click()
    with page.expect_response(lambda r: r.url.endswith("/u1/device/firmware/settings") and r.request.method == "GET"):
        confirm("确认")
    expect(page.locator("#notice")).to_contain_text("设备已确认设置")
    expect(setting).to_have_value("on")
    path = "/u1/device/firmware/settings"
    assert [c[0] for c in printer.calls if c[1] == path][-3:] == ["GET", "POST", "GET"]
    assert printer.writes(path) == [{"id": "bed_mesh", "value": "on", "inputs": {}, "confirm": True}]

    page.get_by_role("link", name="MultiACE 参数").click()
    speed = page.get_by_label("进料速度（mm/s）")
    speed.fill("88")
    btn("保存配置").click()
    confirm("确认")
    expect(page.get_by_text("设备上的配置已被修改。", exact=False)).to_be_visible()
    expect(speed).to_have_value("88")
    assert printer.writes("/multiace/api/config") == [{"content": printer.config.replace("80","88"),
                                                       "base_sha1": "initial-revision", "restart_klipper": False}]
    nav("jobs")
    expect(page.get_by_text("dual-color.gcode")).to_be_visible()
    expect(page.get_by_text("已完成", exact=True)).to_be_visible()
    expect(page).to_have_url(origin + "/u1/#jobs")
    page.go_back()
    expect(page).to_have_url(origin + "/u1/#aceconfig")
    assert len(page.context.pages) == 1
    assert not errors, errors


def main():
    printer = Printer()
    server = serve(printer)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(viewport={"width": 390, "height": 844})
                exercise(page, f"http://127.0.0.1:{server.server_port}", printer)
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print("PASS mobile console: routing, per-bay tasks, settings, conflict handling and history")


if __name__ == "__main__":
    main()
