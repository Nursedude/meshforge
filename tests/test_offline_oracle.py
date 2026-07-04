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


class TestCitationShownBudget:
    """2026-07-04 review fix: the excerpt budget can truncate retrieved hits
    out of the prompt; citations must validate against what was SHOWN, not
    what was retrieved (an unshown cite is exactly as invented as S9)."""

    def test_retrieved_but_unshown_citation_is_dropped(self, tmp_path,
                                                       monkeypatch):
        monkeypatch.setattr(oo, "_EXCERPT_CHARS_BUDGET", 10)  # only S1 fits
        be = FakeBackend(_oracle_reply(["S1", "S2"]))
        r = oo.ask("rnstatus wedged bridge bytes", be,
                   roots=_corpus(tmp_path))
        assert len(r["retrieved"]) >= 2          # S2 WAS retrieved...
        assert r["excerpts_shown"] == 1          # ...but never shown
        assert [s["id"] for s in r["sources"]] == ["S1"]
        assert "S2" in r["invented_citations"]

    def test_only_unshown_citations_discards_the_answer(self, tmp_path,
                                                        monkeypatch):
        monkeypatch.setattr(oo, "_EXCERPT_CHARS_BUDGET", 10)
        be = FakeBackend(_oracle_reply(["S2"]))
        r = oo.ask("rnstatus wedged bridge bytes", be,
                   roots=_corpus(tmp_path))
        assert r["brain_tier"] == "rules"
        assert r["answer"] is None
        assert "cited nothing" in r["note"]


class TestAskReplyShapeGuards:
    """2026-07-04 sweep fix: valid-JSON-but-wrong-shape replies must degrade
    honestly, never AttributeError/TypeError past the handler."""

    def test_non_object_json_reply_degrades_not_crashes(self, tmp_path):
        be = FakeBackend(json.dumps(["not", "an", "object"]))
        r = oo.ask("rnstatus wedged", be, roots=_corpus(tmp_path))
        assert r["brain_tier"] == "rules" and r["answer"] is None
        assert "synthesis failed" in r["note"]

    def test_non_string_source_ids_degrade_not_typeerror(self, tmp_path):
        be = FakeBackend(json.dumps({"answer": "a",
                                     "source_ids": [{"id": "S1"}],
                                     "confidence": "high"}))
        r = oo.ask("rnstatus wedged", be, roots=_corpus(tmp_path))
        assert r["brain_tier"] == "rules" and r["answer"] is None


class TestIndexCache:
    """2026-07-04 efficiency fix: mtime-keyed in-process corpus+index cache.
    The charter forbids a PERSISTED index (goes stale on disk); this cache
    self-invalidates the moment any lore file changes."""

    def test_second_call_reuses_the_index(self, tmp_path, monkeypatch):
        roots = _corpus(tmp_path)
        c1, _n1, i1 = oo.load_corpus_indexed(roots)

        def boom(_roots=None):
            raise AssertionError("corpus rebuilt despite unchanged files")
        monkeypatch.setattr(oo, "load_corpus", boom)
        c2, _n2, i2 = oo.load_corpus_indexed(roots)
        assert c2 is c1 and i2 is i1

    def test_file_change_invalidates(self, tmp_path):
        roots = _corpus(tmp_path)
        c1, _, _ = oo.load_corpus_indexed(roots)
        f = tmp_path / "issues.md"
        f.write_text(f.read_text() + "\n# Issue #99: fresh lore\n\nnew fact\n")
        c2, _, _ = oo.load_corpus_indexed(roots)
        assert c2 is not c1
        assert any("#99" in c["heading"] for c in c2)

    def test_prebuilt_index_matches_fresh_ranking(self, tmp_path):
        chunks, _, index = oo.load_corpus_indexed(_corpus(tmp_path))
        assert (oo.rank(chunks, "rnstatus wedged", index=index)
                == oo.rank(chunks, "rnstatus wedged"))


class TestRetrieveOnlyShared:
    """2026-07-04 reuse fix: ONE retrieve-only assembly (CLI + TUI)."""

    def test_retrieve_only_is_the_one_assembly(self, tmp_path):
        r = oo.retrieve_only("rnstatus wedged", roots=_corpus(tmp_path),
                             top_k=3)
        assert r["brain_tier"] == "rules"
        assert r["note"] == "retrieval only (local LLM not used)"
        assert r["retrieved"]
        assert set(r["retrieved"][0]) == {"id", "path", "heading", "score"}


class TestMemoryDirSudoSafe:
    """2026-07-04 review fix: the memory corpus root must survive the
    sudo-launched TUI (expanduser('~') under sudo is /root — the operator's
    2+ MB memory corpus silently vanished from the in-app oracle)."""

    def test_memory_dir_honors_sudo_user(self, monkeypatch):
        import pwd
        import types
        monkeypatch.setenv("SUDO_USER", "op")
        monkeypatch.setattr(pwd, "getpwnam",
                            lambda u: types.SimpleNamespace(pw_dir="/home/op"))
        assert oo._memory_dir().startswith("/home/op/")

    def test_memory_dir_without_sudo_uses_home(self, monkeypatch):
        import os
        monkeypatch.delenv("SUDO_USER", raising=False)
        assert oo._memory_dir().startswith(os.path.expanduser("~"))
