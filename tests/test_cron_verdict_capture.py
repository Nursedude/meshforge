"""Tests for cron_verdict.sh evidence capture + cron_capture_wire.py.

2026-07-31: ``harness_audit`` logged ``FAIL(1)`` on 07-30 and WHICH of its 14
checks failed was unrecoverable — the crontab idiom discards job output and the
verdict log stores only name+status. The witness recorded that an alarm fired
and destroyed its cause (honest_failure_modes #9).

These pin the cure AND its honesty: the four capture states must stay distinct
(uncaptured / empty / preserved / capture_failed), because collapsing "this cron
is not wired for capture" into "this run said nothing" would rebuild the exact
lie the feature exists to remove (#1/#2 — a degraded state wearing a healthy
value).

Every test drives real files under a tmpdir via CRON_VERDICT_* env overrides —
no test here reads this box's crontab or its real verdict log, so the verdict
does not depend on which box runs the suite.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SCRIPT = SCRIPTS / "cron_verdict.sh"

sys.path.insert(0, str(SCRIPTS))
import cron_capture_wire as wire  # noqa: E402


def _run(tmp, name, status, msg="", keep=None, ts=None):
    """Invoke cron_verdict.sh with log + capture dir confined to tmp.

    ``ts`` injects CRON_VERDICT_TS: capture filenames carry 1-second
    timestamps, so back-to-back same-second invocations collapse onto ONE
    path and retention assertions pass vacuously (ultra review 2026-07-31).
    Loops that assert on capture COUNTS must pass distinct timestamps."""
    env = dict(
        os.environ,
        CRON_VERDICT_LOG=str(Path(tmp) / "v.log"),
        CRON_VERDICT_OUT_DIR=str(Path(tmp) / "cron_out"),
    )
    if keep is not None:
        env["CRON_VERDICT_KEEP_CAPTURES"] = str(keep)
    if ts is not None:
        env["CRON_VERDICT_TS"] = ts
    argv = ["bash", str(SCRIPT), name, status]
    if msg:
        argv.append(msg)
    r = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=30)
    log = (Path(tmp) / "v.log").read_text().strip().splitlines()
    return r, log


class TestCaptureTriState(unittest.TestCase):
    """The four states must never collapse into each other."""

    def test_fail_with_no_out_file_says_uncaptured(self):
        """A cron not wired for capture must NOT read as 'produced no output'."""
        with tempfile.TemporaryDirectory() as tmp:
            _, log = _run(tmp, "unwired_job", "1")
            self.assertIn("out=uncaptured", log[-1])
            self.assertNotIn("out=empty", log[-1])

    def test_fail_with_empty_out_file_says_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cron_out"
            out.mkdir()
            (out / "quiet_job.out").write_text("")
            _, log = _run(tmp, "quiet_job", "1")
            self.assertIn("out=empty", log[-1])
            self.assertNotIn("out=uncaptured", log[-1])

    def test_fail_preserves_output_and_names_the_path(self):
        """The whole point: the verdict line must lead to the evidence."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cron_out"
            out.mkdir()
            (out / "harness_audit.out").write_text(
                "  hooksPath      PASS  .githooks\n"
                "  memory index   FAIL  28001B over limit\n"
                "--> 13 PASS, 1 FAIL, 0 UNKNOWN\n"
            )
            _, log = _run(tmp, "harness_audit", "1")
            line = log[-1]
            self.assertIn("FAIL(1)", line)
            # A path is named, and that path exists with the full output.
            self.assertIn("out=", line)
            preserved = [p for p in out.iterdir() if p.name != "harness_audit.out"]
            self.assertEqual(len(preserved), 1, "exactly one capture preserved")
            body = preserved[0].read_text()
            self.assertIn("memory index", body)
            self.assertIn("13 PASS, 1 FAIL", body)
            self.assertIn(str(preserved[0]), line)

    def test_excerpt_prefers_the_failing_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cron_out"
            out.mkdir()
            (out / "j.out").write_text("all fine\nERROR: disk full\ntrailing noise\n")
            _, log = _run(tmp, "j", "1")
            self.assertIn("ERROR: disk full", log[-1])

    def test_excerpt_is_one_line_so_the_log_stays_parseable(self):
        """The log is <ts> <name> <STATUS> <msg>; a raw multi-line excerpt would
        break every reader that splits on it, including the truncation awk."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cron_out"
            out.mkdir()
            (out / "j.out").write_text("line one\nline two\nline three\n")
            _, log = _run(tmp, "j", "1")
            self.assertEqual(len(log), 1, "one verdict = one log line")
            self.assertEqual(log[0].split(None, 3)[1], "j")
            self.assertEqual(log[0].split(None, 3)[2], "FAIL(1)")

    def test_ok_run_captures_nothing(self):
        """Healthy runs must not accumulate output on an SD card."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cron_out"
            out.mkdir()
            (out / "j.out").write_text("chatty but fine\n")
            _, log = _run(tmp, "j", "0")
            self.assertNotIn("out=", log[-1])
            self.assertEqual([p.name for p in out.iterdir()], ["j.out"])

    def test_concern_captures_too(self):
        """CONCERN is non-OK — it has a cause worth keeping."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cron_out"
            out.mkdir()
            (out / "j.out").write_text("2 of 3 peers slow\n")
            _, log = _run(tmp, "j", "CONCERN")
            self.assertIn("out=", log[-1])
            self.assertIn("concern-", " ".join(p.name for p in out.iterdir()))


class TestCaptureRetention(unittest.TestCase):
    def test_prunes_to_keep_limit(self):
        """Distinct injected timestamps make all 6 captures land as 6 files —
        without them this test passed vacuously at len=1 and the prune loop
        was never exercised (ultra review 2026-07-31)."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cron_out"
            out.mkdir()
            for i in range(6):
                (out / "j.out").write_text(f"failure {i}\n")
                _run(tmp, "j", "1", keep=3, ts=f"2026-07-31T00:00:0{i}Z")
            kept = sorted(p.name for p in out.iterdir() if p.name != "j.out")
            self.assertEqual(len(kept), 3, kept)
            self.assertEqual(kept, [
                "j.fail-2026-07-31T00:00:03Z.out",
                "j.fail-2026-07-31T00:00:04Z.out",
                "j.fail-2026-07-31T00:00:05Z.out",
            ], "the NEWEST captures survive, the oldest are pruned")

    def test_prune_cannot_reap_a_dot_suffix_neighbour(self):
        """`sync` pruning must never delete `sync.extra`'s evidence: the glob
        `sync.`*-*.out DOES match `sync.extra.fail-<ts>.out`, so the prune
        pipeline filters candidates to <name>.<dot-free-slug>-<ts> (review
        2026-07-31, finding 10 — the dot anchors underscore neighbours only)."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cron_out"
            out.mkdir()
            (out / "sync.extra.out").write_text("neighbour failed\n")
            _run(tmp, "sync.extra", "1", keep=5, ts="2026-07-31T00:00:00Z")
            neighbour = sorted(p.name for p in out.iterdir()
                               if p.name.startswith("sync.extra.")
                               and p.name != "sync.extra.out")
            self.assertEqual(len(neighbour), 1)
            # A frequently-failing `sync` churns past its keep limit; the
            # neighbour's single preserved capture must survive the rotation.
            for i in range(4):
                (out / "sync.out").write_text(f"mine {i}\n")
                _run(tmp, "sync", "1", keep=1, ts=f"2026-07-31T00:00:0{i + 1}Z")
            still = sorted(p.name for p in out.iterdir()
                           if p.name.startswith("sync.extra.")
                           and p.name != "sync.extra.out")
            self.assertEqual(still, neighbour, "dot-suffix neighbour reaped")
            own = [p.name for p in out.iterdir()
                   if p.name.startswith("sync.fail-")]
            self.assertEqual(len(own), 1, "own captures still pruned to keep")

    def test_prune_cannot_reap_a_name_prefix_neighbour(self):
        """`brain_backup` pruning must never delete `brain_backup_extra`'s
        evidence — the literal dot after the name anchors the glob."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cron_out"
            out.mkdir()
            (out / "brain_backup_extra.out").write_text("neighbour failed\n")
            _run(tmp, "brain_backup_extra", "1", keep=1)
            neighbour = [p for p in out.iterdir()
                         if p.name.startswith("brain_backup_extra.")
                         and p.name != "brain_backup_extra.out"]
            self.assertEqual(len(neighbour), 1)
            for i in range(4):
                (out / "brain_backup.out").write_text(f"mine {i}\n")
                _run(tmp, "brain_backup", "1", keep=1)
            still = [p for p in out.iterdir()
                     if p.name.startswith("brain_backup_extra.")
                     and p.name != "brain_backup_extra.out"]
            self.assertEqual(still, neighbour, "neighbour's capture survived")

    def test_capture_failure_is_loud_not_silent(self):
        """If the evidence cannot be preserved, the verdict says so — an
        unwritable capture dir must not read as 'nothing to report'."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cron_out"
            out.mkdir()
            (out / "j.out").write_text("boom\n")
            os.chmod(out, 0o500)  # readable, not writable
            try:
                _, log = _run(tmp, "j", "1")
            finally:
                os.chmod(out, 0o700)
            self.assertIn("out=capture_failed", log[-1])


class TestWirePlanning(unittest.TestCase):
    """cron_capture_wire.plan_line — pure, so no crontab is read here."""

    OUT = "$HOME/.local/state/meshforge/cron_out"

    def test_rewrites_the_discard_idiom(self):
        line = ("35 5 * * * /opt/meshforge/scripts/harness_audit.sh >/dev/null 2>&1; "
                "/opt/meshforge/scripts/cron_verdict.sh harness_audit $?")
        action, _, new = wire.plan_line(line, self.OUT)
        self.assertEqual(action, "wire")
        self.assertIn(f'>"{self.OUT}/harness_audit.out" 2>&1', new)
        self.assertIn("cron_verdict.sh harness_audit $?", new)
        self.assertNotIn("/dev/null", new)

    def test_is_idempotent(self):
        line = ("35 5 * * * /x/job.sh >/dev/null 2>&1; "
                "/opt/meshforge/scripts/cron_verdict.sh harness_audit $?")
        _, _, once = wire.plan_line(line, self.OUT)
        action, _, twice = wire.plan_line(once, self.OUT)
        self.assertEqual(action, "wired")
        self.assertEqual(once, twice)

    def test_leaves_a_job_that_logs_elsewhere_alone(self):
        """Rewriting would move a log the operator may already tail."""
        line = ("45 4 * * * /x/calibration_reverify.sh >> $HOME/cal.log 2>&1; "
                "/opt/meshforge/scripts/cron_verdict.sh calibration_reverify $?")
        action, reason, new = wire.plan_line(line, self.OUT)
        self.assertEqual(action, "skip")
        self.assertIn("own log", reason)
        self.assertEqual(new, line)

    def test_handles_the_wrapper_crashed_or_idiom(self):
        line = ("13 5 * * * /x/memory_health_cron.sh >/dev/null 2>&1 || "
                "/opt/meshforge/scripts/cron_verdict.sh memory_health FAIL wrapper_crashed")
        action, _, new = wire.plan_line(line, self.OUT)
        self.assertEqual(action, "wire")
        self.assertIn(f'>"{self.OUT}/memory_health.out" 2>&1', new)

    def test_adds_capture_when_the_job_has_no_redirect_at_all(self):
        """Output going to cron mail on a headless Pi is read by nobody — the
        same witness gap in a different shape."""
        line = ("17 * * * * /opt/meshforge/scripts/fleet_ntfy_ack.sh; "
                "/opt/meshforge/scripts/cron_verdict.sh ntfy_ack $?")
        action, reason, new = wire.plan_line(line, self.OUT)
        self.assertEqual(action, "wire")
        self.assertIn("cron mail", reason)
        self.assertIn(f'>"{self.OUT}/ntfy_ack.out" 2>&1', new)
        self.assertIn("fleet_ntfy_ack.sh", new)
        self.assertIn("cron_verdict.sh ntfy_ack $?", new)
        # and still idempotent on a second pass
        self.assertEqual(wire.plan_line(new, self.OUT)[0], "wired")

    def test_no_redirect_form_keeps_an_env_var_prefix(self):
        line = ("0 */2 * * * MESHFORGE_LOOPBACK_INTERVAL_S=7200 /x/loop.sh; "
                "/opt/meshforge/scripts/cron_verdict.sh ntfy_loopback $?")
        action, _, new = wire.plan_line(line, self.OUT)
        self.assertEqual(action, "wire")
        self.assertIn("MESHFORGE_LOOPBACK_INTERVAL_S=7200 /x/loop.sh >", new)

    def test_ignores_lines_with_no_verdict_call(self):
        line = "0 * * * * /x/cron_verdict_freshness.sh >/dev/null 2>&1"
        action, _, new = wire.plan_line(line, self.OUT)
        self.assertEqual(action, "skip")
        self.assertEqual(new, line)

    def test_ignores_comments_and_blanks(self):
        for line in ("# a comment >/dev/null 2>&1; cron_verdict.sh x $?", "", "   "):
            self.assertEqual(wire.plan_line(line, self.OUT)[0], "skip")


class TestSharedConstant(unittest.TestCase):
    """The wirer and the verdict script must not carry independent copies of the
    capture path (honest_failure_modes #5 — two consumers, one constant)."""

    def test_out_dir_is_read_from_the_verdict_script(self):
        self.assertEqual(wire.read_out_dir(),
                         "$HOME/.local/state/meshforge/cron_out")

    def test_missing_declaration_raises_instead_of_guessing(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "cron_verdict.sh"
            fake.write_text("#!/bin/bash\necho no out dir here\n")
            with self.assertRaises(wire.WireError):
                wire.read_out_dir(fake)

    def test_wired_path_matches_what_the_script_would_read(self):
        """End-to-end: wire a line, then prove cron_verdict.sh finds the file at
        exactly the path the wirer chose."""
        out_dir = wire.read_out_dir()
        line = ("35 5 * * * /x/job.sh >/dev/null 2>&1; "
                "/opt/meshforge/scripts/cron_verdict.sh harness_audit $?")
        _, _, new = wire.plan_line(line, out_dir)
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "cron_out"
            real.mkdir()
            (real / "harness_audit.out").write_text("evidence\n")
            # The wired redirect target, with the tmp dir standing in for $HOME's.
            self.assertIn("/harness_audit.out", new)
            _, log = _run(tmp, "harness_audit", "1")
            self.assertIn("out=", log[-1])
            self.assertNotIn("uncaptured", log[-1])


class TestConsumersParseStructurally(unittest.TestCase):
    """Evidence capture embeds RAW JOB OUTPUT in verdict lines, so any
    consumer that whole-line-greps the log can be poisoned by an excerpt that
    happens to contain a sibling cron's name or " OK " (review 2026-07-31,
    finding 7). These drive the REAL harness_audit.sh — the consumer of
    record, not a copy of its parsing — against a crafted log via its
    CRON_VERDICT_LOG override. Only the two verdict-log legs are asserted;
    every other leg reads box state this test neither controls nor judges.
    """

    def _audit_lines(self, log_text):
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "v.log"
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            log.write_text(log_text.replace("{now}", now))
            r = subprocess.run(
                ["bash", str(SCRIPTS / "harness_audit.sh")],
                # MESHFORGE_REPO is pinned to THIS checkout: the script's
                # default is /opt/meshforge, and on a box where that path does
                # not exist (CI runners) `set -u` kills it before the verdict
                # legs even print — the test's verdict must not depend on
                # where the repo happens to be mounted.
                env=dict(os.environ, CRON_VERDICT_LOG=str(log),
                         MESHFORGE_REPO=str(SCRIPTS.parent)),
                capture_output=True, text=True, timeout=120,
            )
            return r.stdout.splitlines()

    def test_excerpt_naming_a_sibling_cron_cannot_steal_its_row(self):
        """A newer harness_audit FAIL whose excerpt mentions
        calibration_reverify must not become calibration_reverify's verdict."""
        lines = self._audit_lines(
            "{now} calibration_reverify OK \n"
            "{now} harness_audit FAIL(1) out=/x/harness_audit.fail.out |"
            "   calibration_reverify verdict PASS OK, 11469s ago\n"
        )
        row = [ln for ln in lines if "calibration_reverify verdict" in ln]
        self.assertEqual(len(row), 1, lines)
        # Column-positional: the echoed message may itself contain PASS/FAIL
        # text, so a substring assert can pass against the broken parser.
        self.assertEqual(row[0].split()[2], "PASS", row[0])

    def test_excerpt_containing_ok_cannot_green_a_fail(self):
        """A cron_freshness FAIL whose own excerpt says '13 OK, 1 stale' must
        stay FAIL — the status field, not the message, is the verdict."""
        lines = self._audit_lines(
            "{now} cron_freshness FAIL out=/x/cron_freshness.fail.out |"
            " peer moc1 OK peer moc2 stale\n"
        )
        row = [ln for ln in lines if ln.strip().startswith("cron_freshness")]
        self.assertEqual(len(row), 1, lines)
        # Column-positional (see above): the message echoes the raw line,
        # which contains both "FAIL" and " OK " — only the verdict column
        # distinguishes the fixed parser from the broken one.
        self.assertEqual(row[0].split()[1], "FAIL", row[0])


if __name__ == "__main__":
    unittest.main()
