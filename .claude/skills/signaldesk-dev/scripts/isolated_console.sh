#!/usr/bin/env bash
# A throwaway SignalDesk beside any console on the default ports (the Docker one
# usually holds 8765/4173): backend on API_PORT (8766), frontend on UI_PORT (5175),
# with its own data and reports under STATE_DIR, so the real history is untouched.
#   scripts/isolated_console.sh start|stop|status|logs
# PYTHON defaults to .venv/bin/python (needs 3.11+ with the repo and
# backend/requirements-dev.txt installed). Delete STATE_DIR for a fresh database.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 1
PYTHON="${PYTHON:-.venv/bin/python}"
API_PORT="${API_PORT:-8766}"
UI_PORT="${UI_PORT:-5175}"
STATE_DIR="${STATE_DIR:-${TMPDIR:-/tmp}/signaldesk-isolated}"

listening() { lsof -ti "tcp:$1" -sTCP:LISTEN 2>/dev/null; }
wait_for() {
  for _ in $(seq 1 60); do curl -sf "$1" > /dev/null && return 0; sleep 1; done
  return 1
}

case "${1:-status}" in
  start)
    for port in "$API_PORT" "$UI_PORT"; do
      if [ -n "$(listening "$port")" ]; then echo "port $port is already in use" >&2; exit 1; fi
    done
    mkdir -p "$STATE_DIR/data" "$STATE_DIR/reports"
    TRADING_CONSOLE_DATA_DIR="$STATE_DIR/data" TRADING_CONSOLE_REPORTS_DIR="$STATE_DIR/reports" \
      nohup "$PYTHON" -m uvicorn backend.app.main:app --host 127.0.0.1 --port "$API_PORT" \
      > "$STATE_DIR/backend.log" 2>&1 &
    (cd frontend && TRADING_CONSOLE_API_URL="http://127.0.0.1:$API_PORT" TRADING_CONSOLE_FRONTEND_PORT="$UI_PORT" \
      nohup npx vite --host 127.0.0.1 > "$STATE_DIR/frontend.log" 2>&1 &)
    wait_for "http://127.0.0.1:$API_PORT/api/health" || { echo "backend did not start; see $STATE_DIR/backend.log" >&2; exit 1; }
    wait_for "http://127.0.0.1:$UI_PORT" || { echo "frontend did not start; see $STATE_DIR/frontend.log" >&2; exit 1; }
    echo "SignalDesk (isolated) at http://127.0.0.1:$UI_PORT  ·  API http://127.0.0.1:$API_PORT  ·  state $STATE_DIR"
    ;;
  stop)
    for port in "$API_PORT" "$UI_PORT"; do
      pids=$(listening "$port")
      if [ -n "$pids" ]; then kill $pids && echo "stopped port $port"; fi
    done
    ;;
  status)
    curl -sf "http://127.0.0.1:$API_PORT/api/health" && echo || echo "backend not running on $API_PORT"
    curl -sf -o /dev/null "http://127.0.0.1:$UI_PORT" && echo "frontend up on $UI_PORT" || echo "frontend not running on $UI_PORT"
    ;;
  logs)
    tail -n 40 "$STATE_DIR/backend.log" "$STATE_DIR/frontend.log"
    ;;
  *)
    echo "usage: $0 start|stop|status|logs" >&2; exit 2
    ;;
esac
