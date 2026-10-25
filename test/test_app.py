"""Tests: python -m unittest -v   (no network to VoltKit; a fake is used)."""
from __future__ import annotations

import json
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.config import read_config
from app.server import handle_request, serve
from app.voltkit import VoltKitClient, VoltKitError

KEY = "example-app-key-0123456789"


class FakeRtc:
    """A VoltKit stand-in that records calls."""

    def __init__(self, raise_on_ensure: VoltKitError | None = None) -> None:
        self.calls: list[tuple] = []
        self.raise_on_ensure = raise_on_ensure

    def ensure_room(self, room_id, host_identity):
        self.calls.append(("ensure_room", room_id, host_identity))
        if self.raise_on_ensure:
            raise self.raise_on_ensure
        return {"roomId": room_id, "hostToken": "never-sent"}

    def create_token(self, room_id, identity, role):
        self.calls.append(("create_token", room_id, identity, role))
        return {"token": "room-token", "wsUrl": "wss://rtc.example/ws", "identity": identity, "role": role, "expiresAt": 123}


def post(rtc, body=None, key=KEY, raw=None):
    status, _, out = handle_request("POST", "/token", key, raw if raw is not None else json.dumps(body).encode(), KEY, rtc)
    return status, out


class HandlerTest(unittest.TestCase):
    def test_token_flow(self):
        rtc = FakeRtc()
        status, body = post(rtc, {"room": "demo-room", "identity": "asha", "role": "viewer"})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"wsUrl": "wss://rtc.example/ws", "token": "room-token", "room": "demo-room", "identity": "asha", "role": "viewer", "expiresAt": 123})
        self.assertEqual(rtc.calls, [("ensure_room", "demo-room", "asha"), ("create_token", "demo-room", "asha", "viewer")])
        self.assertEqual(post(rtc, {"room": "demo-room", "identity": "bo"})[1]["role"], "host")

    def test_app_key_required(self):
        rtc = FakeRtc()
        self.assertEqual(post(rtc, {"room": "demo-room", "identity": "a"}, key=None)[0], 401)
        self.assertEqual(post(rtc, {"room": "demo-room", "identity": "a"}, key="wrong-key")[0], 401)
        self.assertEqual(rtc.calls, [])

    def test_bad_input(self):
        rtc = FakeRtc()
        for body in ({"identity": "a"}, {"room": "X", "identity": "a"}, {"room": "demo-room", "identity": "has space"}, {"room": "demo-room", "identity": "a", "role": "owner"}):
            self.assertEqual(post(rtc, body)[0], 400, body)
        self.assertEqual(post(rtc, raw=b"{bad json")[0], 400)
        self.assertEqual(post(rtc, raw=json.dumps({"room": "x" * 5000}).encode())[0], 413)
        self.assertEqual(rtc.calls, [])

    def test_voltkit_errors_and_routes(self):
        rtc = FakeRtc(VoltKitError("quota-exhausted", 402, "No minutes left"))
        status, body = post(rtc, {"room": "demo-room", "identity": "a"})
        self.assertEqual((status, body["error"]["code"]), (402, "quota-exhausted"))
        self.assertEqual(post(FakeRtc(VoltKitError("network", 0, "timed out")), {"room": "demo-room", "identity": "a"})[0], 502)
        self.assertEqual(handle_request("GET", "/health", None, b"", KEY, rtc)[0], 200)
        self.assertEqual(handle_request("GET", "/voltkit/v1/lab/rooms", None, b"", KEY, rtc)[0], 404)

    def test_config(self):
        env = {"LAB_KEY": KEY, "VOLTKIT_RTC_URL": "https://rtc.example", "VOLTKIT_RTC_SECRET": "secret"}
        self.assertEqual(read_config(env).port, 7000)
        for k, v in (("LAB_KEY", "short"), ("VOLTKIT_RTC_URL", "http://rtc.example"), ("VOLTKIT_RTC_SECRET", "")):
            with self.assertRaisesRegex(ValueError, k):
                read_config({**env, k: v})


class _FakeVoltKit(BaseHTTPRequestHandler):
    """Answers the two endpoints the client uses, echoing what it saw."""

    def _reply(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_PUT(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/v1/rooms/quota-room":
            return self._reply(402, {"error": "quota-exhausted", "message": "No minutes left"})
        self._reply(201, {"roomId": self.path.rsplit("/", 1)[1], "hostIdentity": body["hostIdentity"], "seenKey": self.headers.get("X-API-Key")})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self._reply(200, {"token": "tok", "wsUrl": "wss://rtc.example/ws", "identity": body["identity"], "role": body["role"], "expiresAt": 123})

    def log_message(self, *a):
        pass


class ClientTest(unittest.TestCase):
    def setUp(self):
        self.httpd = HTTPServer(("127.0.0.1", 0), _FakeVoltKit)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.client = VoltKitClient(f"http://127.0.0.1:{self.httpd.server_port}", "secret-key", timeout=3)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()  # shutdown() stops the loop; this closes the listening socket

    def test_calls(self):
        room = self.client.ensure_room("demo-room", "asha")
        self.assertEqual((room["roomId"], room["seenKey"]), ("demo-room", "secret-key"))
        token = self.client.create_token("demo-room", "asha", "viewer")
        self.assertEqual((token["token"], token["role"]), ("tok", "viewer"))
        with self.assertRaises(VoltKitError) as ctx:
            self.client.ensure_room("quota-room", "asha")
        self.assertEqual((ctx.exception.status, ctx.exception.code), (402, "quota-exhausted"))


class HttpServerTest(unittest.TestCase):
    """The real http.server wiring: headers, status codes, body limit."""

    def setUp(self):
        self.rtc = FakeRtc()
        self.httpd = serve("127.0.0.1", 0, KEY, self.rtc)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def _post(self, data: bytes, key=KEY):
        req = urllib.request.Request(self.base + "/token", data=data, method="POST", headers={"X-App-Key": key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read()), r.headers
        except urllib.error.HTTPError as e:
            with e:  # an HTTPError holds the open response; close it
                return e.code, json.loads(e.read()), e.headers

    def test_end_to_end(self):
        status, body, headers = self._post(json.dumps({"room": "demo-room", "identity": "asha"}).encode())
        self.assertEqual((status, body["token"], headers["Cache-Control"]), (200, "room-token", "no-store"))
        self.assertEqual(self._post(b"x" * 5000)[0], 413)
        with urllib.request.urlopen(self.base + "/health") as r:
            self.assertEqual(json.loads(r.read()), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
