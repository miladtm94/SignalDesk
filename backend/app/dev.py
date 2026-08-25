from __future__ import annotations

import json
import socket
from urllib.error import URLError
from urllib.request import urlopen

import uvicorn

from .settings import HOST, PORT

HEALTH_URL = f"http://{HOST}:{PORT}/api/health"


def _console_is_running() -> bool:
    try:
        with urlopen(HEALTH_URL, timeout=1.5) as response:  # noqa: S310 - fixed localhost URL
            payload = json.load(response)
    except (OSError, URLError, TimeoutError, json.JSONDecodeError):
        return False
    return payload.get("status") == "ok" and payload.get("upstream", "").startswith(
        "TradingAgents"
    )


def _port_is_available() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((HOST, PORT))
        except OSError:
            return False
    return True


def main() -> None:
    if _console_is_running():
        print(f"TradingAgents web backend is already running at http://{HOST}:{PORT}")
        return
    if not _port_is_available():
        raise SystemExit(
            f"Port {PORT} is occupied by an unresponsive or different process. "
            f"Inspect it with: lsof -nP -iTCP:{PORT} -sTCP:LISTEN"
        )
    uvicorn.run(
        "backend.app.main:app",
        host=HOST,
        port=PORT,
        reload=True,
        reload_dirs=["."],
    )


if __name__ == "__main__":
    main()
