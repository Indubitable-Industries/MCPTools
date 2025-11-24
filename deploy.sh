#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
LOG_DIR="$ROOT_DIR/logs"

echo "== MCP Terminal Server deploy =="
echo "Root: $ROOT_DIR"

if [ ! -d "$VENV_DIR" ]; then
  echo "Creating venv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
else
  echo "Using existing venv at $VENV_DIR"
fi

echo "Activating venv"
source "$VENV_DIR/bin/activate"

echo "Installing requirements"
pip install --upgrade pip
pip install -r "$ROOT_DIR/requirements.txt"

echo "Ensuring log directory at $LOG_DIR"
mkdir -p "$LOG_DIR"

echo "Ensuring permission config"
if [ ! -f "$ROOT_DIR/permission_config.json" ]; then
  cp "$ROOT_DIR/permission_config.json.example" "$ROOT_DIR/permission_config.json"
fi

cat <<'EOF'

Note: Paths are hardcoded in terminal_server.py for dev.
A future production deploy can stamp paths into Claude config.

EOF
