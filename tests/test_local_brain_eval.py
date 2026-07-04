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


class TestSeedFileIsValid:
    def test_shipped_seed_cases_load(self):
        import glob as _glob
        paths = sorted(_glob.glob(lbe.DEFAULT_CASES_GLOB))
        cases = lbe.load_cases(paths)
        assert len(cases) >= 5
        assert all(c.get("provenance") for c in cases), (
            "every eval case must name the real incident that taught it "
            "(the distillation-flywheel convention)")
