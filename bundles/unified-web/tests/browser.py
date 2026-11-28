"""Exercise the packaged console in Chrome against same-origin printer fixtures."""
import json
import mimetypes
import os
import re
import struct
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from playwright.sync_api import expect, sync_playwright


ASSETS = Path(__file__).resolve().parents[1]
HEATERS = ["extruder", "extruder1", "extruder2", "extruder3"]
PANEL_FIXTURES = {
    "/multiace/": ("text/html", b"""<!doctype html><meta charset="utf-8">
        <link rel="stylesheet" href="fixture-panel.css">
        <p id="panel-status">Opening panel</p><script defer src="fixture-panel.js"></script>"""),
    "/multiace/fixture-panel.css": ("text/css", b"#panel-status{color:rgb(24,86,140)}"),
    "/multiace/fixture-panel.js": ("text/javascript", b"""
        fetch('/multiace/api/fixture-read').then(r => r.json()).then(data => {
            document.querySelector('#panel-status').textContent = data.status;
        });
    """),
    "/multiace/api/fixture-read": ("application/json", b'{"status":"Original panel loaded"}'),
}



def sample_image(color):
    def chunk(kind, content):
        return (struct.pack(">I", len(content)) + kind + content
                + struct.pack(">I", zlib.crc32(kind + content)))
    header = struct.pack(">IIBBBBB", 24, 16, 8, 2, 0, 0, 0)
    pixels = (b"\x00" + color * 24) * 16
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


