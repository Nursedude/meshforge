"""Knowledge base: the Meshtastic Wi-Fi reason-code entry is findable, and
every entry's related_entries resolve to a real title."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.knowledge_base import KnowledgeBase

TITLE = "Meshtastic Wi-Fi Reason Codes"


def test_entry_loaded():
    kb = KnowledgeBase()
    entry = kb.get_entry(TITLE)
    assert entry is not None
    assert "4WAY_HANDSHAKE_TIMEOUT" in entry.content
    assert "NO_AP_FOUND" in entry.content


def test_found_by_what_the_operator_sees():
    # The serial log line is the operator's starting point.
    kb = KnowledgeBase()
    for question in ("wifi reason 15 4WAY_HANDSHAKE_TIMEOUT",
                     "esp32 wifi won't join",
                     "NO_AP_FOUND wifi"):
        titles = [e.title for e, _ in kb.query(question)]
        assert TITLE in titles, (question, titles)


def test_related_entries_resolve():
    kb = KnowledgeBase()
    dangling = [(e.title, r) for e in kb._entries.values()
                for r in e.related_entries if kb.get_entry(r) is None]
    assert not dangling, dangling
