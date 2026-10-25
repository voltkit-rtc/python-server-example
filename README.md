# Token server (Python)

The one piece of backend a VoltKit app needs, in Python 3.11+ with only the standard library: it keeps your VoltKit
API secret and gives each user a short-lived room token. It exposes the same `POST /token` API as the other VoltKit token servers, so the
[Flutter](https://github.com/voltkit-rtc/flutter-example) and [Android](https://github.com/voltkit-rtc/android-example)
example apps work against it unchanged.

## Run

```bash
cp .env.example .env            # fill in LAB_KEY (any long random string) and VOLTKIT_RTC_SECRET
python -m app                   # http://localhost:7000
python -m app --check-config    # validate .env and exit
python -m unittest -v           # tests; no VoltKit account needed
```

Nothing to `pip install`. `python -m app` uses `ThreadingHTTPServer`, which is fine for a lab; for real traffic
run `app.server:serve` behind a WSGI/ASGI server of your choice or put a reverse proxy in front.
`docker build -t voltkit-token-py . && docker run --env-file .env -p 7000:7000 voltkit-token-py` also works.

## The API

`GET /health` → `{ "status": "ok" }`

`POST /token` with header `X-App-Key: <LAB_KEY>` and body `{ "room": "demo-room", "identity": "asha", "role": "host" }`

```json
{ "wsUrl": "wss://…", "token": "…", "room": "demo-room", "identity": "asha", "role": "host", "expiresAt": 1761040000 }
```

| Field | Rule |
| --- | --- |
| `room` | 3–64 chars of `a-z 0-9 -`, starting with a letter or digit |
| `identity` | 1–64 chars of `A-Z a-z 0-9 . _ -` |
| `role` | `host`, `cohost`, `moderator` or `viewer`; defaults to `host` |

Errors are `{ "error": { "code", "message" } }`:

| Status | Code | When |
| --- | --- | --- |
| 400 | `bad-json`, `bad-room`, `bad-identity`, `bad-role` | Input failed a rule above |
| 401 | `unauthorized` | Missing or wrong `X-App-Key` |
| 404 | `not-found` | Any other route or method |
| 413 | `too-large` | Body over 4 KB |
| 4xx/5xx | VoltKit's own code | VoltKit refused (for example `quota-exhausted` when no minutes are left) |
| 502 | `voltkit-unavailable` | VoltKit did not answer (network error or timeout after retries) |

## The code

| File | Role |
| --- | --- |
| [`app/server.py`](app/server.py) | `handle_request()`: check the app key (constant time), check the input, ask VoltKit. Pure function, so the tests call it directly. `make_handler()` adapts it to `http.server`. |
| [`app/voltkit.py`](app/voltkit.py) | `VoltKitClient`: `ensure_room()` is `PUT /v1/rooms/{id}`, `create_token()` is `POST /v1/rooms/{id}/tokens`, both with `X-API-Key`. Retries network errors, 429 and 5xx twice with backoff; never follows redirects (that would leak the key). |
| [`app/config.py`](app/config.py) | Reads `.env` and validates `LAB_KEY`, `VOLTKIT_RTC_URL`, `VOLTKIT_RTC_SECRET`, `PORT`, `HOST`. |
| [`app/__main__.py`](app/__main__.py) | Starts the server and stops cleanly on SIGTERM/SIGINT. |
| [`test/test_app.py`](test/test_app.py) | Handler tests with a fake VoltKit, the real client against a fake VoltKit on a local port, and one end-to-end HTTP test. |

In your own product, replace the `X-App-Key` check with your user login, and decide the `role` from who that user is.
