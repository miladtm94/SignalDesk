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

skip=()
if [ "$(uname)" = "Darwin" ]; then
  # These simulate Windows file locking with errno.EDEADLOCK, which macOS lacks.
  for name in test_on_windows_the_lock_is_waited_for_while_another_writer_holds_it \
              test_on_windows_a_lock_that_cannot_be_taken_is_reported_not_retried_forever \
              test_on_windows_a_held_lock_is_reported_when_not_waiting; do
    skip+=(--deselect "tests/test_cache_writes.py::$name")
  done
fi

failed=()
step() {
  local label=$1; shift
  echo "== $label"
  if "$@"; then echo "   ok"; else failed+=("$label"); fi
}
step "core tests"          "$PYTHON" -m pytest -q -p no:cacheprovider tests "${skip[@]}"
step "console tests"       "$PYTHON" -m pytest -q -p no:cacheprovider backend/tests
step "lint"                "$PYTHON" -m ruff check .
step "frontend type-check" bash -c 'cd frontend && npx tsc -b --pretty false'
step "frontend build"      bash -c 'cd frontend && npm run build --silent > /dev/null'

if [ ${#failed[@]} -gt 0 ]; then
  echo "FAILED: ${failed[*]}" >&2
  exit 1
fi
echo "All checks passed."
