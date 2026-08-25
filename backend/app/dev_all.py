from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from .settings import HOST, PORT

ROOT_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT_DIR / "frontend"
BACKEND_HEALTH_URL = f"http://{HOST}:{PORT}/api/health"
FRONTEND_HOST = "127.0.0.1"
FRONTEND_PORT = 5174
FRONTEND_URL = f"http://{FRONTEND_HOST}:{FRONTEND_PORT}"
STARTUP_TIMEOUT_SECONDS = 30


def _http_ready(url: str, *, backend: bool = False) -> bool:
    try:
        with urlopen(url, timeout=1.5) as response:  # noqa: S310 - fixed localhost URLs
            if response.status != 200:
                return False
            if backend:
                payload = json.load(response)
                return payload.get("status") == "ok" and payload.get("upstream", "").startswith(
                    "TradingAgents"
                )
            return True
    except (OSError, URLError, TimeoutError, json.JSONDecodeError):
        return False


def _start(command: list[str], cwd: Path) -> subprocess.Popen:
    environment = {**os.environ, "PYTHONUNBUFFERED": "1"}
    return subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        start_new_session=True,
    )


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def _wait_until_ready(
    name: str,
    url: str,
    process: subprocess.Popen,
    *,
    backend: bool = False,
) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            raise RuntimeError(f"{name} exited during startup with code {exit_code}.")
        if _http_ready(url, backend=backend):
            return
        time.sleep(0.25)
    raise RuntimeError(f"{name} did not become ready within {STARTUP_TIMEOUT_SECONDS} seconds.")


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        os.killpg(process.pid, signal.SIGTERM)
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=5)


def main() -> None:
    started: list[tuple[str, subprocess.Popen]] = []
    try:
        if _http_ready(BACKEND_HEALTH_URL, backend=True):
            print(f"Backend already running at http://{HOST}:{PORT}", flush=True)
        else:
            backend_process = _start(
                [sys.executable, "-m", "backend.app.dev"],
                ROOT_DIR,
            )
            started.append(("Backend", backend_process))
            _wait_until_ready(
                "Backend", BACKEND_HEALTH_URL, backend_process, backend=True
            )

        if _http_ready(FRONTEND_URL):
            print(f"Frontend already running at {FRONTEND_URL}", flush=True)
        else:
            if not _port_available(FRONTEND_HOST, FRONTEND_PORT):
                raise RuntimeError(
                    f"Frontend port {FRONTEND_PORT} is occupied but not responding. "
                    f"Inspect it with: lsof -nP -iTCP:{FRONTEND_PORT} -sTCP:LISTEN"
                )
            frontend_process = _start(["npm", "run", "dev"], FRONTEND_DIR)
            started.append(("Frontend", frontend_process))
            _wait_until_ready("Frontend", FRONTEND_URL, frontend_process)

        print(f"\nSignal Desk is ready: {FRONTEND_URL}", flush=True)
        print("Press Ctrl+C once to stop both services.\n", flush=True)
        if not started:
            return

        while True:
            for name, process in started:
                exit_code = process.poll()
                if exit_code is not None:
                    raise RuntimeError(f"{name} stopped unexpectedly with code {exit_code}.")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping Signal Desk...")
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        for _, process in reversed(started):
            _stop(process)


if __name__ == "__main__":
    main()
