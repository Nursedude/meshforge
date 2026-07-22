"""Tests for the local-brain eval harness (W4 — mini_dudeai.local_brain_eval).

Under test: the harness's own honesty — loud loading (a malformed case file
must never silently grade against nothing), deterministic grading through the
PRODUCTION code paths, sequential accounting, and the gate. The LLM itself is
faked; its live pass-rate is what the harness measures on-box.
"""
from __future__ import annotations

import json

import pytest

from mini_dudeai import local_brain_eval as lbe
from mini_dudeai.chat_compiler import CompilerError

NOW = 1_800_000_000.0


class FakeBackend:
    model = "fake:4b"
    url = "http://fake:11434"

    def __init__(self, replies=None, exc=None):
        self.replies = list(replies or [])
        self.exc = exc
        self.calls = []

    def complete(self, system, user, fmt="json"):
        self.calls.append((system, user, fmt))
        if self.exc:
            raise self.exc
        return self.replies.pop(0) if self.replies else "{}"


def _triage_case(cid="t1", keys=("a", "b")):
    return {"id": cid, "kind": "triage",
            "input": {"deltas": [{"key": k, "summary": f"about {k}"}
                                 for k in keys]},
            "expect": {"coverage_min": 1.0}}


def _triage_reply(keys):
    return json.dumps({"summary": "ok", "deltas": [
        {"key": k, "assessment": "a", "suggested_disposition":
         "needs-live-check"} for k in keys]})


VALID_RULE = {"id": "eval_rule", "match": {"kind": "sensor_breach",
                                           "subject_glob": "*"},
              "action": {"kind": "ntfy", "title": "t", "message": "m"},
              "grace_s": 600, "cooldown_s": 900}


def _compile_case(cid="c1", fields=None):
    return {"id": cid, "kind": "compile",
            "input": {"intent": "page on breach"},
            "expect": {"fields": fields or {"match.kind": "sensor_breach"}}}