class Printer:
    def __init__(self):
        self.calls = []
        self.job = {"state": "standby"}
        self.print_progress = {}
        self.file_progress = {}
        self.exclude_object = {}
        self.pending_skip = None
        self.thumbnail_requests = []
        self.thumbnail_mode = 'metadata'
        self.thumbnail_image = sample_image(b"\xd1\x9f\x44")
        self.camera = None
        self.webrtc_failure = False
        self.screen_revision = 1
        self.screen_tags = []
        self.screen_frame = ("image/png", sample_image(b"\x17\x63\xa5"))
        self.camera_frame = ("image/png", sample_image(b"\xca\x86\x37"))
        self.tasks = []
        self.selected = []
        self.setting = "off"
        self.fail_save = True
        self.panel_auth = False
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
                status = dict(webhooks={"state": "ready"}, print_stats=self.job,
                              display_status=self.print_progress, virtual_sdcard=self.file_progress,
                              exclude_object=self.exclude_object,
                              toolhead={"extruder": "extruder", "position": [20, 30, 40, 0]},
                              **{h: {"temperature": 35 + i, "target": 0} for i, h in enumerate(HEATERS + ["heater_bed"])})
            return 200, {"result": {"status": status, "eventtime": time.monotonic()}}
        if path == "/multiace/api/print-control":
            if method == "POST":
                self.job["state"] = {"pause":"paused", "resume":"printing", "cancel":"cancelled"}[body["verb"]]
                return 200, {"ok": True}
            return 200, {"state": self.job["state"], "speed_factor": 100, "extrude_factor": 100, "fan": 0}
        if path == "/printer/gcode/script":
            prefix = "EXCLUDE_OBJECT NAME="
            assert method == "POST" and body["script"].startswith(prefix)
            self.pending_skip = body["script"].removeprefix(prefix)
            return 200, {"result": "ok"}
        if path == "/multiace/api/history/reprint":
            assert method == "POST" and body["filename"] == self.job["filename"]
            self.job["state"] = "printing"
            return 200, {"ok": True}
        if path == "/server/files/metadata":
            assert parse_qs(urlsplit(url).query)["filename"] == [self.job["filename"]]
            metadata = {"estimated_time": 2400, "layer_count": 200}
            if self.thumbnail_mode == 'metadata':
                metadata["thumbnails"] = [{"relative_path": ".thumbs/small.png", "width": 96},
                                          {"relative_path": ".thumbs/预览 & 300.png", "width": 300},
                                          {"relative_path": "../../unexpected.png", "width": 2000}]
            return 200, {"result": metadata}
        if path == "/multiace/api/webcam/info":
            options = {"webrtc": {"signal_path": "/api/webcam/webrtc-signal"},
                       "mjpeg": {"proxy_path": "/api/webcam/stream"}}
            return 200, {"available": bool(self.camera), "name": "case",
                         "snapshot_path": "/api/webcam/snapshot", **options.get(self.camera, {})}
        if path == "/multiace/api/webcam/webrtc-signal":
            assert method == "POST"
            if self.webrtc_failure:
                return 502, {"detail": "Camera disconnected"}
            if body == {"type": "request"}:
                return 200, {"type": "offer", "id": "fixture-peer", "sdp": "offer-fixture"}
            assert body == {"type": "answer", "id": "fixture-peer", "sdp": "answer-fixture"}
            return 200, {"ok": True}
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
            if path == "/" and urlsplit(self.path).query == "workspace=1":
                self.send(200, "text/html", b"""<!doctype html><title>Printer workspace</title>
                    <button id="sign-in">Sign in to fixture</button><script>
                    document.querySelector('#sign-in').onclick = () => {
                        const key = 'user-token-' + location.host.replace(/[^a-zA-Z0-9]/g,'_');
                        localStorage.setItem(key, 'fixture-printer-session');
                    };</script>""")
                return
            if path in PANEL_FIXTURES or path == "/u1/device/firmware/settings":
                if printer.panel_auth and self.headers.get("Authorization") != "Bearer fixture-printer-session":
                    self.send(401, "application/json", b'{"detail":"Session required"}')
                    return
            if path in PANEL_FIXTURES:
                media, content = PANEL_FIXTURES[path]
                self.send(200, media, content)
                return
            if self.command == "GET" and (path.startswith("/server/files/gcodes/") or path == "/multiace/api/thumbnail"):
                target = unquote(path)
                printer.thumbnail_requests.append(target)
                if target.endswith("/.thumbs/预览 & 300.png") or (path == "/multiace/api/thumbnail"
                                                                  and printer.thumbnail_mode == 'proxy'):
                    self.send(200, "image/png", printer.thumbnail_image)
                else:
                    self.send(404, "application/json", b'{"detail":"No thumbnail"}')
                return
            if self.command == "GET" and path == "/screen/snapshot":
                tag = '"screen-%s"' % printer.screen_revision
                request_tag = self.headers.get("If-None-Match")
                printer.screen_tags.append(request_tag)
                if request_tag == tag:
                    self.send(304, printer.screen_frame[0], b"", etag=tag)
                else:
                    self.send(200, *printer.screen_frame, etag=tag)
                return
            if self.command == "GET" and path in ("/multiace/api/webcam/snapshot", "/multiace/api/webcam/stream"):
                printer.calls.append(("GET", path, None))
                self.send(200, *printer.camera_frame)
                return
            if self.command == "GET" and path in assets:
                asset = assets[path]
                self.send(200, mimetypes.guess_type(asset)[0], asset.read_bytes())
                return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else None
            status, data = printer.respond(self.command, self.path, body)
            self.send(status, "application/json", json.dumps(data).encode())

        do_POST = do_PUT = do_GET

        def send(self, status, media, data, etag=None):
            self.send_response(status)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if etag:
                self.send_header("ETag", etag)
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
    # Verify Chrome actually used a font with Chinese glyphs for this
    # title. document.fonts.check can report true even for missing glyphs.
    page.evaluate("document.fonts.ready")
    session = page.context.new_cdp_session(page)
    try:
        session.send("DOM.enable")
        session.send("CSS.enable")
        document = session.send("DOM.getDocument")
        title = session.send("DOM.querySelector", {"nodeId": document["root"]["nodeId"], "selector": "#title"})
        used = session.send("CSS.getPlatformFontsForNode", {"nodeId": title["nodeId"]})["fonts"]
        assert any("Noto Sans CJK" in face["familyName"] and face["glyphCount"] >= 2 for face in used), used
    finally:
        session.detach()
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
    for width, columns in ((390, 1), (940, 2), (1280, 4)):
        page.set_viewport_size({"width": width, "height": 844 if width == 390 else 900})
        page.wait_for_function("""count => {
            const cards = [...document.querySelectorAll('.mat-unit')];
            return cards.length === 4 && cards.filter(e => e.offsetTop === cards[0].offsetTop).length === count;
        }""", arg=columns)
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


def check_integrated_panel(page, origin, printer):
    page.goto(origin + "/u1/#printhub")
    normal = page.frame_locator("#page iframe").locator("#panel-status")
    expect(normal).to_have_text("Original panel loaded")
    expect(normal).to_have_css("color", "rgb(24, 86, 140)")
    expect(page).to_have_url(origin + "/u1/#printhub")
    assert len(page.context.pages) == 1

    # A protected printer with no signed-in session must not render the panel.
    printer.panel_auth = True
    page.reload()
    expect(page.locator("#login")).to_be_visible()
    expect(page.locator("#page iframe")).to_have_count(0)

    # An inline workspace login supplies the host-specific token for native
    # API requests. Reconnect must restore the section that requested login.
    page.locator('#nav a[href="#settings"]').click()
    expect(page.locator("#page .error-box")).to_contain_text("Session required")
    page.locator("#open-login").click()
    workspace = page.frame_locator('#page iframe[src="/?workspace=1"]')
    workspace.get_by_role("button", name="Sign in to fixture").click()
    expect(page).to_have_url(origin + "/u1/#fluidd")
    page.locator("#retry-login").click()
    expect(page).to_have_url(origin + "/u1/#settings")
    expect(page.locator(".settings-group summary")).to_contain_text("设备")
    expect(page.locator("#login")).to_be_hidden()
    assert len(page.context.pages) == 1

    # On a host that accepts only Authorization headers the old HTML panel
    # stays protected even when native API reads now succeed.
    page.goto(origin + "/u1/#printhub")
    expect(page.locator("#page .error-box")).to_contain_text("打印机已确认登录")
    expect(page.locator("#login")).to_be_hidden()
    expect(page.locator("#page iframe")).to_have_count(0)

    # A saved Fluidd hash arrives with the redirected /u1/ document, then
    # the startup router transfers it intact to the original workspace.
    page.goto(origin + "/?workspace=1")  # ensure the bookmark loads a fresh document
    page.goto(origin + "/u1/#/jobs")
    expect(page).to_have_url(origin + "/?workspace=1#/jobs")
    expect(page.get_by_role("button", name="Sign in to fixture")).to_be_visible()


