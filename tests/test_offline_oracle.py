"""Tests for the offline oracle (W3 — mini_dudeai.offline_oracle).

Under test: chunking anchors, BM25 ranking sanity, and the citation
honesty layer — the model can only cite what it was shown; an answer whose
citations all evaporate is discarded (never presented as grounded), and a
missing corpus root is reported, not silently folded into "searched
everything".
"""
from __future__ import annotations

import json

from mini_dudeai import offline_oracle as oo
from mini_dudeai.chat_compiler import CompilerError


class FakeBackend:
    model = "fake:4b"

    def __init__(self, reply=None, exc=None):
        self.reply = reply
        self.exc = exc
        self.calls = []

    def complete(self, system, user, fmt="json"):
        self.calls.append((system, user, fmt))
        if self.exc:
            raise self.exc
        return self.reply


def _corpus(tmp_path):
    (tmp_path / "issues.md").write_text(
        "# Issue #72: wedged rnsd RPC\n\nrnstatus hangs though the socket "
        "accepts. Quick check: timeout 8 rnstatus. Recovery: restart rnsd "
        "then RNS-using services.\n\n# Issue #40: bridge bytes\n\ndecode "
        "bytes to str at entry.\n")
    (tmp_path / "other.md").write_text(
        "# Unrelated banana document\n\nbananas are yellow and curved.\n")
    return [("test", str(tmp_path / "*.md"))]


def _oracle_reply(ids, answer="restart rnsd", confidence="high"):
    return json.dumps({"answer": answer, "source_ids": ids,
                       "confidence": confidence})


class TestChunkAndRank:
    def test_chunks_are_heading_anchored(self, tmp_path):
        roots = _corpus(tmp_path)
        chunks, notes = oo.load_corpus(roots)
        assert notes == []
        headings = {c["heading"] for c in chunks}
        assert any("#72" in h for h in headings)

    def test_missing_root_is_reported_not_silent(self, tmp_path):
        chunks, notes = oo.load_corpus([("ghost", str(tmp_path / "no" / "*.md"))])
        assert chunks == []
        assert any("ghost" in n for n in notes)

    def test_bm25_ranks_the_relevant_doc_first(self, tmp_path):
        chunks, _ = oo.load_corpus(_corpus(tmp_path))
        hits = oo.rank(chunks, "rnstatus hangs wedged rpc", top_k=3)
        assert hits and "#72" in hits[0]["heading"]
        assert all("banana" not in h["heading"] for h in hits)

    def test_no_term_overlap_returns_nothing(self, tmp_path):
        chunks, _ = oo.load_corpus(_corpus(tmp_path))
        assert oo.rank(chunks, "zzz qqq xyzzy", top_k=3) == []

    def test_issue_number_tokens_survive(self):
        assert "#72" in oo.tokenize("the Issue #72 shape")


class TestAskCitations:
    def test_cited_answer_is_local_tier(self, tmp_path):
        be = FakeBackend(_oracle_reply(["S1"]))
        r = oo.ask("rnstatus wedged", be, roots=_corpus(tmp_path))
        assert r["brain_tier"] == "local"
        assert r["answer"] == "restart rnsd"
        assert [s["id"] for s in r["sources"]] == ["S1"]
        assert r["confidence"] == "high"

    def test_invented_citations_are_dropped(self, tmp_path):
        be = FakeBackend(_oracle_reply(["S1", "S9"]))
        r = oo.ask("rnstatus wedged", be, roots=_corpus(tmp_path))
        assert r["brain_tier"] == "local"
        assert [s["id"] for s in r["sources"]] == ["S1"]
        assert r["invented_citations"] == ["S9"]

    def test_all_citations_invented_discards_the_answer(self, tmp_path):
        be = FakeBackend(_oracle_reply(["S9"]))
        r = oo.ask("rnstatus wedged", be, roots=_corpus(tmp_path))
        assert r["brain_tier"] == "rules"
        assert r["answer"] is None
        assert "cited nothing" in r["note"]
        assert r["retrieved"]  # retrieval is still served

    def test_llm_down_degrades_to_retrieval(self, tmp_path):
        be = FakeBackend(exc=CompilerError("ollama down"))
        r = oo.ask("rnstatus wedged", be, roots=_corpus(tmp_path))
        assert r["brain_tier"] == "rules"
        assert "synthesis failed" in r["note"]
        assert r["retrieved"]

    def test_malformed_reply_degrades(self, tmp_path):
        be = FakeBackend(json.dumps({"answer": "", "source_ids": [],
                                     "confidence": "high"}))
        r = oo.ask("rnstatus wedged", be, roots=_corpus(tmp_path))
        assert r["brain_tier"] == "rules" and r["answer"] is None

    def test_zero_hits_never_calls_the_llm(self, tmp_path):
        be = FakeBackend(_oracle_reply(["S1"]))
        r = oo.ask("xyzzy qqq zzz", be, roots=_corpus(tmp_path))
        assert r["brain_tier"] == "rules"
        assert be.calls == []
        assert "found nothing" in r["note"]

    def test_schema_goes_down_the_wire(self, tmp_path):
        be = FakeBackend(_oracle_reply(["S1"]))
        oo.ask("rnstatus wedged", be, roots=_corpus(tmp_path))
        assert be.calls[0][2] == oo.ORACLE_SCHEMA


class TestReadOnlyTripwire:
    def test_module_never_writes_state(self):
        import inspect
        src = inspect.getsource(oo)
        for forbidden in ("atomic_write", "write_candidate", "memory_apply",
                          "append_jsonl", "os.remove", "shutil"):
            assert forbidden not in src, (
                f"offline_oracle is read-only by charter — {forbidden} "
                f"does not belong here")


class TestSubtokenEmission:
    def test_compound_jargon_also_emits_parts(self):
        # The map-wedge eval finding (run 3, 2026-07-03): fused corpus
        # jargon must be reachable by its parts.
        toks = oo.tokenize("http_local_unresponsive json.dumps rns 1.2.5+mf.5")
        assert "http_local_unresponsive" in toks  # precision kept
        assert "unresponsive" in toks             # recall gained
        assert "json" in toks and "dumps" in toks
        assert "mf" in toks
