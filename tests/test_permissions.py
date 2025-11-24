import json
from pathlib import Path

from terminal_server import (
    DANGEROUS_PATTERNS,
    PermissionBuckets,
    PermissionOverrideManager,
    _base_cmd,
    _match_dangerous,
)


def test_base_cmd_parsing():
    assert _base_cmd("ls -la /tmp") == "ls"
    assert _base_cmd("/usr/bin/rm -rf /") == "rm"
    assert _base_cmd("") == ""


def test_permission_buckets_reload(tmp_path):
    cfg = tmp_path / "perm.json"
    data = {
        "always_allow": ["ls"],
        "always_ask": ["rm"],
        "always_block": ["vim"],
    }
    cfg.write_text(json.dumps(data), encoding="utf-8")
    buckets = PermissionBuckets(cfg)

    assert buckets.classify("ls") == "always_allow"
    assert buckets.classify("rm") == "always_ask"
    assert buckets.classify("vim") == "always_block"
    assert buckets.classify("foo") == "always_ask"  # default ask

    buckets.move_ask_to_allow("rm")
    assert buckets.classify("rm") == "always_allow"


def test_dangerous_pattern_detection():
    assert _match_dangerous("rm -rf /") is not None
    assert _match_dangerous("echo hello") is None


def test_override_rate_limits():
    mgr = PermissionOverrideManager(rate_limit_seconds=1, max_per_hour=2)
    allowed, _ = mgr.check_rate_limit()
    assert allowed
    mgr.add_override("rm", "x" * 60)

    allowed, msg = mgr.check_rate_limit()
    assert not allowed and "Rate limited" in msg

    # Simulate two overrides within an hour
    mgr.override_history = [
        {"timestamp": mgr.last_override_time},
        {"timestamp": mgr.last_override_time - 10},
    ]
    allowed, msg = mgr.check_rate_limit()
    assert not allowed and "Hourly limit" in msg
