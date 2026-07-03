"""Tests for mini_dudeai.calibration_ledger — the make-the-math-visible layer.

RED + GREEN. The load-bearing properties:
  * claims + verdicts round-trip through the append-only log,
  * fold reduces to held/broke/open honestly — empty set → ratio None, NOT a
    fabricated 100%%/0%% (honest_failure_modes #2),
  * the latest verdict wins (a claim can recover or regress over re-derivations),
  * re-derivation is CONSERVATIVE — a verdict is minted ONLY when a full
    honest_status marker definitively covers the claimed head; absence leaves the
    claim open (never silently "recovered"),
  * the 'broke' detector is NOT vacuous — a head claimed green whose marker later
    reads exit 1 produces a 'broke' verdict (the "you said 100%%" miss, surfaced).
"""
from __future__ import annotations

from mini_dudeai import calibration_ledger as cl


HEAD = "a" * 40
HEAD2 = "b" * 40


def _ledger(tmp_path):
    return str(tmp_path / "calibration_ledger.jsonl")


# ── id + round-trip ─────────────────────────────────────────────────────

def test_make_claim_id_deterministic():
    a = cl.make_claim_id(100.0, "all green", HEAD)
    b = cl.make_claim_id(100.0, "all green", HEAD)
    c = cl.make_claim_id(100.0, "all green", HEAD2)
    assert a == b and a != c and len(a) == 12


def test_record_and_load_roundtrip(tmp_path):
    p = _ledger(tmp_path)
    rec = cl.record_claim("all tests pass", "tests_passed", "pytest exit 0",
                          HEAD, model_id="claude-opus-4-8", session_id="s1",
                          ts=100.0, path=p)
    assert rec["kind"] == "claim" and rec["status"] == "open"
    events = cl.load_events(p)
    assert len(events) == 1
    assert events[0]["model_id"] == "claude-opus-4-8"
    assert events[0]["head_full"] == HEAD
    assert events[0]["id"] == rec["id"]


def test_record_claim_source_provenance(tmp_path):
    """Feed provenance (gate automatic vs manual hatch) is recorded so
    held-rates can later be split per feed; omitted → explicit None (legacy
    pre-source records lack the key entirely — consumers .get it)."""
    p = _ledger(tmp_path)
    cl.record_claim("gate claim", "completion", "e", HEAD, ts=1.0, path=p,
                    source="claim_gate")
    cl.record_claim("manual claim", "completion", "e", HEAD, ts=2.0, path=p,
                    source="manual")
    cl.record_claim("unattributed", "completion", "e", HEAD, ts=3.0, path=p)
    by_text = {e["claim_text"]: e for e in cl.load_events(p)}
    assert by_text["gate claim"]["source"] == "claim_gate"
    assert by_text["manual claim"]["source"] == "manual"
    assert by_text["unattributed"]["source"] is None


def test_load_missing_file_is_empty(tmp_path):
    assert cl.load_events(str(tmp_path / "nope.jsonl")) == []


def test_load_skips_torn_and_blank_lines(tmp_path):
    p = _ledger(tmp_path)
    cl.record_claim("c1", "k", "e", HEAD, ts=1.0, path=p)
    # Append a blank line and a torn/garbage line; a valid claim after it.
    with open(p, "a") as f:
        f.write("\n")
        f.write('{"kind": "claim", "id": "torn"')  # no newline, malformed
    cl.record_claim("c2", "k", "e", HEAD, ts=2.0, path=p)
    events = cl.load_events(p)
    kinds = [e.get("claim_text") for e in events if e.get("kind") == "claim"]
    assert "c1" in kinds and "c2" in kinds
    assert all(e.get("id") != "torn" for e in events)  # torn line skipped


# ── fold ────────────────────────────────────────────────────────────────

class TestFold:
    def test_empty_ratio_is_none_not_zero(self):
        s = cl.fold([])
        assert s["ratio"] is None  # never a fabricated 0.0/1.0 from no data
        assert s["n_total"] == 0

    def test_open_claim_no_verdict_is_unverified(self):
        s = cl.fold([{"kind": "claim", "id": "x", "head_full": HEAD}])
        assert s["n_open"] == 1 and s["n_held"] == 0 and s["n_broke"] == 0
        assert s["ratio"] is None  # an unverified claim does not count either way

    def test_held_and_broke_counted(self):
        events = [
            {"kind": "claim", "id": "h", "head_full": HEAD},
            {"kind": "verdict", "claim_id": "h", "ts": 1.0, "outcome": "held"},
            {"kind": "claim", "id": "b", "head_full": HEAD},
            {"kind": "verdict", "claim_id": "b", "ts": 1.0, "outcome": "broke"},
        ]
        s = cl.fold(events)
        assert s["n_held"] == 1 and s["n_broke"] == 1
        assert s["ratio"] == 0.5

    def test_latest_verdict_wins(self):
        events = [
            {"kind": "claim", "id": "x", "head_full": HEAD},
            {"kind": "verdict", "claim_id": "x", "ts": 1.0, "outcome": "broke"},
            {"kind": "verdict", "claim_id": "x", "ts": 2.0, "outcome": "held"},
        ]
        s = cl.fold(events)
        assert s["n_held"] == 1 and s["n_broke"] == 0  # recovered

    def test_verdict_for_unknown_claim_ignored(self):
        s = cl.fold([{"kind": "verdict", "claim_id": "ghost", "ts": 1.0,
                      "outcome": "held"}])
        assert s["n_total"] == 0 and s["ratio"] is None

    def test_nondefinitive_outcome_not_counted(self):
        events = [
            {"kind": "claim", "id": "x", "head_full": HEAD},
            {"kind": "verdict", "claim_id": "x", "ts": 1.0,
             "outcome": "indeterminate"},
        ]
        s = cl.fold(events)
        assert s["n_open"] == 1 and s["ratio"] is None

    def test_ratio_two_thirds(self):
        events = []
        for cid, oc in [("a", "held"), ("b", "held"), ("c", "broke")]:
            events.append({"kind": "claim", "id": cid, "head_full": HEAD})
            events.append({"kind": "verdict", "claim_id": cid, "ts": 1.0,
                           "outcome": oc})
        assert cl.fold(events)["ratio"] == 2 / 3


