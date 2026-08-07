#!/usr/bin/env bash
set -u

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$PROJECT_DIR" || exit 1
bash "$PROJECT_DIR/control_center/launch.sh"
status=$?

if [ "$status" -ne 0 ]; then
  printf '\nPRISM stopped with an error.\n'
  printf 'Press Return to close this window.\n'
  read -r _
fi
exit "$status"
