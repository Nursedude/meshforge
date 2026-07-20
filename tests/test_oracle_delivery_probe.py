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

from utils.watchdog_probe_core import SIGNAL_CLASSES  # noqa: E402
from utils.watchdog_probes import probe_oracle_delivery_degraded  # noqa: E402
from utils.watchdog_probes_gateway import (  # noqa: E402
    _classify_oracle_record,
    _read_oracle_window,
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


# ── INERT self-guards ────────────────────────────────────────────────

def test_inert_when_log_absent(tmp_path):
    missing = tmp_path / "nope.jsonl"
    assert probe_oracle_delivery_degraded(
        log_path=str(missing), now=NOW,
        debounce_path=str(tmp_path / "d.json")) is None


def test_inert_when_operator_unresolvable(monkeypatch, tmp_path):
    # log_path=None → resolves via _find_operator_user; force it None
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
    assert sig.extra["confirmable"] == 10
    assert sig.extra["delivered"] == 3
    assert sig.extra["send_errors"] == 7
    assert sig.extra["rate"] == pytest.approx(0.3, abs=0.01)


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
    assert sig.extra["confirmable"] == 10            # 4 delivered + 6 send_error
    assert sig.extra["declines_excluded"] == 15      # 10 cooldown + 5 not_allowlisted
    assert sig.extra["benign_nondeliveries_excluded"] == 3
    assert sig.extra["rate"] == pytest.approx(0.4, abs=0.01)


def test_min_sample_guard_holds(tmp_path):
    # Below-threshold rate but only 3 confirmable (< 8) → pass@small-N → None.
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

def test_old_records_excluded_from_window(tmp_path):
    # 10 send_error all OLDER than the window + 10 fresh delivered.
    # Only the fresh ones count → rate 1.0 → None (the old failures are stale).
    log = tmp_path / "oracle.jsonl"
    recs = ([_rec(False, reason="send_error: old", age_s=10 * 3600)
             for _ in range(10)]
            + [_rec(True, age_s=60) for _ in range(10)])
    _write_log(log, recs)
    assert _probe(log, tmp_path, window_s=6 * 3600.0, debounce_ticks=1) is None


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
    assert sig.extra["confirmable"] == 10  # garbage lines ignored


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

def test_read_oracle_window_unreadable_returns_none(tmp_path):
    assert _read_oracle_window(str(tmp_path), NOW, 3600.0, 4096) is None


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
        assert sig.extra["confirmable"] == 10          # 4 delivered + 6 send_error
        assert sig.extra["rate"] == pytest.approx(0.4, abs=0.01)

    def test_named_benign_reason_is_no_longer_ambiguous(self, tmp_path):
        """Row 2's cure: send_to_rns now returns an RnsSendResult, so a benign
        RNS non-delivery arrives NAMED (no_path / circuit_open). It stays out of
        the failure set, but it is explained — so the ambiguity measure must
        shrink, not hold steady, as the blind spot closes."""
        sig = self._fire(tmp_path,
                         [_rec(False, reason="no_path", transport="rns")
                          for _ in range(3)]
                         + [_rec(False, reason="circuit_open", transport="rns")
                            for _ in range(2)])
        assert sig.extra["benign_nondeliveries_excluded"] == 5  # still excluded
        assert sig.extra["benign_rns_ambiguous"] == 0           # but not blind

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
