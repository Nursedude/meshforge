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

import json

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
    # The claim is stamped 1.0 and the marker 5.0: a marker must POST-DATE the
    # claim to count as a re-check (finding 11, 2026-09-09) — see
    # TestMarkerMustPostDateClaim for the rule itself.
    def _open_claim(self, head=HEAD):
        return {"kind": "claim", "id": cl.make_claim_id(1.0, "c", head),
                "head_full": head, "ts": 1.0}

    def _marker(self, **over):
        m = {"head_full": HEAD, "exit_code": 0, "ran_full_suite": True,
             "ts": 5.0}
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

    def test_verdict_names_the_instrument_that_produced_it(self):
        """The detail used to hardcode 'honest_status ...' for EVERY marker.

        calibration_reverify supplies its own marker from its own pytest+lint
        run, so its verdicts were filed under honest_status's name — evidence
        attributed to an instrument that did not produce it. The 2026-07-31
        provenance audit could not answer 'which verdicts came from the
        unguarded bare-rc path?' from the ledger at all and had to derive it
        from wall-clock landing time. Producer travels in `instrument`;
        `summary` is the message riding along.
        """
        events = [self._open_claim()]
        new = cl.rederive_open(
            events, HEAD,
            self._marker(instrument="calibration_reverify",
                         summary="suite=PASS(rc=0)+lint(rc=0)"),
            now_ts=9.0)
        assert len(new) == 1
        assert new[0]["detail"].startswith("calibration_reverify (suite=PASS")
        assert "honest_status" not in new[0]["detail"], (
            "a reverify-minted verdict must not claim honest_status as its source")

    def test_broke_verdict_also_names_its_source(self):
        events = [self._open_claim()]
        new = cl.rederive_open(
            events, HEAD,
            self._marker(exit_code=1, instrument="calibration_reverify",
                         summary="suite=FAIL(rc=1)+lint(rc=0)"),
            now_ts=9.0)
        assert len(new) == 1 and new[0]["outcome"] == "broke"
        assert new[0]["detail"].startswith("calibration_reverify (")

    def test_honest_status_message_summary_is_not_read_as_a_producer(self):
        """Finding 8's core case: honest_status writes its verdict MESSAGE in
        `summary` ("12 checks: 12 OK …"), so the summary-as-source fix still
        named no instrument on the MAJORITY path and doubled the text. With
        `instrument` present the producer leads and the message follows."""
        events = [self._open_claim()]
        new = cl.rederive_open(
            events, HEAD,
            self._marker(instrument="honest_status",
                         summary="12 checks: 12 OK (fully verified green)"),
            now_ts=9.0)
        assert len(new) == 1
        assert new[0]["detail"].startswith(
            "honest_status (12 checks: 12 OK (fully verified green)) green on")

    def test_summary_only_marker_is_attributed_as_unattributed(self):
        """A legacy or third-party marker with a message but no producer name
        must say exactly that — never dress the message up as a tool name (a
        third marker writer would otherwise be indistinguishable from the
        first two)."""
        events = [self._open_claim()]
        new = cl.rederive_open(
            events, HEAD,
            self._marker(summary="something a tool once printed"),
            now_ts=9.0)
        assert len(new) == 1
        assert new[0]["detail"].startswith(
            "unattributed marker (something a tool once printed)")

    def test_marker_without_summary_still_reads_honest_status(self):
        """The warm-start emitter supplies no summary — that path is genuinely
        honest_status, and its verdicts must keep saying so."""
        events = [self._open_claim()]
        new = cl.rederive_open(events, HEAD, self._marker(), now_ts=9.0)
        assert "honest_status green on" in new[0]["detail"]

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
    marker = {"head_full": HEAD, "exit_code": 0, "ran_full_suite": True,
              "ts": 150.0}
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
    marker = {"head_full": HEAD, "exit_code": 1, "ran_full_suite": True,
              "ts": 150.0}
    state = cl.rederive_and_persist(p, HEAD, marker, now_ts=200.0)
    assert state["n_broke"] == 1 and state["ratio"] == 0.0
    assert state["broke"][0]["claim_text"] == "100% verified"


# ── format_brief_block ───────────────────────────────────────────────────

