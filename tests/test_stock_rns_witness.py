"""scripts/stock_rns_witness.sh — the stock-RNS witness (2026-11-21).

What these pin: the witness is its OWN RNS instance (never transport, never the
fleet's instance ports — MF009), stamp enforcement is an explicit yes|no, and it
REFUSES on a box that carries the fleet's RNS config. Nothing here installs,
starts, or execs systemd-run: every case exits before that (refusal or render).
"""
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "stock_rns_witness.sh"
FLEET_PORTS = ("37428", "37429")


def _run(*args, home, extra=None):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home),
           "STOCK_WITNESS_BASE": str(Path(home) / "witness"),
           "STOCK_WITNESS_ENTRY_HOST": "entry.example.invalid"}
    env.update(extra or {})
    return subprocess.run(["/bin/bash", str(SCRIPT), *args], capture_output=True,
                          text=True, timeout=20, env=env)


@pytest.mark.parametrize("enforce", ["yes", "no"])
def test_config_is_its_own_non_transport_instance(tmp_path, enforce):
    r = _run("render-config", enforce, home=tmp_path)
    assert r.returncode == 0, r.stderr
    cfg = r.stdout
    assert "enable_transport = No" in cfg
    assert f"enforce_stamps = {enforce}" in cfg
    for port in FLEET_PORTS:                       # MF009: never the fleet rnsd's ports
        assert f"= {port}" not in cfg, cfg
    assert cfg.count("type = ") == 1               # ONE leg: the entry point
    assert "type = TCPClientInterface" in cfg
    assert "AutoInterface" not in cfg


def test_enforce_value_outside_vocabulary_is_refused(tmp_path):
    r = _run("render-config", "maybe", home=tmp_path)
    assert r.returncode == 2
    assert "enforce must be yes|no" in r.stderr


def test_refuses_on_a_box_with_the_fleet_rns_config(tmp_path):
    """The witness must never share a box with the fleet's RNS (identity and
    ports). Planted ~/.reticulum/config -> refused before anything runs. (On a
    box with /etc/reticulum/config it refuses on that instead — same exit.)"""
    (tmp_path / ".reticulum").mkdir()
    (tmp_path / ".reticulum" / "config").write_text("[reticulum]\n")
    for cmd in ("install", "start"):
        r = _run(cmd, home=tmp_path)
        assert r.returncode == 2, (cmd, r.stdout, r.stderr)
        assert "refusing:" in r.stderr, (cmd, r.stderr)
    assert not (tmp_path / "witness").exists(), "refusal must happen before any write"


def test_status_on_an_uninstalled_box_says_so(tmp_path):
    r = _run("status", home=tmp_path)
    assert "NOT INSTALLED" in r.stdout, r.stdout


def test_entry_host_has_no_default(tmp_path):
    """No guessed entry point: unset host is refused, never defaulted."""
    r = _run("render-config", "no", home=tmp_path, extra={"STOCK_WITNESS_ENTRY_HOST": ""})
    assert r.returncode == 2
    assert "STOCK_WITNESS_ENTRY_HOST is not set" in r.stderr
