from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import uvicorn

from .settings import HOST, PORT

HEALTH_URL = f"http://{HOST}:{PORT}/api/health"
ROOT_DIR = Path(__file__).resolve().parents[2]
STALE_PROCESS_TIMEOUT_SECONDS = 5


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


def _command_output(command: list[str]) -> str:
    """Return a short local process-inspection command's stdout."""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _listener_pids() -> list[int]:
    """Return PIDs listening on the configured backend port when lsof exists."""
    if os.name != "posix":
        return []
    output = _command_output(
        ["lsof", f"-tiTCP:{PORT}", "-sTCP:LISTEN"]
    )
    return sorted(
        {int(line) for line in output.splitlines() if line.strip().isdigit()}
    )


def _process_details(pid: int) -> tuple[int, str, Path | None]:
    """Return parent PID, command, and cwd for a local process."""
    process = _command_output(
        ["ps", "-p", str(pid), "-o", "ppid=", "-o", "command="]
    )
    if not process:
        return 0, "", None
    parts = process.split(maxsplit=1)
    parent = int(parts[0]) if parts[0].isdigit() else 0
    command = parts[1] if len(parts) > 1 else ""
    cwd_output = _command_output(
        ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"]
    )
    cwd_line = next(
        (line[1:] for line in cwd_output.splitlines() if line.startswith("n")),
        "",
    )
    return parent, command, Path(cwd_line).resolve() if cwd_line else None


def _backend_root_for(pid: int) -> int | None:
    """Prove a listener descends from this checkout's backend dev process."""
    seen: set[int] = set()
    current = pid
    while current > 1 and current not in seen:
        seen.add(current)
        parent, command, cwd = _process_details(current)
        if cwd == ROOT_DIR and "-m backend.app.dev" in command:
            return current
        current = parent
    return None


def _signal_processes(pids: set[int], sig: signal.Signals) -> None:
    for pid in pids:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            continue


def _recover_stale_console() -> bool:
    """Reclaim the port only from a proven, unresponsive backend of this repo."""
    listeners = _listener_pids()
    if not listeners:
        return False
    roots = {_backend_root_for(pid) for pid in listeners}
    if None in roots:
        return False

    _signal_processes({pid for pid in roots if pid is not None}, signal.SIGTERM)
    deadline = time.monotonic() + STALE_PROCESS_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _port_is_available():
            return True
        time.sleep(0.1)

    # The listener PIDs were already proven to belong to the stale backend.
    # Escalate only for those exact processes; never target a port occupant
    # discovered after the initial validation.
    _signal_processes(set(listeners), signal.SIGKILL)
    deadline = time.monotonic() + STALE_PROCESS_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _port_is_available():
            return True
        time.sleep(0.1)
    return False


def main() -> None:
    if _console_is_running():
        print(f"TradingAgents web backend is already running at http://{HOST}:{PORT}")
        return
    if not _port_is_available() and _recover_stale_console():
        print(f"Recovered a stale TradingAgents backend on port {PORT}.")
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