class TestAnnotations:
    """Annotations qualify a verdict's EVIDENCE without changing the verdict.

    Born 2026-07-31: 21 `held` verdicts were minted by calibration_reverify.sh
    while it trusted pytest's bare exit code — a signal measured to flap to 0
    on a failed full-suite run. Those verdicts are not shown to be wrong; their
    evidence cannot distinguish green from red. The ledger had no way to say
    that, so the ratio was quoted without its caveat.
    """

    def _events(self, tmp_path):
        head = HEAD
        cid = cl.make_claim_id(1.0, "c", head)
        return cid, [
            {"kind": "claim", "id": cid, "head_full": head},
            {"kind": "verdict", "claim_id": cid, "ts": 5.0, "outcome": "held",
             "detail": "x"},
        ]

    def test_annotation_does_not_move_a_claim_between_buckets(self, tmp_path):
        cid, events = self._events(tmp_path)
        before = cl.fold(events)
        events.append({"kind": "annotation", "claim_id": cid, "ts": 6.0,
                       "topic": "evidence_provenance", "note": "weak"})
        after = cl.fold(events)
        assert (after["n_held"], after["n_broke"], after["n_open"]) == \
               (before["n_held"], before["n_broke"], before["n_open"])
        assert after["ratio"] == before["ratio"]

    def test_annotation_is_counted_so_the_ratio_carries_its_caveat(self, tmp_path):
        cid, events = self._events(tmp_path)
        assert cl.fold(events)["n_annotated"] == 0
        events.append({"kind": "annotation", "claim_id": cid, "ts": 6.0,
                       "topic": "evidence_provenance", "note": "weak"})
        assert cl.fold(events)["n_annotated"] == 1

    def test_brief_surfaces_the_annotation_beside_the_percentage(self, tmp_path):
        """A writer with no reader is the defect this very session fixed —
        the annotation must reach the line the operator actually reads."""
        cid, events = self._events(tmp_path)
        events.append({"kind": "annotation", "claim_id": cid, "ts": 6.0,
                       "topic": "evidence_provenance", "note": "weak"})
        block = cl.format_brief_block(cl.fold(events))
        assert "evidence annotation" in block
        assert "1 of those re-checked" in block

    def test_brief_has_no_annotation_line_when_there_are_none(self, tmp_path):
        _, events = self._events(tmp_path)
        assert "evidence annotation" not in cl.format_brief_block(cl.fold(events))

    def test_record_annotation_writes_an_annotation_not_a_verdict(self, tmp_path):
        """Hand-written verdicts are forbidden (calibrated_claims rule 6); an
        annotation can only reduce confidence, never manufacture it."""
        p = tmp_path / "ledger.jsonl"
        p.write_text("")
        cl.record_annotation("cid1", "evidence_provenance", "note text",
                             ts=7.0, path=str(p))
        rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        assert len(rows) == 1
        assert rows[0]["kind"] == "annotation"
        assert rows[0]["kind"] != "verdict"
        assert rows[0]["claim_id"] == "cid1"
        assert rows[0]["topic"] == "evidence_provenance"

    def test_annotation_extra_cannot_forge_a_verdict(self, tmp_path):
        """Ultra review 2026-07-31: ``rec.update(extra)`` let an extra dict
        override the reserved keys — {"kind": "verdict", "outcome": "held"}
        minted a definitive hand-written verdict through the one event kind
        whose docstring promises it can only reduce confidence. The
        unforgeability must live in code, not caller convention."""
        p = tmp_path / "ledger.jsonl"
        p.write_text("")
        cl.record_annotation(
            "cid1", "evidence_provenance", "note", ts=7.0, path=str(p),
            extra={"kind": "verdict", "outcome": "held", "source": "manual"})
        rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        assert len(rows) == 1
        assert rows[0]["kind"] == "annotation", "reserved key overridden"
        assert "outcome" not in rows[0]
        assert rows[0]["source"] == "manual", "non-reserved extras still land"
        # And the fold must agree: nothing here counts as a verdict.
        cid = "cid1"
        events = [{"kind": "claim", "id": cid, "head_full": HEAD}] + rows
        state = cl.fold(events)
        assert state["n_held"] == 0 and state["n_open"] == 1

    def test_annotation_for_an_unknown_claim_is_inert(self, tmp_path):
        """Annotating a claim id that isn't in the ledger must not invent a
        bucket entry or crash the fold."""
        _, events = self._events(tmp_path)
        events.append({"kind": "annotation", "claim_id": "no-such-claim",
                       "ts": 6.0, "topic": "evidence_provenance", "note": "x"})
        state = cl.fold(events)
        assert state["n_total"] == 1 and state["n_annotated"] == 0


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


