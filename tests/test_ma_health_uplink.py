"""Tests for scripts/ma_health_uplink.sh transitional-state recheck.

2026-07-19: the daily 04:30 `ma_health FAIL(1)` page (07-17/18/19 in mini
history) was meshanchor-map-restart.timer firing at 04:30:00 while the */15
probe hit at :30:02 — the probe read `deactivating` and mapped a deliberate
restart straight to FAIL (honest_failure_modes: ambiguous != failed). Fix
d2512ce7 rechecks transitional unit states (activating/deactivating/
reloading) once before judging. These tests pin that logic by executing the
script against PATH-shimmed ssh/systemctl/sleep/timeout — the remote body
runs locally, so the recheck itself (not a canned transcript) is under test.
"""
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ma_health_uplink.sh"

SSH_SHIM = """#!/usr/bin/env bash
# Take the last argument (the remote command string) and run it locally.
for last; do :; done
exec bash -c "$last"
"""

SYSTEMCTL_SHIM = """#!/usr/bin/env bash
# is-active <unit>: answer from a per-unit scripted sequence, one state per
# line in $FAKE_SYSTEMCTL_DIR/seq_<unit>; past the end, repeat the last line.
unit="$2"
d="$FAKE_SYSTEMCTL_DIR"
n=$(cat "$d/count_$unit" 2>/dev/null || echo 0)
echo $((n + 1)) > "$d/count_$unit"
line=$(sed -n "$((n + 1))p" "$d/seq_$unit")
[ -z "$line" ] && line=$(tail -1 "$d/seq_$unit")
echo "$line"
"""

SLEEP_SHIM = """#!/usr/bin/env bash
echo "$@" >> "$FAKE_SYSTEMCTL_DIR/sleeps"
exit 0
"""

TIMEOUT_SHIM = """#!/usr/bin/env bash
shift
case "$1" in
  ssh|*/ssh) exec "$@" ;;
  *) echo "OK dep_floor_ok test-pinned"; exit 0 ;;
esac
"""


def _write_shim(bindir, name, body):
    p = bindir / name
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run(tmp, unit_seqs):
    """Run the script with shimmed tools; unit_seqs maps unit -> [states]."""
    tmp = Path(tmp)
    bindir = tmp / "bin"
    fake_dir = tmp / "fake"
    state_dir = tmp / "state"
    bindir.mkdir()
    fake_dir.mkdir()
    _write_shim(bindir, "ssh", SSH_SHIM)
    _write_shim(bindir, "systemctl", SYSTEMCTL_SHIM)
    _write_shim(bindir, "sleep", SLEEP_SHIM)
    _write_shim(bindir, "timeout", TIMEOUT_SHIM)
    for unit, states in unit_seqs.items():
        (fake_dir / f"seq_{unit}").write_text("\n".join(states) + "\n")
    env = dict(
        os.environ,
        PATH=f"{bindir}:{os.environ['PATH']}",
        FAKE_SYSTEMCTL_DIR=str(fake_dir),
        MA_HEALTH_HOST="fakehost",
        MA_HEALTH_STATE_DIR=str(state_dir),
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60
    )
    return proc, fake_dir, state_dir


def _calls(fake_dir, unit):
    f = Path(fake_dir) / f"count_{unit}"
    return int(f.read_text()) if f.exists() else 0


class TestTransitionalRecheck(unittest.TestCase):
    def test_restart_window_rides_through(self):
        """The live failure shape: probe catches the daily restart mid-flight
        (`deactivating`), rechecks once, sees `active` -> OK, not a page."""
        with tempfile.TemporaryDirectory() as tmp:
            proc, fake, state = _run(
                tmp,
                {"meshanchor": ["active"], "meshanchor-map": ["deactivating", "active"]},
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            detail = (state / "ma_health_last.txt").read_text()
            self.assertIn("OK   meshanchor-map active", detail)
            # The recheck actually happened: two is-active calls + a bounded wait.
            self.assertEqual(_calls(fake, "meshanchor-map"), 2)
            self.assertIn("8", (fake / "sleeps").read_text())

    def test_still_transitional_after_recheck_fails(self):
        """Still activating after the recheck = real flapping, judged FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            proc, fake, state = _run(
                tmp,
                {
                    "meshanchor": ["active"],
                    "meshanchor-map": ["deactivating", "activating"],
                },
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("meshanchor-map activating", proc.stdout)
            # FAIL evidence survives the next OK overwrite.
            self.assertIn(
                "FAIL meshanchor-map activating",
                (state / "ma_health_last_fail.txt").read_text(),
            )

    def test_steady_active_needs_no_recheck(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, fake, _ = _run(
                tmp, {"meshanchor": ["active"], "meshanchor-map": ["active"]}
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(_calls(fake, "meshanchor-map"), 1)

    def test_hard_down_fails_without_recheck(self):
        """`failed`/`inactive` is not transitional — dead is dead, no grace."""
        with tempfile.TemporaryDirectory() as tmp:
            proc, fake, _ = _run(
                tmp, {"meshanchor": ["active"], "meshanchor-map": ["failed"]}
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("meshanchor-map failed", proc.stdout)
            self.assertEqual(_calls(fake, "meshanchor-map"), 1)


if __name__ == "__main__":
    unittest.main()
