"""Tests for the local-tier cadence fallback (W1 — mini_dudeai.cadence_fallback).

The invariants under test are the HONESTY ones, not the LLM's quality:
a local product is always stamped brain_tier and never_ratifies; a failed
LLM degrades to a deterministic rules-tier note (never a fabricated triage);
the model cannot invent backlog (unknown keys dropped); and the module
never touches ratification machinery (source-scan tripwire).
"""
from __future__ import annotations

import inspect
import json
import os

import pytest

from mini_dudeai import cadence_fallback as cf
from mini_dudeai.brief import build_brief
from mini_dudeai.chat_compiler import CompilerError

NOW = 1_800_000_000.0


def _delta(key, status="proposed", summary="s"):
    return {"ts": NOW - 100, "kind": "escalation", "key": key,
            "status": status, "summary": summary}


def _deltas_file(tmp_path, deltas, torn_tail=False):
    p = tmp_path / "deltas.jsonl"
    text = "\n".join(json.dumps(d) for d in deltas)
    if torn_tail:
        text += '\n{"ts": 1, "key": "torn'
    p.write_text(text + "\n")
    return str(p)


class FakeBackend:
    model = "fake:3b"

    def __init__(self, reply=None, exc=None):
        self.reply = reply
        self.exc = exc
        self.calls = []

    def complete(self, system, user, fmt="json"):
        self.calls.append((system, user, fmt))
        if self.exc:
            raise self.exc
        return self.reply


def _good_reply(keys):
    return json.dumps({
        "summary": "backlog looks routine",
        "deltas": [{"key": k, "assessment": f"about {k}",
                    "suggested_disposition": "needs-live-check"}
                   for k in keys],
    })


class TestLoadProposedDeltas:
    def test_filters_status_caps_and_counts_total(self, tmp_path):
        deltas = [_delta(f"k{i}") for i in range(5)] + [
            _delta("ratified1", status="ratified")]
        path = _deltas_file(tmp_path, deltas)
        got, total = cf.load_proposed_deltas(path, cap=3)
        assert [d["key"] for d in got] == ["k0", "k1", "k2"]
        assert total == 5  # cap limits the fed set, never the honest count

    def test_torn_tail_is_skipped_not_fatal(self, tmp_path):
        path = _deltas_file(tmp_path, [_delta("a")], torn_tail=True)
        got, total = cf.load_proposed_deltas(path)
        assert [d["key"] for d in got] == ["a"] and total == 1

    def test_missing_file_is_zero(self, tmp_path):
        assert cf.load_proposed_deltas(str(tmp_path / "nope")) == ([], 0)


class TestRun:
    def test_local_triage_is_stamped_and_bounded(self, tmp_path):
        path = _deltas_file(tmp_path, [_delta("a"), _delta("b")])
        be = FakeBackend(reply=_good_reply(["a", "b"]))
        w = cf.run(path, be, frontier_rc=1, now=NOW)
        assert w["brain_tier"] == "local"
        assert w["never_ratifies"] is True
        assert w["frontier_rc"] == 1
        assert w["triaged"] == 2 and w["proposed_total"] == 2
        assert w["model"] == "fake:3b"
        # the schema went down the wire (constrained decoding, not hope)
        assert be.calls[0][2] == cf.TRIAGE_SCHEMA

    def test_llm_failure_degrades_to_rules_note(self, tmp_path):
        path = _deltas_file(tmp_path, [_delta("a")])
        be = FakeBackend(exc=CompilerError("ollama down"))
        w = cf.run(path, be, frontier_rc=None, now=NOW)
        assert w["brain_tier"] == "rules"
        assert w["triaged"] == 0 and w["deltas"] == []
        assert "ollama down" in w["error"]
        assert "pending" in w["summary"]  # honest: backlog exists, untriaged

    def test_garbage_reply_degrades_not_fabricates(self, tmp_path):
        path = _deltas_file(tmp_path, [_delta("a")])
        w = cf.run(path, FakeBackend(reply="not json {"), frontier_rc=2,
                   now=NOW)
        assert w["brain_tier"] == "rules" and "error" in w

    def test_invented_keys_are_dropped(self, tmp_path):
        path = _deltas_file(tmp_path, [_delta("real")])
        reply = json.dumps({"summary": "ok", "deltas": [
            {"key": "real", "assessment": "fine",
             "suggested_disposition": "looks-ratifiable"},
            {"key": "invented", "assessment": "??",
             "suggested_disposition": "looks-ratifiable"},
        ]})
        w = cf.run(path, FakeBackend(reply=reply), frontier_rc=1, now=NOW)
        assert w["brain_tier"] == "local"
        assert [d["key"] for d in w["deltas"]] == ["real"]
        assert w["dropped_entries"] == 1

    def test_all_entries_invalid_is_a_failure_not_empty_success(self, tmp_path):
        path = _deltas_file(tmp_path, [_delta("real")])
        reply = json.dumps({"summary": "ok", "deltas": [
            {"key": "invented", "assessment": "x",
             "suggested_disposition": "looks-ratifiable"}]})
        w = cf.run(path, FakeBackend(reply=reply), frontier_rc=1, now=NOW)
        assert w["brain_tier"] == "rules" and "error" in w

    def test_assessment_clamped(self, tmp_path):
        path = _deltas_file(tmp_path, [_delta("a")])
        reply = json.dumps({"summary": "ok", "deltas": [
            {"key": "a", "assessment": "x" * 900,
             "suggested_disposition": "needs-live-check"}]})
        w = cf.run(path, FakeBackend(reply=reply), frontier_rc=1, now=NOW)
        assert len(w["deltas"][0]["assessment"]) == 300

    def test_no_proposed_deltas_is_a_rules_note(self, tmp_path):
        path = _deltas_file(tmp_path, [_delta("done", status="ratified")])
        be = FakeBackend(reply=_good_reply([]))
        w = cf.run(path, be, frontier_rc=None, now=NOW)
        assert w["brain_tier"] == "rules" and w["proposed_total"] == 0
        assert be.calls == []  # no backlog -> no token spend


