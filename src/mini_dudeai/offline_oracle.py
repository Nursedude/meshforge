"""Offline oracle — W3 of the degraded-brain ladder
(.claude/research/dudeclaw_local_brain_2026_07_03.md §4).

When the frontier is away, the fleet's accumulated lore — persistent issues,
research docs, rules, runbooks, memory topic files — is still on disk. This
module makes it answerable offline:

  retrieval  pure-Python BM25 over heading-chunked markdown, built fresh
             per query (the corpus is ~3 MB; a persistent index would just
             be one more artifact that can go stale — honest by construction).
             Lexical FIRST: on this corpus (small, jargon-dense, well-titled)
             lexical likely beats embeddings per token; the eval harness's
             oracle cases are the gate that decides if that ever changes.
  synthesis  the local LLM answers ONLY from the retrieved excerpts under a
             citation-forcing schema. The model cites excerpt ids it was
             actually SHOWN — an invented citation is dropped (the model
             cannot cite what it wasn't given), and if no citation survives
             the answer degrades to retrieve-only output: "here is what
             retrieval found; the synthesis failed" is honest, a confident
             uncited answer is not (#80).

Tier provenance: a synthesized answer is stamped ``brain_tier: local``;
``--retrieve-only`` output is deterministic (tier R) and needs no LLM at
all — the oracle stays useful even when Ollama is down.

Read-only by charter: this module reads markdown and calls the local model.
It never writes state, rules, or memory.
"""
from __future__ import annotations

import argparse
import glob as _glob
import json
import math
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

