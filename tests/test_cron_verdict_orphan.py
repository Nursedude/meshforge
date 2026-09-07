"""The orphan-verdict gap: a live emitter nothing judges (2026-09-06).

``probe_cron_verdict_stale`` judges only crons WIRED via a ``cron_verdict.sh
<name>`` token in the crontab, and that orphan filter is CORRECT — a parked or
removed cron leaves a fossil verdict behind, and judging it would false-alarm
forever (#78's dead-cron lesson).

But it left a hole. ``wan_path_probe.py --verdict`` writes its OWN verdict line
rather than carrying the token, so it was an orphan here, and mini said nothing
through 45 consecutive FAIL verdicts over seven hours. The instrument was
working perfectly and speaking to nobody.

The distinguishing fact is FRESHNESS, and it is not a new idea invented here:
``fleet_snapshot._read_cron_verdicts`` already drops an unwired verdict only
when it is ALSO stale, keeping live orphans. A fossil ages out; a live emitter
keeps writing. This module pins both halves — the gap closes and the fossil
stays quiet.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from utils.watchdog_probes import probe_cron_verdict_stale  # noqa: E402
from utils.watchdog_probe_core import (  # noqa: E402
    collect_dispositions, reset_dispositions,
)

NOW = 2_000_000_000.0
WIRED = ("*/5 * * * * /opt/job.py >/dev/null 2>&1; "
         "/opt/meshforge/scripts/cron_verdict.sh myjob $?\n")


def _v(name, status, age_s):
    ts = datetime.datetime.fromtimestamp(NOW - age_s, datetime.timezone.utc)
    return "%s %s %s\n" % (ts.strftime("%Y-%m-%dT%H:%M:%SZ"), name, status)


def _fire(tmp_path, **kw):
    """Two ticks — the probe debounces — and return the second result.

    Dispositions are reset first: they accumulate PROCESS-WIDE with worst-wins
    semantics, so without this a later test reads an earlier test's note and
    'proves' the wrong thing. (Cost one confusing failure while writing these —
    the note said 'held by 2-tick debounce' from a previous case entirely.)
    """
    reset_dispositions()
    sp = str(tmp_path / "cron_debounce.json")
    probe_cron_verdict_stale(state_path=sp, now=NOW, **kw)
    return probe_cron_verdict_stale(state_path=sp, now=NOW, **kw)


def _disp():
    """The entry recorded for this class. The key is ``disp``, not
    ``disposition`` — collect_dispositions() returns {cls: {disp, reason}}."""
    return collect_dispositions().get("cron_verdict_stale", {}) or {}


class TestTheGapCloses:
    """A FRESH orphan cannot be a fossil — a retired cron stops writing — so its
    FAIL gets the same hearing a wired cron's does."""

    def test_a_failing_fresh_orphan_is_reported(self, tmp_path):
        """The real 2026-09-06 shape: boot_survival FAILED, unwired, unheard."""
        verdicts = _v("myjob", "OK", 60) + _v("boot_survival", "FAIL", 120)
        sig = _fire(tmp_path, crontab_text=WIRED, verdicts_text=verdicts)
        assert sig is not None
        assert any("boot_survival" in x for x in sig.extra["unwired_failing"])
        assert "nothing else would have told you" in sig.detail

    def test_a_concern_from_a_fresh_orphan_also_counts(self, tmp_path):
        sig = _fire(tmp_path, crontab_text=WIRED,
                    verdicts_text=_v("myjob", "OK", 60) + _v("x", "CONCERN", 60))
        assert sig is not None and sig.extra["unwired_failing"]

    def test_a_HEALTHY_fresh_orphan_is_not_a_finding(self, tmp_path):
        """Measured that day: 3 of 5 fresh orphans were fine. Nagging about
        working software while the real failures go unheard is an instrument
        talking about itself instead of serving the product."""
        verdicts = _v("myjob", "OK", 60) + _v("identity_backup", "OK", 60)
        sig = _fire(tmp_path, crontab_text=WIRED, verdicts_text=verdicts)
        assert sig is None

    def test_the_detail_says_nothing_else_would_have_caught_it(self, tmp_path):
        sig = _fire(tmp_path, crontab_text=WIRED,
                    verdicts_text=_v("myjob", "OK", 60) + _v("newthing", "FAIL(2)", 60))
        assert "failing UNWIRED" in sig.detail


