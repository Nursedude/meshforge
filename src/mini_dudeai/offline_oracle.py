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

from .chat_compiler import (
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    CompilerError,
    OllamaBackend,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


def _memory_dir() -> str:
    """The Claude memory topic-file dir for THIS repo, derived (never
    hardcoded — the slug is the repo path with '/' → '-')."""
    slug = _REPO_ROOT.replace(os.sep, "-")
    return os.path.expanduser(os.path.join("~", ".claude", "projects",
                                           slug, "memory"))


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

def rank(chunks: List[dict], query: str, top_k: int = 6,
         k1: float = 1.5, b: float = 0.75) -> List[dict]:
    """Classic BM25, small and dependency-free. Returns the top_k chunks
    (score attached) that share at least one query term."""
    q_terms = list(dict.fromkeys(tokenize(query)))
    if not q_terms or not chunks:
        return []
    docs = [tokenize(c["text"]) + tokenize(c["heading"]) for c in chunks]
    n = len(docs)
    avg_len = sum(len(d) for d in docs) / n
    df: Dict[str, int] = {}
    for d in docs:
        for t in set(d):
            df[t] = df.get(t, 0) + 1
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


def ask(question: str, backend, roots=None, top_k: int = 6) -> dict:
    """Retrieve + synthesize. Always returns a dict with ``retrieved``;
    ``brain_tier`` is 'local' only when a validated cited answer exists."""
    chunks, notes = load_corpus(roots)
    hits = rank(chunks, question, top_k=top_k)
    base = {
        "question": question,
        "corpus_chunks": len(chunks),
        "corpus_notes": notes,
        "retrieved": [{"id": f"S{i+1}", "path": h["path"],
                       "heading": h["heading"], "score": h["score"]}
                      for i, h in enumerate(hits)],
    }
    if not hits:
        return {**base, "brain_tier": "rules", "answer": None,
                "sources": [], "confidence": None,
                "note": "retrieval found nothing for this query"}

    budget = _EXCERPT_CHARS_BUDGET
    parts = []
    for i, h in enumerate(hits):
        body = h["text"][:max(0, budget)]
        budget -= len(body)
        parts.append(f"[S{i+1}] {h['path']} — {h['heading']}\n{body}")
        if budget <= 0:
            break
    user = (f"Question: {question}\n\nExcerpts:\n\n" + "\n\n".join(parts))

    try:
        raw = backend.complete(_SYSTEM_PROMPT, user, fmt=ORACLE_SCHEMA)
        doc = json.loads(raw)
        answer = doc.get("answer")
        cited = doc.get("source_ids")
        confidence = doc.get("confidence")
        if not isinstance(answer, str) or not answer.strip() \
                or not isinstance(cited, list) \
                or confidence not in ("high", "medium", "low"):
            raise ValueError(f"oracle reply missing required shape: "
                             f"{str(doc)[:160]}")
    except (CompilerError, ValueError) as e:
        return {**base, "brain_tier": "rules", "answer": None, "sources": [],
                "confidence": None,
                "note": f"synthesis failed ({str(e)[:200]}) — retrieval "
                        f"results above are still good"}

    by_id = {r["id"]: r for r in base["retrieved"]}
    sources = [by_id[c] for c in cited if c in by_id]
    invented = [c for c in cited if c not in by_id]
    if not sources:
        # A confident answer with zero surviving citations is exactly the
        # valid-looking-value trap: degrade to retrieval, loudly.
        return {**base, "brain_tier": "rules", "answer": None, "sources": [],
                "confidence": None,
                "note": f"answer cited nothing it was shown "
                        f"(invented: {invented!r}) — discarded; retrieval "
                        f"results above are still good"}
    return {**base, "brain_tier": "local",
            "model": getattr(backend, "model", "?"),
            "answer": answer.strip(),
            "sources": sources,
            "invented_citations": invented,
            "confidence": confidence}


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
    ap.add_argument("--timeout-s", type=float, default=480.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    question = " ".join(args.question)

    if args.retrieve_only:
        chunks, notes = load_corpus()
        hits = rank(chunks, question, top_k=args.top_k)
        result = {"question": question, "brain_tier": "rules",
                  "corpus_chunks": len(chunks), "corpus_notes": notes,
                  "retrieved": [{"id": f"S{i+1}", "path": h["path"],
                                 "heading": h["heading"],
                                 "score": h["score"]}
                                for i, h in enumerate(hits)]}
    else:
        backend = OllamaBackend(url=args.url, model=args.model,
                                timeout_s=args.timeout_s)
        result = ask(question, backend, top_k=args.top_k)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    for note in result.get("corpus_notes") or []:
        print(f"  (corpus: {note})", file=sys.stderr)
    if result.get("answer"):
        print(f"[{result['brain_tier']} tier · {result.get('model', '?')} · "
              f"confidence {result['confidence']}]")
        print(result["answer"])
        print("\nSources:")
        for s in result["sources"]:
            print(f"  {s['id']}  {s['path']} — {s['heading']}")
    else:
        if result.get("note"):
            print(f"[{result['brain_tier']} tier] {result['note']}")
        print("Top retrieval hits:")
        for s in result.get("retrieved") or []:
            print(f"  {s['id']}  ({s['score']})  {s['path']} — {s['heading']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
