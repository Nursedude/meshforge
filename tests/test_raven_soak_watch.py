"""Tests for the self-healing raven soak watch (scripts/raven_soak_watch.sh,
added 2026-06-27 after the raven_soak FAIL(1) hAP-OOM episode).

The defect being closed: the original watch only DETECTED `no-pid`. On the
56 MB no-swap AREDN hAP the kernel OOM-kills raven.uc under memory spikes;
after a few kills procd hits its respawn ceiling and GIVES UP, leaving Raven
dark for hours until a human restarts it by hand. A detector without a
recovery is half a loop.

Self-heal design (honest_failure_modes.md #1/#9 — never mask a degraded state):
  alive + RSS ok        -> OK (exit 0); clears the flap streak.
  alive + RSS > limit   -> FAIL rss-high (exit 1); distinct fault, no restart.
  no-pid (reachable)    -> ONE `/etc/init.d/raven restart`, then re-probe:
      recovered, < FLAP_THRESHOLD revivals in FLAP_WINDOW_S -> OK  (transient).
      recovered, >= FLAP_THRESHOLD revivals in window       -> FAIL (RED):
          datapath restored, but the box is OOM-flapping — keep the signal
          loud rather than silently papering over chronic memory pressure.
      restart did NOT bring it back                          -> FAIL.
  unreachable           -> FAIL (exit 1); cannot ssh-restart a dead host.

These run the real bash script hermetically: the SSH probe/restart are replaced
by fake executables (RAVEN_PROBE_CMD / RAVEN_RESTART_CMD) whose per-call output
and exit code the test controls. No network, no hAP.
"""

import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = (Path(__file__).resolve().parent.parent
          / "scripts" / "raven_soak_watch.sh")


@pytest.fixture
def sandbox(tmp_path):
    """Temp HOME plus fake `probe`/`restart` executables driven by env files:
    the probe emits the n-th line of a responses file on its n-th call; the
    restart records each invocation and returns RAVEN_TEST_RESTART_EXIT."""
    home = tmp_path / "home"
    home.mkdir()
    binp = tmp_path / "bin"
    binp.mkdir()

    probe = binp / "fake_probe"
    probe.write_text(
        '#!/usr/bin/env bash\n'
        'printf x >> "$RAVEN_TEST_PROBE_CALLS"\n'
        'n=$(cat "$RAVEN_TEST_PROBE_COUNTER" 2>/dev/null || echo 0)\n'
        'n=$((n + 1)); printf %s "$n" > "$RAVEN_TEST_PROBE_COUNTER"\n'
        'total=$(wc -l < "$RAVEN_TEST_PROBE_RESPONSES")\n'
        '[ "$n" -gt "$total" ] && n=$total\n'
        'sed -n "${n}p" "$RAVEN_TEST_PROBE_RESPONSES"\n'
    )
    probe.chmod(0o755)

    restart = binp / "fake_restart"
    restart.write_text(
        '#!/usr/bin/env bash\n'
        'printf x >> "$RAVEN_TEST_RESTART_CALLS"\n'
        'exit "${RAVEN_TEST_RESTART_EXIT:-0}"\n'
    )
    restart.chmod(0o755)

    return SimpleNamespace(
        home=home,
        probe=probe,
        restart=restart,
        responses=tmp_path / "responses",
        counter=tmp_path / "counter",
        probe_calls=tmp_path / "probe_calls",
        restart_calls=tmp_path / "restart_calls",
        log=home / "raven_soak.log",
        state=home / ".raven_soak_state",
    )


