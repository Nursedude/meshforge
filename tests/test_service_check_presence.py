"""service_unit_presence: installed / absent / UNKNOWN (non-author review 2026-10-08)."""
import os
import subprocess
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import utils.service_check as sc  # noqa: E402


def _run(rc, err=""):
    return lambda argv, **k: SimpleNamespace(returncode=rc, stdout="", stderr=err, argv=argv)


def test_installed_absent_and_unknown(monkeypatch):
    monkeypatch.setattr(sc, "_operator_user_prefix", lambda: [])
    monkeypatch.setattr(sc.subprocess, "run", _run(0))
    assert sc.service_unit_presence("rnsd") == "installed"
    monkeypatch.setattr(sc.subprocess, "run", _run(1, "No files found for x.service.\n"))
    assert sc.service_unit_presence("x") == "absent"
    monkeypatch.setattr(sc.subprocess, "run", _run(1, "Failed to connect to user scope bus"))
    assert sc.service_unit_presence("nomadnet", user=True) == "unknown"


def test_errors_are_unknown_and_the_bool_keeps_its_contract(monkeypatch):
    monkeypatch.setattr(sc, "_operator_user_prefix", lambda: [])

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("systemctl", 5)
    monkeypatch.setattr(sc.subprocess, "run", boom)
    assert sc.service_unit_presence("rnsd") == "unknown"
    assert sc.is_service_unit_installed("rnsd") is False


def test_user_scope_goes_through_the_operator_prefix(monkeypatch):
    seen = []
    monkeypatch.setattr(sc, "_operator_user_prefix", lambda: ["sudo", "-u", "op", "-H", "env"])
    monkeypatch.setattr(sc.subprocess, "run",
                        lambda argv, **k: seen.append(argv) or SimpleNamespace(returncode=0, stderr=""))
    sc.service_unit_presence("nomadnet", user=True)
    sc.service_unit_presence("rnsd")
    assert seen[0][:5] == ["sudo", "-u", "op", "-H", "env"] and seen[0][-3:] == ["--user", "cat", "nomadnet"]
    assert seen[1] == ["systemctl", "cat", "rnsd"]
