"""A minimal VoltKit REST client (urllib, no dependencies).

Only the two calls a token server needs:
  ensure_room()   PUT  /v1/rooms/{roomId}         create the room or return it (idempotent)
  create_token()  POST /v1/rooms/{roomId}/tokens  mint a short-lived token for one identity + role

Every request carries the tenant secret in X-API-Key; apps never reach this code directly.
"""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

MAX_ATTEMPTS = 3  # original try + 2 retries on transient failures


class VoltKitError(Exception):
    """A VoltKit error. `status` is the HTTP status (0 = network), `code` VoltKit's error code."""

    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: doing so would hand X-API-Key to whatever host it names."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class VoltKitClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 8.0) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect)

    def ensure_room(self, room_id: str, host_identity: str) -> dict[str, Any]:
        """Create the room, or get it back if it already exists. One round trip either way."""
        return self._request("PUT", f"/v1/rooms/{urllib.parse.quote(room_id, safe='')}", {"hostIdentity": host_identity})

    def create_token(self, room_id: str, identity: str, role: str) -> dict[str, Any]:
        """Mint a room token. The role is decided here, on the server, and signed into the token."""
        path = f"/v1/rooms/{urllib.parse.quote(room_id, safe='')}/tokens"
        return self._request("POST", path, {"identity": identity, "role": role})

    def _request(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """Send one JSON request, retrying on network errors, 429 and 5xx."""
        last: VoltKitError | None = None
        for attempt in range(MAX_ATTEMPTS):
            if attempt:
                time.sleep(0.2 * 2 ** (attempt - 1) + random.random() * 0.1)  # backoff with jitter
            try:
                return self._once(method, path, body)
            except VoltKitError as e:
                retryable = e.status == 0 or e.status == 429 or e.status >= 500
                if not retryable or e.code == "billing-unavailable":
                    raise
                last = e
        assert last is not None
        raise last

    def _once(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            self._base_url + path,
            data=json.dumps(body).encode(),
            method=method,
            headers={"Content-Type": "application/json", "X-API-Key": self._api_key},
        )
        try:
            with self._opener.open(req, timeout=self._timeout) as res:
                status, text = res.status, res.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:  # any non-2xx (redirects included, since we refuse them)
            status, text = e.code, e.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as e:
            raise VoltKitError("network", 0, f"{method} {path}: {e.reason if hasattr(e, 'reason') else e}") from None

        if 300 <= status < 400:
            raise VoltKitError("redirected", status, f"{method} {path}: redirected; point VOLTKIT_RTC_URL at the controller itself.")
        try:
            decoded = json.loads(text) if text else None
        except ValueError:
            decoded = None
        if not 200 <= status < 300:
            # VoltKit answers { error, message } (or nested { error: { code, message } }).
            err = decoded.get("error") if isinstance(decoded, dict) else None
            code = err.get("code") if isinstance(err, dict) else err
            message = err.get("message") if isinstance(err, dict) else (decoded or {}).get("message") if isinstance(decoded, dict) else None
            raise VoltKitError(
                code if isinstance(code, str) and code else f"http-{status}",
                status,
                f"{method} {path}: {message if isinstance(message, str) and message else text[:300]}",
            )
        if not isinstance(decoded, dict):
            raise VoltKitError(f"http-{status}", status, f"{method} {path}: the reply is not JSON.")
        return decoded
