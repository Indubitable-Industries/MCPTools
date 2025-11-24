#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
LOG_DIR="$ROOT_DIR/logs"
LAUNCHER="$ROOT_DIR/launch_terminal.sh"

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

echo "Ensuring launcher is executable"
chmod +x "$LAUNCHER"

CLAUDE_CONFIG="$HOME/.config/Claude/claude_desktop_config.json"
echo "Stamping Claude Desktop config at $CLAUDE_CONFIG"
python3 - <<PY
import json
from pathlib import Path

root = Path("$ROOT_DIR").resolve()
launcher = Path("$LAUNCHER").resolve()
config_path = Path("$CLAUDE_CONFIG").expanduser()
config_path.parent.mkdir(parents=True, exist_ok=True)

data = {}
if config_path.exists():
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
if not isinstance(data, dict):
    data = {}

servers = data.setdefault("mcpServers", {})
servers["terminal"] = {
    "command": str(launcher),
    "args": [],
}

config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
print(f"Updated Claude config at {config_path}")
PY

cat <<'EOF'

Claude Desktop config stamped. Restart Claude Desktop so it picks up the new terminal MCP entry.

EOF
