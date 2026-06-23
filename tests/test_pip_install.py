"""Tests for the unified hardened pip installer (src/utils/pip_install.py).

Covers the root-cause fix for the fresh-user failure (no pip-presence guard)
and the silent-failure closes: ensure_pip ladder, return-code checking,
conflict-retry firing exactly once, PEP 668 override precedence, and
import-as-consumer verification flipping a "successful" install to not-ok.
"""

import subprocess
from unittest.mock import patch

import pytest

from utils.pip_install import (
    PipResult,
    check_import,
    ensure_pip,
    pep668_active,
    pip_install,
    resolve_target_python,
)


def CP(returncode, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


class Recorder:
    """Dispatches mocked subprocess.run by command substring; records all calls."""

    def __init__(self, rules):
        self.calls = []
        self.rules = rules  # list of (predicate(str) -> bool, CP-or-callable)

    def __call__(self, cmd, **kwargs):
        s = " ".join(map(str, cmd))
        self.calls.append(s)
        for pred, resp in self.rules:
            if pred(s):
                return resp() if callable(resp) else resp
        return CP(0)

    def ran(self, needle):
        return any(needle in c for c in self.calls)


# --------------------------------------------------------------------------
# ensure_pip — the root-cause ladder
# --------------------------------------------------------------------------

def test_ensure_pip_present_returns_early():
    rec = Recorder([(lambda s: "-m pip --version" in s, CP(0, "pip 24.0 from /x"))])
    with patch("utils.pip_install.subprocess.run", rec):
        r = ensure_pip("python3")
    assert r.ok and r.verified
    assert "pip 24.0" in r.detail
    assert not rec.ran("ensurepip"), "must not bootstrap when pip already works"


def test_ensure_pip_recovers_via_ensurepip():
    state = {"n": 0}

    def pip_probe():
        state["n"] += 1
        return CP(0, "pip 24.0") if state["n"] > 1 else CP(1, "", "No module named pip")

    rec = Recorder([
        (lambda s: "-m pip --version" in s, pip_probe),
        (lambda s: "ensurepip" in s, CP(0)),
    ])
    with patch("utils.pip_install.subprocess.run", rec):
        r = ensure_pip("python3")
    assert r.ok
    assert "ensurepip" in r.detail
    assert rec.ran("ensurepip")
    assert not rec.ran("apt-get"), "apt must not run once ensurepip recovered pip"


def test_ensure_pip_recovers_via_apt_as_root():
    state = {"n": 0}

    def pip_probe():
        state["n"] += 1
        # fails through the ensurepip recheck, succeeds only after apt
        return CP(0, "pip 24.0") if state["n"] >= 3 else CP(1, "", "No module named pip")

    rec = Recorder([
        (lambda s: "-m pip --version" in s, pip_probe),
        (lambda s: "ensurepip" in s, CP(1, "", "ensurepip is disabled in Debian")),
        (lambda s: "apt-get" in s, CP(0)),
    ])
    with patch("utils.pip_install.subprocess.run", rec), \
         patch("utils.pip_install.os.geteuid", return_value=0), \
         patch("utils.pip_install.shutil.which", return_value="/usr/bin/apt-get"):
        r = ensure_pip("python3")
    assert r.ok
    assert "apt" in r.detail.lower()
    assert rec.ran("apt-get install -y python3-pip")


def test_ensure_pip_fails_loud_with_actionable_detail():
    rec = Recorder([
        (lambda s: "-m pip --version" in s, CP(1, "", "No module named pip")),
        (lambda s: "ensurepip" in s, CP(1)),
        (lambda s: "apt-get" in s, CP(1)),
    ])
    with patch("utils.pip_install.subprocess.run", rec), \
         patch("utils.pip_install.os.geteuid", return_value=0), \
         patch("utils.pip_install.shutil.which", return_value="/usr/bin/apt-get"):
        r = ensure_pip("python3")
    assert not r.ok
    assert "python3-pip" in r.detail and "apt" in r.detail.lower()


def test_ensure_pip_allow_apt_false_skips_apt():
    """A *checker* path must never apt-install as a side effect."""
    rec = Recorder([
        (lambda s: "-m pip --version" in s, CP(1, "", "No module named pip")),
        (lambda s: "ensurepip" in s, CP(1)),
    ])
    with patch("utils.pip_install.subprocess.run", rec), \
         patch("utils.pip_install.os.geteuid", return_value=0), \
         patch("utils.pip_install.shutil.which", return_value="/usr/bin/apt-get"):
        r = ensure_pip("python3", allow_apt=False)
    assert not r.ok
    assert not rec.ran("apt-get"), "allow_apt=False must not run apt"


# --------------------------------------------------------------------------
# pip_install — the one checked invoker
# --------------------------------------------------------------------------

def test_pip_install_fails_loud_when_pip_absent_and_does_not_install():
    rec = Recorder([
        (lambda s: "-m pip --version" in s, CP(1, "", "No module named pip")),
        (lambda s: "ensurepip" in s, CP(1)),
    ])
    with patch("utils.pip_install.subprocess.run", rec), \
         patch("utils.pip_install.os.geteuid", return_value=1):  # not root → no apt
        r = pip_install(["meshtastic"], python="python3")
    assert not r.ok
    assert not rec.ran("-m pip install"), "must not attempt install without pip"


def test_pip_install_nonzero_returncode_is_not_swallowed():
    rec = Recorder([
        (lambda s: "-m pip --version" in s, CP(0, "pip 24.0")),
        (lambda s: "-m pip install" in s, CP(1, "", "ERROR: could not build wheels")),
    ])
    with patch("utils.pip_install.subprocess.run", rec):
        r = pip_install(["badpkg"], python="python3", break_system=False)
    assert not r.ok
    assert r.returncode == 1
    assert "could not build wheels" in r.detail


def test_pip_install_conflict_retries_once_with_ignore_installed():
    calls = {"install": 0}

    def install_resp():
        calls["install"] += 1
        if calls["install"] == 1:
            return CP(1, "", "error: externally-managed-environment; cannot uninstall")
        return CP(0, "Successfully installed")

    rec = Recorder([
        (lambda s: "-m pip --version" in s, CP(0, "pip 24.0")),
        (lambda s: "-m pip install" in s, install_resp),
    ])
    with patch("utils.pip_install.subprocess.run", rec):
        r = pip_install(["meshtastic"], python="python3", break_system=False)
    assert r.ok
    assert calls["install"] == 2, "conflict must trigger exactly one retry"
    install_cmds = [c for c in rec.calls if "-m pip install" in c]
    assert "--ignore-installed" in install_cmds[-1]
    assert "--ignore-installed" not in install_cmds[0]


def test_pip_install_break_system_override_precedence():
    rec = Recorder([
        (lambda s: "-m pip --version" in s, CP(0, "pip 24.0")),
        (lambda s: "-m pip install" in s, CP(0)),
    ])
    # explicit True forces the flag even when pep668 would say no
    with patch("utils.pip_install.subprocess.run", rec), \
         patch("utils.pip_install.pep668_active", return_value=False):
        pip_install(["x"], python="python3", break_system=True)
    assert any("--break-system-packages" in c for c in rec.calls if "-m pip install" in c)

    # explicit False omits it even when pep668 would say yes
    rec2 = Recorder([
        (lambda s: "-m pip --version" in s, CP(0, "pip 24.0")),
        (lambda s: "-m pip install" in s, CP(0)),
    ])
    with patch("utils.pip_install.subprocess.run", rec2), \
         patch("utils.pip_install.pep668_active", return_value=True):
        pip_install(["x"], python="python3", break_system=False)
    assert not any("--break-system-packages" in c for c in rec2.calls if "-m pip install" in c)


def test_pip_install_verify_import_failure_flips_ok_false():
    rec = Recorder([
        (lambda s: "-m pip --version" in s, CP(0, "pip 24.0")),
        (lambda s: "-m pip install" in s, CP(0, "Successfully installed meshtastic")),
        (lambda s: "import meshtastic" in s, CP(1, "", "ImportError: bad")),
    ])
    with patch("utils.pip_install.subprocess.run", rec):
        r = pip_install(["meshtastic"], python="python3", break_system=False,
                        verify_import="meshtastic")
    assert not r.ok and not r.verified
    assert "not importable" in r.detail


def test_pip_install_verify_import_success_sets_verified():
    rec = Recorder([
        (lambda s: "-m pip --version" in s, CP(0, "pip 24.0")),
        (lambda s: "-m pip install" in s, CP(0, "Successfully installed")),
        (lambda s: "import meshtastic" in s, CP(0, "2.7.9")),
    ])
    with patch("utils.pip_install.subprocess.run", rec):
        r = pip_install(["meshtastic"], python="python3", break_system=False,
                        verify_import="meshtastic")
    assert r.ok and r.verified


def test_pip_install_requires_packages_or_requirements():
    assert not pip_install([], python="python3").ok


# --------------------------------------------------------------------------
# pep668_active / resolve_target_python / check_import
# --------------------------------------------------------------------------

def test_pep668_active_true_for_system_marker():
    rec = Recorder([(lambda s: "EXTERNALLY-MANAGED" in s, CP(0, "1"))])
    with patch("utils.pip_install.subprocess.run", rec):
        assert pep668_active("python3") is True


def test_pep668_active_false_in_venv():
    rec = Recorder([(lambda s: "EXTERNALLY-MANAGED" in s, CP(0, "0"))])
    with patch("utils.pip_install.subprocess.run", rec):
        assert pep668_active("/opt/meshforge/venv/bin/python") is False


def test_resolve_target_python_explicit_wins():
    assert resolve_target_python("/custom/python") == "/custom/python"


def test_resolve_target_python_falls_back_to_python3():
    with patch("updates.version_checker.get_meshforge_venv_dir", return_value=None):
        assert resolve_target_python() == "python3"


def test_resolve_target_python_uses_venv():
    with patch("updates.version_checker.get_meshforge_venv_dir",
               return_value="/opt/meshforge/venv"):
        assert resolve_target_python().endswith("/venv/bin/python")


def test_check_import_ok_and_fail():
    ok = Recorder([(lambda s: "import rich" in s, CP(0, "13.0"))])
    with patch("utils.pip_install.subprocess.run", ok):
        assert check_import("rich").ok
    bad = Recorder([(lambda s: "import rich" in s, CP(1, "", "ImportError"))])
    with patch("utils.pip_install.subprocess.run", bad):
        assert not check_import("rich").ok


def test_pipresult_is_truthy():
    assert bool(PipResult(True)) is True
    assert bool(PipResult(False)) is False
