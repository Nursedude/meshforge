"""Tests for src/lab/lxmf_propagation_soak.py — the store-and-forward canary.

Verdict logic is deliberately pure (no RNS/LXMF import at module scope) so CI's
minimal-deps run covers it. The live round-trip is exercised on the fleet, not
here.
"""

import json
import sys

sys.path.insert(0, 'src')

import pytest  # noqa: E402

from lab.lxmf_propagation_soak import (  # noqa: E402
    DEFAULT_OK_RATIO_THRESHOLD,
    MARKER,
    RoundResult,
    _resolve_propagation_node,
    build_report,
    worst_round,
)


def _ok(seq=1, total=12.0):
    return RoundResult(seq=seq, ok=True, store_latency_s=4.0,
                       retrieve_latency_s=8.0, total_latency_s=total)


def _fail(seq=1, stage="pull", reason="stored but not retrieved"):
    return RoundResult(seq=seq, ok=False, stage=stage, reason=reason)


class TestEnvelopeVerdict:
    def test_all_rounds_ok_passes(self):
        r = build_report([_ok(1), _ok(2)], propagation_node="abc",
                         started_at_iso="s", finished_at_iso="f")
        assert r.pass_envelope is True
        assert r.ok_ratio == 1.0

    def test_a_failed_round_fails_the_envelope(self):
        r = build_report([_ok(1), _fail(2)], propagation_node="abc",
                         started_at_iso="s", finished_at_iso="f")
        assert r.pass_envelope is False

    def test_zero_rounds_never_passes(self):
        """A run that produced nothing has proven nothing.

        An empty result set trivially satisfying a ratio test is
        honest_failure_modes #1 in arithmetic form — the healthiest-looking
        possible verdict from the least informative possible run.
        """
        r = build_report([], propagation_node="abc",
                         started_at_iso="s", finished_at_iso="f")
        assert r.pass_envelope is False
        assert r.ok_ratio == 0.0

    def test_threshold_allows_partial_credit_when_asked(self):
        r = build_report([_ok(1), _fail(2)], propagation_node="abc",
                         started_at_iso="s", finished_at_iso="f",
                         ok_ratio_threshold=0.5)
        assert r.pass_envelope is True

    def test_default_threshold_demands_every_round(self):
        assert DEFAULT_OK_RATIO_THRESHOLD == 1.0


class TestEnvelopeShape:
    def test_dict_is_json_serialisable_and_carries_probe_keys(self):
        r = build_report([_ok(1)], propagation_node="3968a2ee",
                         started_at_iso="s", finished_at_iso="f")
        d = json.loads(json.dumps(r.to_dict()))   # must survive a round-trip

        for key in ("pass_envelope", "ok_ratio", "ok_ratio_threshold",
                    "total_ok", "total_samples", "propagation_node",
                    "round_results", "marker"):
            assert key in d, f"probe/rollup key {key} missing from envelope"
        assert d["marker"] == MARKER

    def test_latency_reported_when_a_round_succeeded(self):
        r = build_report([_ok(1, total=10.0), _ok(2, total=20.0)],
                         propagation_node="abc",
                         started_at_iso="s", finished_at_iso="f")
        lat = r.to_dict()["latency_s"]
        assert lat["min"] == 10.0
        assert lat["max"] == 20.0

    def test_latency_is_null_not_zero_when_everything_failed(self):
        """Zero would read as 'instant' — the best possible number for the
        worst possible run. Absence must look like absence."""
        r = build_report([_fail(1)], propagation_node="abc",
                         started_at_iso="s", finished_at_iso="f")
        assert r.to_dict()["latency_s"] is None


class TestWorstRound:
    def test_reports_the_first_failure(self):
        rounds = [_ok(1), _fail(2, stage="pull", reason="not retrieved")]
        summary = worst_round([r.__dict__ for r in rounds])
        assert "round 2" in summary
        assert "pull" in summary

    def test_none_when_nothing_failed(self):
        assert worst_round([_ok(1).__dict__]) is None

    @pytest.mark.parametrize("bad", [None, "nope", 42, [None], [{"ok": None}]])
    def test_never_raises_on_misshaped_input(self, bad):
        """A summary helper runs inside a probe — it must not crash it."""
        assert worst_round(bad) is None

    def test_accepts_dataclass_instances_too(self):
        assert "round 3" in worst_round([_fail(3)])


class TestPropagationNodeResolution:
    def test_explicit_wins(self):
        assert _resolve_propagation_node("ABCdef") == "abcdef"

    def test_reads_gateway_json(self, tmp_path, monkeypatch):
        cfg = tmp_path / ".config" / "meshforge"
        cfg.mkdir(parents=True)
        (cfg / "gateway.json").write_text(
            json.dumps({"rns": {"propagation_node": "3968A2EE"}}))
        monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)

        assert _resolve_propagation_node(None) == "3968a2ee"

    def test_unconfigured_is_none_not_a_failure(self, tmp_path, monkeypatch):
        """Empty propagation_node means 'nothing to exercise here'."""
        cfg = tmp_path / ".config" / "meshforge"
        cfg.mkdir(parents=True)
        (cfg / "gateway.json").write_text(json.dumps({"rns": {"propagation_node": ""}}))
        monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)

        assert _resolve_propagation_node(None) is None

    def test_missing_config_is_none_not_an_exception(self, tmp_path, monkeypatch):
        monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)
        assert _resolve_propagation_node(None) is None

    def test_garbage_config_is_none_not_an_exception(self, tmp_path, monkeypatch):
        cfg = tmp_path / ".config" / "meshforge"
        cfg.mkdir(parents=True)
        (cfg / "gateway.json").write_text("{not json")
        monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)

        assert _resolve_propagation_node(None) is None
