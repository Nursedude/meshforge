"""utils.wan_autotrace — trace when the ladder goes red, and say why not (2026-09-06).

Born from the operator's rule: *a tool that's silent has no diagnostic meaning
to a user.* The ladder had logged FAIL every ten minutes for seven hours and
nothing had localized it, because localizing was a thing somebody had to know
to do by hand.

The throttle is the part worth pinning: a trace costs ~2 minutes of probes, the
ladder ticks every 10 minutes, and that event ran 7 hours — naive wiring fires
42 traces and teaches nobody anything after the first.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from utils import wan_autotrace as wa  # noqa: E402


def _last(ts, cause="transit"):
    return {"generated_at": ts, "trigger": {"cause": cause}}


class TestShouldAutotrace:
    def test_a_green_ladder_never_traces(self):
        run, why = wa.should_autotrace("ok", "clean", None, now=1000.0)
        assert not run and "nothing to localize" in why

    def test_first_red_tick_traces(self):
        run, why = wa.should_autotrace("fail", "transit", None, now=1000.0)
        assert run and "no previous trace" in why

    def test_lan_loss_is_not_traced_and_says_why(self):
        """The ladder has already named the first hop; walking past it spends
        probes to learn nothing."""
        run, why = wa.should_autotrace("fail", "lan", None, now=1000.0)
        assert not run and "already names it" in why

    def test_inside_the_cooldown_it_skips_with_a_countdown(self):
        run, why = wa.should_autotrace("fail", "transit", _last(1000.0), now=2200.0,
                                       cooldown_s=3600.0)
        assert not run
        assert "traced 20 min ago" in why and "next after 40 min" in why

    def test_past_the_cooldown_it_traces_again(self):
        run, why = wa.should_autotrace("fail", "transit", _last(1000.0), now=5000.0,
                                       cooldown_s=3600.0)
        assert run and "cooldown" in why

    def test_a_changed_cause_traces_immediately_inside_the_cooldown(self):
        """A cause change is new information — re-tracing is how you learn what
        moved, and waiting out a cooldown would hide it."""
        run, why = wa.should_autotrace("fail", "edge", _last(1000.0, cause="transit"),
                                       now=1100.0, cooldown_s=3600.0)
        assert run and "transit -> edge" in why

    def test_a_future_stamped_trace_is_retraced_not_trusted(self):
        """RTC-less Pis step their clocks; a future stamp is not evidence."""
        run, why = wa.should_autotrace("fail", "transit", _last(9000.0), now=1000.0)
        assert run and "future" in why

    def test_concern_also_traces(self):
        run, _ = wa.should_autotrace("concern", "transit", None, now=1000.0)
        assert run

    def test_the_skip_reason_is_always_populated(self):
        """A silent skip is the same defect one level down."""
        for status, cause in (("ok", "clean"), ("fail", "lan"), ("fail", "bogus")):
            run, why = wa.should_autotrace(status, cause, None, now=1.0)
            assert not run and why.strip()


class TestPickTargets:
    LADDER = {
        "fail_pct": 5.0,
        "rungs": [
            {"rung": "lan", "host": "10.0.0.1", "loss_pct": 0.0},
            {"rung": "far", "host": "lossy.example", "loss_pct": 35.0},
            {"rung": "far", "host": "middling.example", "loss_pct": 20.0},
            {"rung": "far", "host": "clean.example", "loss_pct": 0.0},
        ],
    }

    def test_it_traces_the_worst_far_target_against_the_cleanest(self):
        assert wa.pick_targets(self.LADDER) == ("lossy.example", "clean.example")

    def test_no_clean_far_target_means_no_control(self):
        doc = {"fail_pct": 5.0, "rungs": [
            {"rung": "far", "host": "a", "loss_pct": 30.0},
            {"rung": "far", "host": "b", "loss_pct": 20.0}]}
        assert wa.pick_targets(doc) == ("a", None)

    def test_an_all_clean_ladder_has_nothing_to_trace(self):
        doc = {"fail_pct": 5.0, "rungs": [{"rung": "far", "host": "a", "loss_pct": 0.0}]}
        assert wa.pick_targets(doc) == (None, None)

    def test_unmeasured_far_rungs_are_not_chosen_as_targets(self):
        doc = {"fail_pct": 5.0, "rungs": [
            {"rung": "far", "host": "unmeasured", "loss_pct": None},
            {"rung": "far", "host": "lossy", "loss_pct": 30.0}]}
        assert wa.pick_targets(doc)[0] == "lossy"

    def test_a_ladder_with_no_far_rungs_at_all(self):
        assert wa.pick_targets({"rungs": []}) == (None, None)


class TestState:
    def test_round_trip_and_summary(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        from utils import path_trace as pt
        res = pt.TraceResult(target="lossy.example", addr="203.0.113.9",
                             finding=pt.Finding("localized", "loss begins at X",
                                                confidence="verified"))
        state = wa.build_trace_state({"status": "fail", "cause": "transit",
                                      "message": "m", "generated_at": 5.0},
                                     [res], ["cmp line"], "because", now=100.0)
        assert state["status"] == "localized"
        assert "LOCALIZED" in state["summary"] and "verified" in state["summary"]
        assert state["trigger"]["cause"] == "transit"
        wa.write_trace_state(state)
        back = wa.read_trace_state()
        assert back["summary"] == state["summary"]

    def test_a_missing_state_file_reads_as_none_not_an_exception(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        assert wa.read_trace_state() is None

    def test_a_corrupt_state_file_reads_as_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        p = wa.trace_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json", encoding="utf-8")
        assert wa.read_trace_state() is None

    def test_summarize_of_nothing_is_unknown_not_clean(self):
        assert wa.summarize([])[0] == "unknown"
