"""Tests for the offline-oracle TUI handler (W3 in-app surface).

Pins the pure render (a cited answer vs the honest retrieval fallback —
a degraded result is never dressed as grounded), plus registration and the
no-duplicate-menu-tag invariant (the 2026-07-03 TUI-audit orphan/dup guard).
The dialog flow itself is thin glue over the production offline_oracle
module, which carries its own tests.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "launcher_tui"))

from handlers.offline_oracle import OfflineOracleHandler, _render  # noqa: E402


def test_render_cited_answer_shows_tier_and_sources():
    out = _render({
        "question": "how do I check a wedged rnsd?",
        "brain_tier": "local", "model": "qwen3:4b", "confidence": "high",
        "answer": "run timeout 8 rnstatus; restart rnsd to recover.",
        "sources": [{"id": "S1",
                     "path": ".claude/foundations/persistent_issues.md",
                     "heading": "Issue #72"}],
    })
    assert "[local tier · qwen3:4b · confidence high]" in out
    assert "timeout 8 rnstatus" in out
    assert "S1  .claude/foundations/persistent_issues.md — Issue #72" in out


def test_render_degraded_shows_note_and_hits_not_a_fake_answer():
    out = _render({
        "question": "q", "brain_tier": "rules", "answer": None,
        "note": "answer cited nothing it was shown — discarded",
        "retrieved": [{"id": "S1", "path": "docs/a.md", "heading": "H",
                       "score": 12.3}],
    })
    assert "[rules tier] answer cited nothing it was shown" in out
    assert "Top retrieval hits" in out
    assert "S1  (12.3)  docs/a.md — H" in out
    # a discarded answer must not leak a confidence/answer line
    assert "confidence" not in out


def test_render_reports_invented_citations():
    out = _render({
        "question": "q", "brain_tier": "local", "model": "m",
        "confidence": "medium", "answer": "a",
        "sources": [{"id": "S1", "path": "p", "heading": "h"}],
        "invented_citations": ["S9", "S12"],
    })
    assert "dropped 2 invented citation(s)" in out


def test_render_surfaces_corpus_notes():
    out = _render({"question": "q", "brain_tier": "rules", "answer": None,
                   "note": "n", "retrieved": [],
                   "corpus_notes": ["root 'memory' empty/absent"]})
    assert "(corpus: root 'memory' empty/absent)" in out
    assert "Retrieval found nothing" in out


def test_handler_registered():
    from handlers import get_all_handlers
    assert OfflineOracleHandler in get_all_handlers()


def test_registers_without_tag_collision():
    # The registry refuses a duplicate (section, tag) LOUDLY (would silently
    # shadow a handler's action). Registering every real handler exercises
    # that guard against the live set — if the oracle's dashboard tag
    # collides with a sibling, register() raises here.
    from handler_registry import HandlerRegistry
    from handler_protocol import TUIContext
    from handlers import get_all_handlers

    reg = HandlerRegistry(TUIContext(dialog=None))
    for cls in get_all_handlers():
        reg.register(cls())  # injects ctx; raises ValueError on a real dup
    assert reg.get_handler("offline_oracle") is not None
    tags = [t for t, _d in reg.get_menu_items("dashboard")]
    assert "offline_oracle" in tags
