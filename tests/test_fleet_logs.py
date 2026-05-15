"""Tests for the /fleet/logs backend (utils.fleet_logs).

Covers the T1 logs surface: journalctl tail with allowlist, in-process
60s cache, ISO-timestamp parsing, scope-aware sudo + XDG_RUNTIME_DIR,
and graceful failure paths.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils import fleet_logs
from utils.fleet_logs import _parse_iso_to_unix, fetch_unit_logs


# ─── Timestamp parsing ─────────────────────────────────────────────────


def test_parse_iso_handles_short_iso_offset_no_colon():
    """journalctl short-iso emits '2026-05-15T07:31:02-1000' (no colon
    in zone). Our parser must inject the colon."""
    ts = _parse_iso_to_unix("2026-05-15T07:31:02-1000")
    assert ts is not None
    assert ts > 0


def test_parse_iso_handles_z_suffix():
    ts = _parse_iso_to_unix("2026-05-15T17:31:02Z")
    assert ts is not None
    assert ts > 0


def test_parse_iso_returns_none_on_garbage():
    assert _parse_iso_to_unix("") is None
    assert _parse_iso_to_unix("hello") is None
    assert _parse_iso_to_unix(None) is None  # type: ignore


# ─── fetch_unit_logs — happy path ──────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_cache():
    fleet_logs._CACHE.clear()
    yield
    fleet_logs._CACHE.clear()


def _mock_journalctl(stdout: str = "", returncode: int = 0, stderr: str = ""):
    """Return a MagicMock subprocess result + a fake run() that captures cmd."""
    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)

    return _fake_run, captured


def test_fetch_parses_journalctl_lines():
    stdout = (
        "2026-05-15T07:30:00-1000 host1 meshforge-tracer[1234]: tracer fire ok\n"
        "2026-05-15T07:31:00-1000 host1 meshforge-tracer[1234]: ack seq=4 timeout\n"
    )
    fake_run, _ = _mock_journalctl(stdout)
    with patch("utils.fleet_logs.subprocess.run", fake_run):
        result = fetch_unit_logs(
            unit="meshforge-tracer", scope="user", n=10, priority="warning",
        )
    assert result["unit"] == "meshforge-tracer"
    assert result["scope"] == "user"
    assert result["error"] is None
    assert len(result["lines"]) == 2
    assert result["lines"][0]["msg"] == "tracer fire ok"
    assert result["lines"][0]["level"] == "WARN"
    assert result["lines"][1]["msg"] == "ack seq=4 timeout"


def test_fetch_skips_boot_markers():
    """`-- Boot 12345 --` markers in journal output must be filtered out."""
    stdout = (
        "-- Boot 12345 -- \n"
        "2026-05-15T07:30:00-1000 host1 meshforge[1]: started\n"
    )
    fake_run, _ = _mock_journalctl(stdout)
    with patch("utils.fleet_logs.subprocess.run", fake_run):
        result = fetch_unit_logs(
            unit="meshforge", scope="system", n=5, priority="info",
        )
    msgs = [ln["msg"] for ln in result["lines"]]
    assert "started" in msgs
    assert not any("Boot 12345" in m for m in msgs)


def test_fetch_uses_user_scope_command_shape():
    """User scope: journalctl --user; no sudo."""
    fake_run, captured = _mock_journalctl()
    with patch("utils.fleet_logs.subprocess.run", fake_run):
        fetch_unit_logs(
            unit="meshforge-tracer", scope="user", n=10, priority="info",
        )
    assert captured["cmd"][0] == "journalctl"
    assert "--user" in captured["cmd"]
    assert "sudo" not in captured["cmd"][0]


def test_fetch_uses_system_scope_command_shape():
    """System scope: sudo -n /usr/bin/journalctl (sudoers drop-in required)."""
    fake_run, captured = _mock_journalctl()
    with patch("utils.fleet_logs.subprocess.run", fake_run):
        fetch_unit_logs(
            unit="meshforge", scope="system", n=10, priority="warning",
        )
    cmd = captured["cmd"]
    assert cmd[0] == "sudo"
    assert "-n" in cmd
    assert "/usr/bin/journalctl" in cmd
    assert "--user" not in cmd


def test_fetch_injects_xdg_runtime_dir_for_user_scope(monkeypatch):
    """Daemon context lacks XDG_RUNTIME_DIR — inject /run/user/<euid>."""
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    fake_run, captured = _mock_journalctl()
    with patch("utils.fleet_logs.subprocess.run", fake_run):
        fetch_unit_logs(
            unit="meshforge-tracer", scope="user", n=5, priority="info",
        )
    assert captured["env"] is not None
    assert "XDG_RUNTIME_DIR" in captured["env"]
    assert captured["env"]["XDG_RUNTIME_DIR"].startswith("/run/user/")


def test_fetch_respects_existing_xdg_runtime_dir(monkeypatch):
    """If env already has XDG_RUNTIME_DIR, don't override the subprocess env."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/9999")
    fake_run, captured = _mock_journalctl()
    with patch("utils.fleet_logs.subprocess.run", fake_run):
        fetch_unit_logs(
            unit="meshforge-tracer", scope="user", n=5, priority="info",
        )
    # env=None means subprocess uses the parent env (which already has it).
    assert captured["env"] is None


