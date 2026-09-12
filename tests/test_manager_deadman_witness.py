"""scripts/manager_deadman.sh must leave a witness on every DARK run.

2026-09-12: moc1 logged ``manager_deadman FAIL(1) out=empty`` at 16:10:02Z and
its state file recorded a real MANAGER DARK page one second earlier -- while
the manager's own ``manager_heartbeat`` verdicts read OK at 15:50:01Z,
16:00:02Z and 16:10:02Z, 65 runs with no gap. Two instruments flatly
disagreed, and the age the deadman judged on had gone only into an ntfy push.
A notification is not a retained artifact, so by morning the one number that
would have settled it was unrecoverable (honest_failure_modes #9 -- every
swallow gets a witness; the 2026-07-30 harness_audit FAIL is the same shape).

These tests run the REAL script against a stub pager. The stub is placed
beside a copy of the script because PUSH is derived from ``BASH_SOURCE``, not
from the environment -- so no test here can page the operator.
"""
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEADMAN = REPO / "scripts" / "manager_deadman.sh"
CRON_VERDICT = REPO / "scripts" / "cron_verdict.sh"

WITNESS = "manager_deadman: manager DARK"

STUB_PUSH = """#!/usr/bin/env bash
printf '%s\\n' "$1" >> "PAGELOG"
exit 0
"""


class _Sandbox:
    """A copy of the deadman with a stub pager next to it."""

    def __init__(self, tmp):
        self.tmp = Path(tmp)
        self.script = self.tmp / "manager_deadman.sh"
        shutil.copy2(DEADMAN, self.script)
        self.pagelog = self.tmp / "pages.txt"
        push = self.tmp / "fleet_ntfy_push.sh"
        push.write_text(STUB_PUSH.replace("PAGELOG", str(self.pagelog)))
        push.chmod(0o755)
        self.beat = self.tmp / ".manager_heartbeat"
        self.state = self.tmp / "state.json"

    def write_beat(self, age_s, epoch=None):
        """Lay down a beat file whose mtime is ``age_s`` seconds old."""
        when = int(time.time()) - age_s
        self.beat.write_text(f"{epoch if epoch is not None else when}\n")
        os.utime(self.beat, (when, when))

    def run(self, **extra_env):
        env = dict(
            os.environ,
            MANAGER_DEADMAN_NAME="TestManager",
            MANAGER_DEADMAN_FILE=str(self.beat),
            MANAGER_DEADMAN_STATE=str(self.state),
            HOME=str(self.tmp),
        )
        env.update(extra_env)
        return subprocess.run(
            ["bash", str(self.script)],
            env=env, capture_output=True, text=True, timeout=60,
        )

    def pages(self):
        if not self.pagelog.exists():
            return []
        return [ln for ln in self.pagelog.read_text().splitlines() if ln.strip()]


