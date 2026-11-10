# SPDX-License-Identifier: GPL-3.0-or-later
"""Real loopback HTTP regression tests; no printer or external network is used."""

import importlib.util
import json
import os
from pathlib import Path
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch


def load_service():
    spec = importlib.util.spec_from_file_location(
        "device_service_proxy_tests", Path(__file__).with_name("device_service.py")
    )
    service = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(service)
    return service


class RecordingHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def handle_request(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.server.requests.append({
            "method": self.command,
            "path": self.path,
            "headers": {key.lower(): value for key, value in self.headers.items()},
            "data": json.loads(raw) if raw else None,
        })
        body = json.dumps({"ok": True, "peer": "loopback"}).encode()
        self.send_response(self.server.response_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = handle_request
    do_POST = handle_request


class LoopbackProxyTests(unittest.TestCase):
    def setUp(self):
        self.target = self.start_server(200)
        self.proxy = self.start_server(502)
        self.base = "http://127.0.0.1:%d" % self.target.server_port

    def start_server(self, response_code):
        server = ThreadingHTTPServer(("127.0.0.1", 0), RecordingHandler)
        server.response_code = response_code
        server.requests = []
        worker = threading.Thread(
            target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
        )
        worker.start()

        def cleanup():
            server.shutdown()
            server.server_close()
            worker.join()

        self.addCleanup(cleanup)
        return server

    def proxy_environment(self, upper):
        # Empty both spellings of NO_PROXY so the tests cannot accidentally
        # succeed because the host environment already exempts loopback.
        env = {key: value for key, value in os.environ.items()
               if key.lower() not in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")
               and key != "REQUEST_METHOD"}
        env.update({
            "no_proxy": "", "NO_PROXY": "",
            "U1_PAXX_API_URL": self.base + "/firmware-config/api",
            "U1_PAXX_API_KEY": "local-api-key",
            "U1_PAXX_TOKEN": "local-access-token",
        })
        for name in ("http_proxy", "https_proxy", "all_proxy"):
            env[name.upper() if upper else name] = "http://127.0.0.1:%d" % self.proxy.server_port
        return env

    def check_requests(self, helper):
        for upper in (False, True):
            with self.subTest(uppercase=upper):
                self.target.requests.clear()
                self.proxy.requests.clear()
                with patch.dict(os.environ, self.proxy_environment(upper), clear=True):
                    with patch.object(urllib.request, "_opener", None):
                        # Initialize under the poisoned environment too: this
                        # proves the private opener does not cache its proxies.
                        service = load_service()
                        payload = {"name": "耗材", "enabled": True}
                        if helper == "paxx":
                            get = service.paxx_request("settings")
                            post = json.loads(service.paxx_request("settings/test/enabled", payload))
                        else:
                            headers = service.paxx_auth_headers()
                            get = service.json_request(self.base + "/printer/objects/query", headers=headers)
                            post = service.json_request(self.base + "/api/test", payload, "POST", headers)
                self.assertEqual(get, {"ok": True, "peer": "loopback"})
                self.assertEqual(post, get)
                self.assertEqual(self.proxy.requests, [], "local requests must bypass the proxy")
                self.assertEqual([r["method"] for r in self.target.requests], ["GET", "POST"])
                self.assertEqual(self.target.requests[1]["data"], payload)
                for request in self.target.requests:
                    self.assertEqual(request["headers"]["x-api-key"], "local-api-key")
                    self.assertEqual(request["headers"]["authorization"], "Bearer local-access-token")

    def test_paxx_get_and_post_ignore_environment_proxies(self):
        self.check_requests("paxx")

    def test_json_get_and_post_ignore_environment_proxies(self):
        self.check_requests("json")


if __name__ == "__main__":
    unittest.main()
