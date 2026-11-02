#!/usr/bin/env bash
# Everything a change should pass before it is called done: the core suite, the
# console suite, lint, and the frontend type-check and production build.
#   PYTHON=/path/to/python3.11+ scripts/check_all.sh   (default: .venv/bin/python)
set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 1
PYTHON="${PYTHON:-.venv/bin/python}"
if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  echo "$PYTHON is missing or older than Python 3.11; set PYTHON to a 3.11+ interpreter with the repo installed." >&2
  exit 2
fi

failed=()
step() {
  local label=$1; shift
  echo "== $label"
  if "$@"; then echo "   ok"; else failed+=("$label"); fi
}
step "core tests"          "$PYTHON" -m pytest -q -p no:cacheprovider tests
step "console tests"       "$PYTHON" -m pytest -q -p no:cacheprovider backend/tests
step "lint"                "$PYTHON" -m ruff check .
step "frontend type-check" bash -c 'cd frontend && npx tsc -b --pretty false'
step "frontend build"      bash -c 'cd frontend && npm run build --silent > /dev/null'

if [ ${#failed[@]} -gt 0 ]; then
  echo "FAILED: ${failed[*]}" >&2
  exit 1
fi
echo "All checks passed."
