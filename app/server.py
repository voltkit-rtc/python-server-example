"""The whole token server. Your app asks for a room token, this server checks the app is yours
and asks VoltKit for one. The VoltKit API secret never leaves the server; the app only ever
holds a short-lived room token.

handle_request() is pure (request in, (status, headers, body) out) so it is testable without
sockets; TokenHandler adapts it to http.server.
"""
from __future__ import annotations

import hmac
import json
import logging
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol
from urllib.parse import urlsplit

from .voltkit import VoltKitError

ROOM_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
IDENTITY_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
ROLES = ("host", "cohost", "moderator", "viewer")
MAX_BODY_BYTES = 4096

log = logging.getLogger("token-server")
Response = tuple[int, dict[str, str], dict[str, Any]]


class Rtc(Protocol):
    """Anything with the two VoltKit calls: the real client or a test fake."""

    def ensure_room(self, room_id: str, host_identity: str) -> dict[str, Any]: ...
    def create_token(self, room_id: str, identity: str, role: str) -> dict[str, Any]: ...


def handle_request(method: str, path: str, app_key_in: str | None, raw_body: bytes, app_key: str, rtc: Rtc) -> Response:
    if method == "GET" and path == "/health":
        return 200, {}, {"status": "ok"}
    if not (method == "POST" and path == "/token"):
        return fail(404, "not-found", "Use POST /token.")

    # 1. Check the caller is your app. In a real product this is your own user login.
    if not same_key(app_key_in, app_key):
        return fail(401, "unauthorized", "Wrong app key.")

    # 2. Check the input.
    if len(raw_body) > MAX_BODY_BYTES:
        return fail(413, "too-large", "Body too large.")
    try:
        body = json.loads(raw_body)
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return fail(400, "bad-json", "Send a JSON body.")
    room, identity, role = body.get("room", ""), body.get("identity", ""), body.get("role", "host")
    if not isinstance(room, str) or not ROOM_RE.match(room):
        return fail(400, "bad-room", "room: 3–64 of a-z 0-9 -")
    if not isinstance(identity, str) or not IDENTITY_RE.match(identity):
        return fail(400, "bad-identity", "identity: 1–64 of A-Z a-z 0-9 . _ -")
    if role not in ROLES:
        return fail(400, "bad-role", "role: " + ", ".join(ROLES))

    # 3. Make sure the room exists, then mint a token for this person and role.
    try:
        rtc.ensure_room(room, identity)
        t = rtc.create_token(room, identity, role)
        return 200, {"Cache-Control": "no-store"}, {
            "wsUrl": t.get("wsUrl"),
            "token": t.get("token"),
            "room": room,
            "identity": t.get("identity", identity),
            "role": t.get("role", role),
            "expiresAt": t.get("expiresAt"),
        }
    except VoltKitError as e:
        # VoltKit's own errors (e.g. no minutes left) keep their status and code.
        if e.status >= 400:
            return fail(e.status, e.code, str(e))
        return fail(502, "voltkit-unavailable", "VoltKit did not answer.")
    except Exception as e:  # noqa: BLE001 - last line of defence; never leak details
        log.error("Request failed: %s", type(e).__name__)
        return fail(500, "internal", "Unexpected server error.")


def fail(status: int, code: str, message: str) -> Response:
    """The error shape every failure uses: { "error": { "code", "message" } }."""
    return status, {}, {"error": {"code": code, "message": message}}


def same_key(given: str | None, expected: str) -> bool:
    """Constant-time comparison, so the key cannot be guessed from response times."""
    return isinstance(given, str) and hmac.compare_digest(given.encode(), expected.encode())


def make_handler(app_key: str, rtc: Rtc) -> type[BaseHTTPRequestHandler]:
    """Bind the handler to its dependencies (http.server instantiates the class per request)."""

    class TokenHandler(BaseHTTPRequestHandler):
        server_version = "voltkit-token-server"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def _dispatch(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            # Read at most one byte over the limit so an oversized body is a clean 413.
            raw = self.rfile.read(min(length, MAX_BODY_BYTES + 1)) if length else b""
            if length > MAX_BODY_BYTES + 1:
                self.close_connection = True  # the rest of the body is left unread
            path = urlsplit(self.path).path
            status, headers, body = handle_request(self.command, path, self.headers.get("X-App-Key"), raw, app_key, rtc)
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _dispatch

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet access log, tokens never logged
            log.info("%s %s", self.command, urlsplit(self.path).path)

    return TokenHandler


def serve(host: str, port: int, app_key: str, rtc: Rtc) -> ThreadingHTTPServer:
    """Create (but do not start) the HTTP server; the caller runs serve_forever()."""
    return ThreadingHTTPServer((host, port), make_handler(app_key, rtc))
