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


@pytest.fixture(autouse=True)
def _default_to_non_root_euid(monkeypatch):
    """Pin geteuid to a non-zero UID so the bulk of these tests exercise
    the original (non-root) code path regardless of who invokes pytest.
    Tests that need the root-drop branch re-patch geteuid explicitly."""
    monkeypatch.setattr("utils.fleet_test_runner.os.geteuid", lambda: 1000)


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


# Root-firing-operator case: meshanchor-server runs meshanchor-map.service
# as User=root, so fire_unit must drop privilege to the operator's UID to
# reach a live `systemctl --user` bus. See
# `project_ma_runtest_user_mismatch` for the trace.


def test_fire_as_root_user_scope_drops_to_operator():
    fake, cap = _capture_run()
    with patch("utils.fleet_test_runner.os.geteuid", return_value=0), \
         patch(
             "utils.fleet_test_runner._find_operator_user",
             return_value=(1000, "wh6gxz"),
         ), \
         patch("utils.fleet_test_runner.subprocess.run", fake):
        r = fire_unit(unit="meshforge-tracer.service", scope="user")
    assert r["ok"] is True
    assert cap["cmd"][0] == "sudo"
    assert "-n" in cap["cmd"]
    assert cap["cmd"][cap["cmd"].index("-u") + 1] == "wh6gxz"
    # env XDG_RUNTIME_DIR=/run/user/<uid> wedged between sudo and systemctl
    env_idx = cap["cmd"].index("env")
    assert cap["cmd"][env_idx + 1] == "XDG_RUNTIME_DIR=/run/user/1000"
    assert "systemctl" in cap["cmd"]
    assert "--user" in cap["cmd"]
    assert "--no-block" in cap["cmd"]
    assert "meshforge-tracer.service" in cap["cmd"]


def test_fire_as_root_system_scope_stays_plain_systemctl():
    """Root + system scope is the normal path — no drop, no --user."""
    fake, cap = _capture_run()
    with patch("utils.fleet_test_runner.os.geteuid", return_value=0), \
         patch("utils.fleet_test_runner.subprocess.run", fake):
        r = fire_unit(unit="rnsd.service", scope="system")
    assert r["ok"] is True
    assert cap["cmd"][0] == "systemctl"
    assert "--user" not in cap["cmd"]
    assert "sudo" not in cap["cmd"]


def test_fire_as_root_user_scope_no_operator_returns_clean_error():
    """If `/run/user/` has no candidate UID, return a descriptive error
    rather than letting `systemctl --user` from root produce the
    cryptic 'No such file or directory' bus error."""

    def _should_not_be_called(*args, **kwargs):
        raise AssertionError("subprocess.run must not be invoked")

    with patch("utils.fleet_test_runner.os.geteuid", return_value=0), \
         patch(
             "utils.fleet_test_runner._find_operator_user", return_value=None,
         ), \
         patch(
             "utils.fleet_test_runner.subprocess.run", _should_not_be_called,
         ):
        r = fire_unit(unit="meshforge-tracer.service", scope="user")
    assert r["ok"] is False
    assert "operator" in r["error"]
    assert "root" in r["error"]


def _fake_pwd(uid_to_name: dict):
    """Build a pwd.getpwuid stub that resolves only the supplied UIDs."""

    class _Pw:
        def __init__(self, name):
            self.pw_name = name

    def _lookup(uid):
        if uid in uid_to_name:
            return _Pw(uid_to_name[uid])
        raise KeyError(uid)

    return _lookup


def test_find_operator_user_picks_smallest_non_root_uid(tmp_path, monkeypatch):
    """`_find_operator_user` must skip root (uid 0), require a `bus`
    socket sibling, and pick the smallest matching UID."""
    from utils import fleet_test_runner

    fake_run_user = tmp_path / "run_user"
    fake_run_user.mkdir()
    # uid 0 has a bus — must be ignored.
    (fake_run_user / "0").mkdir()
    (fake_run_user / "0" / "bus").touch()
    # uid 1001 has a bus.
    (fake_run_user / "1001").mkdir()
    (fake_run_user / "1001" / "bus").touch()
    # uid 1000 has a bus — should win (smallest).
    (fake_run_user / "1000").mkdir()
    (fake_run_user / "1000" / "bus").touch()
    # uid 1002 has no bus — must be skipped.
    (fake_run_user / "1002").mkdir()
    # garbage entry must be tolerated.
    (fake_run_user / "not-a-uid").mkdir()

    monkeypatch.setattr(
        fleet_test_runner, "Path",
        lambda p: fake_run_user if p == "/run/user" else Path(p),
    )
    monkeypatch.setattr(
        fleet_test_runner.pwd, "getpwuid", _fake_pwd({1000: "operator"}),
    )

    assert fleet_test_runner._find_operator_user() == (1000, "operator")


def test_find_operator_user_returns_none_when_no_candidates(tmp_path, monkeypatch):
    from utils import fleet_test_runner

    fake_run_user = tmp_path / "run_user"
    fake_run_user.mkdir()
    (fake_run_user / "0").mkdir()
    (fake_run_user / "0" / "bus").touch()

    monkeypatch.setattr(
        fleet_test_runner, "Path",
        lambda p: fake_run_user if p == "/run/user" else Path(p),
    )

    assert fleet_test_runner._find_operator_user() is None


def test_find_operator_user_tolerates_missing_run_user_dir(tmp_path, monkeypatch):
    from utils import fleet_test_runner

    missing = tmp_path / "does_not_exist"
    monkeypatch.setattr(
        fleet_test_runner, "Path",
        lambda p: missing if p == "/run/user" else Path(p),
    )

    assert fleet_test_runner._find_operator_user() is None


def test_find_operator_user_skips_uid_without_pwd_entry(tmp_path, monkeypatch):
    """If the smallest matching UID has no /etc/passwd entry, return
    None rather than guessing. (Conservative — a future caller could
    fall through to the next UID, but we'd rather surface the misconfig
    than silently fire as the wrong user.)"""
    from utils import fleet_test_runner

    fake_run_user = tmp_path / "run_user"
    fake_run_user.mkdir()
    (fake_run_user / "1500").mkdir()
    (fake_run_user / "1500" / "bus").touch()

    monkeypatch.setattr(
        fleet_test_runner, "Path",
        lambda p: fake_run_user if p == "/run/user" else Path(p),
    )
    monkeypatch.setattr(
        fleet_test_runner.pwd, "getpwuid", _fake_pwd({}),
    )

    assert fleet_test_runner._find_operator_user() is None
