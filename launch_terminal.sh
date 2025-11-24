#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PY="$ROOT_DIR/.venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
  echo "Terminal MCP: missing venv at $ROOT_DIR/.venv. Run deploy.sh first." >&2
  exit 1
fi

exec "$VENV_PY" "$ROOT_DIR/terminal_server.py"
