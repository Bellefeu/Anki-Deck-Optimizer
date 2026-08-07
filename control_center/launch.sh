#!/usr/bin/env bash
set -u

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
    PYTHON_BIN="$candidate"
    break
  fi
done

if [ -z "${PYTHON_BIN:-}" ]; then
  printf '%s\n' "Python 3.10+ was not found. Opening the guided setup first…"
  bash "$PROJECT_DIR/control_center/install/setup.sh" || exit $?
  for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
  if [ -z "${PYTHON_BIN:-}" ]; then
    printf '%s\n' "Setup finished, but this window cannot see Python yet." >&2
    printf '%s\n' "Close it, open PRISM again, and it should start." >&2
    exit 1
  fi
fi

cd "$PROJECT_DIR" || exit 1
exec "$PYTHON_BIN" "$PROJECT_DIR/control_center/app.py" --root "$PROJECT_DIR"
