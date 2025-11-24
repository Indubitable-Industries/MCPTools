# MCP Terminal Server (Linux)

Python-based MCP server that provides a persistent bash session with a hybrid permission/override model. It is intended for Claude Desktop on Linux (including claude-desktop-debian) to work around the wrapped/sandboxed shell by exposing a controlled terminal.

## Features
- Persistent bash via `pexpect` (respects user rc files; keeps `cd`, env, history).
- Permission buckets: `always_allow`, `always_ask`, `always_block` (configurable via `permission_config.json`, hot-reload with `SIGHUP`).
- Hybrid overrides: session-scoped overrides with friction (reason length, accept risk, rate limiting); user-only permanent approvals.
- Dangerous pattern detection and interactive/TUI/background blocking with educational responses.
- Smart timeouts (30s idle, 60s max) with partial output and Ctrl+C on timeout.
- Real-time output streaming to the MCP client.
- File-only logging, new log file per run.
- Hot reload permission config: `kill -HUP $(pgrep -f terminal_server.py)`.

## Layout
```
terminal-mcp/
  README.md
  LICENSE
  pyproject.toml
  requirements.txt
  terminal_server.py
  permission_config.json
  deploy.sh
  logs/            # runtime logs (gitignored)
  tests/
```

## Quickstart (dev)
```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python terminal_server.py
```

## Claude Desktop config (example)
Point Claude Desktop to run the server (paths will be stamped by deploy script later):
```json
{
  "mcpServers": {
    "terminal": {
      "command": "/home/YOUR_USER/PycharmProjects/Tools/MCPTools/terminal-mcp/.venv/bin/python",
      "args": ["/home/YOUR_USER/PycharmProjects/Tools/MCPTools/terminal-mcp/terminal_server.py"]
    }
  }
}
```

## Deploy script
`deploy.sh` will:
- create a venv (no system site packages),
- install deps,
- create `logs/`,
- leave placeholders for path stamping (production paths can be added manually).

## Tests
Basic pytest coverage for permission classification, override validation, and pattern detection. Integration tests with `pexpect` are guarded/skipped if not suitable in CI.

## Notes
- Python 3.10 target.
- No stdout logging; all logs go to timestamped files in `logs/`.
- Bash is spawned with rc files enabled (no `--norc/--noprofile`).
