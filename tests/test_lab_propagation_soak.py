"""Tests for src/lab/lxmf_propagation_soak.py — the store-and-forward canary.

Verdict logic is deliberately pure (no RNS/LXMF import at module scope) so CI's
minimal-deps run covers it. The live round-trip is exercised on the fleet, not
here.
"""

import json
import os
import sys

sys.path.insert(0, 'src')

import pytest  # noqa: E402

import lab.lxmf_propagation_soak as soak_mod  # noqa: E402
from lab.lxmf_propagation_soak import (  # noqa: E402
    DEFAULT_OK_RATIO_THRESHOLD,
    MARKER,
    RoundResult,
    _resolve_propagation_node,
    build_report,
    main,
    worst_round,
)


class TestCheckConfigured:
    """The fire script's INERT gate (2026-07-21 review F3) — no RNS, no
    state writes, three honest exit codes."""

    def test_unconfigured_exits_3(self, monkeypatch):
        monkeypatch.setattr(soak_mod, "_resolve_propagation_node",
                            lambda e: None)
        assert main(["--check-configured"]) == 3

    def test_configured_exits_0(self, monkeypatch):
        monkeypatch.setattr(soak_mod, "_resolve_propagation_node",
                            lambda e: "3968a2eeac25e2e7a7961f25842d3d85")
        assert main(["--check-configured"]) == 0

    def test_malformed_hash_exits_4(self, monkeypatch):
        """A typo'd hash is a REAL operator error — loud, never a silent
        perpetual indeterminate (it used to crash both children on
        bytes.fromhex)."""
        monkeypatch.setattr(soak_mod, "_resolve_propagation_node",
                            lambda e: "not-a-hash")
        assert main(["--check-configured"]) == 4


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
        honest_failure_modes #1 in arithmetic form. It is INDETERMINATE (None),
        never a pass — and never a False either, since nothing was tested.
        """
        r = build_report([], propagation_node="abc",
                         started_at_iso="s", finished_at_iso="f")
        assert r.pass_envelope is None
        assert r.pass_envelope is not True
        assert r.ok_ratio == 0.0

    def test_threshold_allows_partial_credit_when_asked(self):
        r = build_report([_ok(1), _fail(2)], propagation_node="abc",
                         started_at_iso="s", finished_at_iso="f",
                         ok_ratio_threshold=0.5)
        assert r.pass_envelope is True

    def test_default_threshold_demands_every_round(self):
        assert DEFAULT_OK_RATIO_THRESHOLD == 1.0


class TestIndeterminateVsFailure:
    """A send-stamp timeout is NOT a store-and-forward failure.

    Only a PULL failure (the node had the message and did not return it) proves
    store-and-forward is broken. A SEND failure means the message never reached
    the node — most often the sender was too slow generating the propagation
    stamp (CPU-bound PoW, high variance). Mapping "couldn't test" to "test
    failed" is honest_failure_modes #1; live-caught on moc3 2026-07-21 where a
    slow stamp produced a false CONCERN against a healthy node.
    """

    def _send_fail(self, seq=1):
        return RoundResult(seq=seq, ok=False, stage="send",
                           reason="send phase exceeded 330s")

    def test_send_stall_is_none_not_false(self):
        """The core fix: an all-send-stall run is indeterminate, never a fail."""
        r = build_report([self._send_fail()], propagation_node="n",
                         started_at_iso="s", finished_at_iso="f")
        assert r.pass_envelope is None
        assert r.total_indeterminate == 1

    def test_pull_failure_is_still_false(self):
        """A real store-and-forward failure must still page."""
        r = build_report([_fail(1, stage="pull")], propagation_node="n",
                         started_at_iso="s", finished_at_iso="f")
        assert r.pass_envelope is False

    def test_ok_round_beside_a_send_stall_still_passes(self):
        """The stall round doesn't count against the ratio — the node was
        exercised once and delivered."""
        r = build_report([_ok(1), self._send_fail(2)], propagation_node="n",
                         started_at_iso="s", finished_at_iso="f")
        assert r.pass_envelope is True
        assert r.total_indeterminate == 1

    def test_pull_failure_beside_a_send_stall_still_fails(self):
        r = build_report([_fail(1, stage="pull"), self._send_fail(2)],
                         propagation_node="n",
                         started_at_iso="s", finished_at_iso="f")
        assert r.pass_envelope is False

    def test_none_survives_json_round_trip(self):
        import json as _j
        d = _j.loads(_j.dumps(build_report(
            [self._send_fail()], propagation_node="n",
            started_at_iso="s", finished_at_iso="f").to_dict()))
        assert d["pass_envelope"] is None
        assert d["total_indeterminate"] == 1


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

    def test_prefers_definitive_failure_over_indeterminate_stall(self):
        """2026-07-21 review (F7): on a mixed failed envelope the first-match
        rule reported the send-stall — the one round that is by design NOT a
        failure — as THE failure."""
        rounds = [_fail(1, stage="send", reason="send timeout in state ..."),
                  _fail(2, stage="pull", reason="sync completed, not returned")]
        summary = worst_round([r.__dict__ for r in rounds])
        assert "round 2" in summary and "pull" in summary

    def test_all_indeterminate_still_summarised(self):
        rounds = [_fail(1, stage="send", reason="stall"),
                  _fail(2, stage="pull_link", reason="no path")]
        assert "round 1" in worst_round([r.__dict__ for r in rounds])


class TestPullLinkTriState:
    """2026-07-21 review (F1): a pull the transport never completed is
    indeterminate — only a COMPLETED sync that lacked our message is a
    store-and-forward failure."""

    def test_pull_link_failure_is_none_not_false(self):
        rep = build_report([_fail(1, stage="pull_link", reason="no path")],
                           propagation_node="x", started_at_iso="s",
                           finished_at_iso="f")
        assert rep.pass_envelope is None
        assert rep.total_indeterminate == 1

    def test_pull_link_beside_an_ok_round_still_passes(self):
        rep = build_report([_ok(1), _fail(2, stage="pull_link", reason="lf")],
                           propagation_node="x", started_at_iso="s",
                           finished_at_iso="f")
        assert rep.pass_envelope is True

    def test_completed_pull_failure_is_still_false(self):
        rep = build_report([_fail(1, stage="pull", reason="not returned")],
                           propagation_node="x", started_at_iso="s",
                           finished_at_iso="f")
        assert rep.pass_envelope is False

    def test_ok_ratio_uses_the_verdicts_denominator(self):
        """The envelope must not publish a ratio/threshold pair that
        contradicts its own pass_envelope (review): indeterminate rounds are
        out of BOTH denominators."""
        rep = build_report([_ok(1), _fail(2, stage="send", reason="stall")],
                           propagation_node="x", started_at_iso="s",
                           finished_at_iso="f")
        assert rep.pass_envelope is True
        assert rep.ok_ratio == 1.0


class TestIdentityPathIsUniquePerFire:
    """A fixed identity filename is a collision, not a convention (hfm #8).

    Live-caught 2026-07-21 on moc3: `enable --now` fired the service while a
    manual fire was running; both used one fixed-name identity file, so the
    second fire's send overwrote it and the first fire's pull loaded the wrong
    identity and retrieved nothing — a false CONCERN against a healthy node.
    """

    def test_distinct_tokens_give_distinct_paths(self, tmp_path, monkeypatch):
        from lab import lxmf_propagation_soak as m
        monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)
        a = m.default_identity_path("111_1")
        b = m.default_identity_path("222_1")
        assert a != b
        assert a.endswith("111_1")
        assert b.endswith("222_1")

    def test_token_is_filename_sanitised(self, tmp_path, monkeypatch):
        """A token can come from a PID string; never let it escape the dir."""
        from lab import lxmf_propagation_soak as m
        monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)
        path = m.default_identity_path("../../etc/passwd")
        assert "/etc/passwd" not in path
        assert os.path.dirname(path) == str(
            tmp_path / ".local" / "state" / "meshforge" / "propagation_soak")

    def test_empty_token_still_yields_a_path(self, tmp_path, monkeypatch):
        from lab import lxmf_propagation_soak as m
        monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)
        assert m.default_identity_path("").endswith("round")


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
