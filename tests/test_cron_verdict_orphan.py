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
    def test_a_fresh_unwired_emitter_is_reported(self, tmp_path):
        """The wan_path shape: a live script writing verdicts nothing judges."""
        verdicts = _v("myjob", "OK", 60) + _v("selfverdicting", "OK", 120)
        sig = _fire(tmp_path, crontab_text=WIRED, verdicts_text=verdicts)
        assert sig is not None
        assert "selfverdicting" in sig.extra["unwired"]
        assert "nothing judges" in sig.detail

    def test_it_reports_the_gap_even_when_the_orphan_says_OK(self, tmp_path):
        """The defect is unjudgeABILITY, not current health: an emitter that is
        fine today is exactly the one whose future FAIL nobody will hear."""
        verdicts = _v("myjob", "OK", 60) + _v("selfverdicting", "OK", 60)
        sig = _fire(tmp_path, crontab_text=WIRED, verdicts_text=verdicts)
        assert sig is not None and sig.extra["unwired"] == ["selfverdicting"]

    def test_the_detail_says_how_to_close_it(self, tmp_path):
        sig = _fire(tmp_path, crontab_text=WIRED,
                    verdicts_text=_v("myjob", "OK", 60) + _v("newthing", "OK", 60))
        assert "cron_verdict.sh <name>" in sig.detail
        assert "CRON_VERDICT_ORPHAN_ACKNOWLEDGED" in sig.detail


class TestTheFossilStaysQuiet:
    """The non-regression that matters: this must not resurrect dead crons."""

    def test_a_stale_unwired_verdict_does_not_fire(self, tmp_path):
        """A parked cron's fossil — old, unwired. Judging it is the #78 defect."""
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
        assert sig.extra["unwired"] == []
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

    def test_an_acknowledged_orphan_does_not_fire(self, tmp_path):
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
    """Caught by reading the LIVE signal, not by a test: the detail opened
    "Wired cron(s) unhealthy — 5 writing verdicts nothing judges", which
    contradicts itself, and advised "fix the job" when the job is fine and the
    wiring is missing. A misread instrument is a bug report against it."""

    def test_a_coverage_gap_alone_does_not_claim_wired_crons_are_unhealthy(self, tmp_path):
        sig = _fire(tmp_path, crontab_text=WIRED,
                    verdicts_text=_v("myjob", "OK", 60) + _v("selfverdicting", "OK", 60))
        assert sig.detail.startswith("Cron verdict coverage gap")
        assert "Wired cron(s) unhealthy" not in sig.detail

    def test_a_coverage_gap_alone_does_not_advise_fixing_a_job(self, tmp_path):
        sig = _fire(tmp_path, crontab_text=WIRED,
                    verdicts_text=_v("myjob", "OK", 60) + _v("selfverdicting", "OK", 60))
        assert "fix the job" not in sig.detail

    def test_a_real_wired_failure_keeps_its_original_wording(self, tmp_path):
        """The #78 leg's voice must not change underneath the operator."""
        verdicts = _v("myjob", "FAIL(1)", 360) + _v("myjob", "FAIL(1)", 60)
        sig = _fire(tmp_path, crontab_text=WIRED, verdicts_text=verdicts)
        assert sig.detail.startswith("Wired cron(s) unhealthy")
        assert "silence is the failure mode" in sig.detail

    def test_a_mixed_finding_leads_with_the_unhealthy_cron(self, tmp_path):
        verdicts = (_v("myjob", "FAIL(1)", 360) + _v("myjob", "FAIL(1)", 60)
                    + _v("selfverdicting", "OK", 60))
        sig = _fire(tmp_path, crontab_text=WIRED, verdicts_text=verdicts)
        assert sig.detail.startswith("Wired cron(s) unhealthy")
        assert "nothing judges" in sig.detail
