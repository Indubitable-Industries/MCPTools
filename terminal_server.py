"""
MCP Terminal Server (Linux)

Persistent bash session with permission buckets, override friction,
dangerous pattern blocking, and file-only logging. Designed for Claude Desktop
on Linux (including claude-desktop-debian) to bypass the wrapped sandbox safely.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import signal
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from types import FrameType
from typing import Any, Callable, Dict, List, Optional, Tuple

import anyio
try:
    import pexpect
except ModuleNotFoundError:  # pragma: no cover - startup guard
    sys.stderr.write(
        "Terminal MCP failed to start: missing dependency 'pexpect'.\n"
        "Ensure you launch using the project virtualenv "
        "(.venv/bin/python terminal_server.py).\n"
    )
    raise

# MCP imports (assumes mcp[cli] installed)
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError


ROOT_DIR = Path(__file__).resolve().parent
LOG_DIR = ROOT_DIR / "logs"
PERMISSION_CONFIG_PATH = ROOT_DIR / "permission_config.json"
DEFAULT_SHELL = "/bin/bash"  # respects user rc files; no --norc/--noprofile
DONE_SENTINEL = "__MCP_DONE__"
EXPECTED_PYTHON = ROOT_DIR / ".venv" / "bin" / "python"

if EXPECTED_PYTHON.exists() and Path(sys.executable).resolve() != EXPECTED_PYTHON.resolve():
    sys.stderr.write(
        f"Terminal MCP warning: running with {sys.executable}, expected {EXPECTED_PYTHON}.\n"
        "If dependencies are missing, restart Claude Desktop or configure it to use the venv python.\n"
    )


DANGEROUS_PATTERNS: List[Tuple[str, str]] = [
    (r"&\s*$", "Backgrounding not supported"),
    (r"\|\s*(bash|sh|zsh|fish)", "Piping to shells is blocked"),
    (r">\s*/dev/(null|zero|random|urandom)", "Dangerous redirection to devices"),
    (r"rm\s+-rf\s+/", "Recursive delete from root is blocked"),
    (r":\(\)\s*\{.*:\|:", "Fork bomb pattern"),
    (r"dd\s+.*of=/dev/[sh]d", "Direct disk write attempt"),
    (r"curl.*\|\s*(bash|sh)", "Download+execute blocked"),
    (r"wget.*\|\s*(bash|sh)", "Download+execute blocked"),
]


class SmartTimeout:
    """Adaptive timeout that resets on output and aborts long/idle runs."""

    def __init__(self, initial_timeout: float = 30.0, max_timeout: float = 60.0):
        self.initial_timeout = initial_timeout
        self.max_timeout = max_timeout
        self.start_time = time.time()
        self.last_output_time = time.time()

    def saw_output(self) -> None:
        """Record that we saw output to reset the idle timer."""
        self.last_output_time = time.time()

    def check(self) -> Optional[str]:
        """Return a timeout reason if thresholds are exceeded."""
        now = time.time()
        if now - self.start_time > self.max_timeout:
            return "max_timeout"
        if now - self.last_output_time > self.initial_timeout:
            return "output_timeout"
        return None


class PermissionBuckets:
    """Load and classify commands into permission buckets."""

    def __init__(self, path: Path):
        self.path = path
        self.buckets = self._load()

    def _load(self) -> Dict[str, set]:
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {k: set(v) for k, v in data.items()}

    def reload(self) -> None:
        self.buckets = self._load()

    def classify(self, base_cmd: str) -> str:
        for category, commands in self.buckets.items():
            if base_cmd in commands:
                return category
        return "always_ask"  # default to ask for uncategorized

    def move_ask_to_allow(self, base_cmd: str) -> None:
        self.buckets.setdefault("always_ask", set()).discard(base_cmd)
        self.buckets.setdefault("always_allow", set()).add(base_cmd)
        self._persist()

    def _persist(self) -> None:
        serializable = {k: sorted(list(v)) for k, v in self.buckets.items()}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2)


class PermissionOverrideManager:
    """Session-scoped overrides with rate limiting and history tracking."""

    def __init__(self, rate_limit_seconds: int = 60, max_per_hour: int = 10):
        self.rate_limit_seconds = rate_limit_seconds
        self.max_per_hour = max_per_hour
        self.session_allows: set[str] = set()
        self.override_history: List[Dict[str, Any]] = []
        self.last_override_time: float = 0.0
        self.override_count: int = 0

    def check_rate_limit(self) -> Tuple[bool, str]:
        now = time.time()
        if now - self.last_override_time < self.rate_limit_seconds:
            wait = self.rate_limit_seconds - (now - self.last_override_time)
            return False, f"Rate limited. Wait {wait:.0f}s"

        hour_ago = now - 3600
        recent = [o for o in self.override_history if o["timestamp"] > hour_ago]
        if len(recent) >= self.max_per_hour:
            return False, f"Hourly limit reached ({self.max_per_hour} overrides/hour)"
        return True, "OK"

    def add_override(self, command: str, reason: str) -> None:
        self.override_history.append(
            {"command": command, "reason": reason, "timestamp": time.time()}
        )
        self.last_override_time = time.time()
        self.override_count += 1
        self.session_allows.add(_base_cmd(command))


class AuditLogger:
    """Append-only JSONL audit logs for commands, overrides, and errors."""

    def __init__(self, log_dir: Path):
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.command_log = log_dir / f"commands-{timestamp}.log"
        self.override_log = log_dir / f"overrides-{timestamp}.log"
        self.error_log = log_dir / f"errors-{timestamp}.log"
        log_dir.mkdir(parents=True, exist_ok=True)

    def log_command(self, entry: Dict[str, Any]) -> None:
        self._write(self.command_log, entry)

    def log_override(self, entry: Dict[str, Any]) -> None:
        self._write(self.override_log, entry)

    def log_error(self, entry: Dict[str, Any]) -> None:
        self._write(self.error_log, entry)

    def _write(self, path: Path, entry: Dict[str, Any]) -> None:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


class TerminalSession:
    """Persistent pexpect-backed shell session (respects user rc files)."""

    def __init__(self, shell: str = DEFAULT_SHELL):
        self.shell_path = shell
        self._spawn_shell()

    def _spawn_shell(self) -> None:
        self.proc = pexpect.spawn(self.shell_path, encoding="utf-8", timeout=None)
        self.proc.setwinsize(24, 80)

    def restart(self) -> None:
        try:
            self.proc.close(force=True)
        except Exception:
            pass
        self._spawn_shell()

    def execute(
        self,
        command: str,
        stream_callback: Optional[Callable[[str], None]] = None,
        timeout: Optional[SmartTimeout] = None,
    ) -> Dict[str, Any]:
        """
        Execute a command in the persistent shell.
        Returns dict with output, timeout info, exit flags.
        """
        timeout = timeout or SmartTimeout()
        wrapped = f"{command}\nprintf '{DONE_SENTINEL}\\n'\n"
        output_lines: List[str] = []
        self.proc.sendline(wrapped)

        while True:
            try:
                idx = self.proc.expect(
                    [rf"{DONE_SENTINEL}\r?\n", "\r\n", pexpect.EOF, pexpect.TIMEOUT],
                    timeout=0.1,
                )
                if idx == 0:
                    line = self.proc.before
                    if line:
                        output_lines.append(line)
                        if stream_callback:
                            stream_callback(line)
                        timeout.saw_output()
                    break
                elif idx == 1:
                    line = self.proc.before
                    if line:
                        output_lines.append(line)
                        if stream_callback:
                            stream_callback(line)
                        timeout.saw_output()
                elif idx == 2:  # EOF
                    return {
                        "success": False,
                        "error": "Session terminated unexpectedly",
                        "output": "\n".join(output_lines),
                    }
                elif idx == 3:  # TIMEOUT
                    status = timeout.check()
                    if status:
                        self.proc.sendcontrol("c")
                        return {
                            "success": False,
                            "timeout": True,
                            "timeout_reason": status,
                            "output": "\n".join(output_lines),
                        }
            except pexpect.TIMEOUT:
                status = timeout.check()
                if status:
                    self.proc.sendcontrol("c")
                    return {
                        "success": False,
                        "timeout": True,
                        "timeout_reason": status,
                        "output": "\n".join(output_lines),
                    }
        return {"success": True, "output": "\n".join(output_lines)}


def _base_cmd(command: str) -> str:
    try:
        parsed = shlex.split(command)
        if not parsed:
            return ""
        return Path(parsed[0]).name
    except Exception:
        return ""


def _match_dangerous(command: str) -> Optional[str]:
    for pattern, reason in DANGEROUS_PATTERNS:
        if re.search(pattern, command):
            return reason
    return None


def _educational_block(command: str) -> str:
    """Message explaining why interactive/TUI commands are blocked."""
    return (
        "BLOCKED interactive/TUI program: "
        f"{command}\n"
        "This server cannot run interactive programs; use non-interactive alternatives "
        "(cat/head/tail for viewing, single-shot ps for monitoring)."
    )


server = FastMCP(name="terminal-mcp")
permissions = PermissionBuckets(PERMISSION_CONFIG_PATH)
override_manager = PermissionOverrideManager()
audit_logger = AuditLogger(LOG_DIR)
session: Optional[TerminalSession] = None


def _get_session() -> TerminalSession:
    """Lazily create the persistent shell session to avoid import-time PTY use."""
    global session
    if session is None:
        session = TerminalSession(shell=DEFAULT_SHELL)
    return session


def _reload_permissions(signum: int, frame: Optional[FrameType]) -> None:
    """SIGHUP handler to reload permissions from disk."""
    permissions.reload()


signal.signal(signal.SIGHUP, _reload_permissions)


def _check_permission(command: str) -> Tuple[bool, str, str]:
    """Return (allowed?, category, message) for a command based on buckets/overrides."""
    base = _base_cmd(command)
    if not base:
        return False, "always_ask", "Empty command"

    if base in override_manager.session_allows:
        return True, "session_allowed", "Session override"

    category = permissions.classify(base)
    if category == "always_block":
        return False, category, _educational_block(base)
    if category == "always_allow":
        return True, category, "Allowed"
    if category == "always_ask":
        return False, category, "Permission required"
    # default
    return False, "always_ask", "Permission required"


async def _execute_internal(
    command: str,
    ctx: Optional[Context],
    is_override: bool = False,
) -> Dict[str, Any]:
    """Execute a command with streaming, audit logging, and safety checks."""
    dangerous = _match_dangerous(command)
    if dangerous:
        raise ToolError(f"Blocked: {dangerous}")

    loop = asyncio.get_running_loop()
    sess = _get_session()

    def stream_cb(line: str) -> None:
        if ctx:
            try:
                loop.call_soon_threadsafe(
                    asyncio.create_task, ctx.info(line.rstrip("\r\n"))
                )
            except Exception:
                pass

    try:
        result = await anyio.to_thread.run_sync(
            sess.execute, command, stream_cb, SmartTimeout()
        )
    except Exception as exc:
        audit_logger.log_error(
            {
                "timestamp": datetime.now().isoformat(),
                "command": command,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
        if ctx:
            try:
                await ctx.error(f"Terminal MCP error while running '{command}': {exc}")
            except Exception:
                pass
        raise ToolError(str(exc)) from exc
    audit_logger.log_command(
        {
            "timestamp": datetime.now().isoformat(),
            "command": command,
            "success": result.get("success"),
            "is_override": is_override,
            "timeout": result.get("timeout", False),
            "timeout_reason": result.get("timeout_reason"),
        }
    )
    return result


@server.tool()
async def execute_command(command: str, ctx: Context) -> Dict[str, Any]:
    """Execute a command that is already allowed by the permission buckets."""
    allowed, category, message = _check_permission(command)
    if not allowed:
        if category == "always_ask":
            raise ToolError(f"Permission required: {message}")
        raise ToolError(message)
    return await _execute_internal(command, ctx, is_override=False)


@server.tool()
async def execute_with_override(
    command: str, safety_override_reason: str, accept_risk: bool, ctx: Context
) -> Dict[str, Any]:
    """Run a command requiring override, enforcing friction and rate limits."""
    base = _base_cmd(command)
    if not base:
        raise ToolError("Command cannot be empty")
    category = permissions.classify(base)
    if category == "always_block":
        raise ToolError("Cannot override interactive/dangerous commands")
    if category != "always_ask":
        raise ToolError(f"Command '{base}' does not require override")
    if len(safety_override_reason) < 50:
        raise ToolError("Provide a detailed reason (50+ chars)")
    if not accept_risk:
        raise ToolError("Must set accept_risk=True to proceed")

    allowed, msg = override_manager.check_rate_limit()
    if not allowed:
        raise ToolError(msg)

    override_manager.add_override(command, safety_override_reason)
    audit_logger.log_override(
        {
            "timestamp": datetime.now().isoformat(),
            "command": command,
            "reason": safety_override_reason,
            "override_count": override_manager.override_count,
        }
    )

    result = await _execute_internal(command, ctx, is_override=True)
    result["warning"] = "Executed with safety override"
    result["override_reason"] = safety_override_reason
    return result


@server.tool()
async def check_permission_status(command: str) -> Dict[str, Any]:
    """Return the permission category for a command (and session overrides)."""
    base = _base_cmd(command)
    if base in override_manager.session_allows:
        return {
            "status": "session_allowed",
            "message": f"'{base}' temporarily allowed for this session",
            "original_category": permissions.classify(base),
        }
    category = permissions.classify(base)
    return {
        "status": category,
        "message": "defaults to ask" if category == "always_ask" else "categorized",
        "can_override": category == "always_ask",
    }


@server.tool()
async def user_approve_command(
    command: str, user_confirmation: str, duration: str = "session"
) -> Dict[str, Any]:
    """Elevate a command to session/permanent allow with explicit confirmation."""
    EXACT = "I understand the risks and approve this command"
    if user_confirmation != EXACT:
        raise ToolError(f"Confirmation must be exactly: '{EXACT}'")
    base = _base_cmd(command)
    if not base:
        raise ToolError("Command cannot be empty")
    category = permissions.classify(base)
    if category == "always_block":
        raise ToolError("Interactive/dangerous commands cannot be approved")
    if category == "always_allow":
        return {"success": True, "message": f"'{base}' already always_allow"}
    if duration == "session":
        override_manager.session_allows.add(base)
        return {"success": True, "message": f"'{base}' approved for this session"}
    if duration == "permanent":
        permissions.move_ask_to_allow(base)
        return {"success": True, "message": f"'{base}' moved to always_allow"}
    raise ToolError("duration must be 'session' or 'permanent'")


@server.tool()
async def get_working_directory() -> Dict[str, Any]:
    """Return the current working directory of the MCP server process."""
    return {"cwd": os.getcwd()}


@server.tool()
async def reset_session() -> Dict[str, Any]:
    """Restart the shell session and clear session-scoped approvals."""
    sess = _get_session()
    sess.restart()
    override_manager.session_allows.clear()
    return {"success": True, "message": "Session reset and overrides cleared"}


@server.tool()
async def view_override_history() -> Dict[str, Any]:
    """Return the in-memory override history for this process."""
    return {"overrides": override_manager.override_history}


def run() -> None:
    """Entrypoint used by FastMCP script execution."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    server.run()


if __name__ == "__main__":
    run()
