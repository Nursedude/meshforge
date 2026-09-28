"""
MeshForge Domain Knowledge Base.

Provides structured knowledge about mesh networking for intelligent diagnostics
and user assistance. Works offline for standalone mode.

Knowledge categories:
- RF fundamentals (propagation, antennas, LoRa)
- Protocol details (Meshtastic, Reticulum, MQTT)
- Hardware specifics (devices, serial ports, GPIO)
- Network topology (routing, relays, gateways)
- Troubleshooting guides

Usage:
    kb = KnowledgeBase()
    answer = kb.query("What causes low SNR?")
    guide = kb.get_troubleshooting_guide("no_connection")
"""

import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class KnowledgeTopic(Enum):
    """Knowledge topic categories."""
    RF_FUNDAMENTALS = "rf_fundamentals"
    MESHTASTIC = "meshtastic"
    RETICULUM = "reticulum"
    MQTT = "mqtt"
    HARDWARE = "hardware"
    NETWORKING = "networking"
    TROUBLESHOOTING = "troubleshooting"
    BEST_PRACTICES = "best_practices"


@dataclass
class KnowledgeEntry:
    """A piece of knowledge in the knowledge base."""
    topic: KnowledgeTopic
    title: str
    content: str
    keywords: List[str] = field(default_factory=list)
    related_entries: List[str] = field(default_factory=list)  # titles
    expertise_level: str = "intermediate"  # novice, intermediate, expert


@dataclass
class TroubleshootingStep:
    """A step in a troubleshooting guide."""
    instruction: str
    command: Optional[str] = None  # Shell command to run
    expected_result: Optional[str] = None
    if_fail: Optional[str] = None  # Next step if this fails


@dataclass
class TroubleshootingGuide:
    """A complete troubleshooting guide."""
    problem: str
    description: str
    prerequisites: List[str] = field(default_factory=list)
    steps: List[TroubleshootingStep] = field(default_factory=list)
    related_problems: List[str] = field(default_factory=list)


class KnowledgeBase:
    """
    Domain knowledge base for mesh networking.

    Provides:
    - Keyword-based queries
    - Troubleshooting guides
    - Concept explanations
    - Best practice recommendations
    """

    def __init__(self):
        """Initialize the knowledge base."""
        self._entries: Dict[str, KnowledgeEntry] = {}
        self._guides: Dict[str, TroubleshootingGuide] = {}
        self._keyword_index: Dict[str, List[str]] = {}  # keyword -> entry titles

        # Import and load knowledge content
        from . import knowledge_content as content
        from . import knowledge_content_extended as extended

        content.load_rf_knowledge(self)
        content.load_meshtastic_knowledge(self)
        content.load_reticulum_knowledge(self)
        content.load_hardware_knowledge(self)
        content.load_troubleshooting_guides(self)
        content.load_best_practices(self)
        content.load_rns_troubleshooting(self)
        extended.load_aredn_knowledge(self)
        extended.load_rf_fundamentals_extended(self)
        extended.load_mqtt_knowledge(self)
        extended.load_meshtastic_wifi_knowledge(self)

        # Build index
        self._build_keyword_index()

    def _add_entry(self, entry: KnowledgeEntry) -> None:
        """Add an entry to the knowledge base."""
        self._entries[entry.title] = entry

    def _add_guide(self, guide: TroubleshootingGuide) -> None:
        """Add a troubleshooting guide."""
        self._guides[guide.problem] = guide

    def _build_keyword_index(self) -> None:
        """Build keyword search index."""
        for title, entry in self._entries.items():
            for keyword in entry.keywords:
                keyword_lower = keyword.lower()
                if keyword_lower not in self._keyword_index:
                    self._keyword_index[keyword_lower] = []
                self._keyword_index[keyword_lower].append(title)

    # ===== Query Methods =====

    def query(self, question: str, max_results: int = 3) -> List[Tuple[KnowledgeEntry, float]]:
        """
        Query the knowledge base.

        Args:
            question: Natural language question
            max_results: Maximum number of results

        Returns:
            List of (entry, relevance_score) tuples

        API Contract:
            - ALWAYS returns a list (never None)
            - Empty list if no matches found
            - Each element is a 2-tuple: (KnowledgeEntry, float)
            - Results sorted by relevance (highest first)
            - Callers MUST check 'if results:' before accessing results[0]
            - Tests: tests/test_ai_tools.py::TestKnowledgeBase
        """
        # Extract keywords from question
        words = re.findall(r'\b\w+\b', question.lower())
        stop_words = {'what', 'why', 'how', 'is', 'the', 'a', 'an', 'to', 'for', 'of', 'in', 'on'}
        keywords = [w for w in words if w not in stop_words and len(w) > 2]

        # Score entries by keyword matches
        scores: Dict[str, float] = {}

        for keyword in keywords:
            if keyword in self._keyword_index:
                for title in self._keyword_index[keyword]:
                    scores[title] = scores.get(title, 0) + 1.0

            # Partial matches
            for indexed_keyword in self._keyword_index:
                if keyword in indexed_keyword or indexed_keyword in keyword:
                    for title in self._keyword_index[indexed_keyword]:
                        scores[title] = scores.get(title, 0) + 0.5

        # Sort by score
        sorted_titles = sorted(scores.keys(), key=lambda t: scores[t], reverse=True)

        results = []
        for title in sorted_titles[:max_results]:
            entry = self._entries[title]
            results.append((entry, scores[title]))

        return results

    def get_entry(self, title: str) -> Optional[KnowledgeEntry]:
        """Get a specific knowledge entry by title."""
        return self._entries.get(title)

    def get_troubleshooting_guide(self, problem: str) -> Optional[TroubleshootingGuide]:
        """Get a troubleshooting guide by problem name."""
        return self._guides.get(problem)

    def list_topics(self) -> List[str]:
        """List all available topics."""
        return list(set(e.topic.value for e in self._entries.values()))

    def get_entries_by_topic(self, topic: KnowledgeTopic) -> List[KnowledgeEntry]:
        """Get all entries for a topic."""
        return [e for e in self._entries.values() if e.topic == topic]

    def get_all_guides(self) -> List[TroubleshootingGuide]:
        """Get all troubleshooting guides."""
        return list(self._guides.values())


# Singleton instance
_kb: Optional[KnowledgeBase] = None
_kb_lock = threading.Lock()


def get_knowledge_base() -> KnowledgeBase:
    """Get the global knowledge base instance (thread-safe)."""
    global _kb
    with _kb_lock:
        if _kb is None:
            _kb = KnowledgeBase()
        return _kb
