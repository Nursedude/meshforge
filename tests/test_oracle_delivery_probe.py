"""Tests for probe_oracle_delivery_degraded (mesh-oracle health leg, 2026-06-22).

The read-only "ask dude-AI over the mesh" responder (``src/oracle``) appends one
JSONL audit record per handled query to ``~/.local/share/meshforge/mesh_oracle_log.jsonl``
(``oracle.oracle_log_path``; rotates at 2 MB). This probe watches the v1
DELIVERY-RATE leg over a recent ``ts`` window:

    rate = delivered / (delivered + send_errors)

with intentional declines (cooldown / not_allowlisted) and benign non-deliveries
(reason-less ``delivered:false``) EXCLUDED from the failure set (and surfaced).
Red-first: each honest-failure trap (declines counted, benign counted, small-N
fire, silence fire, clock-forged window) gets a test that would FAIL the naive
implementation.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.watchdog_probe_core import (  # noqa: E402
    SIGNAL_CLASSES,
    collect_dispositions,
    reset_dispositions,
)
from utils.watchdog_probes import probe_oracle_delivery_degraded  # noqa: E402
from utils.watchdog_probes_gateway import (  # noqa: E402
    _classify_oracle_record,
    _read_oracle_recent,
)

NOW = 1_800_000_000.0


def _write_log(path, records):
    """Write a list of audit records as JSONL."""
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def _rec(delivered, *, reason=None, transport="rns", age_s=60.0):
    """One audit record at ts = NOW - age_s."""
    r = {"ts": NOW - age_s, "transport": transport, "from": "abcd1234",
         "query": "status", "intent": "status", "answer": "ok",
         "delivered": delivered}
    if reason is not None:
        r["reason"] = reason
    return r


def _probe(log, tmp_path, **kw):
    """Run the probe against a tmp log + tmp debounce path, fixed now."""
    kw.setdefault("now", NOW)
    kw.setdefault("debounce_path", str(tmp_path / "debounce.json"))
    return probe_oracle_delivery_degraded(log_path=str(log), **kw)


# ── enum + wiring ────────────────────────────────────────────────────

def test_signal_class_registered():
    assert "oracle_delivery_degraded" in SIGNAL_CLASSES


# ── classification unit (the THE trap lives here) ────────────────────

def test_classify_buckets():
    assert _classify_oracle_record({"delivered": True}) == "delivered"
    assert _classify_oracle_record(
        {"delivered": False, "reason": "send_error: boom"}) == "send_error"
    assert _classify_oracle_record(
        {"delivered": False, "reason": "cooldown"}) == "decline"
    assert _classify_oracle_record(
        {"delivered": False, "reason": "not_allowlisted"}) == "decline"
    # reason-less non-delivery = benign (RNS no-path / MeshCore restart race)
    assert _classify_oracle_record({"delivered": False}) == "benign"
    # a delivered record is success even if some odd reason were present
    assert _classify_oracle_record(
        {"delivered": True, "reason": "whatever"}) == "delivered"
    # misshaped → skipped
    assert _classify_oracle_record("nope") is None
    assert _classify_oracle_record({"delivered": None}) is None


def test_reason_vocabulary_lockstep_nothing_real_reads_benign():
    """2026-07-21 review (C1): RnsSendResult's named reasons must never be
    laundered into the benign bucket — circuit_open/not_connected/
    no_lxmf_source are infrastructure failures (they must DEGRADE the
    delivery rate), broadcast_unsupported is a structural decline. Only
    no_path stays benign (recipient genuinely unreachable, not our fault).
    Pinned against the producer's own closed vocabulary so a reason added
    on one side breaks this test until the classifier learns it
    (honest_failure_modes #5/#7)."""
    from gateway.bridge_send_mixin import RNS_SEND_REASONS
    expected = {
        "": None,                # success carries no reason; not a bucket call
        "no_path": "benign",
        "circuit_open": "send_error",
        "not_connected": "send_error",
        "no_lxmf_source": "send_error",
        "broadcast_unsupported": "decline",
        "send_error": "send_error",
    }
    # both directions: every producer reason has a decided bucket, and the
    # test's map names no reason the producer retired
    assert set(expected) == set(RNS_SEND_REASONS)
    for reason, bucket in expected.items():
        if not reason:
            continue
        rec = {"delivered": False, "transport": "rns", "reason": reason}
        assert _classify_oracle_record(rec) == bucket, reason


# ── INERT self-guards ────────────────────────────────────────────────

def test_inert_when_log_absent(tmp_path):
    missing = tmp_path / "nope.jsonl"
    assert probe_oracle_delivery_degraded(
        log_path=str(missing), now=NOW,
        debounce_path=str(tmp_path / "d.json")) is None


def test_indeterminate_when_operator_unresolvable(monkeypatch, tmp_path):
    # log_path=None → resolves via _find_operator_user; force it None.
    # ⚠️ This case is INDETERMINATE, not inert — we cannot even locate the
    # log, so we know nothing about whether an oracle runs here. The test
    # was named "inert" for two months while asserting only `is None`,
    # which cannot tell the two apart (see TestOracleDispositions).
    import utils.fleet_test_runner as ftr
    monkeypatch.setattr(ftr, "_find_operator_user", lambda: None)
    assert probe_oracle_delivery_degraded(
        now=NOW, debounce_path=str(tmp_path / "d.json")) is None


def test_unreadable_log_holds_streak(tmp_path):
    # A directory exists() but can't be read as a file → parsed None → hold.
    dbp = tmp_path / "debounce.json"
    dbp.write_text(json.dumps({"streak": 1}))
    out = probe_oracle_delivery_degraded(
        log_path=str(tmp_path), now=NOW, debounce_path=str(dbp))
    assert out is None
    # streak HELD, not reset (unobservable ≠ healthy)
    assert json.loads(dbp.read_text())["streak"] == 1


# ── the rate math + exclusions ───────────────────────────────────────

def test_fires_below_threshold(tmp_path):
    log = tmp_path / "oracle.jsonl"
    recs = [_rec(True) for _ in range(3)] + [
        _rec(False, reason="send_error: boom") for _ in range(7)]
    _write_log(log, recs)
    sig = _probe(log, tmp_path, debounce_ticks=1)
    assert sig is not None
    assert sig.cls == "oracle_delivery_degraded"
    assert sig.severity == "degraded"
    # The sample is the LAST 8 confirmable (v2 count window), so the oldest of
    # the 3 deliveries falls out: 1 delivered + 7 send_error.
    assert sig.extra["confirmable"] == 8
    assert sig.extra["sample_n"] == 8
    assert sig.extra["delivered"] == 1
    assert sig.extra["send_errors"] == 7
    assert sig.extra["rate"] == pytest.approx(0.125, abs=0.01)


def test_healthy_above_threshold_no_fire(tmp_path):
    log = tmp_path / "oracle.jsonl"
    recs = [_rec(True) for _ in range(10)] + [
        _rec(False, reason="send_error: boom")]
    _write_log(log, recs)
    assert _probe(log, tmp_path, debounce_ticks=1) is None


def test_declines_do_not_count_as_failures(tmp_path):
    # 8 delivered, 0 send_error, but MANY declines — a naive
    # delivered/total would read ~0.17 and false-fire. The correct rate is 1.0.
    log = tmp_path / "oracle.jsonl"
    recs = ([_rec(True) for _ in range(8)]
            + [_rec(False, reason="cooldown") for _ in range(20)]
            + [_rec(False, reason="not_allowlisted") for _ in range(20)])
    _write_log(log, recs)
    assert _probe(log, tmp_path, debounce_ticks=1) is None


def test_benign_nondelivery_does_not_count_as_failure(tmp_path):
    # 9 delivered, 0 send_error, several reason-less false (RNS no-path).
    # Naive would read 9/13 ≈ 0.69 and fire; correct rate is 1.0.
    log = tmp_path / "oracle.jsonl"
    recs = [_rec(True) for _ in range(9)] + [_rec(False) for _ in range(4)]
    _write_log(log, recs)
    assert _probe(log, tmp_path, debounce_ticks=1) is None


def test_excluded_buckets_are_surfaced_on_fire(tmp_path):
    # A genuine degrade (send_error) WITH declines + benign present: the rate
    # ignores the excluded buckets but the COUNTS are surfaced (never hidden).
    log = tmp_path / "oracle.jsonl"
    recs = ([_rec(True) for _ in range(4)]
            + [_rec(False, reason="send_error: boom") for _ in range(6)]
            + [_rec(False, reason="cooldown") for _ in range(10)]
            + [_rec(False, reason="not_allowlisted") for _ in range(5)]
            + [_rec(False) for _ in range(3)])
    _write_log(log, recs)
    sig = _probe(log, tmp_path, debounce_ticks=1)
    assert sig is not None
    assert sig.extra["confirmable"] == 8             # last 8: 2 delivered + 6 send_error
    assert sig.extra["declines_excluded"] == 15      # 10 cooldown + 5 not_allowlisted
    assert sig.extra["benign_nondeliveries_excluded"] == 3
    assert sig.extra["rate"] == pytest.approx(0.25, abs=0.01)


def test_min_sample_guard_holds(tmp_path):
    # Below-threshold rate but only 3 confirmable EVER (< 8) → pass@small-N →
    # None. v2 counts over the log's lifetime, not per window, so this
    # resolves permanently once the oracle has answered 8 times.
    log = tmp_path / "oracle.jsonl"
    recs = [_rec(True)] + [_rec(False, reason="send_error: x") for _ in range(2)]
    _write_log(log, recs)
    assert _probe(log, tmp_path, debounce_ticks=1) is None


def test_silence_is_not_a_failure(tmp_path):
    # Empty log present (oracle enabled, nobody asked) → no confirmable → None.
    log = tmp_path / "oracle.jsonl"
    log.write_text("")
    assert _probe(log, tmp_path, debounce_ticks=1) is None


# ── ts windowing + forged clock (trap #5) ────────────────────────────

def test_old_failures_pushed_out_of_the_sample(tmp_path):
    # 10 old send_error then 10 fresh delivered. Under v1 the 6h TIME window
    # dropped the old ones; under v2's count window newer answers push them
    # out of the last-8 sample. Same verdict, different mechanism → None.
    log = tmp_path / "oracle.jsonl"
    recs = ([_rec(False, reason="send_error: old", age_s=10 * 3600)
             for _ in range(10)]
            + [_rec(True, age_s=60) for _ in range(10)])
    _write_log(log, recs)
    assert _probe(log, tmp_path, debounce_ticks=1) is None


def test_future_and_negative_ts_skipped(tmp_path):
    # Forged ts (far future, negative) must be skipped, not counted.
    log = tmp_path / "oracle.jsonl"
    bad = ([_rec(False, reason="send_error: f", age_s=-100000)  # far future
            for _ in range(20)]
           + [{"ts": -1, "delivered": False, "reason": "send_error: n"}
              for _ in range(20)])
    good = [_rec(True) for _ in range(10)]
    _write_log(log, bad + good)
    # Only the 10 good remain → confirmable 10, rate 1.0 → None.
    assert _probe(log, tmp_path, debounce_ticks=1) is None


def test_malformed_lines_skipped(tmp_path):
    log = tmp_path / "oracle.jsonl"
    with open(log, "w", encoding="utf-8") as fh:
        fh.write("not json at all\n")
        fh.write(json.dumps([1, 2, 3]) + "\n")        # non-dict
        fh.write(json.dumps({"no_ts": 1}) + "\n")     # missing ts
        for r in [_rec(True) for _ in range(3)]:
            fh.write(json.dumps(r) + "\n")
        for r in [_rec(False, reason="send_error: x") for _ in range(7)]:
            fh.write(json.dumps(r) + "\n")
    sig = _probe(log, tmp_path, debounce_ticks=1)
    assert sig is not None
    assert sig.extra["confirmable"] == 8  # garbage lines ignored; last 8 sampled


# ── debounce ─────────────────────────────────────────────────────────

def test_debounce_requires_two_ticks(tmp_path):
    log = tmp_path / "oracle.jsonl"
    recs = [_rec(True) for _ in range(2)] + [
        _rec(False, reason="send_error: x") for _ in range(8)]
    _write_log(log, recs)
    dbp = str(tmp_path / "debounce.json")
    # default debounce_ticks=2: first tick holds, second fires.
    assert probe_oracle_delivery_degraded(
        log_path=str(log), now=NOW, debounce_path=dbp) is None
    sig = probe_oracle_delivery_degraded(
        log_path=str(log), now=NOW, debounce_path=dbp)
    assert sig is not None
    assert sig.extra["debounce_streak"] == 2


def test_healthy_observation_resets_streak(tmp_path):
    dbp = str(tmp_path / "debounce.json")
    bad = tmp_path / "bad.jsonl"
    _write_log(bad, [_rec(True) for _ in range(2)]
               + [_rec(False, reason="send_error: x") for _ in range(8)])
    # one degraded tick (streak → 1, no fire under default ticks=2)
    assert probe_oracle_delivery_degraded(
        log_path=str(bad), now=NOW, debounce_path=dbp) is None
    assert json.loads(Path(dbp).read_text())["streak"] == 1
    # a healthy observation resets the streak to 0
    good = tmp_path / "good.jsonl"
    _write_log(good, [_rec(True) for _ in range(10)]
               + [_rec(False, reason="send_error: x")])
    assert probe_oracle_delivery_degraded(
        log_path=str(good), now=NOW, debounce_path=dbp) is None
    assert json.loads(Path(dbp).read_text())["streak"] == 0


# ── helper direct ────────────────────────────────────────────────────

def test_read_oracle_recent_unreadable_returns_none(tmp_path):
    assert _read_oracle_recent(str(tmp_path), NOW, 8, 4096) is None


class TestRnsAmbiguousBenignSplit:
    """Row 2 (2026-07-19): the benign bucket blended a KNOWN blind spot.

    On the Meshtastic/MQTT/MeshCore legs a reason-less non-delivery really is
    benign — their send_fn lets a real exception reach the responder, so a
    genuine failure arrives as send_error. On the RNS leg it is AMBIGUOUS:
    send_to_rns catches exceptions and returns a bare False, so a crash is
    indistinguishable from a no-path. Reporting one blended "benign" number
    averages that blind spot into a clean-looking figure; counting the
    ambiguous leg separately makes its SIZE visible while the root fix waits
    on the RNS roll (honest_failure_modes #5 — surface it, don't average it).
    """

    def _fire(self, tmp_path, extra_recs):
        log = tmp_path / "oracle.jsonl"
        recs = ([_rec(True) for _ in range(4)]
                + [_rec(False, reason="send_error: boom") for _ in range(6)]
                + extra_recs)
        _write_log(log, recs)
        sig = _probe(log, tmp_path, debounce_ticks=1)
        assert sig is not None
        return sig

    def test_rns_benign_is_counted_as_ambiguous(self, tmp_path):
        sig = self._fire(tmp_path, [_rec(False, transport="rns") for _ in range(3)])
        assert sig.extra["benign_nondeliveries_excluded"] == 3
        assert sig.extra["benign_rns_ambiguous"] == 3   # the whole bucket is ambiguous

    def test_non_rns_benign_is_not_ambiguous(self, tmp_path):
        """A Meshtastic benign non-delivery is genuinely benign — that leg
        surfaces real failures as send_error, so it must NOT be counted as
        blind-spot volume."""
        sig = self._fire(tmp_path,
                         [_rec(False, transport="meshtastic") for _ in range(3)])
        assert sig.extra["benign_nondeliveries_excluded"] == 3
        assert sig.extra["benign_rns_ambiguous"] == 0

    def test_mixed_legs_split_correctly(self, tmp_path):
        sig = self._fire(tmp_path,
                         [_rec(False, transport="rns") for _ in range(2)]
                         + [_rec(False, transport="meshcore") for _ in range(5)])
        assert sig.extra["benign_nondeliveries_excluded"] == 7
        assert sig.extra["benign_rns_ambiguous"] == 2

    def test_ambiguous_count_never_enters_the_failure_set(self, tmp_path):
        """It measures what we cannot tell apart — it must not become a
        failure count, or the probe would fire on RNS no-paths."""
        sig = self._fire(tmp_path, [_rec(False, transport="rns") for _ in range(9)])
        assert sig.extra["confirmable"] == 8           # last 8: 2 delivered + 6 send_error
        assert sig.extra["rate"] == pytest.approx(0.25, abs=0.01)

    def test_named_benign_reason_is_no_longer_ambiguous(self, tmp_path):
        """Row 2's cure: send_to_rns now returns an RnsSendResult, so an RNS
        non-delivery arrives NAMED. no_path stays benign-and-explained (out of
        the failure set, out of the ambiguity count). circuit_open does NOT —
        2026-07-21 review (C1) corrected this test's original premise: a
        tripped write breaker is an infrastructure failure and must degrade
        the delivery rate, never sit in the benign bucket."""
        sig = self._fire(tmp_path,
                         [_rec(False, reason="no_path", transport="rns")
                          for _ in range(3)]
                         + [_rec(False, reason="circuit_open", transport="rns")
                            for _ in range(2)])
        assert sig.extra["benign_nondeliveries_excluded"] == 3  # no_path only
        assert sig.extra["benign_rns_ambiguous"] == 0           # but not blind
        assert sig.extra["send_errors"] == 8   # 6 baseline + 2 circuit_open

    def test_reasonless_rns_benign_still_counts_as_ambiguous(self, tmp_path):
        """The measure must keep working for any leg still returning a bare
        bool — a reason-less non-delivery is exactly what we cannot tell apart
        (absence of a reason is not evidence of benignity)."""
        sig = self._fire(tmp_path,
                         [_rec(False, reason="no_path", transport="rns")]
                         + [_rec(False, transport="rns") for _ in range(2)])
        assert sig.extra["benign_rns_ambiguous"] == 2

    def test_declines_are_not_counted_as_ambiguous(self, tmp_path):
        """A cooldown decline on the RNS leg is a correct refusal, not an
        unknown — it has a reason, so it is never blind-spot volume."""
        sig = self._fire(tmp_path,
                         [_rec(False, reason="cooldown", transport="rns")
                          for _ in range(4)])
        assert sig.extra["benign_rns_ambiguous"] == 0


class TestOracleDispositions:
    """2026-08-08 (Tier-3 re-decide): the quiet answers were collapsed.

    Every quiet return above asserts only ``is None``, which cannot
    distinguish FIVE different reasons for silence — the same collapse
    the audit found one layer up in ``nomadnet_crashloop``, and the reason
    this probe sat ``indeterminate`` on moc3 (its only enrolled box)
    permanently: a reactive service nobody queried can never meet the
    6h/min-8 gate, so "cannot judge" was rendered forever as a blind
    detector. These tests pin the disposition, not just the silence.
    """

    @staticmethod
    def _disp(log, tmp_path, **kw):
        reset_dispositions()
        out = _probe(log, tmp_path, **kw)
        return out, collect_dispositions().get("oracle_delivery_degraded")

    def test_absent_log_is_inert_and_says_never_wrote(self, tmp_path):
        out, cell = self._disp(tmp_path / "nope.jsonl", tmp_path)
        assert out is None
        assert cell["disp"] == "inert"
        assert "never wrote a log" in cell["reason"]

    def test_empty_window_is_inert_idle_not_indeterminate(self, tmp_path):
        """The moc3 steady state: log present, oracle enrolled, nobody asked.

        The observation SUCCEEDED — its answer is "no queries" — so this is
        inert, and its reason must NOT be the absent-log one (an enrolled
        idle oracle and a box with no oracle are different facts)."""
        log = tmp_path / "oracle.jsonl"
        log.write_text("")
        out, cell = self._disp(log, tmp_path)
        assert out is None
        assert cell["disp"] == "inert"
        assert "idle" in cell["reason"]
        assert "never wrote a log" not in cell["reason"]

    def test_moc3_shape_is_inert_stale_with_the_rate_in_the_reason(self, tmp_path):
        """moc3 exactly: 99 confirmable answers, all delivered, newest ~19 d.

        Under v1 this was `indeterminate` FOREVER — the 6h/min-8 gate could
        never be met by a service answering ~5 queries a month. Under v2 the
        probe has a measurement to report, and the staleness guard makes it
        `inert` rather than `clean`: old answers are evidence about the past,
        never a claim about now."""
        log = tmp_path / "oracle.jsonl"
        _write_log(log, [_rec(True, age_s=19 * 24 * 3600) for _ in range(99)])
        out, cell = self._disp(log, tmp_path)
        assert out is None
        assert cell["disp"] == "inert"
        assert "stale" in cell["reason"]
        assert "1.00" in cell["reason"]     # the rate it CAN now report
        assert "19.0d" in cell["reason"]    # and how old that evidence is

    def test_small_sample_in_a_NON_empty_window_stays_indeterminate(self, tmp_path):
        """The anti-hiding pin. A window with records but < min_sample
        confirmable is a genuine cannot-judge — never 'nothing to watch'."""
        log = tmp_path / "oracle.jsonl"
        _write_log(log, [_rec(True), _rec(False, reason="send_error: x")])
        out, cell = self._disp(log, tmp_path, debounce_ticks=1)
        assert out is None
        assert cell["disp"] == "indeterminate"

    def test_all_benign_rns_window_is_never_inert(self, tmp_path):
        """THE trap this split had to avoid: an oracle whose every RNS reply
        comes back a reason-less False has confirmable == 0 — the row-2
        blind spot at full size. Calling that idle would hide the exact
        failure the probe exists to size (honest_failure_modes #2)."""
        log = tmp_path / "oracle.jsonl"
        _write_log(log, [_rec(False, transport="rns") for _ in range(30)])
        out, cell = self._disp(log, tmp_path, debounce_ticks=1)
        assert out is None
        assert cell["disp"] == "indeterminate"

    def test_all_declines_window_is_never_inert(self, tmp_path):
        """Same shape from the other side: a cooldowned oracle IS being
        exercised. Nothing to rate, but not nothing to watch."""
        log = tmp_path / "oracle.jsonl"
        _write_log(log, [_rec(False, reason="cooldown") for _ in range(30)])
        out, cell = self._disp(log, tmp_path, debounce_ticks=1)
        assert out is None
        assert cell["disp"] == "indeterminate"

    def test_healthy_rate_is_clean(self, tmp_path):
        log = tmp_path / "oracle.jsonl"
        _write_log(log, [_rec(True) for _ in range(10)])
        out, cell = self._disp(log, tmp_path, debounce_ticks=1)
        assert out is None
        assert cell["disp"] == "clean"

    def test_unreadable_tail_is_indeterminate(self, tmp_path):
        reset_dispositions()
        assert probe_oracle_delivery_degraded(
            log_path=str(tmp_path), now=NOW,
            debounce_path=str(tmp_path / "d.json")) is None
        cell = collect_dispositions()["oracle_delivery_degraded"]
        assert cell["disp"] == "indeterminate"
        assert "unreadable" in cell["reason"]

    def test_operator_unresolvable_is_indeterminate_not_inert(self, monkeypatch,
                                                              tmp_path):
        import utils.fleet_test_runner as ftr
        monkeypatch.setattr(ftr, "_find_operator_user", lambda: None)
        reset_dispositions()
        assert probe_oracle_delivery_degraded(
            now=NOW, debounce_path=str(tmp_path / "d.json")) is None
        assert collect_dispositions()[
            "oracle_delivery_degraded"]["disp"] == "indeterminate"


class TestCountWindowAndStalenessGuard:
    """v2 (2026-08-08): rate the last N answers, guard on their AGE.

    v1 rated a 6 h time window and needed ≥8 confirmable inside it. The
    fleet's only oracle box answers ~5 queries a month in bursts weeks
    apart, so that gate was unmeetable and the probe could never judge
    anything at all — it sat `indeterminate` on the one box that could see.

    A count window fixes the yield but introduces a new way to lie: a
    sample can span weeks, so "the last 8 answers" is not automatically a
    statement about NOW. These tests pin the guard that keeps the two
    apart — and pin it from the failing side, since a guard that has never
    refused anything is not evidence it works.
    """

    @staticmethod
    def _disp(log, tmp_path, **kw):
        reset_dispositions()
        out = _probe(log, tmp_path, **kw)
        return out, collect_dispositions().get("oracle_delivery_degraded")

    def test_stale_degraded_sample_is_reported_but_never_paged(self, tmp_path):
        """5 of the last 8 answers failed — three weeks ago. Nobody can act on
        that, and at a 30 s cadence it would page forever. Report it in the
        cell; do not fire."""
        old = 21 * 24 * 3600.0
        log = tmp_path / "oracle.jsonl"
        _write_log(log, [_rec(True, age_s=old) for _ in range(3)]
                   + [_rec(False, reason="send_error: x", age_s=old)
                      for _ in range(5)])
        out, cell = self._disp(log, tmp_path, debounce_ticks=1)
        assert out is None                       # NOT a page
        assert cell["disp"] == "inert"
        assert "BELOW" in cell["reason"]         # but the finding is visible
        assert "0.38" in cell["reason"]

    def test_a_stale_healthy_sample_is_never_called_clean(self, tmp_path):
        """THE guard. 8 old deliveries are evidence the path worked in July,
        not evidence it works now — `clean` there would be absence-of-evidence
        dressed as health (honest_failure_modes #2)."""
        log = tmp_path / "oracle.jsonl"
        _write_log(log, [_rec(True, age_s=30 * 24 * 3600) for _ in range(8)])
        out, cell = self._disp(log, tmp_path, debounce_ticks=1)
        assert out is None
        assert cell["disp"] == "inert"
        assert cell["disp"] != "clean"

    def test_fresh_failures_fire(self, tmp_path):
        """The case the whole change exists to make reachable: a burst of
        queries after weeks of silence, and the replies are not landing."""
        log = tmp_path / "oracle.jsonl"
        _write_log(log, [_rec(True, age_s=40 * 24 * 3600) for _ in range(2)]
                   + [_rec(False, reason="send_error: x", age_s=120)
                      for _ in range(6)])
        sig = _probe(log, tmp_path, debounce_ticks=1)
        assert sig is not None
        assert sig.extra["fresh_send_errors"] == 6
        assert sig.extra["rate"] == pytest.approx(0.25, abs=0.01)
        # the span is surfaced, so a wide sample can never read as "last hour"
        assert sig.extra["sample_span_h"] > 900

    def test_old_failures_inside_a_fresh_sample_do_not_page(self, tmp_path):
        """The pollution a count window would otherwise introduce: the sample
        is fresh (newest answer minutes ago) and its rate is 0.38, but every
        failure in it is from last month and the recent answers all landed.
        Clean, not a page — the guard is on ACTION, not on the measure."""
        log = tmp_path / "oracle.jsonl"
        _write_log(log, [_rec(False, reason="send_error: x",
                              age_s=30 * 24 * 3600) for _ in range(5)]
                   + [_rec(True, age_s=120) for _ in range(3)])
        out, cell = self._disp(log, tmp_path, debounce_ticks=1)
        assert out is None                       # NOT a page
        assert cell["disp"] == "clean"
        assert "older than" in cell["reason"]

    def test_one_fresh_failure_is_enough_to_arm_the_fire(self, tmp_path):
        """Boundary from the other side of the same guard: identical sample,
        one of the failures moved inside the freshness bound → it fires."""
        log = tmp_path / "oracle.jsonl"
        _write_log(log, [_rec(False, reason="send_error: x",
                              age_s=30 * 24 * 3600) for _ in range(4)]
                   + [_rec(False, reason="send_error: x", age_s=3600)]
                   + [_rec(True, age_s=120) for _ in range(3)])
        sig = _probe(log, tmp_path, debounce_ticks=1)
        assert sig is not None
        assert sig.extra["fresh_send_errors"] == 1

    def test_sample_is_capped_at_n_however_long_the_log(self, tmp_path):
        log = tmp_path / "oracle.jsonl"
        _write_log(log, [_rec(True) for _ in range(200)]
                   + [_rec(False, reason="send_error: x") for _ in range(7)])
        sig = _probe(log, tmp_path, debounce_ticks=1)
        assert sig is not None
        assert sig.extra["confirmable"] == 8      # 1 delivered + 7 send_error
        assert sig.extra["delivered"] == 1

    def test_freshness_is_configurable_and_actually_gates(self, tmp_path):
        """Same log, two freshness bounds: inside it the sample is live and
        fires, outside it the sample is stale and cannot."""
        log = tmp_path / "oracle.jsonl"
        _write_log(log, [_rec(True, age_s=2 * 3600) for _ in range(2)]
                   + [_rec(False, reason="send_error: x", age_s=2 * 3600)
                      for _ in range(6)])
        assert _probe(log, tmp_path, debounce_ticks=1,
                      fresh_s=6 * 3600.0) is not None
        out, cell = self._disp(log, tmp_path, debounce_ticks=1,
                               fresh_s=1 * 3600.0)
        assert out is None
        assert cell["disp"] == "inert"

    def test_ordering_is_file_order_not_ts_order(self, tmp_path):
        """A stepped clock (RTC-less Pis, NTP) can scramble ts; the append
        sequence cannot. The last 8 records WRITTEN are the sample, so a
        later record carrying an older ts does not reorder the window."""
        log = tmp_path / "oracle.jsonl"
        # written last, but stamped 10 h ago: still in the sample, and it is
        # what makes the sample stale under a 1 h bound.
        _write_log(log, [_rec(True, age_s=60) for _ in range(8)]
                   + [_rec(True, age_s=10 * 3600)])
        out, cell = self._disp(log, tmp_path, fresh_s=3600.0)
        assert out is None
        assert cell["disp"] == "inert"
        assert "stale" in cell["reason"]
