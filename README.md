# MCP Terminal Server (Linux)

MCP server that exposes a persistent bash session with safety rails. Built for Claude Desktop on Linux, where the bundled shell is sandboxed; this bridges to a real shell while enforcing permissions and overrides.

## Why
- Claude Desktop on Linux can’t run arbitrary bash; this provides a controlled terminal via MCP.
- Persistent shell (`pexpect`) keeps `cd`, env, history, and rc files intact.
- Safety controls: permission buckets, override friction, pattern blocking, timeouts, and audit logs.

## Features
- Permission buckets: `always_allow`, `always_ask`, `always_block` (config in `permission_config.json`, hot-reload with `SIGHUP`).
- Overrides with friction: 50+ char reason, `accept_risk`, session/permanent approvals, rate limits.
- Dangerous pattern detection and interactive/TUI/background blocking with clear messages.
- Smart timeouts (30s idle, 60s max) with Ctrl+C and partial output; streaming output to the MCP client.
- File-only JSONL audit logs per run.
- Deploy stamps Claude Desktop config to use the project venv via launcher script.

## Tools (MCP methods)
- `execute_command(command)`: run an allowed command.
- `execute_with_override(command, safety_override_reason, accept_risk)`: run an `always_ask` command with friction and rate limits.
- `check_permission_status(command)`: inspect bucket and session override state.
- `user_approve_command(command, user_confirmation, duration=session|permanent)`: elevate a command.
- `get_working_directory()`: return server cwd.
- `reset_session()`: restart shell and clear session approvals.
- `view_override_history()`: return in-memory override history.

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
  launch_terminal.sh
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

## Deploy (recommended)
```
./deploy.sh
```
`deploy.sh` will:
- create `.venv` (no system site packages),
- install deps,
- ensure `logs/`,
- ensure `launch_terminal.sh` is executable,
- stamp Claude Desktop config at `~/.config/Claude/claude_desktop_config.json` to run `launch_terminal.sh` (which uses the venv and `terminal_server.py`).

After deploy, restart Claude Desktop so it picks up the stamped MCP entry.

## Claude Desktop usage
- Stamped config sets:
  - `command`: `/home/YOUR_USER/.../terminal-mcp/launch_terminal.sh`
  - `args`: `[]`
- Claude launches the server automatically when needed; no manual start required.

## Logs & debugging
- Claude side: `~/.config/Claude/logs/mcp-server-terminal.log`.
- Server side: `logs/commands-*.log`, `logs/overrides-*.log`, `logs/errors-*.log`.
- Hot-reload permissions: `kill -HUP $(pgrep -f terminal_server.py)`.
- Startup guard warns if not running under the venv; missing deps are printed to stderr (visible in Claude logs).

## Tests
```
.venv/bin/pytest
```
Coverage includes permission buckets, overrides, dangerous pattern detection, and terminal session output handling (skips PTY test if unavailable).

## Notes
- Target: Python 3.10.
- Bash is spawned with rc files (no `--norc/--noprofile`).
- No stdout logging; all logs are file-based.