class TestTheFossilStaysQuiet:
    """The non-regression that matters: this must not resurrect dead crons."""

    def test_a_stale_unwired_FAIL_does_not_fire(self, tmp_path):
        """A parked cron's fossil — old, unwired, and FAILING. This is the exact
        case the orphan filter exists for; judging it is the #78 defect."""
        verdicts = _v("myjob", "OK", 60) + _v("parked_long_ago", "FAIL(1)", 40 * 3600)
        sig = _fire(tmp_path, crontab_text=WIRED, verdicts_text=verdicts)
        assert sig is None

    def test_a_stale_orphan_is_not_smuggled_into_a_real_signal(self, tmp_path):
        """When a WIRED cron legitimately fires, the fossil must still not be
        listed — otherwise the fix leaks the old false alarm into a new place."""
        verdicts = (_v("myjob", "FAIL(1)", 360) + _v("myjob", "FAIL(1)", 60)
                    + _v("parked_long_ago", "FAIL(1)", 40 * 3600))
        sig = _fire(tmp_path, crontab_text=WIRED, verdicts_text=verdicts)
        assert sig is not None
        assert sig.extra["unwired_failing"] == []
        assert "parked_long_ago" not in sig.detail

    def test_no_wired_crons_is_still_inert(self, tmp_path):
        """The opt-in property survives: a box wiring nothing says nothing, even
        with fresh verdicts in the log."""
        sig = _fire(tmp_path, crontab_text="*/5 * * * * /opt/job.py\n",
                    verdicts_text=_v("something", "FAIL(1)", 60))
        assert sig is None


class TestAcknowledgement:
    """An unwired name owned by a dedicated detector is not a finding — but the
    acknowledgement must be readable, or it is just a mute button."""

    def test_an_acknowledged_failing_orphan_does_not_fire(self, tmp_path):
        sig = _fire(tmp_path, crontab_text=WIRED,
                    verdicts_text=_v("myjob", "OK", 60) + _v("wan_path", "FAIL", 60))
        assert sig is None

    def test_the_acknowledgement_names_its_owner_in_the_disposition(self, tmp_path):
        _fire(tmp_path, crontab_text=WIRED,
              verdicts_text=_v("myjob", "OK", 60) + _v("wan_path", "FAIL", 60))
        d = _disp()
        assert d.get("disp") == "clean"
        assert "wan_path" in (d.get("reason") or "")
        assert "mini_dudeai.sources.wan_path" in (d.get("reason") or "")

    def test_every_acknowledgement_carries_a_reason(self):
        """An entry with an empty reason would be an invisible exception."""
        from utils.watchdog_probe_core import CRON_VERDICT_ORPHAN_ACKNOWLEDGED
        assert CRON_VERDICT_ORPHAN_ACKNOWLEDGED
        for name, why in CRON_VERDICT_ORPHAN_ACKNOWLEDGED.items():
            assert isinstance(why, str) and len(why.strip()) > 20, name

    def test_acknowledgement_does_not_mask_a_real_wired_failure(self, tmp_path):
        verdicts = (_v("myjob", "FAIL(1)", 360) + _v("myjob", "FAIL(1)", 60)
                    + _v("wan_path", "FAIL", 60))
        sig = _fire(tmp_path, crontab_text=WIRED, verdicts_text=verdicts)
        assert sig is not None and any("myjob" in f for f in sig.extra["failed"])
        assert sig.extra["acknowledged"] == ["wan_path"]


class TestTheSentenceMeansWhatItSays:
    """The first cut of this leg opened "Wired cron(s) unhealthy — 5 writing
    verdicts nothing judges", which contradicts itself: those are the UNWIRED
    ones. Caught by reading the LIVE signal, not by a test. Now every bucket
    this leg reports IS a failing cron, wired or not, so one honest lead
    covers them all."""

    def test_the_lead_does_not_claim_wiring_it_has_not_established(self, tmp_path):
        sig = _fire(tmp_path, crontab_text=WIRED,
                    verdicts_text=_v("myjob", "OK", 60) + _v("orph", "FAIL", 60))
        assert sig.detail.startswith("Cron(s) unhealthy")
        assert "Wired cron(s) unhealthy" not in sig.detail

    def test_a_wired_failure_still_carries_the_78_advice(self, tmp_path):
        verdicts = _v("myjob", "FAIL(1)", 360) + _v("myjob", "FAIL(1)", 60)
        sig = _fire(tmp_path, crontab_text=WIRED, verdicts_text=verdicts)
        assert "silence is the failure mode" in sig.detail

    def test_a_mixed_finding_carries_both_buckets(self, tmp_path):
        verdicts = (_v("myjob", "FAIL(1)", 360) + _v("myjob", "FAIL(1)", 60)
                    + _v("orph", "FAIL", 60))
        sig = _fire(tmp_path, crontab_text=WIRED, verdicts_text=verdicts)
        assert "failing:" in sig.detail and "failing UNWIRED" in sig.detail