# ─── Caching ───────────────────────────────────────────────────────────


def test_fetch_caches_within_ttl():
    """Two calls within 60s with same args → second hits cache."""
    fake_run, _ = _mock_journalctl(
        "2026-05-15T07:30:00-1000 h1 unit[1]: first\n"
    )
    call_count = {"n": 0}

    def _counting_run(cmd, **kwargs):
        call_count["n"] += 1
        return fake_run(cmd, **kwargs)

    with patch("utils.fleet_logs.subprocess.run", _counting_run):
        r1 = fetch_unit_logs(
            unit="meshforge", scope="system", n=10, priority="warning",
        )
        r2 = fetch_unit_logs(
            unit="meshforge", scope="system", n=10, priority="warning",
        )

    assert call_count["n"] == 1, "second call must hit cache"
    assert r1["from_cache"] is False
    assert r2["from_cache"] is True
    assert r1["lines"] == r2["lines"]


def test_fetch_cache_keyed_per_unit_n_priority():
    """Different args must miss cache and re-fetch."""
    fake_run, _ = _mock_journalctl()
    call_count = {"n": 0}

    def _counting_run(cmd, **kwargs):
        call_count["n"] += 1
        return fake_run(cmd, **kwargs)

    with patch("utils.fleet_logs.subprocess.run", _counting_run):
        fetch_unit_logs(unit="meshforge", scope="system", n=10, priority="warning")
        fetch_unit_logs(unit="meshforge", scope="system", n=50, priority="warning")
        fetch_unit_logs(unit="meshforge", scope="system", n=10, priority="err")
        fetch_unit_logs(unit="rnsd",       scope="system", n=10, priority="warning")

    assert call_count["n"] == 4, "each distinct key must re-fetch"


# ─── Failure paths ─────────────────────────────────────────────────────


def test_fetch_returns_empty_lines_on_nonzero_returncode():
    fake_run, _ = _mock_journalctl(
        "", returncode=1, stderr="sudo: a password is required",
    )
    with patch("utils.fleet_logs.subprocess.run", fake_run):
        result = fetch_unit_logs(
            unit="meshforge", scope="system", n=10, priority="warning",
        )
    assert result["lines"] == []
    assert result["error"] is not None
    assert "password" in result["error"]


def test_fetch_returns_error_on_timeout():
    import subprocess as _sp

    def _timeout(cmd, **kwargs):
        raise _sp.TimeoutExpired(cmd=cmd, timeout=8)

    with patch("utils.fleet_logs.subprocess.run", _timeout):
        result = fetch_unit_logs(
            unit="meshforge", scope="system", n=10, priority="warning",
        )
    assert result["lines"] == []
    assert "timed out" in result["error"]


def test_fetch_handles_missing_journalctl_binary():
    def _missing(cmd, **kwargs):
        raise FileNotFoundError("journalctl")

    with patch("utils.fleet_logs.subprocess.run", _missing):
        result = fetch_unit_logs(
            unit="meshforge-tracer", scope="user", n=10, priority="warning",
        )
    assert result["lines"] == []
    assert "exec error" in result["error"]


def test_fetch_surfaces_unparseable_line_as_raw():
    """Non-standard lines (e.g. journalctl info banner) must surface
    as level=RAW rather than vanish — operator sees something."""
    stdout = (
        "Logs begin at Tue 2026-05-13 ... end at Fri 2026-05-15.\n"
        "2026-05-15T07:30:00-1000 h1 unit[1]: normal line\n"
    )
    fake_run, _ = _mock_journalctl(stdout)
    with patch("utils.fleet_logs.subprocess.run", fake_run):
        result = fetch_unit_logs(
            unit="meshforge", scope="system", n=5, priority="info",
        )
    levels = [ln["level"] for ln in result["lines"]]
    assert "RAW" in levels
    assert any(ln["msg"] == "normal line" for ln in result["lines"])
