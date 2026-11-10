"""scripts/fleet_admin_account.sh — the fleet admin-account recipe.

Every refusal is exercised unprivileged: argument validation, the hostname
guard and the root check all run BEFORE any change, and --dry-run prints
the plan without root. The privileged path (adduser, sudoers) is proven
only on a real box (first applied 2026-11-09, verified from the manager:
`sudo -n true`, `visudo -c`, `passwd -S` = L).

It installs NO ssh key, by design (operator 2026-11-09): S11 of the NOC
read-grant pass — test_box_truth_guards::test_s11_only_the_grant_cli_
touches_authorized_keys — is the pin that keeps it that way.
"""
from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fleet_admin_account.sh"
HOST = socket.gethostname()


def _run(*args):
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True,
                          text=True, timeout=20)


def _args(host=HOST, user="fleetadmin"):
    return ["--host", host, "--user", user]


def test_dry_run_validates_and_prints_the_plan_unprivileged():
    r = _run(*_args(), "--dry-run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"DRY-RUN on {HOST}" in r.stdout
    assert "010-fleetadmin" in r.stdout and "NOPASSWD: ALL" in r.stdout
    # it says, out loud, that it leaves the key to the operator
    assert "NO key installed" in r.stdout


def test_the_wrong_box_is_refused_before_anything():
    r = _run(*_args(host="not-" + HOST), "--dry-run")
    assert r.returncode == 1 and f"this is '{HOST}'" in r.stdout
    assert "nothing changed" in r.stdout


@pytest.mark.skipif(os.geteuid() == 0, reason="running as root — the root refusal cannot fire")
def test_a_real_run_needs_root():
    r = _run(*_args())
    assert r.returncode == 1 and "run as root" in r.stdout


@pytest.mark.parametrize("argv, says", [
    ([], "both required"),
    (["--host", HOST], "both required"),
    (["--bogus"], "unknown argument"),
    # the retired key argument is refused, never silently ignored
    (["--host", HOST, "--user", "x", "--pubkey-file", "/dev/null"], "unknown argument"),
])
def test_missing_or_unknown_arguments_are_refused(argv, says):
    r = _run(*argv)
    assert r.returncode == 1 and says in r.stdout


@pytest.mark.parametrize("user, says", [
    ("Bad User", "not a plain Linux user name"),
    ("../etc", "not a plain Linux user name"),
    ("root", "cannot be root"),
])
def test_a_bad_user_name_is_refused(user, says):
    r = _run(*_args(user=user), "--dry-run")
    assert r.returncode == 1 and says in r.stdout
