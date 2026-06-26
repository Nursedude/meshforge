"""Tests for the mini-dudeai cadence launcher timeout-backoff
(scripts/mini_cadence_launch.sh, added 2026-06-26).

The defect being pinned: a cadence `claude -p` session that hits TIMEOUT_S
exits 124 and CANNOT ratify its deltas, so they stay "proposed" and the next
hourly run re-fires the SAME doomed session — on 2026-06-24 this burned 4
consecutive 900s sessions on an already-slow box. The backoff records the
timeout (ts + a hash of the proposed-delta set) and skips re-firing within
TIMEOUT_BACKOFF_S *while the proposed set is unchanged*, exiting 75 so the
stuck state stays VISIBLE to cron_verdict_stale (drop the COST, keep the SIGNAL).

These run the real bash script in a hermetic sandbox: a fake `claude` on PATH
whose exit code (and whether it was invoked at all) the test controls, with
--dream skipped so no python module / live truth is touched.
"""

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = (Path(__file__).resolve().parent.parent
          / "scripts" / "mini_cadence_launch.sh")

PROPOSED_A = '{"status": "proposed", "key": "test::a", "n": 1}\n'
PROPOSED_B = '{"status": "proposed", "key": "test::b", "n": 2}\n'


@pytest.fixture
def sandbox(tmp_path):
    """A temp HOME with a fake `claude` (records calls, exit code controllable)
    and a temp runbook, so the launcher runs without touching the real CLI."""
    home = tmp_path / "home"
    binp = home / ".local" / "bin"
    binp.mkdir(parents=True)
    claude = binp / "claude"
    # Each invocation appends a line to the marker; exit code from FAKE_CLAUDE_EXIT.
    claude.write_text(
        '#!/usr/bin/env bash\n'
        'echo called >> "$CLAUDE_CALL_MARKER"\n'
        'exit "${FAKE_CLAUDE_EXIT:-0}"\n'
    )
    claude.chmod(0o755)
    runbook = home / ".claude" / "prompts" / "mini_cadence.md"
    runbook.parent.mkdir(parents=True)
    runbook.write_text("# test runbook\n")
    return SimpleNamespace(
        home=home,
        deltas=home / "deltas.jsonl",
        state=home / "state",
        marker=home / "claude_calls",
    )


def run(sandbox, *, claude_exit=0, cooldown=10800):
    env = {
        **os.environ,
        "HOME": str(sandbox.home),
        "MESHFORGE_REPO": str(sandbox.home),       # runbook resolves under HOME
        "MINI_SKIP_DREAM": "1",                    # no python module / live truth
        "MINI_DELTAS_PATH": str(sandbox.deltas),
        "MINI_CADENCE_STATE_FILE": str(sandbox.state),
        "MINI_CADENCE_TIMEOUT_COOLDOWN_S": str(cooldown),
        "FAKE_CLAUDE_EXIT": str(claude_exit),
        "CLAUDE_CALL_MARKER": str(sandbox.marker),
    }
    proc = subprocess.run(
        ["bash", str(SCRIPT)], env=env,
        capture_output=True, text=True, timeout=60,
    )
    calls = sandbox.marker.read_text().count("called") if sandbox.marker.exists() else 0
    return proc.returncode, (proc.stdout + proc.stderr), calls


def _calls_so_far(sandbox):
    return sandbox.marker.read_text().count("called") if sandbox.marker.exists() else 0


class TestTimeoutBackoff:
    def test_no_proposed_deltas_exits_cheap_and_touches_nothing(self, sandbox):
        sandbox.deltas.write_text('{"status": "ratified", "key": "x"}\n')
        rc, out, calls = run(sandbox)
        assert rc == 0
        assert calls == 0
        assert not sandbox.state.exists()

    def test_first_timeout_arms_state(self, sandbox):
        sandbox.deltas.write_text(PROPOSED_A)
        rc, out, calls = run(sandbox, claude_exit=124)
        assert rc == 124
        assert calls == 1                       # the session DID fire once
        assert sandbox.state.exists()
        body = sandbox.state.read_text()
        assert "last_timeout_ts=" in body
        assert "last_timeout_hash=" in body
        # hash is non-empty (sha256sum present on the fleet)
        hline = [l for l in body.splitlines() if l.startswith("last_timeout_hash=")][0]
        assert hline.split("=", 1)[1].strip() != ""

    def test_backoff_skips_same_deltas_without_firing(self, sandbox):
        sandbox.deltas.write_text(PROPOSED_A)
        run(sandbox, claude_exit=124)           # arm
        before = _calls_so_far(sandbox)
        rc, out, calls = run(sandbox, claude_exit=124)   # second run, same deltas
        assert rc == 75                         # EX_TEMPFAIL → FAIL verdict, visible
        assert calls == before                  # claude NOT invoked again
        assert "backing off" in out
        assert sandbox.state.exists()           # state preserved

    def test_new_deltas_bypass_backoff_and_fire(self, sandbox):
        sandbox.deltas.write_text(PROPOSED_A)
        run(sandbox, claude_exit=124)           # arm on set A
        before = _calls_so_far(sandbox)
        sandbox.deltas.write_text(PROPOSED_B)   # genuinely new proposed set
        rc, out, calls = run(sandbox, claude_exit=124)
        assert rc == 124
        assert calls == before + 1              # fired fresh
        assert "backing off" not in out

    def test_cooldown_elapsed_fires_again(self, sandbox):
        sandbox.deltas.write_text(PROPOSED_A)
        run(sandbox, claude_exit=124)           # arm (real hash)
        before = _calls_so_far(sandbox)
        # Age the recorded timeout past the cooldown, keeping the hash.
        lines = sandbox.state.read_text().splitlines()
        aged = []
        for l in lines:
            if l.startswith("last_timeout_ts="):
                aged.append("last_timeout_ts=1")   # epoch 1 = ancient
            else:
                aged.append(l)
        sandbox.state.write_text("\n".join(aged) + "\n")
        rc, out, calls = run(sandbox, claude_exit=124, cooldown=10800)
        assert rc == 124
        assert calls == before + 1              # cooldown elapsed → fired
        assert "backing off" not in out

    def test_clean_exit_clears_state(self, sandbox):
        sandbox.deltas.write_text(PROPOSED_A)
        run(sandbox, claude_exit=124)           # arm
        # Age it so the next run actually fires (not backed off), then exit 0.
        lines = sandbox.state.read_text().splitlines()
        sandbox.state.write_text(
            "\n".join("last_timeout_ts=1" if l.startswith("last_timeout_ts=") else l
                      for l in lines) + "\n")
        rc, out, calls = run(sandbox, claude_exit=0)
        assert rc == 0
        assert not sandbox.state.exists()       # clean ratify/reject clears backoff

    def test_non_timeout_failure_clears_state(self, sandbox):
        """A loud non-124 failure (e.g. the sticky-by-design model-swap FAIL,
        exit 1) must NOT be absorbed by the timeout-backoff — it clears the
        state so the model-swap path keeps escalating every run as intended."""
        sandbox.deltas.write_text(PROPOSED_A)
        run(sandbox, claude_exit=124)           # arm
        lines = sandbox.state.read_text().splitlines()
        sandbox.state.write_text(
            "\n".join("last_timeout_ts=1" if l.startswith("last_timeout_ts=") else l
                      for l in lines) + "\n")
        rc, out, calls = run(sandbox, claude_exit=1)
        assert rc == 1
        assert not sandbox.state.exists()       # backoff cleared, not armed on exit 1
