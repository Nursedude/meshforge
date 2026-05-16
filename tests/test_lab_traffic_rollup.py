"""Tests for scripts/lab_traffic_rollup.sh — dual-window aggregator.

The bash script is the rollup writer behind the NOC dashboard's
`/lab/rollup` endpoint. It SSH-fans tracer state files from each fleet
box, concatenates them as JSONL, and pipes through an embedded Python
aggregator that emits markdown tables.

These tests exercise the local-only path (no SSH): fixture tracer JSON
files in a temp $XDG_STATE_HOME and an empty fleet_hosts. The
aggregator's `NOW_OVERRIDE` env hook pins wall-clock so age bucketing
is deterministic.

Coverage focus: the 1h-vs-24h bucketing behavior added on 2026-05-15
after moc1's 18h rnsd-RPC wedge poisoned the 24h fail% for days.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

ROLLUP_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "lab_traffic_rollup.sh"


def _write_fire(tracer_dir: Path, fire_at_unix: float, sender: str, results):
    path = tracer_dir / f"tracer-{int(fire_at_unix)}.json"
    doc = {
        "schema_version": 1,
        "fire_at_unix": fire_at_unix,
        "fire_at_iso": "2026-05-15T00:00:00Z",
        "self_short": sender,
        "results": results,
    }
    path.write_text(json.dumps(doc) + "\n", encoding="utf-8")


@pytest.fixture
def rollup_env(tmp_path):
    """Build a hermetic environment for the rollup script.

    Lays out:
      $HOME/.local/state/meshforge/tracer/  — caller writes fire JSON here
      $HOME/.config/meshforge/fleet_hosts   — empty (script only does local)

    Returns (env_dict, tracer_dir, run_fn). run_fn invokes the script
    with the env baked in and returns (stdout, stderr, returncode).
    """
    home = tmp_path / "home"
    state = home / ".local" / "state" / "meshforge" / "tracer"
    config = home / ".config" / "meshforge"
    state.mkdir(parents=True)
    config.mkdir(parents=True)
    fleet_hosts = config / "fleet_hosts"
    fleet_hosts.write_text("", encoding="utf-8")

    base_env = {
        **os.environ,
        "HOME": str(home),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "FLEET_HOSTS": str(fleet_hosts),
        # Pin "now" for deterministic age bucketing.
        "NOW_OVERRIDE": "1778890800",
    }

    def run(env_overrides=None):
        env = dict(base_env)
        if env_overrides:
            env.update(env_overrides)
        result = subprocess.run(
            ["bash", str(ROLLUP_SCRIPT)],
            env=env, capture_output=True, text=True, timeout=30,
        )
        return result.stdout, result.stderr, result.returncode

    return base_env, state, run


def test_default_emits_1h_and_24h_sections(rollup_env):
    """No env overrides → 1h table first, 24h table second."""
    _, tracer_dir, run = rollup_env
    now = 1778890800.0
    # Recent fire (10 min ago) — clean
    _write_fire(tracer_dir, now - 600, "host-a", [
        {"seq": 1, "peer": "host-a", "result": "ok", "rtt_ms": 1000},
        {"seq": 2, "peer": "host-b", "result": "ok", "rtt_ms": 1500},
    ])
    # 12h-old fire — all failed (the "wedge" history)
    _write_fire(tracer_dir, now - 43200, "host-a", [
        {"seq": 1, "peer": "host-a", "result": "timeout"},
        {"seq": 2, "peer": "host-b", "result": "timeout"},
    ])

    out, err, rc = run()
    assert rc == 0, f"stderr={err}"
    assert "### Last 1h (right-now signal)" in out
    assert "### Last 24h" in out

    # 1h section reflects only the recent fire → 0% fail.
    one_h_section = out.split("### Last 1h")[1].split("### Last 24h")[0]
    assert "host-a -> host-b" in one_h_section
    # The recent fire's row: samples=1, fail%=0.0.
    assert "| 1 | 1500 | 1500 | 0.0 |" in one_h_section

    # 24h section includes both fires → 50% fail on host-a→host-b.
    twentyfour_section = out.split("### Last 24h")[1]
    assert "| 2 | 1500 | 1500 | 50.0 | timeout=1 |" in twentyfour_section


def test_lookback_1_does_not_duplicate_table(rollup_env):
    """LOOKBACK_HOURS=1 collapses to a single table — no twin 1h sections."""
    _, tracer_dir, run = rollup_env
    now = 1778890800.0
    _write_fire(tracer_dir, now - 300, "host-a", [
        {"seq": 1, "peer": "host-b", "result": "ok", "rtt_ms": 800},
    ])

    out, err, rc = run({"LOOKBACK_HOURS": "1"})
    assert rc == 0, f"stderr={err}"
    assert out.count("### Last 1h") == 1
    assert "### Last 24h" not in out


def test_custom_windows_renders_each_bucket(rollup_env):
    """WINDOWS=1,6,24 → three sections in ascending order."""
    _, tracer_dir, run = rollup_env
    now = 1778890800.0
    _write_fire(tracer_dir, now - 300, "host-a", [
        {"seq": 1, "peer": "host-b", "result": "ok", "rtt_ms": 800},
    ])
    _write_fire(tracer_dir, now - 4 * 3600, "host-a", [
        {"seq": 1, "peer": "host-b", "result": "timeout"},
    ])
    _write_fire(tracer_dir, now - 12 * 3600, "host-a", [
        {"seq": 1, "peer": "host-b", "result": "timeout"},
    ])

    out, err, rc = run({"WINDOWS": "1,6,24", "LOOKBACK_HOURS": "24"})
    assert rc == 0, f"stderr={err}"
    idx_1 = out.find("### Last 1h")
    idx_6 = out.find("### Last 6h")
    idx_24 = out.find("### Last 24h")
    assert 0 < idx_1 < idx_6 < idx_24, f"section order wrong: {out!r}"


def test_no_fires_in_1h_window_renders_empty_note(rollup_env):
    """All data is older than 1h → the 1h section shows a friendly empty
    note, not just a blank table header. The 24h section still renders."""
    _, tracer_dir, run = rollup_env
    now = 1778890800.0
    _write_fire(tracer_dir, now - 3 * 3600, "host-a", [
        {"seq": 1, "peer": "host-b", "result": "ok", "rtt_ms": 1200},
    ])

    out, err, rc = run()
    assert rc == 0, f"stderr={err}"
    one_h = out.split("### Last 1h")[1].split("### Last 24h")[0]
    assert "no tracer fires landed in the last 1h" in one_h
    # 24h table still has the row.
    twentyfour = out.split("### Last 24h")[1]
    assert "host-a -> host-b" in twentyfour


def test_future_dated_fire_clamped_into_buckets(rollup_env):
    """Clock-skew safety: a fire timestamped after NOW (clamped age=0)
    must land in EVERY bucket rather than getting filtered out silently."""
    _, tracer_dir, run = rollup_env
    now = 1778890800.0
    _write_fire(tracer_dir, now + 60, "host-a", [
        {"seq": 1, "peer": "host-b", "result": "ok", "rtt_ms": 999},
    ])

    out, err, rc = run({"WINDOWS": "1,24"})
    assert rc == 0, f"stderr={err}"
    one_h = out.split("### Last 1h")[1].split("### Last 24h")[0]
    twentyfour = out.split("### Last 24h")[1]
    assert "host-a -> host-b" in one_h
    assert "host-a -> host-b" in twentyfour


def test_fire_without_timestamp_is_skipped_as_malformed(rollup_env):
    """Older state-file schemas lacked `fire_at_unix`. The aggregator
    can't bucket them, so it skips them and bumps the malformed counter.
    Otherwise they'd silently land in every bucket and skew counts."""
    _, tracer_dir, run = rollup_env
    now = 1778890800.0
    # Valid recent fire — must still be counted.
    _write_fire(tracer_dir, now - 600, "host-a", [
        {"seq": 1, "peer": "host-b", "result": "ok", "rtt_ms": 1100},
    ])
    # Hand-craft a fire with no fire_at_unix.
    bad = tracer_dir / "tracer-legacy.json"
    bad.write_text(json.dumps({
        "schema_version": 0, "self_short": "host-a",
        "results": [{"peer": "host-b", "result": "ok", "rtt_ms": 9999}],
    }) + "\n", encoding="utf-8")

    out, err, rc = run()
    assert rc == 0, f"stderr={err}"
    assert "malformed-line skipped: 1" in out
    # The good fire's RTT (1100) wins; the malformed one (9999) is dropped.
    assert "1100" in out
    assert "9999" not in out


def test_invalid_windows_arg_is_rejected(rollup_env):
    """Reject non-numeric / negative WINDOWS at the bash-validation layer
    so the Python never sees garbage."""
    _, _, run = rollup_env
    _, err, rc = run({"WINDOWS": "1,abc,24"})
    assert rc == 2
    assert "WINDOWS" in err
