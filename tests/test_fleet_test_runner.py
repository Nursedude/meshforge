"""Tests for the /fleet/run-test backend (utils.fleet_test_runner).

Covers the T1.5 surface: shell out to `systemctl [--user] start <unit>`
with the daemon-context env fix, return a stable shape on every
outcome, no exceptions surfacing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.fleet_test_runner import fire_unit


def _capture_run(returncode: int = 0, stderr: str = ""):
    captured = {}

    def _fake(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return MagicMock(returncode=returncode, stderr=stderr, stdout="")

    return _fake, captured


def test_fire_user_unit_uses_systemctl_user():
    fake, cap = _capture_run()
    with patch("utils.fleet_test_runner.subprocess.run", fake):
        r = fire_unit(unit="meshforge-tracer.service", scope="user")
    assert r["ok"] is True
    assert "--user" in cap["cmd"]
    assert "start" in cap["cmd"]
    assert "meshforge-tracer.service" in cap["cmd"]


def test_fire_uses_no_block_to_return_immediately():
    """Without --no-block, systemctl start blocks until oneshot
    completion (~10-15s for tracer), which times out the HTTP
    request. The operator can refresh the Logs panel to see
    completion."""
    fake, cap = _capture_run()
    with patch("utils.fleet_test_runner.subprocess.run", fake):
        fire_unit(unit="meshforge-tracer.service", scope="user")
    assert "--no-block" in cap["cmd"]


def test_fire_system_unit_omits_user_flag():
    fake, cap = _capture_run()
    with patch("utils.fleet_test_runner.subprocess.run", fake):
        fire_unit(unit="rnsd.service", scope="system")
    assert "--user" not in cap["cmd"]
    assert "rnsd.service" in cap["cmd"]


def test_fire_user_injects_xdg_runtime_dir(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    fake, cap = _capture_run()
    with patch("utils.fleet_test_runner.subprocess.run", fake):
        fire_unit(unit="meshforge-tracer.service", scope="user")
    assert cap["env"] is not None
    assert "XDG_RUNTIME_DIR" in cap["env"]
    assert cap["env"]["XDG_RUNTIME_DIR"].startswith("/run/user/")


def test_fire_system_does_not_inject_xdg(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    fake, cap = _capture_run()
    with patch("utils.fleet_test_runner.subprocess.run", fake):
        fire_unit(unit="rnsd.service", scope="system")
    assert cap["env"] is None


def test_fire_returns_started_at_unix():
    fake, _ = _capture_run()
    with patch("utils.fleet_test_runner.subprocess.run", fake):
        r = fire_unit(unit="x.service", scope="user")
    assert isinstance(r["started_at_unix"], float)
    assert r["started_at_unix"] > 0


def test_fire_returns_error_on_nonzero_rc():
    fake, _ = _capture_run(returncode=5, stderr="Unit x.service not found.")
    with patch("utils.fleet_test_runner.subprocess.run", fake):
        r = fire_unit(unit="x.service", scope="user")
    assert r["ok"] is False
    assert "5" in r["error"]
    assert "not found" in r["stderr"]


def test_fire_returns_error_on_timeout():
    def _timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=10)

    with patch("utils.fleet_test_runner.subprocess.run", _timeout):
        r = fire_unit(unit="x.service", scope="user")
    assert r["ok"] is False
    assert "timed out" in r["error"]


def test_fire_returns_error_on_missing_binary():
    def _missing(cmd, **kwargs):
        raise FileNotFoundError("systemctl")

    with patch("utils.fleet_test_runner.subprocess.run", _missing):
        r = fire_unit(unit="x.service", scope="system")
    assert r["ok"] is False
    assert "exec error" in r["error"]


def test_fire_truncates_long_stderr():
    fake, _ = _capture_run(returncode=1, stderr="x" * 1000)
    with patch("utils.fleet_test_runner.subprocess.run", fake):
        r = fire_unit(unit="x.service", scope="user")
    assert len(r["stderr"]) <= 400
