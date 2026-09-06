"""mini_dudeai.sources.wan_path — the ladder, with its localization (2026-09-06).

The reason this source exists is itself the lesson: the WAN ladder is a
SELF-VERDICTING cron (it writes its own cron_verdict line instead of carrying a
``cron_verdict.sh <name>`` token), so ``probe_cron_verdict_stale`` — which
judges only WIRED crons — treated it as an orphan and skipped it. The
instrument logged FAIL every ten minutes for seven hours and mini's brief never
mentioned it once. A tool that is silent has no diagnostic meaning to a user.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from mini_dudeai.sources.wan_path import WanPathSource  # noqa: E402


def _write(p, doc):
    p.write_text(json.dumps(doc), encoding="utf-8")
    return str(p)


def _ladder(tmp_path, **over):
    import time
    doc = {"generated_at": time.time(), "status": "fail", "cause": "transit",
           "message": "loss beyond the ISP: cloud-vps 35%/216ms",
           "worst_far_loss_pct": 35.0}
    doc.update(over)
    return _write(tmp_path / "wan_path.json", doc)


class TestInertByConstruction:
    def test_no_ladder_file_emits_nothing(self, tmp_path):
        """Absent by design is INERT — never a condition, never a clean claim."""
        src = WanPathSource(ladder_path=str(tmp_path / "nope.json"))
        assert list(src.collect()) == []

    def test_a_corrupt_ladder_file_emits_nothing_rather_than_guessing(self, tmp_path):
        p = tmp_path / "wan_path.json"
        p.write_text("{not json", encoding="utf-8")
        assert list(WanPathSource(ladder_path=str(p)).collect()) == []

    def test_a_green_ladder_is_quiet(self, tmp_path):
        path = _ladder(tmp_path, status="ok", cause="clean")
        assert list(WanPathSource(ladder_path=path).collect()) == []


class TestDegraded:
    def test_a_red_ladder_emits_the_cause_as_subject(self, tmp_path):
        conds = list(WanPathSource(ladder_path=_ladder(tmp_path)).collect())
        assert len(conds) == 1
        c = conds[0]
        assert c.kind == "wan_path_degraded" and c.subject == "transit"
        assert "loss beyond the ISP" in c.detail
        assert c.extras["worst_far_loss_pct"] == 35.0

    def test_unknown_status_also_surfaces(self, tmp_path):
        """Unmeasurable is not healthy — it gets a condition too."""
        conds = list(WanPathSource(ladder_path=_ladder(tmp_path, status="unknown")).collect())
        assert len(conds) == 1

    def test_without_a_trace_it_says_how_to_get_one(self, tmp_path):
        c = list(WanPathSource(ladder_path=_ladder(tmp_path),
                               trace_path=str(tmp_path / "absent.json")).collect())[0]
        assert "--auto-trace" in c.detail

    def test_a_trace_localization_rides_along_in_the_same_condition(self, tmp_path):
        """Two lines saying 'the internet is lossy' and 'here is where' are one
        finding; splitting them makes the reader do the join at 3am."""
        import time
        tp = _write(tmp_path / "wan_trace.json",
                    {"generated_at": time.time(),
                     "summary": "vps: LOCALIZED [verified] loss begins at 203.0.113.9"})
        c = list(WanPathSource(ladder_path=_ladder(tmp_path), trace_path=tp).collect())[0]
        assert "latest trace" in c.detail and "loss begins at 203.0.113.9" in c.detail

    def test_a_trace_older_than_the_ladder_window_is_marked_historical(self, tmp_path):
        """A stale trace describes a DIFFERENT moment and must not read as now."""
        import time
        now = time.time()
        tp = _write(tmp_path / "wan_trace.json",
                    {"generated_at": now - 10000, "summary": "vps: LOCALIZED [verified] x"})
        lp = _ladder(tmp_path, generated_at=now)
        c = list(WanPathSource(ladder_path=lp, trace_path=tp).collect())[0]
        assert "HISTORICAL" in c.detail

    def test_a_trace_that_produced_no_finding_says_why(self, tmp_path):
        tp = _write(tmp_path / "wan_trace.json",
                    {"generated_at": 1.0, "reason": "no far target above the floor"})
        c = list(WanPathSource(ladder_path=_ladder(tmp_path), trace_path=tp).collect())[0]
        assert "no far target above the floor" in c.detail


class TestStale:
    def test_a_stale_ladder_is_its_own_condition_not_a_verdict(self, tmp_path):
        """A frozen green never complains — staleness rides its own axis."""
        import time
        lp = _ladder(tmp_path, generated_at=time.time() - 9999, status="ok", cause="clean")
        conds = list(WanPathSource(ladder_path=lp).collect())
        assert len(conds) == 1
        assert conds[0].kind == "wan_path_stale"
        assert "NOT current" in conds[0].detail
        assert conds[0].extras["last_status"] == "ok"

    def test_a_stale_ladder_does_not_also_emit_degraded(self, tmp_path):
        import time
        lp = _ladder(tmp_path, generated_at=time.time() - 9999)
        kinds = [c.kind for c in WanPathSource(ladder_path=lp).collect()]
        assert kinds == ["wan_path_stale"]