def test_rederive_refuses_narrowed_or_dirty_marker():
    """Second reader of honest_verdict.json (review 2026-09-07): a marker from
    a HONEST_BOXES-narrowed run or a dirty tree must mint NO verdict, exactly
    as claim_gate.marker_satisfies refuses it — else the held-rate is inflated
    by the run class the gate excludes."""
    from mini_dudeai import calibration_ledger as cl
    events = [{"kind": "claim", "id": "c1", "ts": 1.0, "claim": "all green",
               "head_full": HEAD, "status": "open"}]
    base = {"head_full": HEAD, "exit_code": 0, "ran_full_suite": True, "ts": 5.0}
    assert cl.rederive_open(events, HEAD, dict(base), 9.0), "control: a clean marker mints held"
    assert cl.rederive_open(events, HEAD, dict(base, scope_narrowed=True), 9.0) == []
    assert cl.rederive_open(events, HEAD, dict(base, dirty_tree=True), 9.0) == []


# --- the marker must POST-DATE the claim (finding 11, 2026-09-09) ------------
# claim_gate records only marker-BACKED claims, so the marker that ADMITTED a
# claim is still the freshest marker on that head at the next warm start —
# and rederive_open minted `held` from it: zero new evidence, a verdict that
# confirms itself. 9 of 47 live held verdicts had landed within 15 min of
# their claim. A marker is a re-check only if it was produced AFTER the claim.

class TestMarkerMustPostDateClaim:
    def _events(self, claim_ts=100.0):
        return [{"kind": "claim", "id": "c1", "ts": claim_ts,
                 "claim_text": "all green", "head_full": HEAD, "status": "open"}]

    def _marker(self, ts):
        return {"head_full": HEAD, "exit_code": 0, "ran_full_suite": True, "ts": ts}

    def test_the_marker_that_admitted_the_claim_mints_no_verdict(self):
        """THE plant: same ts as the claim (the gate stamps the claim from the
        marker's own run) → not new evidence → stays open."""
        assert cl.rederive_open(self._events(100.0), HEAD, self._marker(100.0), 200.0) == []

    def test_an_older_marker_mints_no_verdict(self):
        assert cl.rederive_open(self._events(100.0), HEAD, self._marker(90.0), 200.0) == []

    def test_a_later_marker_on_the_same_head_mints_held(self):
        """Control: the rule must not make the ledger permanently silent."""
        new = cl.rederive_open(self._events(100.0), HEAD, self._marker(100.5), 200.0)
        assert len(new) == 1 and new[0]["outcome"] == "held"

    def test_undated_marker_mints_nothing(self):
        m = self._marker(150.0); del m["ts"]
        assert cl.rederive_open(self._events(), HEAD, m, 200.0) == []
        assert cl.rederive_open(self._events(), HEAD, self._marker("150"), 200.0) == []

    def test_undated_claim_mints_nothing(self):
        ev = self._events(); del ev[0]["ts"]
        assert cl.rederive_open(ev, HEAD, self._marker(150.0), 200.0) == []


# --- ONE marker predicate, shared with claim_gate (finding 11 / 15) ---------

class TestMarkerRefusal:
    def _ok(self):
        return {"head_full": HEAD, "exit_code": 0, "ran_full_suite": True,
                "scope_narrowed": False, "dirty_tree": False, "ts": 5.0}

    def test_fleet_strength_marker_is_not_refused(self):
        assert cl.marker_refusal(self._ok()) is None

    def test_legacy_marker_without_flags_is_judged_on_what_it_has(self):
        m = self._ok(); del m["scope_narrowed"]; del m["dirty_tree"]
        assert cl.marker_refusal(m) is None

    def test_each_refusal_names_its_field(self):
        assert "no marker" in cl.marker_refusal(None)
        assert "ran_full_suite" in cl.marker_refusal(dict(self._ok(), ran_full_suite=False))
        assert "scope_narrowed" in cl.marker_refusal(dict(self._ok(), scope_narrowed=True))
        assert "dirty_tree" in cl.marker_refusal(dict(self._ok(), dirty_tree=True))

    def test_the_ledger_and_the_gate_apply_the_same_predicate(self):
        """The rule used to be typed twice; this pins that the gate now
        imports it rather than carrying a copy that can drift."""
        import importlib.util
        import pathlib
        p = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "claim_gate.py"
        spec = importlib.util.spec_from_file_location("claim_gate_under_test", p)
        cg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cg)
        assert cg._cl is cl
        assert "marker_refusal" in cg.marker_refusal_reason.__code__.co_names or \
            any("marker_refusal" in c.co_names for c in
                cg.marker_refusal_reason.__code__.co_consts if hasattr(c, "co_names"))