def run(sb, responses, *, restart_exit=0, threshold=3, window=86400,
        seed_state=None, rss_limit=15000):
    sb.responses.write_text("".join(r + "\n" for r in responses))
    if seed_state is not None:
        sb.state.write_text(seed_state)
    env = {
        **os.environ,
        "HOME": str(sb.home),
        "RAVEN_LOG": str(sb.log),
        "RAVEN_STATE": str(sb.state),
        "RAVEN_PROBE_CMD": str(sb.probe),
        "RAVEN_RESTART_CMD": str(sb.restart),
        "RAVEN_RESTART_WAIT_S": "0",
        "RAVEN_FLAP_THRESHOLD": str(threshold),
        "RAVEN_FLAP_WINDOW_S": str(window),
        "RAVEN_RSS_LIMIT_KB": str(rss_limit),
        "RAVEN_TEST_PROBE_RESPONSES": str(sb.responses),
        "RAVEN_TEST_PROBE_COUNTER": str(sb.counter),
        "RAVEN_TEST_PROBE_CALLS": str(sb.probe_calls),
        "RAVEN_TEST_RESTART_CALLS": str(sb.restart_calls),
        "RAVEN_TEST_RESTART_EXIT": str(restart_exit),
    }
    proc = subprocess.run(["bash", str(SCRIPT)], env=env,
                          capture_output=True, text=True, timeout=60)
    log = sb.log.read_text() if sb.log.exists() else ""
    restarts = (sb.restart_calls.read_text().count("x")
                if sb.restart_calls.exists() else 0)
    state = sb.state.read_text().strip() if sb.state.exists() else ""
    return proc.returncode, log, restarts, state


def test_healthy_exits_ok_no_restart(sandbox):
    rc, log, restarts, _ = run(sandbox, ["PID=100 RSS=3000kb AGE=500s"])
    assert rc == 0
    assert "raven_soak OK PID=100" in log
    assert restarts == 0


def test_rss_high_fails_loud_no_restart(sandbox):
    rc, log, restarts, _ = run(sandbox, ["PID=100 RSS=20000kb AGE=500s"])
    assert rc == 1
    assert "rss-high" in log
    assert restarts == 0          # a bloated-but-running raven is a distinct fault


def test_unreachable_fails_no_restart(sandbox):
    rc, log, restarts, _ = run(
        sandbox, ["ssh: connect to host: Connection timed out"])
    assert rc == 1
    assert "unreachable" in log
    assert restarts == 0          # cannot ssh-restart a dead host


def test_nopid_self_heals_once_below_threshold(sandbox):
    rc, log, restarts, state = run(
        sandbox, ["NOPID", "PID=200 RSS=3000kb AGE=10s"])
    assert restarts == 1
    assert rc == 0
    assert "self-healed" in log and "heal #1" in log
    assert state.split()[0] == "1"


def test_nopid_restart_fails_is_loud(sandbox):
    rc, log, restarts, _ = run(sandbox, ["NOPID", "NOPID"])
    assert restarts == 1
    assert rc == 1
    assert "self-heal-failed" in log


def test_flapping_reaches_threshold_is_loud_even_though_recovered(sandbox):
    now = int(time.time())
    rc, log, restarts, state = run(
        sandbox, ["NOPID", "PID=300 RSS=3000kb AGE=10s"],
        threshold=3, seed_state=f"2 {now}\n")
    assert restarts == 1
    assert rc == 1                # datapath up, but the box is flapping -> RED
    assert "flapping" in log
    assert state.split()[0] == "3"


def test_stale_streak_expires_so_single_heal_is_ok(sandbox):
    old = int(time.time()) - 200000      # older than the 86400 window
    rc, log, restarts, state = run(
        sandbox, ["NOPID", "PID=400 RSS=3000kb AGE=10s"],
        threshold=3, window=86400, seed_state=f"5 {old}\n")
    assert rc == 0                # stale streak expired -> a fresh heal #1
    assert "heal #1" in log
    assert state.split()[0] == "1"


def test_healthy_cycle_resets_flap_streak(sandbox):
    now = int(time.time())
    rc, log, restarts, state = run(
        sandbox, ["PID=500 RSS=3000kb AGE=900s"],
        threshold=3, seed_state=f"2 {now}\n")
    assert rc == 0
    assert restarts == 0
    assert state.split()[0] == "0"       # a clean cycle clears the counter


def test_config_missing_hap_fails_loud(sandbox):
    """No probe override AND no RAVEN_HAP -> fail loud before any network."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("RAVEN_HAP", "RAVEN_PROBE_CMD", "RAVEN_RESTART_CMD")}
    env.update({
        "HOME": str(sandbox.home),
        "RAVEN_LOG": str(sandbox.log),
        "RAVEN_STATE": str(sandbox.state),
    })
    proc = subprocess.run(["bash", str(SCRIPT)], env=env,
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 1
    assert "RAVEN_HAP" in sandbox.log.read_text()