# ── rederive_open (cheap, conservative) ─────────────────────────────────

class TestRederiveOpen:
    def _open_claim(self, head=HEAD):
        return {"kind": "claim", "id": cl.make_claim_id(1.0, "c", head),
                "head_full": head}

    def _marker(self, **over):
        m = {"head_full": HEAD, "exit_code": 0, "ran_full_suite": True}
        m.update(over)
        return m

    def test_green_marker_on_matching_head_mints_held(self):
        events = [self._open_claim()]
        new = cl.rederive_open(events, HEAD, self._marker(), now_ts=9.0)
        assert len(new) == 1 and new[0]["outcome"] == "held"

    def test_red_broke_detector_not_vacuous(self):
        """RED proof — a head claimed green whose marker now reads exit 1 must
        mint a 'broke' verdict. If this didn't fire, the whole 'make the math
        visible' layer would never surface a single miss."""
        events = [self._open_claim()]
        new = cl.rederive_open(events, HEAD, self._marker(exit_code=1), now_ts=9.0)
        assert len(new) == 1 and new[0]["outcome"] == "broke"

    def test_exit2_unknown_mints_nothing(self):
        events = [self._open_claim()]
        assert cl.rederive_open(events, HEAD, self._marker(exit_code=2), 9.0) == []

    def test_quick_marker_mints_nothing(self):
        events = [self._open_claim()]
        assert cl.rederive_open(
            events, HEAD, self._marker(ran_full_suite=False), 9.0) == []

    def test_head_moved_on_mints_nothing(self):
        # marker covers HEAD2 but current head is HEAD2 while claim was on HEAD
        events = [self._open_claim(head=HEAD)]
        marker = self._marker(head_full=HEAD2)
        assert cl.rederive_open(events, HEAD2, marker, 9.0) == []

    def test_no_marker_mints_nothing(self):
        assert cl.rederive_open([self._open_claim()], HEAD, None, 9.0) == []

    def test_already_resolved_claim_not_reverdicted(self):
        c = self._open_claim()
        events = [c, {"kind": "verdict", "claim_id": c["id"], "ts": 1.0,
                      "outcome": "held"}]
        # Even with a matching marker, a resolved claim is not in 'open'.
        assert cl.rederive_open(events, HEAD, self._marker(), 9.0) == []


# ── rederive_and_persist (end-to-end) ───────────────────────────────────

def test_rederive_and_persist_end_to_end(tmp_path):
    p = _ledger(tmp_path)
    cl.record_claim("all green", "fleet_green", "honest_status exit 0", HEAD,
                    ts=100.0, path=p)
    marker = {"head_full": HEAD, "exit_code": 0, "ran_full_suite": True}
    state = cl.rederive_and_persist(p, HEAD, marker, now_ts=200.0)
    assert state["n_held"] == 1 and state["ratio"] == 1.0
    # The verdict was persisted — a fresh load reflects it.
    assert cl.fold(cl.load_events(p))["n_held"] == 1


def test_rederive_and_persist_surfaces_a_broken_claim(tmp_path):
    """The headline scenario: I claimed a head green; a later full honest_status
    on that same head FAILS. Re-derivation must flip it to broke and persist."""
    p = _ledger(tmp_path)
    cl.record_claim("100% verified", "tests_passed", "exit 0", HEAD,
                    ts=100.0, path=p)
    marker = {"head_full": HEAD, "exit_code": 1, "ran_full_suite": True}
    state = cl.rederive_and_persist(p, HEAD, marker, now_ts=200.0)
    assert state["n_broke"] == 1 and state["ratio"] == 0.0
    assert state["broke"][0]["claim_text"] == "100% verified"


# ── format_brief_block ───────────────────────────────────────────────────

class TestFormatBriefBlock:
    def test_empty_state_is_silent(self):
        assert cl.format_brief_block(cl.fold([])) == ""

    def test_no_verdicts_says_so_not_a_fake_percentage(self):
        events = [{"kind": "claim", "id": "x", "head_full": HEAD,
                   "claim_text": "all green"}]
        block = cl.format_brief_block(cl.fold(events))
        assert "none re-checked yet" in block
        assert "%" not in block  # never a fabricated ratio from no re-checks

    def test_held_only_is_green(self):
        events = [{"kind": "claim", "id": "x", "head_full": HEAD},
                  {"kind": "verdict", "claim_id": "x", "ts": 1.0,
                   "outcome": "held"}]
        block = cl.format_brief_block(cl.fold(events))
        assert "🟢" in block and "100% held" in block

    def test_broke_is_surfaced_loudly(self):
        events = [{"kind": "claim", "id": "x", "head_full": HEAD,
                   "claim_text": "everything works"},
                  {"kind": "verdict", "claim_id": "x", "ts": 1.0,
                   "outcome": "broke"}]
        block = cl.format_brief_block(cl.fold(events))
        assert "⚠️" in block and "BROKE" in block
        assert "everything works" in block