# --- ONE marker path / builder / tree predicate (finding 11 + 18) -----------

def test_verdict_marker_path_env_override_wins(monkeypatch):
    monkeypatch.setenv("HONEST_VERDICT_PATH", "/x/y.json")
    assert cl.verdict_marker_path() == "/x/y.json"


def test_verdict_marker_path_under_unset_HOME_is_the_pw_home_not_tmp(monkeypatch):
    """honest_status.sh wrote to ${HOME:-/tmp}/…, the readers to
    expanduser('~') — under an unset HOME (cron/daemon) writer and reader
    diverged. One function now; the fallback is the pw-database home."""
    import os
    monkeypatch.delenv("HONEST_VERDICT_PATH", raising=False)
    monkeypatch.delenv("HOME", raising=False)
    p = cl.verdict_marker_path()
    assert not p.startswith("/tmp/")
    assert p.endswith(os.path.join(".cache", "meshforge", "honest_verdict.json"))
    assert p.startswith(os.path.expanduser("~"))


def test_build_marker_carries_every_field_the_readers_judge():
    m = cl.build_marker("a" * 40, 0, instrument="t", summary="s",
                        ran_full_suite=True, scope_narrowed=False,
                        dirty_tree=True, ts=1.0, boxes="x")
    for k in ("head_full", "exit_code", "ts", "iso", "summary", "instrument",
              "ran_full_suite", "scope_narrowed", "dirty_tree"):
        assert k in m, k
    assert m["dirty_tree"] is True and m["boxes"] == "x"
    assert cl.marker_refusal(m) is not None, "a dirty marker must refuse"


def test_write_marker_is_readable_back(tmp_path):
    import json
    p = str(tmp_path / "sub" / "honest_verdict.json")
    cl.write_marker(p, {"head_full": "h", "exit_code": 0})
    assert json.load(open(p))["head_full"] == "h"


def _git_repo(path):
    import subprocess
    subprocess.run(["git", "init", "-q", str(path)], check=True, timeout=30)
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "PATH": __import__("os").environ.get("PATH", "")}
    subprocess.run(["git", "-C", str(path), "commit", "-q", "--allow-empty",
                    "-m", "seed"], check=True, timeout=30, env=env)


def test_tree_is_dirty_sees_untracked_files_and_refuses_on_no_repo(tmp_path):
    """Untracked counts (an uncommitted new test file is the exact shape the
    flag exists for); a non-repo reads DIRTY — unknown is the refusing
    direction, never a clean bill."""
    repo = tmp_path / "r"; _git_repo(repo)
    assert cl.tree_is_dirty(str(repo)) is False
    (repo / "new_test.py").write_text("x")
    assert cl.tree_is_dirty(str(repo)) is True
    assert cl.tree_is_dirty(str(tmp_path / "not-a-repo")) is True


def test_repo_head_returns_the_sha_or_none(tmp_path):
    repo = tmp_path / "r"; _git_repo(repo)
    h = cl.repo_head(str(repo))
    assert isinstance(h, str) and len(h) == 40
    assert cl.repo_head(str(tmp_path / "nope")) is None


def test_old_rows_carrying_the_removed_end_field_still_fold(tmp_path):
    """The `end` field (2026-09-09) was removed the same day: a writer with
    no reader. Rows written while it existed must remain readable."""
    p = str(tmp_path / "led.jsonl")
    open(p, "w").write(json.dumps({"kind": "claim", "id": "c1", "ts": 1.0,
                                   "end": "unknown", "head_full": HEAD,
                                   "status": "open"}) + "\n")
    st = cl.fold(cl.load_events(p))
    assert st["n_total"] == 1 and st["n_open"] == 1
    assert "ends" not in st and "harness_share" not in st
