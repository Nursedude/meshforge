"""Tests for scripts/cron_verdict.sh per-name retention.

2026-07-09 review (medium, live-verified): MAX_LINES=1000 gave ~23.5h retention
on a busy fleet box while three wired crons are DAILY — each daily verdict scrolled out
~30-40 min before its next run, so probe_cron_verdict_stale (Issue #78) read
healthy crons as "silent: (never)". The truncation now keeps the newest
KEEP_PER_NAME lines of EVERY name in addition to the newest MAX_LINES overall,
so a slow-cadence cron's verdicts survive high-churn neighbors (the retention
floor must exceed the slowest wired cadence x the probe's CADENCE_MULT).
"""
import os
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "cron_verdict.sh"


def _run(log_path, name, status, msg=""):
    env = dict(os.environ, CRON_VERDICT_LOG=str(log_path))
    argv = ["bash", str(SCRIPT), name, status]
    if msg:
        argv.append(msg)
    return subprocess.run(argv, env=env, capture_output=True, text=True, timeout=30)


class TestCronVerdictRetention(unittest.TestCase):
    def test_verdict_line_shape(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "v.log"
            r = _run(log, "daily_job", "0", "all green")
            self.assertEqual(r.returncode, 0)
            line = log.read_text().strip()
            parts = line.split(None, 3)
            self.assertEqual(parts[1], "daily_job")
            self.assertEqual(parts[2], "OK")

    def test_daily_cron_survives_high_churn_truncation(self):
        """The live failure shape: one daily verdict + >MAX_LINES of churn from
        5-min crons must NOT truncate the daily cron's line away."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "v.log"
            # Seed the OLDEST line: the daily cron's single verdict.
            with open(log, "w") as fh:
                fh.write("2026-07-08T04:17:01Z backup_rotate OK rotated\n")
                # >MAX_LINES of high-churn neighbors, all newer.
                for i in range(1100):
                    fh.write(f"2026-07-09T00:{i % 60:02d}:00Z fleet_offline_check OK tick{i}\n")
            # One real append triggers the truncation path.
            r = _run(log, "fleet_offline_check", "0", "tick-final")
            self.assertEqual(r.returncode, 0)
            text = log.read_text()
            self.assertIn("backup_rotate OK rotated", text)
            # Overall churn is still bounded near MAX_LINES (+ per-name keeps).
            self.assertLess(len(text.splitlines()), 1200)

    def test_per_name_keep_is_bounded(self):
        """A name with hundreds of old lines keeps only its newest KEEP_PER_NAME
        beyond the global window — retention is bounded, not unbounded."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "v.log"
            with open(log, "w") as fh:
                for i in range(500):
                    fh.write(f"2026-07-01T00:00:{i % 60:02d}Z old_daily OK run{i}\n")
                for i in range(1100):
                    fh.write(f"2026-07-09T00:00:{i % 60:02d}Z churn OK tick{i}\n")
            r = _run(log, "churn", "0")
            self.assertEqual(r.returncode, 0)
            lines = log.read_text().splitlines()
            old_daily = [l for l in lines if " old_daily " in l]
            # Newest 30 per name (KEEP_PER_NAME) — not all 500.
            self.assertEqual(len(old_daily), 30)
            # And it kept the NEWEST ones.
            self.assertIn("run499", old_daily[-1])

    def test_chronological_order_preserved(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "v.log"
            with open(log, "w") as fh:
                fh.write("2026-07-08T04:00:00Z daily OK first\n")
                for i in range(1050):
                    fh.write(f"2026-07-09T00:00:00Z churn OK tick{i}\n")
            _run(log, "churn", "0", "last")
            lines = log.read_text().splitlines()
            self.assertTrue(lines[0].startswith("2026-07-08T04:00:00Z daily"))
            self.assertIn("churn OK last", lines[-1])


class TestCronVerdictConcurrentWriters(unittest.TestCase):
    """honest_failure_modes #8 — concurrent writers: exclude or merge, never
    interleave.

    Truncation is a read-modify-write: `awk` snapshots $LOG, writes a tmp, then
    `mv tmp $LOG` replaces the file. A second cron's atomic append that lands on
    the OLD inode AFTER that snapshot but BEFORE the mv is silently discarded
    when mv overwrites — a verdict the caller logged (exit 0, "recorded") that
    then VANISHES. This is the field shape behind the 2026-07-16 manager_deadman
    investigation: deadman verdicts missing at 18:50 and 00:30 while the cron
    demonstrably ran. Every concurrent verdict must survive.
    """

    def test_concurrent_writers_lose_no_verdict(self):
        import tempfile
        from concurrent.futures import ThreadPoolExecutor

        n = 64
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "v.log"
            # Seed just over MAX_LINES so EVERY writer hits the truncation
            # (read-modify-write) path — maximizing the overlap window.
            with open(log, "w") as fh:
                for i in range(1050):
                    fh.write(f"2026-07-09T00:00:00Z seed OK s{i}\n")

            def write(i):
                # Unique name + marker so each verdict is individually findable.
                return _run(log, f"w{i:03d}", "0", f"MARK{i:03d}")

            with ThreadPoolExecutor(max_workers=n) as ex:
                results = list(ex.map(write, range(n)))

            # Every writer exited 0 — each claims its verdict was recorded.
            self.assertTrue(all(r.returncode == 0 for r in results),
                            "a writer exited non-zero")

            text = log.read_text()
            missing = [i for i in range(n) if f"MARK{i:03d}" not in text]
            self.assertEqual(
                missing, [],
                f"{len(missing)}/{n} concurrent verdicts lost to the truncation "
                f"read-modify-write race (honest_failure_modes #8): {missing}",
            )

    def test_verdict_recorded_when_lock_cannot_be_opened(self):
        """Fallback #1 — if the lock file itself can't be opened (unwritable
        dir), the verdict must STILL be recorded, exactly once. Silence is the
        worse failure than a rare unserialized append (hf_modes #9: every
        swallow leaves a witness — here the witness is the verdict line)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "v.log"
            env = dict(os.environ, CRON_VERDICT_LOG=str(log),
                       CRON_VERDICT_LOCK=str(Path(tmp) / "nodir" / "sub" / "l"))
            r = subprocess.run(["bash", str(SCRIPT), "lockless_job", "0", "still logged"],
                               env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(log.read_text().count("lockless_job"), 1)

    def test_verdict_recorded_when_flock_fails(self):
        """Fallback #2 — if flock is absent or the lock stays wedged past the
        timeout (stubbed here as a failing flock), the verdict must STILL be
        recorded, exactly once (no duplicate from the open-failure fallback)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            bind = Path(tmp) / "bin"
            bind.mkdir()
            stub = bind / "flock"          # shadow flock with a always-fail stub
            stub.write_text("#!/bin/sh\nexit 1\n")
            stub.chmod(0o755)
            log = Path(tmp) / "v.log"
            env = dict(os.environ, CRON_VERDICT_LOG=str(log),
                       PATH=f"{bind}:{os.environ.get('PATH', '')}")
            r = subprocess.run(["bash", str(SCRIPT), "flockless_job", "0", "still logged"],
                               env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(log.read_text().count("flockless_job"), 1)


if __name__ == "__main__":
    unittest.main()
