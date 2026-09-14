"""CONCERN vs FAIL: the cron class splits by loudness (2026-09-14).

``cron_verdict_stale`` began paging on 2026-09-14 because a watchdog-less box
(lehua) failed an hourly cron 26 consecutive times and the only thing that knew
was a web page nobody had open. The same day the operator named the flaw in
paging the WHOLE class: ``fleet_registry_sync`` reports CONCERN *by design*
whenever it self-heals a drift — it never reads OK — so the new page would
carry a permanently-noisy subject in the same channel as the 26-times-unseen
failure, and train the reader to ignore both. A page that always fires is a
page nobody reads.

So the class keeps ONE signal and gains a band: ``extra["verdict_band"]``, with
``fail`` paging and ``concern`` escalating quietly. Two properties matter more
than the split itself and are pinned here:

* SILENCE is never the quiet band. A CONCERN verdict self-heals; a cron that
  stopped running does not, and silence is the failure mode #78 exists for.
* An ABSENT band reads ``fail``. The watchdog writes the band and the rules read
  it, and they roll per box — so between the two deploys the band key is missing,
  and an extras equality filter on a key nobody writes matches NO rule. Absent
  must therefore mean LOUD, or the split would silence the class mid-rollout
  (honest_failure_modes #4: a reader/writer pair wires together or fails
  together).
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from mini_dudeai.candidate import (  # noqa: E402
    WELL_KNOWN_EXTRAS_KEYS, seed_rules_path,
)
from mini_dudeai.engine import _match_rule  # noqa: E402
from mini_dudeai.presets.meshforge_fleet import _watchdog_extractor  # noqa: E402
from mini_dudeai.sources.base import Condition  # noqa: E402
from utils.watchdog_probe_core import (  # noqa: E402
    CRON_VERDICT_BAND_CONCERN, CRON_VERDICT_BAND_FAIL, cron_verdict_band,
    reset_dispositions,
)
from utils.watchdog_probes import probe_cron_verdict_stale  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = 2_000_000_000.0
WIRED = ("*/5 * * * * /opt/job.py >/dev/null 2>&1; "
         "/opt/meshforge/scripts/cron_verdict.sh myjob $?\n")


def _v(name, status, age_s):
    ts = datetime.datetime.fromtimestamp(NOW - age_s, datetime.timezone.utc)
    return "%s %s %s\n" % (ts.strftime("%Y-%m-%dT%H:%M:%SZ"), name, status)


def _fire(tmp_path, **kw):
    """Two ticks (the probe debounces); return the second result."""
    reset_dispositions()
    sp = str(tmp_path / "cron_debounce.json")
    probe_cron_verdict_stale(state_path=sp, now=NOW, **kw)
    return probe_cron_verdict_stale(state_path=sp, now=NOW, **kw)


class TestTheBandPredicate:
    """Pure, and the single source both legs call."""

    def test_concern_only_is_the_quiet_band(self):
        assert cron_verdict_band(["registry_sync(CONCERN)"], [], []) == \
            CRON_VERDICT_BAND_CONCERN

    def test_one_fail_among_concerns_is_loud(self):
        assert cron_verdict_band(
            ["registry_sync(CONCERN)", "hosts_drift(FAIL)"], [], []) == \
            CRON_VERDICT_BAND_FAIL

    def test_silence_is_loud_even_beside_a_concern(self):
        """THE property. A CONCERN self-heals; a dead cron does not, and
        silence is the failure mode this class exists for (#78)."""
        assert cron_verdict_band(
            ["registry_sync(CONCERN)"], ["backup(never)"], []) == \
            CRON_VERDICT_BAND_FAIL

    def test_an_unwired_fail_is_loud(self):
        assert cron_verdict_band([], [], ["boot_survival(FAIL)"]) == \
            CRON_VERDICT_BAND_FAIL

    def test_an_unparsable_entry_is_loud(self):
        """An unreadable finding is not a quiet one."""
        assert cron_verdict_band(["mystery"], [], []) == CRON_VERDICT_BAND_FAIL


class TestTheProbeEmitsTheBand:

    def test_a_concern_only_finding_is_banded_concern(self, tmp_path):
        """The measured shape: fleet_registry_sync CONCERN, nothing else.

        TWO lines, because a first failure on a fast cron is held UNCONFIRMED
        until the next run — a fixture with one line would return None here and
        pass whatever the band did."""
        sig = _fire(tmp_path, crontab_text=WIRED,
                    verdicts_text=(_v("myjob", "CONCERN", 3600)
                                   + _v("myjob", "CONCERN", 60)))
        assert sig is not None
        assert sig.extra["verdict_band"] == CRON_VERDICT_BAND_CONCERN

    def test_a_failing_cron_is_banded_fail(self, tmp_path):
        sig = _fire(tmp_path, crontab_text=WIRED,
                    verdicts_text=_v("myjob", "FAIL", 60) + _v("myjob", "FAIL", 30))
        assert sig is not None
        assert sig.extra["verdict_band"] == CRON_VERDICT_BAND_FAIL

    def test_a_silent_cron_is_banded_fail(self, tmp_path):
        """lehua's shape — and the reason the page exists at all."""
        sig = _fire(tmp_path, crontab_text=WIRED, verdicts_text="")
        assert sig is not None
        assert sig.extra["stale"], sig.detail
        assert sig.extra["verdict_band"] == CRON_VERDICT_BAND_FAIL

    def test_severity_is_untouched_by_the_band(self, tmp_path):
        """The band rides in `extra`, NOT in severity: /fleet and the worst-wins
        rollups read severity, and a CONCERN finding is still a real degraded
        observation there. It is only the PAGE it should not earn."""
        sig = _fire(tmp_path, crontab_text=WIRED,
                    verdicts_text=(_v("myjob", "CONCERN", 3600)
                                   + _v("myjob", "CONCERN", 60)))
        assert sig.severity == "degraded"


class TestTheExtractorFailsLoud:
    """mini's projection of watchdog.json — where a missing band is decided."""

    def _one(self, sig):
        return _watchdog_extractor({"signals": [sig]})[0]

    def test_the_band_passes_through(self):
        item = self._one({"class": "cron_verdict_stale", "subject": "cron",
                          "extra": {"verdict_band": "concern"}})
        assert item["verdict_band"] == "concern"

    def test_an_absent_band_reads_fail(self):
        """A box whose watchdog has not rolled yet keeps paging. If this ever
        flips to 'concern' or to absent, the class goes SILENT on every
        half-rolled box and nothing else in the suite would notice."""
        item = self._one({"class": "cron_verdict_stale", "subject": "cron"})
        assert item["verdict_band"] == CRON_VERDICT_BAND_FAIL

    def test_a_watchdog_with_no_extra_at_all_reads_fail(self):
        item = self._one({"class": "cron_verdict_stale", "subject": "cron",
                          "extra": {}})
        assert item["verdict_band"] == CRON_VERDICT_BAND_FAIL

    def test_the_two_band_constants_are_pinned_together(self):
        """mini must not import `utils` at module scope (it ships standalone),
        so the band vocabulary exists twice — once in the probe core, once in
        the preset. That is the repo's declared fallback for a constant that
        cannot be shared by import: TEST-PIN it, so the two cannot drift
        (honest_failure_modes #5)."""
        from mini_dudeai.presets.meshforge_fleet import (
            _BAND_FAIL, _BANDED_CLASSES)
        assert _BAND_FAIL == CRON_VERDICT_BAND_FAIL
        assert _BANDED_CLASSES == {"cron_verdict_stale"}

    def test_other_classes_carry_no_band(self):
        """Kept narrow: a band key on a condition no rule bands is dead weight,
        and every extras key is live matching vocabulary."""
        item = self._one({"class": "service_inactive", "subject": "rnsd",
                          "extra": {"verdict_band": "fail"}})
        assert "verdict_band" not in item


class TestTheSeededRulesSplit:
    """The rules are the consumer of record — match them the way the engine
    does, not by reading the JSON and believing it."""

    def _rules(self, seed):
        with open(seed_rules_path(REPO, seed), encoding="utf-8") as fh:
            return {r["id"]: r for r in json.load(fh)["rules"]}

    def _cond(self, band):
        extras = {"class": "cron_verdict_stale"}
        if band is not None:
            extras["verdict_band"] = band
        return Condition(kind="signal_class", subject="cron", extras=extras)

    def test_both_seeds_carry_both_halves(self):
        for seed in ("fleet_gateway", "federator"):
            rules = self._rules(seed)
            assert rules["cron_verdict_stale_any"]["action"]["kind"] == "ntfy"
            assert rules["cron_verdict_concern_any"]["action"]["kind"] == \
                "propose_escalation"

    def test_a_concern_finding_does_not_reach_the_page(self):
        for seed in ("fleet_gateway", "federator"):
            rules = self._rules(seed)
            cond = self._cond("concern")
            assert not _match_rule(rules["cron_verdict_stale_any"], cond)
            assert _match_rule(rules["cron_verdict_concern_any"], cond)

    def test_a_fail_finding_pages_and_does_not_double_escalate(self):
        for seed in ("fleet_gateway", "federator"):
            rules = self._rules(seed)
            cond = self._cond("fail")
            assert _match_rule(rules["cron_verdict_stale_any"], cond)
            assert not _match_rule(rules["cron_verdict_concern_any"], cond)

    def test_the_band_key_is_well_known_to_the_authoring_lint(self):
        """Otherwise every promote of these seeds warns that the rule 'will
        never match anything' — the lint's silent-death warning, on a rule that
        is doing exactly what it should."""
        assert "verdict_band" in WELL_KNOWN_EXTRAS_KEYS
