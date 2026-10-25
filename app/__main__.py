"""Start the server:  python -m app   (or  python -m app --check-config)."""
from __future__ import annotations

import logging
import signal
import sys
import threading
from pathlib import Path

from .config import load_dotenv, read_config
from .server import serve
from .voltkit import VoltKitClient


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    try:
        config = read_config()
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    if "--check-config" in sys.argv:
        print(f"Configuration valid, port {config.port}.")
        return 0

    rtc = VoltKitClient(config.voltkit.base_url, config.voltkit.api_key)
    httpd = serve(config.host, config.port, config.app_key, rtc)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: httpd.shutdown())  # graceful stop from another thread
    print(f"VoltKit token server on {config.host}:{config.port}")
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    t.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