from ._util import operator_home
from .chat_compiler import (
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    LOCAL_BRAIN_TIMEOUT_S,
    CompilerError,
    OllamaBackend,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


def _memory_dir() -> str:
    """The Claude memory topic-file dir for THIS repo, derived (never
    hardcoded — the slug is the repo path with '/' → '-'). Anchored on
    operator_home(), NOT ~: the TUI runs under sudo, where expanduser('~')
    is /root and the operator's 2+ MB memory corpus would silently drop
    out of the oracle while the non-sudo CLI still saw it."""
    slug = _REPO_ROOT.replace(os.sep, "-")
    return os.path.join(operator_home(), ".claude", "projects",
                        slug, "memory")


def default_roots() -> List[Tuple[str, str]]:
    """(label, glob) corpus roots. Missing roots are skipped with a note —
    absence of a root is corpus shape, not an error (e.g. the memory dir
    exists only on the box that hosts the sessions)."""
    return [
        ("issues", os.path.join(_REPO_ROOT, ".claude", "foundations",
                                "persistent_issues*.md")),
        ("foundations", os.path.join(_REPO_ROOT, ".claude", "foundations",
                                     "*.md")),
        ("rules", os.path.join(_REPO_ROOT, ".claude", "rules", "*.md")),
        ("research", os.path.join(_REPO_ROOT, ".claude", "research", "*.md")),
        ("docs", os.path.join(_REPO_ROOT, "docs", "*.md")),
        ("memory", os.path.join(_memory_dir(), "*.md")),
    ]


# ── chunking + tokenizing ───────────────────────────────────────────

_MAX_CHUNK_CHARS = 2400
# Keep issue refs (#74), snake_case, dotted names, and +suffix versions —
# the corpus's load-bearing jargon.
_TOKEN_RE = re.compile(r"#\d+|[a-z0-9_.+]{2,}")
_SPLIT_RE = re.compile(r"[._+]+")


def tokenize(text: str) -> List[str]:
    """Compound jargon tokens ALSO emit their parts: without this,
    ``http_local_unresponsive`` never matches a query's "unresponsive" and
    the lore hides behind its own precision (found by the first oracle eval
    run — the map-wedge case ranked memory notes above the issue rows)."""
    out: List[str] = []
    for tok in _TOKEN_RE.findall(text.lower()):
        out.append(tok)
        if "_" in tok or "." in tok or "+" in tok:
            out.extend(p for p in _SPLIT_RE.split(tok) if len(p) >= 2)
    return out


def chunk_file(path: str, label: str) -> List[dict]:
    """Split a markdown file into heading-anchored chunks (long sections
    split further). Unreadable file → [] (the caller counts skips)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    rel = os.path.relpath(path, _REPO_ROOT) if path.startswith(_REPO_ROOT) \
        else path
    chunks: List[dict] = []
    heading = os.path.basename(path)
    buf: List[str] = []

    def flush():
        body = "\n".join(buf).strip()
        buf.clear()
        if not body:
            return
        for i in range(0, len(body), _MAX_CHUNK_CHARS):
            part = body[i:i + _MAX_CHUNK_CHARS]
            chunks.append({"path": rel, "label": label, "heading": heading,
                           "text": part})

    for line in text.splitlines():
        if line.startswith("#"):
            flush()
            heading = line.lstrip("#").strip() or heading
        buf.append(line)
    flush()
    return chunks


def load_corpus(roots: Optional[List[Tuple[str, str]]] = None
                ) -> Tuple[List[dict], List[str]]:
    """All chunks + notes about skipped roots (absence is reported, never
    silently folded into 'searched everything')."""
    roots = roots if roots is not None else default_roots()
    chunks: List[dict] = []
    notes: List[str] = []
    seen: set = set()
    for label, pattern in roots:
        paths = sorted(_glob.glob(pattern))
        if not paths:
            notes.append(f"root '{label}' empty/absent ({pattern})")
            continue
        for p in paths:
            real = os.path.realpath(p)
            if real in seen:  # overlapping globs (issues ⊂ foundations)
                continue
            seen.add(real)
            chunks.extend(chunk_file(p, label))
    return chunks, notes


# ── BM25 ────────────────────────────────────────────────────────────

def build_index(chunks: List[dict]) -> dict:
    """Tokenized docs + document frequencies + avg length — everything in
    BM25 that depends only on the CORPUS, not the query. Building this per
    query re-tokenized ~3 MB of markdown every time; it is pure function of
    the chunks, so it caches with them (load_corpus_indexed)."""
    docs = [tokenize(c["text"]) + tokenize(c["heading"]) for c in chunks]
    n = len(docs)
    df: Dict[str, int] = {}
    for d in docs:
        for t in set(d):
            df[t] = df.get(t, 0) + 1
    return {"docs": docs, "df": df,
            "avg_len": (sum(len(d) for d in docs) / n) if n else 0.0}


# In-process corpus+index cache keyed by the corpus's observable identity
# (every globbed path + mtime_ns + size). The module charter rejects a
# PERSISTENT index (an on-disk artifact that can go stale); this cache is
# process-local and self-invalidates the moment any lore file changes, so
# the honesty argument holds while a TUI session / eval run stops paying a
# full ~3 MB re-read + re-tokenize per question.
_INDEX_CACHE: Dict[tuple, tuple] = {}
_INDEX_CACHE_MAX = 4


def _corpus_signature(roots: List[Tuple[str, str]]) -> tuple:
    sig = []
    for label, pattern in roots:
        for p in sorted(_glob.glob(pattern)):
            try:
                st = os.stat(p)
                sig.append((p, st.st_mtime_ns, st.st_size))
            except OSError:
                sig.append((p, None, None))
    return tuple(sig)


def load_corpus_indexed(roots: Optional[List[Tuple[str, str]]] = None
                        ) -> Tuple[List[dict], List[str], dict]:
    """(chunks, notes, index) — cached while the on-disk corpus is
    byte-for-byte the same files (path+mtime+size), rebuilt otherwise."""
    roots = roots if roots is not None else default_roots()
    # Normalize pairs for hashing: roots historically accepted any iterable
    # of (label, glob) pairs (lists included) — tuple(list-of-lists) would
    # TypeError on the cache lookup and silently narrow the public API.
    key = tuple((str(label), str(pattern)) for label, pattern in roots)
    sig = _corpus_signature(roots)
    hit = _INDEX_CACHE.get(key)
    if hit is not None and hit[0] == sig:
        return hit[1], hit[2], hit[3]
    chunks, notes = load_corpus(roots)
    index = build_index(chunks)
    _INDEX_CACHE[key] = (sig, chunks, notes, index)
    while len(_INDEX_CACHE) > _INDEX_CACHE_MAX:
        _INDEX_CACHE.pop(next(iter(_INDEX_CACHE)))
    return chunks, notes, index


def rank(chunks: List[dict], query: str, top_k: int = 6,
         k1: float = 1.5, b: float = 0.75,
         index: Optional[dict] = None) -> List[dict]:
    """Classic BM25, small and dependency-free. Returns the top_k chunks
    (score attached) that share at least one query term. Pass the matching
    ``index`` from load_corpus_indexed to skip the corpus-side rebuild;
    without it the index is built fresh (same result, more CPU)."""
    q_terms = list(dict.fromkeys(tokenize(query)))
    if not q_terms or not chunks:
        return []
    if index is None:
        index = build_index(chunks)
    docs = index["docs"]
    n = len(docs)
    avg_len = index["avg_len"]
    df: Dict[str, int] = index["df"]
    scored: List[Tuple[float, dict]] = []
    for c, d in zip(chunks, docs):
        if not d:
            continue
        tf: Dict[str, int] = {}
        for t in d:
            tf[t] = tf.get(t, 0) + 1
        score = 0.0
        for t in q_terms:
            if t not in tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * (tf[t] * (k1 + 1)) / (
                tf[t] + k1 * (1 - b + b * len(d) / avg_len))
        if score > 0:
            scored.append((score, c))
    scored.sort(key=lambda s: -s[0])
    out = []
    for score, c in scored[:top_k]:
        out.append({**c, "score": round(score, 2)})
    return out


# ── synthesis ───────────────────────────────────────────────────────

ORACLE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "source_ids": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["answer", "source_ids", "confidence"],
}

_SYSTEM_PROMPT = """You are the LOCAL offline oracle of a mesh-fleet NOC. \
Answer the operator's question USING ONLY the excerpts below — they are \
from the fleet's own issue lore, research and runbooks. Never use outside \
knowledge for fleet-specific facts.

Cite the ids (S1, S2, ...) of every excerpt you actually used in \
source_ids. If the excerpts do not contain the answer, say exactly that in \
the answer and set confidence to "low" — an honest "not in the lore I \
searched" beats a guess. Keep the answer under 200 words, operator-direct."""

_EXCERPT_CHARS_BUDGET = 6000


def _project_hits(hits: List[dict]) -> List[dict]:
    """THE retrieval projection (id/path/heading/score). One assembly —
    ask(), retrieve_only(), and every front-end share it; three copies had
    already shipped before it existed."""
    return [{"id": f"S{i+1}", "path": h["path"],
             "heading": h["heading"], "score": h["score"]}
            for i, h in enumerate(hits)]


def retrieve_only(question: str, roots=None, top_k: int = 6) -> dict:
    """Deterministic tier-R retrieval result (no LLM anywhere) — the one
    assembly behind ``--retrieve-only`` and the TUI's retrieval view."""
    chunks, notes, index = load_corpus_indexed(roots)
    hits = rank(chunks, question, top_k=top_k, index=index)
    return {"question": question, "brain_tier": "rules",
            "corpus_chunks": len(chunks), "corpus_notes": notes,
            "retrieved": _project_hits(hits),
            "note": "retrieval only (local LLM not used)"}


def ask(question: str, backend, roots=None, top_k: int = 6) -> dict:
    """Retrieve + synthesize. Always returns a dict with ``retrieved``;
    ``brain_tier`` is 'local' only when a validated cited answer exists."""
    chunks, notes, index = load_corpus_indexed(roots)
    hits = rank(chunks, question, top_k=top_k, index=index)
    base = {
        "question": question,
        "corpus_chunks": len(chunks),
        "corpus_notes": notes,
        "retrieved": _project_hits(hits),
    }
    if not hits:
        return {**base, "brain_tier": "rules", "answer": None,
                "sources": [], "confidence": None,
                "note": "retrieval found nothing for this query"}

    budget = _EXCERPT_CHARS_BUDGET
    parts = []
    shown_ids: set = set()
    for i, h in enumerate(hits):
        body = h["text"][:max(0, budget)]
        budget -= len(body)
        shown_ids.add(f"S{i+1}")
        parts.append(f"[S{i+1}] {h['path']} — {h['heading']}\n{body}")
        if budget <= 0:
            break
    user = (f"Question: {question}\n\nExcerpts:\n\n" + "\n\n".join(parts))

    try:
        raw = backend.complete(_SYSTEM_PROMPT, user, fmt=ORACLE_SCHEMA)
        doc = json.loads(raw)
        # Valid JSON that is not an object (array/string/number — a model
        # ignoring the schema) must degrade like any other bad reply, not
        # AttributeError out of the honest-failure handler below.
        if not isinstance(doc, dict):
            raise ValueError(
                f"oracle reply not an object: {type(doc).__name__}")
        answer = doc.get("answer")
        cited = doc.get("source_ids")
        confidence = doc.get("confidence")
        if not isinstance(answer, str) or not answer.strip() \
                or not isinstance(cited, list) \
                or not all(isinstance(c, str) for c in cited) \
                or confidence not in ("high", "medium", "low"):
            raise ValueError(f"oracle reply missing required shape: "
                             f"{str(doc)[:160]}")
    except (CompilerError, ValueError) as e:
        return {**base, "brain_tier": "rules", "answer": None, "sources": [],
                "confidence": None,
                "excerpts_shown": len(shown_ids),
                "note": f"synthesis failed ({str(e)[:200]}) — retrieval "
                        f"results above are still good"}

    # Validate citations against the ids the model was actually SHOWN —
    # the excerpt budget can truncate the retrieved tail out of the prompt,
    # and a cite of a retrieved-but-never-shown excerpt is exactly as
    # invented as an S9 (the guard's whole charter: the model cannot cite
    # what it wasn't given).
    by_id = {r["id"]: r for r in base["retrieved"] if r["id"] in shown_ids}
    sources = [by_id[c] for c in cited if c in by_id]
    invented = [c for c in cited if c not in by_id]
    if not sources:
        # A confident answer with zero surviving citations is exactly the
        # valid-looking-value trap: degrade to retrieval, loudly.
        return {**base, "brain_tier": "rules", "answer": None, "sources": [],
                "confidence": None,
                "excerpts_shown": len(shown_ids),
                "note": f"answer cited nothing it was shown "
                        f"(invented: {invented!r}) — discarded; retrieval "
                        f"results above are still good"}
    return {**base, "brain_tier": "local",
            "model": getattr(backend, "model", "?"),
            "answer": answer.strip(),
            "sources": sources,
            "invented_citations": invented,
            "excerpts_shown": len(shown_ids),
            "confidence": confidence}


def render_result(result: dict, include_notes: bool = True) -> str:
    """THE human-readable oracle rendering — a cited answer, or the honest
    retrieval fallback with its tier note verbatim (never dressed up as
    grounded). One renderer for the CLI and the TUI: two parallel
    formatters had already drifted (the TUI reported dropped invented
    citations, the CLI silently didn't)."""
    lines = [f"Q: {result.get('question', '')}", ""]
    answer = result.get("answer")
    if answer:
        lines.append(f"[{result.get('brain_tier', '?')} tier · "
                     f"{result.get('model', '?')} · confidence "
                     f"{result.get('confidence', '?')}]")
        lines.append("")
        lines.append(answer)
        lines.append("")
        lines.append("Sources:")
        for s in result.get("sources") or []:
            lines.append(f"  {s['id']}  {s['path']} — {s['heading']}")
        invented = result.get("invented_citations")
        if invented:
            lines.append("")
            lines.append(f"(dropped {len(invented)} invented citation(s) the "
                         f"model was not shown)")
    else:
        note = result.get("note") or "no answer"
        lines.append(f"[{result.get('brain_tier', 'rules')} tier] {note}")
        lines.append("")
        retrieved = result.get("retrieved") or []
        if retrieved:
            lines.append("Top retrieval hits (start here):")
            for s in retrieved:
                lines.append(f"  {s['id']}  ({s.get('score', '?')})  "
                             f"{s['path']} — {s['heading']}")
        else:
            lines.append("Retrieval found nothing for this question.")
    if include_notes:
        for n in result.get("corpus_notes") or []:
            lines.append(f"  (corpus: {n})")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Offline oracle: answer fleet questions from the "
                    "on-disk lore (lexical retrieval + cited local-LLM "
                    "synthesis; --retrieve-only needs no LLM).")
    ap.add_argument("question", nargs="+")
    ap.add_argument("--top-k", type=int, default=6)
    ap.add_argument("--retrieve-only", action="store_true")
    ap.add_argument("--url", default=os.environ.get(
        "MINI_DUDEAI_OLLAMA_URL", DEFAULT_OLLAMA_URL))
    ap.add_argument("--model", default=os.environ.get(
        "MINI_DUDEAI_OLLAMA_MODEL", DEFAULT_MODEL))
    ap.add_argument("--timeout-s", type=float, default=LOCAL_BRAIN_TIMEOUT_S)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    question = " ".join(args.question)

    if args.retrieve_only:
        result = retrieve_only(question, top_k=args.top_k)
    else:
        backend = OllamaBackend(url=args.url, model=args.model,
                                timeout_s=args.timeout_s)
        result = ask(question, backend, top_k=args.top_k)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    # Corpus notes stay on stderr (pipe-friendly stdout); the shared
    # renderer therefore skips them here.
    for note in result.get("corpus_notes") or []:
        print(f"  (corpus: {note})", file=sys.stderr)
    print(render_result(result, include_notes=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