class TestDarkRunLeavesAWitness(unittest.TestCase):

    def test_stale_beat_prints_the_numbers_it_judged_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            sb = _Sandbox(tmp)
            sb.write_beat(age_s=3000, epoch=1789229000)
            r = sb.run()
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn(WITNESS, r.stderr)
            # Every field a reader needs to re-derive the verdict.
            for field in ("age=", "threshold=1500s", "now=",
                          "mtime=", "beat_epoch=1789229000", "paged=0"):
                self.assertIn(field, r.stderr, f"missing {field}: {r.stderr}")
            # The age is the one that mattered, and it is REAL, not a label.
            age = int(r.stderr.split("age=")[1].split("s")[0])
            self.assertGreaterEqual(age, 3000)
            self.assertLess(age, 3000 + 120)

    def test_absent_beat_says_absent_not_zero(self):
        """An absent file is unobservable, not 'epoch 0' (hfm #1/#2)."""
        with tempfile.TemporaryDirectory() as tmp:
            sb = _Sandbox(tmp)
            r = sb.run()
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn(WITNESS, r.stderr)
            self.assertIn("age=-1s", r.stderr)
            self.assertIn("mtime=absent", r.stderr)
            self.assertIn("beat_epoch=unreadable", r.stderr)

    def test_repage_suppressed_tick_is_still_legible(self):
        """The leg that used to be TOTALLY silent: dark, but inside the
        re-page window, so no page and (before this) no output at all."""
        with tempfile.TemporaryDirectory() as tmp:
            sb = _Sandbox(tmp)
            sb.write_beat(age_s=3000)
            first = sb.run()
            self.assertEqual(first.returncode, 1)
            self.assertEqual(len(sb.pages()), 1, "first dark run should page")

            second = sb.run()
            self.assertEqual(second.returncode, 1)
            self.assertEqual(len(sb.pages()), 1,
                             "re-page window should suppress the second page")
            self.assertIn(WITNESS, second.stderr,
                          "a suppressed tick must STILL say why it is dark")
            self.assertIn("paged=1", second.stderr)

    def test_fresh_run_stays_quiet(self):
        """The witness is for dark runs only -- a healthy fleet writes nothing,
        or the capture regime fills an SD card to say what the OK already says."""
        with tempfile.TemporaryDirectory() as tmp:
            sb = _Sandbox(tmp)
            sb.write_beat(age_s=60)
            r = sb.run()
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn(WITNESS, r.stderr)
            self.assertEqual(r.stderr.strip(), "")
            self.assertEqual(sb.pages(), [])


class TestWitnessReachesTheVerdictLog(unittest.TestCase):
    """The point of the witness is what cron_verdict.sh does with it: the
    crontab redirects stderr into $OUT_DIR/<name>.out, and a non-OK verdict
    PRESERVES that file. Before this change the file was empty and the verdict
    line read `out=empty`. Wired end to end, not asserted by inspection."""

    def test_dark_run_produces_a_preserved_capture_not_out_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            sb = _Sandbox(tmp)
            sb.write_beat(age_s=3000)
            out_dir = Path(tmp) / "cron_out"
            out_dir.mkdir()
            log = Path(tmp) / "cron_verdicts.log"
            dot_out = out_dir / "manager_deadman.out"

            r = sb.run()
            dot_out.write_text(r.stderr)          # what the crontab redirect does
            rc = r.returncode

            v = subprocess.run(
                ["bash", str(CRON_VERDICT), "manager_deadman", str(rc)],
                env=dict(os.environ,
                         CRON_VERDICT_LOG=str(log),
                         CRON_VERDICT_OUT_DIR=str(out_dir)),
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(v.returncode, 0, v.stderr)

            line = log.read_text().strip()
            self.assertIn("manager_deadman FAIL(1)", line)
            self.assertNotIn("out=empty", line)
            self.assertIn("age=", line, "the verdict line must NAME the cause")

            preserved = list(out_dir.glob("manager_deadman.fail-*.out"))
            self.assertEqual(len(preserved), 1, preserved)
            self.assertIn(WITNESS, preserved[0].read_text())

    def test_guard_fails_if_the_witness_is_removed(self):
        """A guard that never failed is not evidence. Strip the echo from a
        copy and the end-to-end leg must go back to reading `out=empty`."""
        with tempfile.TemporaryDirectory() as tmp:
            sb = _Sandbox(tmp)
            body = sb.script.read_text()
            needle = 'echo "manager_deadman: manager DARK'
            self.assertIn(needle, body, "the line this guard drills is gone")
            stripped = "\n".join(
                ln for ln in body.splitlines() if not ln.startswith(needle)
            )
            sb.script.write_text(stripped)

            sb.write_beat(age_s=3000)
            r = sb.run()
            self.assertEqual(r.returncode, 1)
            self.assertNotIn(WITNESS, r.stderr)
            self.assertEqual(r.stderr.strip(), "",
                             "without the echo a dark run is silent -- "
                             "which is exactly the defect")


if __name__ == "__main__":
    unittest.main()
