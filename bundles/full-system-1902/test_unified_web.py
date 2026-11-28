"""Offline packing and HTTP regression tests for the 1902.15 console."""
from contextlib import ExitStack
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from urllib.parse import urlsplit

import compose_1915 as build


class CompositionTests(unittest.TestCase):
    def test_real_bundle_and_unchanged_archives(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = {"firmware_version": build.BASE_VERSION, "integration_commit": build.BASE_COMMIT,
                      "runtime_files": ["etc/FULLVERSION"], "runtime_file_sha256": {}}
            for rel in ("etc/nginx/fluidd.d/auth-check.conf", "etc/nginx/fluidd.d/multiace-web.conf",
                        "etc/nginx/fluidd.d/firmware-config.conf",
                        "usr/local/lib/u1-device-service/device_service.py", "etc/FULLVERSION"):
                dest = root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(build.BASE_VERSION)
            init = root / build.COMPANION_INIT
            init.parent.mkdir(parents=True)
            init.write_text(build.COMPANION_GATE + "\n")
            (root / "usr/local/lib/u1-device-service/device_service.py").write_text(
                'elif self.command == "POST" and route == "/firmware/settings":\n'
                '    result = update_firmware_setting(data)\n')
            for key in ("screen", "provider"):
                archive = root / key
                archive.write_bytes(key.encode())
                report[key + "_archive"] = key
                report[key + "_sha256"] = build.digest(archive)
            previous = root / "previous.json"
            previous.write_text(json.dumps(report))
            output = root / "composition.json"
            build.compose(root, previous, output, "newcommit")
            saved = json.loads(output.read_text())
            self.assertEqual((root / "etc/FULLVERSION").read_text().strip(), build.VERSION)
            self.assertIn("[ -f /usr/local/share/u1-web/index.html ]", init.read_text())
            self.assertEqual(saved["screen_integration_commit"], build.BASE_COMMIT)
            for key in ("screen", "provider"):
                self.assertEqual(build.digest(root / key), report[key + "_sha256"])
            for rel in saved["unified_web_files"]:
                self.assertEqual(saved["runtime_file_sha256"][rel], build.digest(root / rel))
            for name in ("index.html", "print-details.js", "print-actions.js", "print-plate.js", "monitor-media.js"):
                self.assertIn(build.INSTALL_DIR + "/" + name, saved["unified_web_files"])

    def test_missing_script_rejects_partial_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            web = Path(temp)
            (web / "index.html").write_text('<script type="module" src="./missing.js"></script>')
            for name in ("control.js", "material.js"):
                (web / name).touch()
            with self.assertRaisesRegex(ValueError, "missing.js"):
                build.asset_files(web)


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/auth":
            code = 200 if self.headers.get("Authorization") == "Bearer test" else 401
        else:
            code = 200 if self.path == "/firmware/settings" else 404
        self.send_response(code)
        self.end_headers()
        self.wfile.write(self.path.encode())

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200 if self.path == "/firmware/settings" else 404)
        self.end_headers()
        self.wfile.write(body)


@unittest.skipUnless(shutil.which("nginx"), "nginx is installed in the candidate build job")
class NginxTests(unittest.TestCase):
    def test_served_console_and_existing_auth(self):
        with ExitStack() as stack:
            temp = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            temp.chmod(0o755)
            web = temp / "web"
            shutil.copytree(build.WEB, web)
            workspace = temp / "workspace"
            workspace.mkdir()
            (workspace / "index.html").write_text("existing printer workspace")
            (workspace / "app.js").write_text("workspace script")
            upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
            stack.callback(upstream.server_close)
            thread = threading.Thread(target=upstream.serve_forever, daemon=True)
            thread.start()
            stack.callback(upstream.shutdown)
            target = f"127.0.0.1:{upstream.server_port}"
            snippet = (web / "install/unified-web.conf").read_text().replace(
                "/usr/local/share/u1-web/", str(web) + "/").replace("127.0.0.1:7136", target)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            conf = temp / "nginx.conf"
            conf.write_text(f"""
daemon off;
pid {temp}/nginx.pid;
error_log {temp}/error.log;
events {{ worker_connections 32; }}
http {{
 access_log off;
 client_body_temp_path {temp}/body;
 proxy_temp_path {temp}/proxy;
 fastcgi_temp_path {temp}/fastcgi;
 uwsgi_temp_path {temp}/uwsgi;
 scgi_temp_path {temp}/scgi;
 server {{
   listen 127.0.0.1:{port};
   root {workspace};
   index index.html;
   location / {{ try_files $uri $uri/ /index.html; }}
   location = /index.html {{ add_header Cache-Control no-store; }}
   location = /auth_check {{
     internal;
     proxy_pass http://{target}/auth;
     proxy_pass_request_body off;
     proxy_set_header Content-Length "";
     proxy_set_header Authorization $http_authorization;
   }}
   {snippet}
 }}
}}
""")
            cmd = ["nginx", "-p", str(temp), "-c", str(conf)]
            subprocess.run([*cmd, "-t"], check=True)
            process = subprocess.Popen(cmd)
            def stop_nginx():
                process.terminate()
                process.wait(timeout=5)
            stack.callback(stop_nginx)
            def request(path, method="GET", headers=None, body=None, response_header=None):
                client = HTTPConnection("127.0.0.1", port, timeout=3)
                try:
                    client.request(method, path, body=body, headers=headers or {})
                    result = client.getresponse()
                    payload = result.read()
                    return result.status, result.getheader(response_header) if response_header else payload
                finally:
                    client.close()
            for _ in range(60):
                try:
                    status, body = request("/u1/")
                    break
                except OSError:
                    time.sleep(0.05)
            else:
                self.fail("nginx did not listen")
            self.assertEqual(status, 200)
            self.assertEqual(body, (web / "index.html").read_bytes())
            status, destination = request("/", response_header="Location")
            self.assertEqual(status, 302)
            self.assertEqual(urlsplit(destination).path, "/u1/")
            for path in ("/?workspace=1", "/?token=example", "/index.html", "/jobs/history"):
                with self.subTest(workspace_path=path):
                    self.assertEqual(request(path), (200, b"existing printer workspace"))
            self.assertEqual(request("/app.js"), (200, b"workspace script"))
            self.assertEqual(request("/u1")[0], 302)
            self.assertEqual(request("/u1/console.js"), (200, (web / "console.js").read_bytes()))
            endpoint = "/u1/device/firmware/settings"
            auth = {"Authorization": "Bearer test"}
            self.assertEqual(request(endpoint)[0], 401)
            self.assertEqual(request(endpoint, headers=auth), (200, b"/firmware/settings"))
            self.assertEqual(request(endpoint, "PUT", auth)[0], 405)
            self.assertEqual(request(endpoint, "POST", auth, b"{}")[0], 415)
            self.assertEqual(request(endpoint, "POST", {**auth, "Content-Type": "text/plain"}, b"{}")[0], 415)
            typed = {**auth, "Content-Type": "application/json; charset=utf-8"}
            self.assertEqual(request(endpoint, "POST", typed, b'{"id":"test"}'), (200, b'{"id":"test"}'))
            self.assertEqual(request("/u1/device/firmware/start", "GET", auth)[0], 404)