class TestNeverRatifiesTripwire:
    def test_module_never_imports_ratification_machinery(self):
        # The fallback's hard invariant, pinned at the source level: the ONLY
        # write is the triage witness. If someone wires memory_apply, rule
        # candidates, or delta resolution into this module, this fails first.
        src = inspect.getsource(cf)
        for forbidden in ("memory_apply", "write_candidate", "resolve_delta",
                          "merge_seed_rules"):
            assert forbidden not in src, (
                f"cadence_fallback must never touch {forbidden} — "
                f"the local tier triages, the frontier ratifies")


class TestMainCli:
    def test_main_writes_witness_and_exits_zero(self, tmp_path, monkeypatch):
        deltas = _deltas_file(tmp_path, [_delta("a")])
        out = tmp_path / "witness.json"
        fake = FakeBackend(reply=_good_reply(["a"]))
        monkeypatch.setattr(cf, "OllamaBackend", lambda **k: fake)
        rc = cf.main(["--deltas", deltas, "--out", str(out),
                      "--frontier-rc", "7"])
        assert rc == 0
        w = json.loads(out.read_text())
        assert w["brain_tier"] == "local" and w["frontier_rc"] == 7

    def test_main_cli_missing_frontier_rc_is_null(self, tmp_path, monkeypatch):
        deltas = _deltas_file(tmp_path, [_delta("a")])
        out = tmp_path / "witness.json"
        monkeypatch.setattr(cf, "OllamaBackend",
                            lambda **k: FakeBackend(reply=_good_reply(["a"])))
        assert cf.main(["--deltas", deltas, "--out", str(out),
                        "--frontier-rc", ""]) == 0
        assert json.loads(out.read_text())["frontier_rc"] is None


class TestBriefSection:
    def _witness(self, tier="local", ts=NOW - 60, frc=1):
        return {"ts": ts, "brain_tier": tier, "frontier_rc": frc,
                "proposed_total": 3, "triaged": 3 if tier == "local" else 0,
                "model": "fake:3b", "summary": "backlog looks routine",
                "never_ratifies": True}

    def test_local_tier_section_renders(self):
        text = build_brief({}, [], NOW, cadence_triage=self._witness())
        assert "cadence ran on LOCAL tier" in text
        assert "SUGGESTIONS ONLY, nothing ratified" in text
        assert "frontier rc=1" in text

    def test_rules_tier_section_renders(self):
        text = build_brief({}, [], NOW,
                           cadence_triage=self._witness(tier="rules", frc=None))
        assert "fell to RULES tier" in text
        assert "UNTRIAGED" in text
        assert "claude CLI missing" in text

    def test_stale_witness_renders_nothing(self):
        old = self._witness(ts=NOW - cf.TRIAGE_FRESH_S - 1)
        text = build_brief({}, [], NOW, cadence_triage=old)
        assert "LOCAL tier" not in text

    def test_absent_witness_renders_nothing(self):
        text = build_brief({}, [], NOW, cadence_triage=None)
        assert "LOCAL tier" not in text

    def test_future_ts_witness_renders_nothing(self):
        # A clock step must not forge freshness (RTC-less discipline).
        w = self._witness(ts=NOW + 3600)
        assert "LOCAL tier" not in build_brief({}, [], NOW, cadence_triage=w)