class TestSelfVerdictDeclaration:
    """The residual the orphan filter could not close (2026-09-06).

    ``classify_orphan_verdicts``' own docstring states it: an orphan has no
    crontab schedule, so its SILENCE cannot be judged — only its failures.
    "A fresh orphan that dies simply ages into a fossil and drops out quietly.
    Closing that half needs a real declaration mechanism, not this function."

    Measured shape: ``boot_survival_audit.sh`` and ``cron_verdict_freshness.sh``
    call ``cron_verdict.sh`` from inside their own bodies, so no name reaches
    the crontab line. A ``# cron_verdict:<name>`` marker on that line supplies
    the NAME beside the schedule the cadence is read from — the pair completed,
    on one line that moves together.

    ⚠️ This is a DECLARATION, not an acknowledgement. It makes silence JUDGED;
    the ack list mutes. Adding one can only ever surface more failures.
    """

    DECLARED = ("17 7 * * * /opt/meshforge/scripts/boot_survival_audit.sh "
                ">>/tmp/b.log 2>&1  # cron_verdict:boot_survival\n")

    def test_a_declared_cron_is_wired_not_orphaned(self, tmp_path):
        """Its FAIL is judged as a wired cron's, not reported as a wiring gap."""
        verdicts = _v("myjob", "OK", 60) + _v("boot_survival", "FAIL", 120)
        sig = _fire(tmp_path, crontab_text=WIRED + self.DECLARED,
                    verdicts_text=verdicts)
        assert sig is not None
        assert not sig.extra.get("unwired_failing"), (
            "a declared cron is wired — reporting it as an orphan would still "
            "be describing the wiring instead of judging the failure")
        assert any("boot_survival" in x for x in sig.extra["failed"])

    def test_the_silence_of_a_declared_cron_is_now_judged(self, tmp_path):
        """THE point. Undeclared, a dead emitter ages into a fossil in silence.

        Declared, its cadence is known, so 'no verdict at all' is a finding.
        This is the half the orphan filter could not reach.
        """
        verdicts = _v("myjob", "OK", 60)          # boot_survival: nothing, ever
        undeclared = _fire(tmp_path, crontab_text=WIRED, verdicts_text=verdicts)
        assert undeclared is None, (
            "baseline: with no declaration the dead emitter is invisible")

        declared = _fire(tmp_path, crontab_text=WIRED + self.DECLARED,
                         verdicts_text=verdicts)
        assert declared is not None, "a declared cron that never wrote is stale"
        assert any("boot_survival" in x for x in declared.extra["stale"])

    def test_a_declared_cron_that_is_current_stays_quiet(self, tmp_path):
        """The other polarity: declaring must not manufacture a finding."""
        verdicts = _v("myjob", "OK", 60) + _v("boot_survival", "OK", 120)
        assert _fire(tmp_path, crontab_text=WIRED + self.DECLARED,
                     verdicts_text=verdicts) is None


class TestDeclarationExtractor:
    """Unit-level rules for the marker itself."""

    def test_declared_name_is_extracted(self):
        from utils.fleet_snapshot import _verdict_names_in_command
        assert _verdict_names_in_command(
            "/x/job.sh >>log 2>&1  # cron_verdict:boot_survival"
        ) == ["boot_survival"]

    def test_wired_and_declared_same_name_counts_once(self):
        """One cron, not two — a duplicate double-counts in every tally."""
        from utils.fleet_snapshot import _verdict_names_in_command
        assert _verdict_names_in_command(
            "/x/job.sh; /opt/meshforge/scripts/cron_verdict.sh j $?  "
            "# cron_verdict:j") == ["j"]

    def test_an_ordinary_trailing_comment_declares_nothing(self):
        """The marker must be specific — any `#` would enrol junk as crons."""
        from utils.fleet_snapshot import _verdict_names_in_command
        assert _verdict_names_in_command("/x/job.sh  # nightly, see runbook") == []
        assert _verdict_names_in_command("/x/job.sh  # cron_verdict is nice") == []

    def test_marker_is_a_shell_comment_so_it_never_reaches_the_command(self):
        """Pinning the property the whole design rests on: adding a marker to a
        live crontab line must not change what the job RUNS."""
        import subprocess
        r = subprocess.run(
            ["bash", "-c", "echo ran  # cron_verdict:demo"],
            capture_output=True, text=True, timeout=30)
        assert r.returncode == 0 and r.stdout.strip() == "ran"
