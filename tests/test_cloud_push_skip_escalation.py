"""The push must not go quiet while the map goes stale (2026-09-06).

The incident: transit to the VPS ran ~50% loss. ``push_snapshot.sh`` gates the
transfer behind a healthcheck, that gate failed five times running, and the map
sat 39 minutes stale — while systemd, NRestarts and boot_survival all read
healthy, because the unit declares ``SuccessExitStatus=0 1`` and the script's
"retryable" exit 1 is therefore a success. Five skips, five unit successes,
ZERO rsync attempts.

Two defects, both pinned here:

1. **The gate was stricter than the thing it guards.** A single ``--max-time 5``
   probe asks "did this one exchange get through", not "is the VPS up", while
   the rsync below had been hardened the day before to RIDE OUT loss (60 s
   stall timeout, ``--partial-dir`` resume). On the exact condition the
   transfer was built to survive, the gate stopped it from trying.

2. **Transient-quiet without persistent-loud.** Not failing the unit on one
   blip is right; staying quiet through N of them is the #78 silence class
   wearing a different hat.

The escalation is EXECUTED here, not just grepped — a counter that has never
been made to fire is not evidence it fires.
"""
import os
import re
import subprocess
import textwrap

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_PUSH = os.path.join(_ROOT, "scripts", "cloud", "push_snapshot.sh")


def _read(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class TestHealthcheckGate:
    def test_the_healthcheck_retries(self):
        s = _read("scripts/cloud/push_snapshot.sh")
        assert "HEALTH_TRIES" in s, "the gate must retry, not judge on one packet exchange"
        assert re.search(r'for _try in \$\(seq 1 "\$HEALTH_TRIES"\)', s)

    def test_the_gate_is_not_stricter_than_the_transfer_it_guards(self):
        """rsync rides out loss for 60 s; a 5 s single-shot gate that blocks it
        from starting inverts the whole design."""
        s = _read("scripts/cloud/push_snapshot.sh")
        m = re.search(r'curl -sS --max-time (\d+) -o /dev/null "https://\$CLOUD_HOST/healthz"', s)
        assert m, "healthcheck curl not found in the expected shape"
        assert int(m.group(1)) >= 10, (
            "a 5s single probe is what skipped 5 pushes on 2026-09-06")

    def test_a_failed_gate_still_exits_retryable_not_fatal(self):
        s = _read("scripts/cloud/push_snapshot.sh")
        assert 'log "cloud healthcheck failed ${HEALTH_TRIES}x' in s


class TestUnitBoundary:
    """bash and systemd cannot share a constant, so pin them against each other
    (honest_failure_modes #5)."""

    def test_exit_3_is_documented_as_the_loud_code(self):
        assert re.search(r"#\s+3\s+persistent", _read("scripts/cloud/push_snapshot.sh"))

    def test_exit_3_is_NOT_declared_a_success_by_the_unit(self):
        """The whole escalation dies if someone adds 3 here."""
        unit = _read("templates/cloud/meshforge-cloud-push.service")
        m = re.search(r"^SuccessExitStatus=(.*)$", unit, re.M)
        assert m, "SuccessExitStatus missing from the unit"
        codes = m.group(1).split()
        assert "3" not in codes, (
            "exit 3 must FAIL the unit — that is the only thing that makes a "
            "persistent skip visible to systemd, boot_survival and the operator")
        assert "1" in codes, "one transient blip should still not fail the unit"


class TestEscalationActuallyFires:
    """Executed, not grepped."""

    HARNESS = textwrap.dedent("""
        D=$(mktemp -d); CACHE_DIR="$D"
        log() { echo "LOG:$*"; }
        err() { echo "ERR:$*"; }
        PUSH_SKIP_ESCALATE=3
        SKIP_STREAK_FILE="$CACHE_DIR/push_skip_streak"
        sed -n '/^_on_exit() {/,/^}/p' %s > "$D/fn.sh"
        . "$D/fn.sh"
        run() { ( trap _on_exit EXIT; exit "$1" ); echo "RC=$?"; }
        %s
        chmod -R 700 "$D" 2>/dev/null; rm -rf "$D"
    """)

    def _run(self, body):
        out = subprocess.run(["bash", "-c", self.HARNESS % (_PUSH, body)],
                             capture_output=True, text=True, timeout=60)
        return out.stdout + out.stderr

    def test_two_skips_stay_quiet_and_the_third_escalates(self):
        o = self._run("run 1; run 1; run 1")
        assert o.count("RC=1") == 2, o
        assert "RC=3" in o and "no longer transient" in o, o

    def test_a_successful_push_clears_the_streak(self):
        o = self._run('run 1; run 1; run 0; run 1; echo "AFTER=$(cat $SKIP_STREAK_FILE)"')
        assert "RC=0" in o and "AFTER=1" in o, o
        assert "RC=3" not in o, "a real push must reset the streak"

    def test_a_config_error_passes_through_untouched(self):
        assert "RC=2" in self._run("run 2")

    def test_an_unwritable_streak_file_fails_LOUD_rather_than_muting(self):
        """A saver that silently cannot write freezes the streak below its
        threshold and the alarm never fires again (the 2026-09-02 class)."""
        o = self._run('rm -f "$SKIP_STREAK_FILE"; chmod 500 "$D"; run 1; chmod 700 "$D"')
        assert "RC=3" in o and "BLIND" in o, o

    def test_a_readonly_streak_file_also_fails_loud(self):
        o = self._run('echo 1 > "$SKIP_STREAK_FILE"; chmod 400 "$SKIP_STREAK_FILE"; '
                      'run 1; chmod 600 "$SKIP_STREAK_FILE"')
        assert "RC=3" in o and "BLIND" in o, o

    def test_garbage_in_the_streak_file_does_not_crash_or_miscount(self):
        o = self._run('echo junk > "$SKIP_STREAK_FILE"; run 1; '
                      'echo "AFTER=$(cat $SKIP_STREAK_FILE)"')
        assert "RC=1" in o and "AFTER=1" in o, o
