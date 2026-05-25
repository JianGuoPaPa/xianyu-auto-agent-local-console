#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-$APP_DIR/.venv/bin/python}"

mkdir -p "$APP_DIR/logs"
exec "$PYTHON_BIN" "$APP_DIR/monitor_panel.py" "$@"