def check_print_monitor(page, origin, printer):
    page.add_init_script("""(() => {
        window.fixturePeers = [];
        window.RTCPeerConnection = class extends EventTarget {
            constructor() { super(); this.iceGatheringState = 'complete'; fixturePeers.push(this); }
            addTransceiver(kind, options) { if (kind !== 'video' || options.direction !== 'recvonly') throw Error('Expected a receive-only video track'); }
            async setRemoteDescription(offer) { if (offer.sdp !== 'offer-fixture') throw Error('Wrong offer'); }
            async createAnswer() { return {type:'answer', sdp:'answer-fixture'}; }
            async setLocalDescription(answer) {
                this.localDescription = answer;
                const canvas = Object.assign(document.createElement('canvas'), {width:96,height:54});
                const context = canvas.getContext('2d');
                const stream = canvas.captureStream(4);
                const event = Object.assign(new Event('track'), {track:stream.getVideoTracks()[0],streams:[stream]});
                this.stream = stream;
                this.draw = setInterval(() => context.fillRect(0,0,96,54), 80);
                this.dispatchEvent(event);
            }
            close() { this.closed = true; clearInterval(this.draw); this.stream?.getTracks().forEach(t=>t.stop()); }
        };
    })();""")
    printer.job = {"state": "printing", "filename": "测试/gear & lid.gcode", "print_duration": 1020,
                   "total_duration": 1120, "filament_used": 1234, "info": {"current_layer": 83}}
    printer.print_progress = {"progress": 0.425, "message": "正在打印外壁"}
    printer.file_progress = {"progress": 0.39}
    printer.camera = "webrtc"
    page.goto(origin + "/u1/")
    details = page.locator(".print-details")
    expect(details).to_contain_text("测试/gear & lid.gcode")
    expect(details).to_contain_text("42.5%")
    expect(details).to_contain_text("83 / 200")
    expect(details).to_contain_text("18 分钟")
    expect(details).to_contain_text("23 分钟")
    expect(details).to_contain_text("1.23 m")
    expect(details).to_contain_text("正在打印外壁")
    expect(details.get_by_role("progressbar")).to_have_attribute("value", "42.5")
    expect(details.get_by_alt_text("当前模型预览")).to_be_visible()
    assert "/server/files/gcodes/测试/.thumbs/预览 & 300.png" in printer.thumbnail_requests
    assert not any(path.endswith("/unexpected.png") for path in printer.thumbnail_requests)
    expect(page.get_by_text("WebRTC 实时画面")).to_be_visible()
    assert printer.writes("/multiace/api/webcam/webrtc-signal")[-2:] == [
        {"type": "request"}, {"type": "answer", "id": "fixture-peer", "sdp": "answer-fixture"}]
    screen = page.get_by_alt_text("U1 实体触摸屏")
    expect(screen).to_be_visible()
    first_frame = screen.get_attribute("src")
    page.wait_for_timeout(1200)
    assert '"screen-1"' in printer.screen_tags
    expect(screen).to_have_attribute("src", first_frame)
    printer.screen_revision = 2
    expect(screen).not_to_have_attribute("src", first_frame)
    for width in (390, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    preview(page, "desktop-print-monitor-simulated.png")

    before = len(printer.screen_tags)
    page.locator('#nav a[href="#jobs"]').click()
    expect(page.get_by_role("heading", name="当前打印")).to_be_visible()
    expect(page.locator(".print-details")).to_contain_text("42.5%")
    expect(page.locator(".print-details")).to_contain_text("83 / 200")
    assert page.evaluate("fixturePeers[0].closed === true")
    page.wait_for_timeout(1200)
    assert len(printer.screen_tags) <= before + 1

    printer.job = {**printer.job, "state": "paused", "info": {"current_layer": 84}}
    expect(page.locator(".print-details")).to_contain_text("已暂停")
    expect(page.locator(".print-details")).to_contain_text("84 / 200")
    expect(page.get_by_text("预计完成", exact=True).locator("..").locator("dd")).to_have_text("—")
    printer.webrtc_failure = True
    page.locator('#nav a[href="#overview"]').click()
    expect(page.get_by_text("定时快照")).to_be_visible()
    expect(page.get_by_alt_text("case")).to_be_visible()
    assert not printer.writes("/screen/touch")


def check_print_actions(page, origin, printer):
    name = "测试件.stl_id_0_copy_1"
    unsafe = "Bad\nCANCEL_PRINT"
    printer.camera = None
    printer.job = {"state":"printing", "filename":"plate.gcode", "print_duration":100, "total_duration":120}
    printer.exclude_object = {"objects":[{"name": name}, {"name":"Already_done"}, {"name":unsafe}],
                              "current_object":name, "excluded_objects":["Already_done"]}
    printer.thumbnail_mode = "proxy"
    page.goto(origin + "/u1/#jobs")
    details = page.locator(".print-details")
    expect(details.get_by_alt_text("当前模型预览")).to_be_visible()
    assert "/multiace/api/thumbnail" in printer.thumbnail_requests

    page.get_by_text("跳过模型", exact=True).click()
    skip = page.get_by_role("button", name="跳过 " + name, exact=True)
    dialog = page.get_by_role("dialog")
    expect(page.get_by_text("当前对象", exact=True)).to_be_visible()
    expect(page.get_by_text("已跳过", exact=True)).to_have_count(1)
    expect(page.get_by_text("名称不支持跳过", exact=True)).to_be_visible()
    expect(page.locator(".print-object-row").filter(has_text="Bad").get_by_role("button")).to_be_disabled()

    skip.click()
    expect(dialog).to_contain_text(name)
    dialog.get_by_role("button", name="取消", exact=True).click()
    expect(skip).to_be_enabled()
    assert not printer.writes("/printer/gcode/script")

    # A new run of the same file is a different job.
    skip.click()
    printer.job = {**printer.job, "print_duration":1, "total_duration":3}
    dialog.get_by_role("button", name="确认").click()
    expect(page.locator("#notice")).to_contain_text("状态已变化")
    expect(skip).to_be_enabled()
    assert not printer.writes("/printer/gcode/script")

    skip.click()
    dialog.get_by_role("button", name="确认").click()
    expect(page.get_by_text("等待设备确认", exact=True)).to_be_visible()
    assert printer.writes("/printer/gcode/script") == [{"script":"EXCLUDE_OBJECT NAME=" + name}]
    assert printer.pending_skip == name
    printer.exclude_object["excluded_objects"].append(name)
    expect(page.get_by_text("已跳过", exact=True)).to_have_count(2)

    action = lambda label: page.get_by_role("button", name=label, exact=True)
    action("暂停").click()
    expect(details).to_contain_text("已暂停")
    expect(action("继续")).to_be_enabled()
    action("继续").click()
    dialog.get_by_role("button", name="确认").click()
    expect(details.locator(".print-state")).to_have_text("打印中")
    action("取消打印").click()
    dialog.get_by_role("button", name="确认").click()
    expect(details).to_contain_text("已取消")
    assert printer.writes("/multiace/api/print-control")[-3:] == [
        {"verb":"pause"}, {"verb":"resume"}, {"verb":"cancel"}]

    action("重新打印").click()
    expect(dialog).to_contain_text("plate.gcode")
    dialog.get_by_role("button", name="确认").click()
    expect(details.locator(".print-state")).to_have_text("打印中")
    assert printer.writes("/multiace/api/history/reprint") == [{"filename":"plate.gcode"}]

    printer.exclude_object = {}
    printer.thumbnail_mode = "missing"
    printer.job = {**printer.job, "filename":"other.gcode"}
    expect(details.get_by_text("暂无模型预览", exact=True)).to_be_visible()
    expect(details.get_by_alt_text("当前模型预览")).to_have_count(0)
    if not page.locator(".print-object-picker").evaluate("element => element.open"):
        page.get_by_text("跳过模型", exact=True).click()
    expect(page.get_by_text("当前任务未提供可跳过的对象。")).to_be_visible()


def main():
    printer = Printer()
    server = serve(printer)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(viewport={"width": 390, "height": 844})
                origin = f"http://127.0.0.1:{server.server_port}"
                exercise(page, origin, printer)
                check_integrated_panel(page, origin, printer)
                monitor = browser.new_page(viewport={"width": 390, "height": 844})
                check_print_monitor(monitor, origin, printer)
                actions = browser.new_page(viewport={"width":390, "height":844})
                check_print_actions(actions, origin, printer)
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print("PASS unified console: operator workflows, live progress, model preview, guarded skip, playback and framebuffer refresh")


if __name__ == "__main__":
    main()
