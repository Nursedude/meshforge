"""Tests for scripts/claim_gate.py — the calibrated-claims reflective Stop hook.

RED + GREEN per the repo discipline (the honest-dev-env arc): each behavior is
pinned with a positive case AND a deliberately-seeded violation that is actually
caught, so no guard can pass vacuously. The gate's load-bearing properties:

  * it CATCHES an unqualified completion claim (the disease),
  * it PASSES already-calibrated language (autonomy preserved — not a cage),
  * it HONORS a fresh full green verdict marker covering HEAD,
  * it BLOCKS AT MOST ONCE (stop_hook_active loop guard — one reflective beat),
  * it FAILS OPEN on any malformed input (a buggy gate must never wedge a turn).
"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import claim_gate  # noqa: E402


# ── sanity / non-vacuity ────────────────────────────────────────────────

def test_pattern_tables_nonempty():
    assert claim_gate.STRONG_CLAIMS and claim_gate.CALIBRATION_MARKERS
    assert claim_gate.EVIDENCE_PATTERNS


# ── extract_last_assistant_text ─────────────────────────────────────────

class TestExtractLastAssistant:
    def test_extracts_last_assistant_message_nested_shape(self):
        lines = [
            json.dumps({"type": "user", "message": {"role": "user",
                        "content": [{"type": "text", "text": "do it"}]}}),
            json.dumps({"type": "assistant", "message": {"role": "assistant",
                        "content": [{"type": "text", "text": "first reply"}]}}),
            json.dumps({"type": "assistant", "message": {"role": "assistant",
                        "content": [{"type": "text", "text": "all green"},
                                    {"type": "tool_use", "name": "x"}]}}),
        ]
        assert claim_gate.extract_last_assistant_text(lines) == "all green"

    def test_bare_role_shape_and_string_content(self):
        lines = [json.dumps({"role": "assistant", "content": "done here"})]
        assert claim_gate.extract_last_assistant_text(lines) == "done here"

    def test_skips_unparseable_lines(self):
        lines = ["not json{", "", json.dumps(
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]})]
        assert claim_gate.extract_last_assistant_text(lines) == "ok"

    def test_empty_when_no_assistant(self):
        lines = [json.dumps({"role": "user", "content": "hi"})]
        assert claim_gate.extract_last_assistant_text(lines) == ""


# ── has_strong_claim ────────────────────────────────────────────────────

class TestHasStrongClaim:
    @pytest.mark.parametrize("text", [
        "Everything is done — all green.",
        "all tests pass now",
        "Coverage is 100% across the board",
        "fully verified and shipped",
        "it works now, deployed successfully",
    ])
    def test_detects_overclaims(self, text):
        assert claim_gate.has_strong_claim(text)

    @pytest.mark.parametrize("text", [
        "I read the file and made the edit.",
        "The fix is written; I have not run it yet.",
        "Here is what I changed and why.",
        "",
    ])
    def test_ignores_honest_language(self, text):
        # RED-of-false-positive: honest progress reports must NOT trip the gate.
        assert not claim_gate.has_strong_claim(text)


# ── is_calibrated ───────────────────────────────────────────────────────

class TestIsCalibrated:
    @pytest.mark.parametrize("text", [
        "All tests pass (pytest exit 0).",
        "all green — LINT_EXIT=0 quoted above",
        "VERIFIED: ran the suite this turn.",
        "This is BELIEVED, not verified — run honest_status to confirm.",
        "everything works, but I have not verified the fleet leg (UNKNOWN).",
        "it works now, I think this is right but should verify",
    ])
    def test_calibrated_language_and_evidence_pass(self, text):
        assert claim_gate.is_calibrated(text)

    @pytest.mark.parametrize("text", [
        "All tests pass.",
        "Everything works now.",
        "100% green, shipping it.",
    ])
    def test_bare_overclaim_not_calibrated(self, text):
        assert not claim_gate.is_calibrated(text)


# ── marker_satisfies ────────────────────────────────────────────────────

class TestMarkerSatisfies:
    HEAD = "a" * 40

    def _fresh(self, **over):
        m = {"head_full": self.HEAD, "exit_code": 0,
             "ran_full_suite": True, "ts": 1000.0}
        m.update(over)
        return m

    def test_fresh_full_green_matching_head_satisfies(self):
        assert claim_gate.marker_satisfies(self._fresh(), self.HEAD, 1000.0)

    def test_head_mismatch_rejected(self):
        assert not claim_gate.marker_satisfies(self._fresh(), "b" * 40, 1000.0)

    def test_nonzero_exit_rejected(self):
        assert not claim_gate.marker_satisfies(
            self._fresh(exit_code=2), self.HEAD, 1000.0)

    def test_quick_run_rejected(self):
        # A --quick marker is not full verification.
        assert not claim_gate.marker_satisfies(
            self._fresh(ran_full_suite=False), self.HEAD, 1000.0)

    def test_stale_marker_rejected(self):
        old = 1000.0
        now = old + claim_gate.MARKER_MAX_AGE_S + 1
        assert not claim_gate.marker_satisfies(self._fresh(ts=old), self.HEAD, now)

    def test_future_marker_rejected(self):
        # Negative age (clock skew) must not be honored.
        assert not claim_gate.marker_satisfies(
            self._fresh(ts=2000.0), self.HEAD, 1000.0)

    def test_non_dict_and_empty_head_rejected(self):
        assert not claim_gate.marker_satisfies(None, self.HEAD, 1000.0)
        assert not claim_gate.marker_satisfies(self._fresh(), "", 1000.0)


# ── evaluate (the pure decision core) ───────────────────────────────────

class TestEvaluate:
    HEAD = "c" * 40

    def test_bare_overclaim_blocks(self):
        """RED-of-the-disease: an unqualified 'all green' with no evidence and
        no marker must BLOCK. If this passed, the gate would be vacuous."""
        block, reason = claim_gate.evaluate(
            "All done — all tests pass, all green.", self.HEAD, None, 1000.0)
        assert block is True
        assert "calibrated_claims" in reason
        assert self.HEAD[:7] in reason  # surfaces the current HEAD

    def test_calibrated_overclaim_passes(self):
        """Autonomy preserved: the same words, honestly tagged, pass untouched."""
        block, reason = claim_gate.evaluate(
            "All tests pass (pytest exit 0) — VERIFIED this turn.",
            self.HEAD, None, 1000.0)
        assert block is False and reason is None

    def test_overclaim_with_fresh_marker_passes(self):
        marker = {"head_full": self.HEAD, "exit_code": 0,
                  "ran_full_suite": True, "ts": 1000.0, "summary": "green"}
        block, _ = claim_gate.evaluate(
            "all green, shipping", self.HEAD, marker, 1000.0)
        assert block is False

    def test_overclaim_with_stale_marker_blocks(self):
        marker = {"head_full": self.HEAD, "exit_code": 0,
                  "ran_full_suite": True, "ts": 0.0, "summary": "old"}
        now = claim_gate.MARKER_MAX_AGE_S + 100
        block, reason = claim_gate.evaluate("all green", self.HEAD, marker, now)
        assert block is True
        assert "old" in reason  # surfaces the stale verdict so I can see it

    def test_no_claim_passes(self):
        block, reason = claim_gate.evaluate(
            "I edited the file; not yet run.", self.HEAD, None, 1000.0)
        assert block is False and reason is None


# ── main() — I/O wrapper, loop guard, fail-open ─────────────────────────

class TestMainWrapper:
    def _run(self, monkeypatch, capsys, stdin_obj, env=None):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(stdin_obj)))
        if env:
            for k, v in env.items():
                monkeypatch.setenv(k, v)
        rc = claim_gate.main()
        out = capsys.readouterr().out
        return rc, out

    def _transcript(self, tmp_path, text):
        p = tmp_path / "t.jsonl"
        p.write_text(json.dumps(
            {"type": "assistant", "message": {"role": "assistant",
             "content": [{"type": "text", "text": text}]}}) + "\n")
        return str(p)

    def test_blocks_on_unqualified_claim(self, tmp_path, monkeypatch, capsys):
        # Point the marker path at a nonexistent file → no marker → block.
        tp = self._transcript(tmp_path, "All tests pass. All green. Shipping.")
        rc, out = self._run(
            monkeypatch, capsys,
            {"transcript_path": tp, "stop_hook_active": False},
            env={"HONEST_VERDICT_PATH": str(tmp_path / "none.json")})
        assert rc == 0
        decision = json.loads(out)
        assert decision["decision"] == "block"
        assert "calibrated_claims" in decision["reason"]

    def test_loop_guard_passes_when_already_active(self, tmp_path, monkeypatch, capsys):
        """The one-beat guarantee: stop_hook_active=True passes even on the same
        unqualified claim — no second block, no cage."""
        tp = self._transcript(tmp_path, "All tests pass. All green.")
        rc, out = self._run(
            monkeypatch, capsys,
            {"transcript_path": tp, "stop_hook_active": True},
            env={"HONEST_VERDICT_PATH": str(tmp_path / "none.json")})
        assert rc == 0 and out.strip() == ""  # no block emitted

    def test_calibrated_message_passes(self, tmp_path, monkeypatch, capsys):
        tp = self._transcript(
            tmp_path, "all green — VERIFIED (pytest exit 0 quoted above).")
        rc, out = self._run(
            monkeypatch, capsys,
            {"transcript_path": tp, "stop_hook_active": False},
            env={"HONEST_VERDICT_PATH": str(tmp_path / "none.json")})
        assert rc == 0 and out.strip() == ""

    def test_missing_transcript_path_fails_open(self, monkeypatch, capsys):
        rc, out = self._run(monkeypatch, capsys, {"stop_hook_active": False})
        assert rc == 0 and out.strip() == ""

    def test_garbage_stdin_fails_open(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "stdin", io.StringIO("}{ not json"))
        assert claim_gate.main() == 0
        assert capsys.readouterr().out.strip() == ""

    def test_unreadable_transcript_fails_open(self, tmp_path, monkeypatch, capsys):
        rc, out = self._run(
            monkeypatch, capsys,
            {"transcript_path": str(tmp_path / "does_not_exist.jsonl"),
             "stop_hook_active": False})
        assert rc == 0 and out.strip() == ""


# ── auto-recorder: the ledger's only production feed ────────────────────

class TestShouldRecord:
    HEAD = "c" * 40

    def _marker(self, **over):
        m = {"head_full": self.HEAD, "exit_code": 0,
             "ran_full_suite": True, "ts": 1000.0}
        m.update(over)
        return m

    def test_strong_claim_with_satisfying_marker_records(self):
        assert claim_gate.should_record("all green", self.HEAD,
                                        self._marker(), 1000.0)

    def test_strong_claim_without_marker_does_not_record(self):
        # tight scope: only marker-backed claims are logged (re-derivable)
        assert not claim_gate.should_record("all green", self.HEAD, None, 1000.0)

    def test_no_strong_claim_does_not_record(self):
        assert not claim_gate.should_record("edited the file", self.HEAD,
                                            self._marker(), 1000.0)

    def test_stale_marker_does_not_record(self):
        now = 1000.0 + claim_gate.MARKER_MAX_AGE_S + 1
        assert not claim_gate.should_record("all green", self.HEAD,
                                            self._marker(ts=1000.0), now)


class TestExtractModel:
    def test_extracts_model_of_last_assistant(self):
        lines = [json.dumps({"type": "assistant", "message": {
            "role": "assistant", "model": "claude-xyz",
            "content": [{"type": "text", "text": "hi"}]}})]
        assert claim_gate.extract_last_assistant_model(lines) == "claude-xyz"

    def test_none_when_absent(self):
        lines = [json.dumps({"role": "assistant", "content": "hi"})]
        assert claim_gate.extract_last_assistant_model(lines) is None


class TestMainRecording:
    """main() feeds the ledger ONLY on a marker-backed verified pass — never on
    a block, never on the reflective continuation, never on an unbacked claim."""

    def _transcript(self, tmp_path, text, model="claude-test"):
        p = tmp_path / "t.jsonl"
        p.write_text(json.dumps(
            {"type": "assistant", "message": {"role": "assistant",
             "model": model,
             "content": [{"type": "text", "text": text}]}}) + "\n")
        return str(p)

    def _green_marker(self, tmp_path, head):
        m = tmp_path / "marker.json"
        m.write_text(json.dumps({
            "head_full": head, "exit_code": 0, "ran_full_suite": True,
            "ts": time.time(), "summary": "fully verified green"}))
        return str(m)

    def _run(self, tmp_path, monkeypatch, capsys, stdin_obj, marker_path,
             ledger_path):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(stdin_obj)))
        monkeypatch.setenv("HONEST_VERDICT_PATH", marker_path)
        monkeypatch.setenv("CALIBRATION_LEDGER_PATH", ledger_path)
        rc = claim_gate.main()
        return rc, capsys.readouterr().out

    def _claims(self, ledger_path):
        from mini_dudeai import calibration_ledger as cl
        return [e for e in cl.load_events(ledger_path)
                if e.get("kind") == "claim"]

    def test_verified_pass_records_claim(self, tmp_path, monkeypatch, capsys):
        head = claim_gate._current_head()
        if not head:
            pytest.skip("not in a git repo — cannot match marker head")
        ledger = str(tmp_path / "led.jsonl")
        rc, out = self._run(
            tmp_path, monkeypatch, capsys,
            {"transcript_path": self._transcript(
                tmp_path, "All tests pass. All green. Shipping."),
             "stop_hook_active": False, "session_id": "sess1"},
            self._green_marker(tmp_path, head), ledger)
        assert rc == 0 and out.strip() == ""  # marker-backed → pass, no block
        claims = self._claims(ledger)
        assert len(claims) == 1
        assert claims[0]["model_id"] == "claude-test"
        assert claims[0]["head_full"] == head
        assert claims[0]["session_id"] == "sess1"
        assert claims[0]["status"] == "open"
        assert claims[0]["source"] == "claim_gate"  # automatic feed marks itself

    def test_blocked_claim_records_nothing(self, tmp_path, monkeypatch, capsys):
        ledger = str(tmp_path / "led.jsonl")
        rc, out = self._run(
            tmp_path, monkeypatch, capsys,
            {"transcript_path": self._transcript(tmp_path, "All green."),
             "stop_hook_active": False},
            str(tmp_path / "no_marker.json"), ledger)
        assert json.loads(out)["decision"] == "block"
        assert self._claims(ledger) == []

    def test_reflective_continuation_records_nothing(self, tmp_path, monkeypatch, capsys):
        head = claim_gate._current_head()
        if not head:
            pytest.skip("not in a git repo")
        ledger = str(tmp_path / "led.jsonl")
        # Even with a satisfying marker, stop_hook_active=True returns early.
        rc, out = self._run(
            tmp_path, monkeypatch, capsys,
            {"transcript_path": self._transcript(tmp_path, "all green"),
             "stop_hook_active": True},
            self._green_marker(tmp_path, head), ledger)
        assert rc == 0 and out.strip() == ""
        assert self._claims(ledger) == []

    def test_calibrated_but_unbacked_claim_records_nothing(self, tmp_path, monkeypatch, capsys):
        # passes the gate via text evidence, but no marker → not re-derivable →
        # not logged (the tight scope).
        ledger = str(tmp_path / "led.jsonl")
        rc, out = self._run(
            tmp_path, monkeypatch, capsys,
            {"transcript_path": self._transcript(
                tmp_path, "all green — VERIFIED, pytest exit 0"),
             "stop_hook_active": False},
            str(tmp_path / "no_marker.json"), ledger)
        assert rc == 0 and out.strip() == ""  # passed (calibrated text)
        assert self._claims(ledger) == []  # but not recorded (no marker)
