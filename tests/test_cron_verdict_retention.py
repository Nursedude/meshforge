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


if __name__ == "__main__":
    unittest.main()
