"""Offline oracle — ask the fleet's own lore, in-app (In-Domain / MF018).

The TUI sibling of ``python3 -m mini_dudeai.offline_oracle``: an operator
question → lexical retrieval over the on-disk lore (persistent issues,
research, rules, docs, memory) → a CITED local-LLM answer, or an honest
retrieve-only list when the model is away. W3 of the degraded-brain ladder
(.claude/research/dudeclaw_local_brain_2026_07_03.md §4) — the point is to
answer fleet questions WITHOUT leaving the app and WITHOUT the frontier.

Design notes:
- The answer path is EXACTLY the production module (``offline_oracle.ask`` /
  ``rank``) — the TUI adds no second retrieval or synthesis logic, so the
  in-app answer and the CLI answer can never diverge.
- Citations are load-bearing: the module discards an answer that cites
  nothing it was shown, and this handler renders the ``[rules tier]`` note
  verbatim rather than dressing a degraded result as grounded.
- Ollama is optional. If it is unreachable the handler still runs retrieval
  and shows the top lore hits (tier R needs no model) — the oracle stays
  useful when the frontier AND the local LLM are gone.
- No shell, no writes: retrieval reads markdown, synthesis calls the local
  model. Read-only by charter (the module carries a source-scan tripwire).
"""
from __future__ import annotations

import logging
import os

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)


class OfflineOracleHandler(BaseHandler):
    """Ask the on-disk fleet lore a question, answered in-app."""

    handler_id = "offline_oracle"
    menu_section = "dashboard"

    def menu_items(self):
        return [
            ("offline_oracle", "offline oracle (ask the fleet lore)", None),
        ]

    def execute(self, action):
        if action == "offline_oracle":
            self.ctx.safe_call("offline oracle", self._ask_loop)

    def _ask_loop(self):
        from mini_dudeai.chat_compiler import (
            DEFAULT_MODEL, DEFAULT_OLLAMA_URL, OllamaBackend)
        # Reuse the mini handler's bounded reachability probe — there is no
        # second copy of "is Ollama up" in the TUI.
        from handlers.mini_dudeai import probe_ollama

        url = os.environ.get("MINI_DUDEAI_OLLAMA_URL", DEFAULT_OLLAMA_URL)
        model = os.environ.get("MINI_DUDEAI_OLLAMA_MODEL", DEFAULT_MODEL)
        ok, detail = probe_ollama(url)
        # Ollama down is NOT fatal — retrieval alone (tier R) is still useful.
        # Offer the operator the honest choice rather than dead-ending.
        retrieve_only = False
        if not ok:
            if not self.ctx.dialog.yesno(
                    "Local LLM unreachable",
                    f"The oracle's synthesis needs a local Ollama, and\n"
                    f"  {url}\n"
                    f"did not answer:\n  {detail}\n\n"
                    "Retrieval still works without it (the top lore excerpts, "
                    "no synthesized answer).\n\nSearch the lore anyway?"):
                return
            retrieve_only = True

        while True:
            question = self.ctx.dialog.inputbox(
                "Ask the fleet lore",
                ("Retrieval-only (no LLM)\n\n" if retrieve_only
                 else f"Oracle: {model} @ {url}\n\n")
                + "One question about this fleet's issues, research, or "
                "runbooks.\nExample: how do I check whether rnsd RPC is "
                "wedged, and what is the recovery?")
            if not question or not question.strip():
                return
            if retrieve_only:
                self._show_retrieval(question.strip())
            else:
                self._answer(question.strip(),
                             OllamaBackend(url=url, model=model))

    def _answer(self, question, backend):
        from mini_dudeai.offline_oracle import ask
        self.ctx.dialog.infobox(
            "Searching the lore",
            f"{backend.model} @ {backend.url}\n\n"
            "Retrieving excerpts and synthesizing a cited answer — a small "
            "local model can take a minute or two ...")
        try:
            result = ask(question, backend)
        except Exception as e:  # a flow must answer, never traceback
            logger.exception("offline oracle ask failed")
            self.ctx.dialog.msgbox("Oracle error",
                                   f"The oracle failed: {e}")
            return
        self.ctx.dialog.textbox("Oracle answer", _render(result))

    def _show_retrieval(self, question):
        from mini_dudeai.offline_oracle import load_corpus, rank
        chunks, notes = load_corpus()
        hits = rank(chunks, question, top_k=8)
        self.ctx.dialog.textbox(
            "Lore hits (retrieval only)",
            _render({"question": question, "brain_tier": "rules",
                     "retrieved": [{"id": f"S{i+1}", "path": h["path"],
                                    "heading": h["heading"],
                                    "score": h["score"]}
                                   for i, h in enumerate(hits)],
                     "corpus_notes": notes, "corpus_chunks": len(chunks),
                     "note": "retrieval only (local LLM not used)"}))


def _render(result: dict) -> str:
    """Human-readable oracle result — a cited answer, or the honest
    retrieval fallback with its tier note shown verbatim (never dressed up
    as grounded)."""
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
    for n in result.get("corpus_notes") or []:
        lines.append(f"  (corpus: {n})")
    return "\n".join(lines)
