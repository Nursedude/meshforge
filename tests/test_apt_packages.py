"""utils.apt_packages — ONE in-app apt install (2026-10-30).

Was a handler-private method in _nomadnet_service_ops.py; the in-app editor
needs the same act (install `dialog`, since whiptail has no editbox), and a
fixed mechanism with copies drifts (honest_failure_modes #5). Pins the argv
(sudo only when not root, argument list, timeout) and the (ok, output)
contract on every failure leg.
"""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from utils import apt_packages  # noqa: E402


def _run_recorder(rc=0, out="done", err=""):
    seen = {}
    def run(argv, **k):
        seen.update(argv=argv, **k)
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)
    return seen, run


def test_non_root_prefixes_sudo(monkeypatch):
    seen, run = _run_recorder()
    monkeypatch.setattr(apt_packages.subprocess, "run", run)
    monkeypatch.setattr(apt_packages.os, "geteuid", lambda: 1000)
    ok, out = apt_packages.apt_install("dialog")
    assert ok is True and out == "done"
    assert seen["argv"] == ["sudo", "apt-get", "install", "-y", "dialog"]
    assert seen["timeout"] > 0 and "shell" not in seen


def test_root_runs_apt_directly(monkeypatch):
    seen, run = _run_recorder()
    monkeypatch.setattr(apt_packages.subprocess, "run", run)
    monkeypatch.setattr(apt_packages.os, "geteuid", lambda: 0)
    apt_packages.apt_install("dialog")
    assert seen["argv"] == ["apt-get", "install", "-y", "dialog"]


def test_nonzero_exit_is_failure_with_output(monkeypatch):
    _seen, run = _run_recorder(rc=100, out="", err="E: Unable to locate")
    monkeypatch.setattr(apt_packages.subprocess, "run", run)
    ok, out = apt_packages.apt_install("dialog")
    assert ok is False and "Unable to locate" in out


def test_timeout_is_failure(monkeypatch):
    def run(argv, **k):
        raise subprocess.TimeoutExpired(argv, 1)
    monkeypatch.setattr(apt_packages.subprocess, "run", run)
    ok, out = apt_packages.apt_install("dialog", timeout=1)
    assert ok is False and "timed out" in out


def test_missing_binary_is_failure(monkeypatch):
    def run(argv, **k):
        raise FileNotFoundError("apt-get")
    monkeypatch.setattr(apt_packages.subprocess, "run", run)
    assert apt_packages.apt_install("dialog")[0] is False


@pytest.mark.parametrize("bad", ["", "-y", "a b", "dialog;rm", "../x"])
def test_rejects_a_name_apt_would_not_take_as_one_package(bad):
    with pytest.raises(ValueError):
        apt_packages.apt_install(bad)


def test_nomadnet_delegates_instead_of_keeping_a_copy():
    src = (SRC / "launcher_tui" / "handlers" / "_nomadnet_service_ops.py").read_text()
    assert "apt_packages" in src
    assert "['apt-get', 'install'" not in src


def test_unable_to_locate_runs_apt_update_then_retries_once(monkeypatch):
    # A fresh image / cleared lists: "E: Unable to locate package" is a stale
    # index, not a missing package (review round 2 #3).
    calls = []
    def run(argv, **k):
        calls.append(argv)
        if argv[-1] == "update":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        n = sum(1 for c in calls if c[-1] == "dialog")
        rc, err = (100, "E: Unable to locate package dialog") if n == 1 else (0, "")
        return SimpleNamespace(returncode=rc, stdout="", stderr=err)
    monkeypatch.setattr(apt_packages.subprocess, "run", run)
    monkeypatch.setattr(apt_packages.os, "geteuid", lambda: 0)
    ok, _ = apt_packages.apt_install("dialog")
    assert ok is True
    assert calls == [["apt-get", "install", "-y", "dialog"],
                     ["apt-get", "update"],
                     ["apt-get", "install", "-y", "dialog"]]


def test_default_timeout_is_not_shorter_than_pip_install():
    # A killed sudo mid-dpkg leaves the lock / an interrupted dpkg; give apt
    # the same room the other in-app installers get.
    import inspect
    sig = inspect.signature(apt_packages.apt_install)
    assert sig.parameters["timeout"].default >= 600
