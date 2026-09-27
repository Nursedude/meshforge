"""scripts/fleet_down.sh — the manager must never go down on a PARTIAL run.

2026-09-27, caught before first use: `fleet_down.sh --yes kiai` would have
powered off the MANAGER too (--yes skipped the typed SELF prompt, and
nothing tied the self step to a whole-fleet run). These run the real script
against a fake ssh, a tmp box config + posture file, a no-op mirror, and a
self-poweroff command that only writes a marker.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fleet_down.sh"


@pytest.fixture
def sandbox(tmp_path):
    boxes = tmp_path / "boxes.json"
    boxes.write_text(json.dumps({"boxes": [{"name": "a", "host": "a"},
                                           {"name": "b", "host": "b"}]}))
    fake_ssh = tmp_path / "fake_ssh"
    # `true` probes answer "unreachable" (rc 255), so each box reads dark at once;
    # the power verb returns 0. Nothing leaves this machine.
    fake_ssh.write_text('#!/bin/sh\nfor a; do last="$a"; done\n'
                        '[ "$last" = "true" ] && exit 255\nexit 0\n')
    fake_ssh.chmod(fake_ssh.stat().st_mode | stat.S_IEXEC)
    marker = tmp_path / "SELF_POWERED_OFF"
    env = dict(os.environ,
               HOME=str(tmp_path),
               MESHFORGE_OFFLINE_BOXES=str(boxes),
               MESHFORGE_FLEET_POSTURE=str(tmp_path / "posture.json"),
               MESHFORGE_POWER_SSH=str(fake_ssh),
               MESHFORGE_POWER_POSTURE_SYNC=str(tmp_path / "no_such_sync.sh"),
               MESHFORGE_POWER_SELF="themanager",
               FLEET_DOWN_SELF_CMD=f"touch {marker}")
    return env, marker


def _run(env, *args):
    return subprocess.run(["bash", str(SCRIPT), "--yes", *args], env=env,
                          capture_output=True, text=True, timeout=180)


def test_partial_run_never_powers_off_the_manager(sandbox):
    env, marker = sandbox
    p = _run(env, "a")
    assert p.returncode == 0, p.stdout + p.stderr
    assert not marker.exists(), "a PARTIAL --yes run powered off the manager"
    assert "left up" in p.stdout


def test_whole_fleet_run_powers_off_the_manager_last(sandbox):
    env, marker = sandbox
    p = _run(env)
    assert p.returncode == 0, p.stdout + p.stderr
    assert marker.exists(), "a whole-fleet --yes run should end with the manager"


def test_no_self_is_honoured_on_a_whole_fleet_run(sandbox):
    env, marker = sandbox
    p = _run(env, "--no-self")
    assert p.returncode == 0, p.stdout + p.stderr
    assert not marker.exists()