def _cases_file(tmp_path, cases, name="cases.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(c) for c in cases) + "\n")
    return str(p)


class TestLoadCases:
    def test_loads_and_validates(self, tmp_path):
        path = _cases_file(tmp_path, [_triage_case(), _compile_case()])
        cases = lbe.load_cases([path])
        assert [c["id"] for c in cases] == ["t1", "c1"]

    def test_no_files_is_an_error_not_a_pass(self):
        with pytest.raises(lbe.EvalConfigError, match="nothing to run"):
            lbe.load_cases([])

    def test_duplicate_id_is_loud(self, tmp_path):
        path = _cases_file(tmp_path, [_triage_case("x"), _triage_case("x")])
        with pytest.raises(lbe.EvalConfigError, match="duplicate"):
            lbe.load_cases([path])

    def test_unknown_kind_is_loud(self, tmp_path):
        bad = {"id": "b", "kind": "vibes", "input": {}, "expect": {}}
        path = _cases_file(tmp_path, [bad])
        with pytest.raises(lbe.EvalConfigError, match="unknown kind"):
            lbe.load_cases([path])

    def test_torn_json_is_loud(self, tmp_path):
        p = tmp_path / "torn.jsonl"
        p.write_text('{"id": "x", "kind"')
        with pytest.raises(lbe.EvalConfigError, match="not valid JSON"):
            lbe.load_cases([str(p)])


class TestGradeTriage:
    def test_full_coverage_passes(self):
        ok, reasons, w = lbe.grade_triage(
            _triage_case(), FakeBackend([_triage_reply(["a", "b"])]))
        assert ok, reasons
        assert w["brain_tier"] == "local"

    def test_under_coverage_fails_with_the_numbers(self):
        # The qwen2.5:3b failure mode, mechanized.
        ok, reasons, _ = lbe.grade_triage(
            _triage_case(), FakeBackend([_triage_reply(["a"])]))
        assert not ok
        assert any("coverage 1/2" in r for r in reasons)

    def test_llm_down_fails_honestly(self):
        ok, reasons, _ = lbe.grade_triage(
            _triage_case(), FakeBackend(exc=CompilerError("refused")))
        assert not ok
        assert any("no local triage" in r for r in reasons)

    def test_disposition_expectation(self):
        case = _triage_case(keys=("a",))
        case["expect"]["dispositions"] = {"a": ["looks-rejectable"]}
        ok, reasons, _ = lbe.grade_triage(
            case, FakeBackend([_triage_reply(["a"])]))  # needs-live-check
        assert not ok
        assert any("disposition[a]" in r for r in reasons)


class TestGradeCompile:
    def test_matching_fields_pass(self):
        be = FakeBackend([json.dumps(VALID_RULE)])
        ok, reasons, rule = lbe.grade_compile(_compile_case(), be)
        assert ok, reasons
        assert rule["id"] == "eval_rule"

    def test_wrong_field_fails_with_expected_vs_got(self):
        be = FakeBackend([json.dumps(VALID_RULE)])
        case = _compile_case(fields={"match.kind": "source_error"})
        ok, reasons, _ = lbe.grade_compile(case, be)
        assert not ok
        assert any("expected 'source_error', got 'sensor_breach'" in r
                   for r in reasons)

    def test_fields_range_and_in(self):
        be = FakeBackend([json.dumps(VALID_RULE)])
        case = {"id": "c", "kind": "compile",
                "input": {"intent": "x"},
                "expect": {"fields_in": {"action.kind": ["ntfy", "annotate"]},
                           "fields_range": {"grace_s": [300, 900]}}}
        ok, reasons, _ = lbe.grade_compile(case, be)
        assert ok, reasons

    def test_compile_failure_is_a_failed_case(self):
        be = FakeBackend(["not json", "still not json"])  # both rounds bad
        ok, reasons, _ = lbe.grade_compile(_compile_case(), be)
        assert not ok
        assert any("compile failed" in r for r in reasons)


class TestRunAndGate:
    def test_summary_counts_and_ledger_shape(self):
        be = FakeBackend([_triage_reply(["a", "b"]),
                          json.dumps(VALID_RULE)])
        results, summary = lbe.run_cases(
            [_triage_case(), _compile_case()], be)
        assert summary["total"] == 2 and summary["passed"] == 2
        assert summary["pass_rate"] == 1.0
        assert summary["per_kind"]["triage"] == {"passed": 1, "total": 1}
        assert summary["failed_ids"] == []
        assert all("latency_s" in r for r in results)

    def test_grader_crash_is_a_failed_case_not_a_crash(self):
        case = _triage_case()
        case["input"]["deltas"] = None  # will blow up inside the grader
        _results, summary = lbe.run_cases([case], FakeBackend())
        assert summary["passed"] == 0
        assert summary["failed_ids"] == ["t1"]

    def test_main_gate_fails_below_threshold(self, tmp_path, monkeypatch):
        path = _cases_file(tmp_path, [_triage_case()])
        be = FakeBackend([_triage_reply(["a"])])  # under-coverage -> fail
        monkeypatch.setattr(lbe, "OllamaBackend", lambda **k: be)
        hist = tmp_path / "hist.jsonl"
        rc = lbe.main(["--cases", path, "--history", str(hist),
                       "--gate", "1.0"])
        assert rc == 1
        rec = json.loads(hist.read_text().strip().splitlines()[-1])
        assert rec["passed"] == 0 and rec["results"][0]["id"] == "t1"

    def test_main_passes_and_appends_history(self, tmp_path, monkeypatch):
        path = _cases_file(tmp_path, [_triage_case()])
        be = FakeBackend([_triage_reply(["a", "b"])])
        monkeypatch.setattr(lbe, "OllamaBackend", lambda **k: be)
        hist = tmp_path / "hist.jsonl"
        assert lbe.main(["--cases", path, "--history", str(hist),
                         "--gate", "1.0"]) == 0
        assert json.loads(hist.read_text().strip())["pass_rate"] == 1.0

    def test_malformed_case_file_exits_2(self, tmp_path):
        p = tmp_path / "bad.jsonl"
        p.write_text("{broken")
        assert lbe.main(["--cases", str(p), "--history", ""]) == 2


class TestAttemptsBestOfN:
    """best-of-N retry: pass on the first attempt that grades ok, WITHOUT
    lowering the assertion — the honest fix for a flaky test of a
    CAPABLE-but-non-deterministic tier (triage-w1 acceptance case, 2026-07-15)."""

    def test_retries_until_a_clean_pass(self):
        case = _triage_case()
        case["expect"]["attempts"] = 3
        # attempt 1 under-covers (fail); attempt 2 is complete (pass)
        be = FakeBackend([_triage_reply(["a"]), _triage_reply(["a", "b"])])
        results, summary = lbe.run_cases([case], be)
        assert summary["passed"] == 1 and results[0]["ok"] is True
        assert results[0]["attempts"] == 3
        assert results[0]["attempts_used"] == 2      # stopped on first success
        assert len(be.calls) == 2                    # did NOT burn the 3rd try

    def test_exhausted_attempts_still_fail_never_masking_a_miss(self):
        case = _triage_case()
        case["expect"]["attempts"] = 2
        be = FakeBackend([_triage_reply(["a"]), _triage_reply(["a"])])  # both under
        results, summary = lbe.run_cases([case], be)
        assert summary["passed"] == 0 and results[0]["ok"] is False
        assert results[0]["attempts_used"] == 2      # used every try
        assert len(be.calls) == 2

    def test_default_is_single_shot_no_retry(self):
        case = _triage_case()                        # no attempts key
        be = FakeBackend([_triage_reply(["a"])])     # one under-coverage fail
        results, _ = lbe.run_cases([case], be)
        assert results[0]["ok"] is False
        assert results[0]["attempts"] == 1 and results[0]["attempts_used"] == 1
        assert len(be.calls) == 1                    # exactly one attempt

    def test_attempts_must_be_positive_int_at_load(self):
        for bad in (0, -1, "3", 1.5, True):
            case = _triage_case()
            case["expect"]["attempts"] = bad
            with pytest.raises(lbe.EvalConfigError, match="attempts"):
                lbe._validate_expect(case, "x:1")


class TestSeedFileIsValid:
    def test_shipped_seed_cases_load(self):
        import glob as _glob
        paths = sorted(_glob.glob(lbe.DEFAULT_CASES_GLOB))
        cases = lbe.load_cases(paths)
        assert len(cases) >= 5
        assert all(c.get("provenance") for c in cases), (
            "every eval case must name the real incident that taught it "
            "(the distillation-flywheel convention)")


class TestGradeOracle:
    def _result(self, tier="local", answer="run timeout 8 rnstatus",
                paths=(".claude/foundations/persistent_issues.md",)):
        retrieved = [{"id": f"S{i+1}", "path": p, "heading": "h", "score": 1.0}
                     for i, p in enumerate(paths)]
        return {"brain_tier": tier, "answer": answer if tier == "local" else None,
                "retrieved": retrieved,
                "sources": retrieved if tier == "local" else [],
                "confidence": "high" if tier == "local" else None,
                "note": "synthesis failed (x)" if tier != "local" else None}

    def _case(self, expect=None):
        return {"id": "o1", "kind": "oracle",
                "input": {"question": "rnsd wedged?"},
                "expect": expect or {
                    "retrieve_must_include": ["persistent_issues"],
                    "cite_must_include": ["persistent_issues"],
                    "answer_contains_any": ["rnstatus"]}}

    def test_grounded_cited_answer_passes(self, monkeypatch):
        monkeypatch.setattr(lbe.offline_oracle, "ask",
                            lambda *a, **k: self._result())
        ok, reasons, _ = lbe.grade_oracle(self._case(), FakeBackend())
        assert ok, reasons

    def test_retrieval_miss_and_synthesis_miss_are_distinct(self, monkeypatch):
        monkeypatch.setattr(
            lbe.offline_oracle, "ask",
            lambda *a, **k: self._result(paths=("docs/other.md",)))
        ok, reasons, _ = lbe.grade_oracle(self._case(), FakeBackend())
        assert not ok
        assert any("retrieval missing" in r for r in reasons)
        assert any("citations missing" in r for r in reasons)

    def test_degraded_answer_fails_when_answer_required(self, monkeypatch):
        monkeypatch.setattr(lbe.offline_oracle, "ask",
                            lambda *a, **k: self._result(tier="rules"))
        ok, reasons, _ = lbe.grade_oracle(self._case(), FakeBackend())
        assert not ok
        assert any("no grounded answer" in r for r in reasons)

    def test_answer_content_any_of(self, monkeypatch):
        monkeypatch.setattr(
            lbe.offline_oracle, "ask",
            lambda *a, **k: self._result(answer="reboot everything"))
        ok, reasons, _ = lbe.grade_oracle(self._case(), FakeBackend())
        assert not ok
        assert any("contains none of" in r for r in reasons)


class TestDuplicateKeyInflationGate:
    def test_duplicate_key_reply_cannot_fake_full_coverage(self):
        """2026-07-04 review fix: dup A + missing B must grade 1/2, not 2/2
        — coverage is re-derived from UNIQUE keys, never the witness tally
        (calibrated-claims rule 3)."""
        ok, reasons, _ = lbe.grade_triage(
            _triage_case(), FakeBackend([_triage_reply(["a", "a"])]))
        assert not ok
        assert any("coverage 1/2" in r for r in reasons)


class TestLedgerAppendWitness:
    def test_history_append_error_is_surfaced_not_swallowed(
            self, tmp_path, monkeypatch, capsys):
        """2026-07-04 sweep fix: append_jsonl returns error strings (never
        raises), so the old try/except OSError was dead code that ALSO
        discarded the return — a lost calibration record was doubly silent."""
        cases = tmp_path / "c.jsonl"
        cases.write_text(json.dumps(
            {"id": "t1", "kind": "triage",
             "input": {"deltas": [{"key": "a", "summary": "s"}]},
             "expect": {"coverage_min": 1.0}}) + "\n")
        monkeypatch.setattr(
            lbe, "OllamaBackend",
            lambda **kw: FakeBackend([_triage_reply(["a"])]))
        monkeypatch.setattr(lbe, "append_jsonl",
                            lambda *a, **k: "disk full")
        rc = lbe.main(["--cases", str(cases),
                       "--history", str(tmp_path / "ledger.jsonl")])
        assert rc == 0
        assert "history append failed: disk full" in capsys.readouterr().err


class TestExpectShapeValidation:
    """2026-07-04 sweep fix: malformed expectations fail LOUDLY at load
    (EvalConfigError, rc 2), never at grade time as a 'grader crashed'
    FAILED case that counts against pass_rate and can trip --gate."""

    def test_bad_fields_range_fails_at_load_not_grade(self, tmp_path):
        p = tmp_path / "c.jsonl"
        p.write_text(json.dumps({"id": "t1", "kind": "compile",
                                 "input": {"intent": "x"},
                                 "expect": {"fields_range":
                                            {"grace_s": [1, 2, 3]}}}) + "\n")
        with pytest.raises(lbe.EvalConfigError, match="fields_range"):
            lbe.load_cases([str(p)])

    def test_non_numeric_coverage_min_fails_at_load(self, tmp_path):
        p = tmp_path / "c.jsonl"
        p.write_text(json.dumps({"id": "t1", "kind": "triage",
                                 "input": {"deltas": [{"key": "a"}]},
                                 "expect": {"coverage_min": "high"}}) + "\n")
        with pytest.raises(lbe.EvalConfigError, match="coverage_min"):
            lbe.load_cases([str(p)])

    def test_invalid_utf8_case_file_is_config_error(self, tmp_path):
        p = tmp_path / "c.jsonl"
        p.write_bytes(b'\xff\xfe{"id"}')
        with pytest.raises(lbe.EvalConfigError, match="UTF-8"):
            lbe.load_cases([str(p)])


class TestExpectRefusal:
    """W5.1 refusal-honesty knob (QA session 2026-07-05): an ungroundable
    question must degrade, never confabulate."""

    def _result(self, tier="rules", answer=None):
        return {"brain_tier": tier, "answer": answer,
                "retrieved": [], "sources": [],
                "confidence": None, "note": "no grounded synthesis"}

    def _case(self):
        return {"id": "r1", "kind": "oracle",
                "input": {"question": "Issue #150 root cause?"},
                "expect": {"expect_refusal": True}}

    def test_refusal_passes(self, monkeypatch):
        monkeypatch.setattr(lbe.offline_oracle, "ask",
                            lambda *a, **k: self._result())
        ok, reasons, _ = lbe.grade_oracle(self._case(), FakeBackend())
        assert ok, reasons

    def test_fabricated_grounded_answer_fails(self, monkeypatch):
        monkeypatch.setattr(
            lbe.offline_oracle, "ask",
            lambda *a, **k: self._result(tier="local",
                                         answer="Issue #150 was a dns bug"))
        ok, reasons, _ = lbe.grade_oracle(self._case(), FakeBackend())
        assert not ok
        assert any("fabricated" in r for r in reasons)

    def test_validator_rejects_non_bool(self):
        case = self._case()
        case["expect"] = {"expect_refusal": "yes"}
        with pytest.raises(lbe.EvalConfigError, match="bool"):
            lbe._validate_expect(case, "x:1")

    def test_validator_rejects_conflicting_knobs(self):
        case = self._case()
        case["expect"] = {"expect_refusal": True,
                          "answer_contains_any": ["dns"]}
        with pytest.raises(lbe.EvalConfigError, match="conflicts"):
            lbe._validate_expect(case, "x:1")

    def test_seed_refusal_case_loads(self):
        # the shipped case must survive load-time validation
        import os
        cases = lbe.load_cases([os.path.join(
            lbe._REPO_ROOT, "evals", "local_brain", "seed.jsonl")])
        ids = {c["id"] for c in cases}
        assert "oracle-refusal-unknown-issue" in ids


# ── ClaudeCLIBackend + backend-aware tiers (haiku_watcher_eval charter) ──

class TestClaudeCLIBackend:
    """The QTH middle-rung candidate transport. Contract: same seam as
    OllamaBackend — text out, CompilerError on ANY failure, never a fake
    reply. All transport mocked; no CLI is spawned in CI."""

    def _be(self, **kw):
        from mini_dudeai.chat_compiler import ClaudeCLIBackend
        return ClaudeCLIBackend(**kw)

    def _proc(self, rc=0, out="", err=""):
        class P:
            returncode = rc
            stdout = out
            stderr = err
        return P()

    def test_tier_declarations(self):
        from mini_dudeai.chat_compiler import ClaudeCLIBackend, OllamaBackend
        # hfm #5: graders + stampers key on these two constants
        assert OllamaBackend.brain_tier == "local"
        assert ClaudeCLIBackend.brain_tier == "api_small"

    def test_happy_path_strips_fences(self, monkeypatch):
        import subprocess as sp
        be = self._be()
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return self._proc(out="```json\n{\"ok\": true}\n```\n")
        monkeypatch.setattr(sp, "run", fake_run)
        assert be.complete("sys", "user") == '{"ok": true}'
        assert "--model" in seen["cmd"]
        assert "claude-haiku-4-5" in seen["cmd"]

    def test_schema_fmt_rides_in_the_prompt(self, monkeypatch):
        import subprocess as sp
        be = self._be()
        seen = {}

        def fake_run(cmd, **kw):
            seen["prompt"] = cmd[cmd.index("-p") + 1]
            return self._proc(out="{}")
        monkeypatch.setattr(sp, "run", fake_run)
        be.complete("sys", "user", fmt={"type": "object"})
        assert "JSON Schema" in seen["prompt"]
        assert '"object"' in seen["prompt"]

    def test_nonzero_rc_is_loud(self, monkeypatch):
        import subprocess as sp
        from mini_dudeai.chat_compiler import CompilerError
        be = self._be()
        monkeypatch.setattr(sp, "run",
                            lambda *a, **k: self._proc(rc=1, err="no access"))
        with pytest.raises(CompilerError, match="rc=1"):
            be.complete("s", "u")

    def test_missing_cli_is_loud(self, monkeypatch):
        import subprocess as sp
        from mini_dudeai.chat_compiler import CompilerError

        def raise_fnf(*a, **k):
            raise FileNotFoundError("claude")
        monkeypatch.setattr(sp, "run", raise_fnf)
        with pytest.raises(CompilerError, match="not on PATH"):
            self._be().complete("s", "u")

    def test_timeout_is_loud(self, monkeypatch):
        import subprocess as sp
        from mini_dudeai.chat_compiler import CompilerError

        def raise_to(*a, **k):
            raise sp.TimeoutExpired(cmd="claude", timeout=1)
        monkeypatch.setattr(sp, "run", raise_to)
        with pytest.raises(CompilerError, match="exceeded"):
            self._be(timeout_s=1).complete("s", "u")

    def test_empty_reply_is_loud(self, monkeypatch):
        import subprocess as sp
        from mini_dudeai.chat_compiler import CompilerError
        monkeypatch.setattr(sp, "run", lambda *a, **k: self._proc(out="  \n"))
        with pytest.raises(CompilerError, match="no content"):
            self._be().complete("s", "u")


class ApiSmallFakeBackend(FakeBackend):
    """A fake declaring the api_small tier — pins that tier flows from the
    BACKEND through the stampers into the graders."""
    brain_tier = "api_small"
    model = "claude-haiku-4-5"
    url = "cli:claude"


class TestBackendAwareTiers:
    def test_triage_witness_carries_the_backends_tier(self):
        ok, reasons, witness = lbe.grade_triage(
            _triage_case(), ApiSmallFakeBackend([_triage_reply(["a", "b"])]))
        assert ok, reasons
        assert witness["brain_tier"] == "api_small"

    def test_default_backend_still_stamps_local(self):
        # the production Ollama path must be byte-identical in behavior
        ok, reasons, witness = lbe.grade_triage(
            _triage_case(), FakeBackend([_triage_reply(["a", "b"])]))
        assert ok, reasons
        assert witness["brain_tier"] == "local"

    def test_summary_carries_backend_identity(self):
        # ADDITIVE ledger keys: trend readers must be able to split the two
        # calibration histories; blending them would poison tier-L's record
        results, summary = lbe.run_cases(
            [_triage_case()], ApiSmallFakeBackend([_triage_reply(["a", "b"])]))
        assert summary["backend"] == "ApiSmallFakeBackend"
        assert summary["brain_tier"] == "api_small"
        assert summary["model"] == "claude-haiku-4-5"


# ── budget chunking + per-case progress (2026-07-21 timeout fix) ─────────
# The weekly cron burned its full `timeout 6000` and reported NOTHING:
# 33 cases × ~340s ≈ 3.1h, and all output/ledger writes happened only at
# the end — a timeout wiped the whole run (honest_failure_modes #9, no
# partial witness). Cure: --budget-s stops STARTING cases in time, the
# progress hook emits per-case evidence as it lands, and --cursor walks
# the full set across successive cron firings.

class TestBudgetChunking:
    def _three_cases(self):
        return [_triage_case(f"t{i}", keys=("a",)) for i in range(3)]

    def _backend(self, n=3):
        return FakeBackend([_triage_reply(["a"])] * n)

    def test_budget_zero_still_runs_one_case(self):
        # every firing must make progress or the cursor never advances
        results, summary = lbe.run_cases(
            self._three_cases(), self._backend(), budget_s=0)
        assert len(results) == 1
        assert summary["budget_exhausted"] is True
        assert summary["not_run_ids"] == ["t1", "t2"]
        assert summary["planned_total"] == 3

    def test_no_budget_runs_all_and_flags_stay_honest(self):
        results, summary = lbe.run_cases(
            self._three_cases(), self._backend())
        assert len(results) == 3
        assert summary["budget_exhausted"] is False
        assert summary["not_run_ids"] == []
        assert summary["planned_total"] == 3

    def test_deferred_cases_never_touch_pass_rate(self):
        # deferred ≠ failed: 1 completed pass over 2 deferred = rate 1.0
        _results, summary = lbe.run_cases(
            self._three_cases(), self._backend(), budget_s=0)
        assert summary["total"] == 1
        assert summary["pass_rate"] == 1.0
        assert summary["failed_ids"] == []


class TestProgressCallback:
    def test_fires_per_case_in_order_with_result(self):
        seen = []
        cases = [_triage_case("t1", keys=("a",)),
                 _triage_case("t2", keys=("a",))]
        lbe.run_cases(cases, FakeBackend([_triage_reply(["a"])] * 2),
                      progress=lambda done, planned, r:
                      seen.append((done, planned, r["id"], r["ok"])))
        assert seen == [(1, 2, "t1", True), (2, 2, "t2", True)]


class TestCursorRotation:
    def _cases(self):
        return [_triage_case(f"t{i}") for i in range(3)]

    def test_rotates_to_start_after_last_completed(self):
        rotated = lbe._rotate_cases(self._cases(), "t0")
        assert [c["id"] for c in rotated] == ["t1", "t2", "t0"]

    def test_unknown_last_id_starts_from_top(self):
        # case-set edits self-heal instead of crashing the weekly run
        rotated = lbe._rotate_cases(self._cases(), "gone")
        assert [c["id"] for c in rotated] == ["t0", "t1", "t2"]

    def test_none_is_identity(self):
        assert lbe._rotate_cases(self._cases(), None) == self._cases()

    def test_missing_cursor_file_is_fresh_start(self, tmp_path):
        assert lbe._read_cursor(str(tmp_path / "absent.json")) is None

    def test_corrupt_cursor_warns_and_starts_fresh(self, tmp_path, capsys):
        p = tmp_path / "cursor.json"
        p.write_text("{torn")
        assert lbe._read_cursor(str(p)) is None
        assert "cursor" in capsys.readouterr().err

    def test_write_read_roundtrip(self, tmp_path):
        p = str(tmp_path / "cursor.json")
        lbe._write_cursor(p, "t7")
        assert lbe._read_cursor(p) == "t7"


class TestMainChunkedRuns:
    """Two budget-limited main() firings walk the set — the shape the
    weekly cron now runs (--budget-s + --cursor)."""

    def _cases_file(self, tmp_path):
        cases = [{"id": f"t{i}", "kind": "triage",
                  "input": {"deltas": [{"key": "a", "summary": "s"}]},
                  "expect": {"coverage_min": 1.0}} for i in range(3)]
        p = tmp_path / "c.jsonl"
        p.write_text("\n".join(json.dumps(c) for c in cases) + "\n")
        return str(p)

    def test_successive_runs_advance_through_the_set(
            self, tmp_path, monkeypatch, capsys):
        cases = self._cases_file(tmp_path)
        cursor = str(tmp_path / "cursor.json")
        monkeypatch.setattr(
            lbe, "OllamaBackend",
            lambda **kw: FakeBackend([_triage_reply(["a"])] * 3))
        rc = lbe.main(["--cases", cases, "--cursor", cursor,
                       "--budget-s", "0", "--history", ""])
        assert rc == 0
        err = capsys.readouterr().err
        assert "[1/3] PASS  t0" in err          # per-case witness lands live
        assert "deferred to the next run" in err
        assert lbe._read_cursor(cursor) == "t0"
        rc = lbe.main(["--cases", cases, "--cursor", cursor,
                       "--budget-s", "0", "--history", ""])
        assert rc == 0
        assert "[1/3] PASS  t1" in capsys.readouterr().err
        assert lbe._read_cursor(cursor) == "t1"  # walked, not re-graded

    def test_gate_judges_only_completed_cases(
            self, tmp_path, monkeypatch):
        # a passing chunk must clear the gate even with cases deferred
        cases = self._cases_file(tmp_path)
        monkeypatch.setattr(
            lbe, "OllamaBackend",
            lambda **kw: FakeBackend([_triage_reply(["a"])] * 3))
        rc = lbe.main(["--cases", cases, "--budget-s", "0",
                       "--gate", "0.85", "--history", ""])
        assert rc == 0
