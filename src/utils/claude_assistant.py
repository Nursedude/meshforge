"""
MeshForge Claude Assistant (PRO).

AI-powered assistant for mesh network operations using Claude API.
Provides natural language queries, complex situation analysis, and
intelligent recommendations.

PRO Features (requires Claude API key):
- Natural language questions about your mesh
- Intelligent log analysis
- Predictive issue detection
- Contextual help based on your expertise level

Standalone Features (no API needed):
- Rule-based diagnostics (from diagnostic_engine)
- Knowledge base queries (from knowledge_base)
- Structured troubleshooting guides

Usage:
    assistant = ClaudeAssistant(api_key="sk-...")

    # Natural language query
    response = assistant.ask("Why is my node offline?")

    # Analyze logs
    analysis = assistant.analyze_logs(logs)

    # Get contextual help
    help = assistant.get_help("connection_refused", expertise="novice")

Environment:
    ANTHROPIC_API_KEY: Set API key via environment variable
    MESHFORGE_PRO: Set to "1" to enable PRO features
"""

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any, Callable
from enum import Enum

from utils.safe_import import safe_import

_anthropic_mod, _HAS_ANTHROPIC = safe_import('anthropic')
from utils.latency_monitor import get_latency_monitor
from utils.diagnostic_engine import get_diagnostic_engine

# Import standalone components
from utils.diagnostic_engine import (
    DiagnosticEngine, get_diagnostic_engine, diagnose,
    Category, Severity, Diagnosis
)
from utils.knowledge_base import (
    KnowledgeBase, get_knowledge_base, KnowledgeTopic
)

logger = logging.getLogger(__name__)


class ExpertiseLevel(Enum):
    """User expertise levels for response adaptation."""
    NOVICE = "novice"      # New to mesh networking
    INTERMEDIATE = "intermediate"  # Familiar with basics
    EXPERT = "expert"      # Deep technical knowledge


class AssistantMode(Enum):
    """Assistant operating modes."""
    STANDALONE = "standalone"  # Local knowledge only
    PRO = "pro"               # Claude API enhanced


@dataclass
class AssistantResponse:
    """Response from the assistant."""
    answer: str
    confidence: float = 0.0
    sources: List[str] = field(default_factory=list)
    related_topics: List[str] = field(default_factory=list)
    suggested_actions: List[str] = field(default_factory=list)
    mode: AssistantMode = AssistantMode.STANDALONE
    expertise_level: ExpertiseLevel = ExpertiseLevel.INTERMEDIATE


@dataclass
class ConversationMessage:
    """A message in the conversation history."""
    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)


class ClaudeAssistant:
    """
    AI-powered assistant for MeshForge.

    Operates in two modes:
    - STANDALONE: Uses local knowledge base and diagnostic engine
    - PRO: Enhanced with Claude API for natural language understanding

    The assistant automatically falls back to STANDALONE if API is unavailable.
    """

    # System prompt for Claude API
    SYSTEM_PROMPT = """You are an expert assistant for MeshForge, a Network Operations Center (NOC) that bridges Meshtastic and Reticulum (RNS) mesh networks.

Your expertise includes:
- RF fundamentals: propagation, antennas, SNR, RSSI, LoRa modulation
- Meshtastic: node configuration, channels, roles, firmware
- Reticulum: RNS daemon, LXMF messaging, interfaces
- Linux system administration: systemd, serial ports, networking
- Mesh network troubleshooting and optimization

Guidelines:
1. Be concise but thorough
2. Provide actionable steps when diagnosing issues
3. Include relevant commands when appropriate
4. Adapt explanations to the user's expertise level
5. Reference specific MeshForge features when helpful
6. If uncertain, say so and suggest where to find more info

You are embedded in MeshForge, so you have context about:
- The user's mesh network topology (when provided)
- Recent diagnostic events
- Current system health metrics

Always prioritize safety - never suggest actions that could damage hardware
(like transmitting without an antenna) without clear warnings."""

    # Conversation history limit
    MAX_HISTORY = 20

    def __init__(self, api_key: Optional[str] = None,
                 expertise_level: ExpertiseLevel = ExpertiseLevel.INTERMEDIATE):
        """
        Initialize the assistant.

        Args:
            api_key: Anthropic API key. If not provided, uses ANTHROPIC_API_KEY env var.
            expertise_level: User's expertise level for response adaptation.
        """
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._expertise_level = expertise_level
        self._mode = AssistantMode.PRO if self._api_key else AssistantMode.STANDALONE

        # Initialize standalone components
        self._diagnostic_engine = get_diagnostic_engine()
        self._knowledge_base = get_knowledge_base()

        # Conversation history
        self._conversation: List[ConversationMessage] = []
        self._conversation_lock = threading.Lock()

        # Network context (populated by MeshForge)
        self._network_context: Dict[str, Any] = {}

        # Claude client (lazy loaded)
        self._client = None

        logger.info(f"Claude Assistant initialized in {self._mode.value} mode")

    def _get_client(self):
        """Get or create Claude API client."""
        if self._client is None and self._api_key:
            if not _HAS_ANTHROPIC:
                logger.warning("anthropic package not installed. Run: pip install anthropic")
                self._mode = AssistantMode.STANDALONE
            else:
                try:
                    self._client = _anthropic_mod.Anthropic(api_key=self._api_key)
                except Exception as e:
                    logger.error(f"Failed to initialize Claude client: {e}")
                    self._mode = AssistantMode.STANDALONE
        return self._client

    def set_expertise_level(self, level: ExpertiseLevel) -> None:
        """Set the user's expertise level."""
        self._expertise_level = level

    def set_network_context(self, context: Dict[str, Any]) -> None:
        """
        Set network context for more informed responses.

        Context can include:
        - node_count: Number of nodes in mesh
        - nodes: List of node info
        - recent_events: Recent diagnostic events
        - health_summary: System health metrics
        """
        self._network_context = context

    def ask(self, question: str, include_history: bool = True) -> AssistantResponse:
        """
        Ask the assistant a question.

        Args:
            question: Natural language question
            include_history: Include conversation history for context

        Returns:
            AssistantResponse with answer and metadata
        """
        # Add to conversation history
        with self._conversation_lock:
            self._conversation.append(ConversationMessage(
                role="user",
                content=question
            ))

            # Trim history if needed
            if len(self._conversation) > self.MAX_HISTORY:
                self._conversation = self._conversation[-self.MAX_HISTORY:]

        # Try PRO mode first
        if self._mode == AssistantMode.PRO:
            response = self._ask_claude(question, include_history)
            if response:
                return response

        # Fall back to standalone
        return self._ask_standalone(question)

    def _ask_claude(self, question: str, include_history: bool) -> Optional[AssistantResponse]:
        """Ask using Claude API."""
        client = self._get_client()
        if not client:
            return None

        try:
            # Build messages
            messages = []

            if include_history:
                with self._conversation_lock:
                    for msg in self._conversation[:-1]:  # Exclude current question
                        messages.append({
                            "role": msg.role,
                            "content": msg.content
                        })

            # Add current question with context
            context_str = self._build_context_string()
            user_content = question
            if context_str:
                user_content = f"[Context: {context_str}]\n\n{question}"

            messages.append({"role": "user", "content": user_content})

            # Call Claude API
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                system=self._get_system_prompt(),
                messages=messages
            )

            # Safely extract response text
            if not response.content:
                logger.warning("Empty response from Claude API")
                return None
            answer = response.content[0].text

            # Add to conversation history
            with self._conversation_lock:
                self._conversation.append(ConversationMessage(
                    role="assistant",
                    content=answer
                ))

            # Extract suggested actions from response
            actions = self._extract_actions(answer)

            return AssistantResponse(
                answer=answer,
                confidence=0.9,
                sources=["Claude API"],
                suggested_actions=actions,
                mode=AssistantMode.PRO,
                expertise_level=self._expertise_level,
            )

        except Exception as e:
            logger.error(f"Claude API error: {e}")
            return None

    def _ask_standalone(self, question: str) -> AssistantResponse:
        """Ask using local knowledge base."""
        # Query knowledge base
        results = self._knowledge_base.query(question, max_results=3)

        if not results:
            return AssistantResponse(
                answer="I don't have specific information about that topic in my knowledge base. "
                       "Try asking about: SNR, channel utilization, meshtasticd, node roles, or troubleshooting.",
                confidence=0.3,
                mode=AssistantMode.STANDALONE,
            )

        # Build response from top result
        top_entry, score = results[0]

        # Adapt to expertise level
        content = top_entry.content.strip()
        if self._expertise_level == ExpertiseLevel.NOVICE:
            # Simplify for novices
            content = self._simplify_for_novice(content)

        # Build related topics
        related = [entry.title for entry, _ in results[1:]]
        related.extend(top_entry.related_entries)

        # Add to conversation history
        with self._conversation_lock:
            self._conversation.append(ConversationMessage(
                role="assistant",
                content=content
            ))

        return AssistantResponse(
            answer=content,
            confidence=min(0.7, score / 5.0),  # Normalize score
            sources=[f"Knowledge Base: {top_entry.title}"],
            related_topics=related[:3],
            mode=AssistantMode.STANDALONE,
            expertise_level=self._expertise_level,
        )

    def _simplify_for_novice(self, content: str) -> str:
        """Simplify technical content for novice users."""
        # Just return first paragraph for now
        # Future: AI-powered simplification
        paragraphs = content.strip().split('\n\n')
        if paragraphs:
            return paragraphs[0]
        return content

    def _build_context_string(self) -> str:
        """Build context string from network state + live metrics."""
        parts = []

        # Static network context (set by caller)
        if self._network_context:
            if "node_count" in self._network_context:
                parts.append(f"{self._network_context['node_count']} nodes")

            if "health_summary" in self._network_context:
                health = self._network_context["health_summary"]
                if isinstance(health, dict):
                    parts.append(f"health: {health.get('overall_health', 'unknown')}")

            if "recent_events" in self._network_context:
                events = self._network_context["recent_events"]
                if events:
                    parts.append(f"{len(events)} recent events")

        # Live service latency from NOC monitor
        try:
            monitor = get_latency_monitor(auto_start=False)
            if monitor._services:
                svc_parts = []
                for svc in monitor._services.values():
                    if svc.samples:
                        svc_parts.append(
                            f"{svc.name}:{svc.status}"
                            f"({svc.avg_rtt_ms:.0f}ms)"
                        )
                if svc_parts:
                    parts.append(f"services=[{', '.join(svc_parts)}]")

                degraded = monitor.get_degraded()
                if degraded:
                    parts.append(f"DEGRADED: {', '.join(degraded)}")
        except Exception as e:
            logger.debug(f"Failed to get latency context: {e}")

        # Recent diagnostics
        try:
            engine = get_diagnostic_engine()
            recent = engine.get_recent_diagnoses(limit=3)
            if recent:
                diag_parts = [
                    f"{d.symptom.category.name}:{d.symptom.message[:40]}"
                    for d in recent
                ]
                parts.append(f"recent_diag=[{'; '.join(diag_parts)}]")
        except Exception as e:
            logger.debug(f"Failed to get diagnostic context: {e}")

        return ", ".join(parts)

    def _get_system_prompt(self) -> str:
        """Get system prompt adapted for expertise level."""
        base = self.SYSTEM_PROMPT

        level_additions = {
            ExpertiseLevel.NOVICE: "\n\nThe user is new to mesh networking. Use simple terms, "
                                   "avoid jargon, and explain concepts thoroughly.",
            ExpertiseLevel.INTERMEDIATE: "\n\nThe user has basic mesh networking knowledge. "
                                         "You can use standard terminology.",
            ExpertiseLevel.EXPERT: "\n\nThe user is an expert. Be concise and technical. "
                                   "Skip basic explanations unless asked.",
        }

        return base + level_additions.get(self._expertise_level, "")

    def _extract_actions(self, text: str) -> List[str]:
        """Extract actionable items from response text."""
        actions = []

        # Look for command patterns
        import re
        commands = re.findall(r'`([^`]+)`', text)
        for cmd in commands:
            if any(cmd.startswith(prefix) for prefix in
                   ['sudo', 'meshtastic', 'systemctl', 'journalctl', 'cat', 'ls']):
                actions.append(f"Run: {cmd}")

        return actions[:5]

    def analyze_logs(self, logs: List[str], context: Optional[Dict] = None) -> AssistantResponse:
        """
        Analyze log messages for issues.

        Args:
            logs: List of log messages
            context: Additional context

        Returns:
            Analysis with identified issues and recommendations
        """
        issues = []
        recommendations = []

        # Use diagnostic engine to analyze each log
        for log in logs:
            # Determine severity from log
            severity = Severity.INFO
            if "ERROR" in log.upper():
                severity = Severity.ERROR
            elif "WARNING" in log.upper():
                severity = Severity.WARNING
            elif "CRITICAL" in log.upper():
                severity = Severity.CRITICAL

            # Try to diagnose
            diagnosis = diagnose(log, severity=severity, context=context or {})
            if diagnosis:
                issues.append(f"• {diagnosis.likely_cause}")
                recommendations.extend(diagnosis.suggestions[:2])

        if not issues:
            return AssistantResponse(
                answer="No significant issues detected in the provided logs.",
                confidence=0.7,
                mode=self._mode,
            )

        # Build response
        answer_parts = ["**Issues Detected:**\n"]
        answer_parts.extend(issues[:5])
        answer_parts.append("\n**Recommendations:**")
        answer_parts.extend(f"• {r}" for r in list(set(recommendations))[:5])

        return AssistantResponse(
            answer="\n".join(answer_parts),
            confidence=0.8,
            sources=["Diagnostic Engine"],
            suggested_actions=list(set(recommendations))[:5],
            mode=self._mode,
        )

    def get_help(self, topic: str, expertise: Optional[ExpertiseLevel] = None) -> AssistantResponse:
        """
        Get help on a specific topic.

        Args:
            topic: Topic to get help on
            expertise: Override expertise level

        Returns:
            Help content
        """
        if expertise:
            old_level = self._expertise_level
            self._expertise_level = expertise

        # Check for troubleshooting guide first
        guide = self._knowledge_base.get_troubleshooting_guide(topic)
        if guide:
            steps = []
            for i, step in enumerate(guide.steps, 1):
                step_str = f"{i}. {step.instruction}"
                if step.command:
                    step_str += f"\n   Command: `{step.command}`"
                if step.if_fail:
                    step_str += f"\n   If fails: {step.if_fail}"
                steps.append(step_str)

            answer = f"**{guide.description}**\n\n" + "\n\n".join(steps)

            result = AssistantResponse(
                answer=answer,
                confidence=0.9,
                sources=[f"Troubleshooting Guide: {guide.problem}"],
                related_topics=guide.related_problems,
                mode=self._mode,
            )
        else:
            # Fall back to regular query
            result = self.ask(f"How do I fix {topic}?", include_history=False)

        if expertise:
            self._expertise_level = old_level

        return result

    def get_health_explanation(self, health_summary: Dict) -> str:
        """
        Generate human-readable explanation of health summary.

        Args:
            health_summary: Health summary from diagnostic engine

        Returns:
            Human-readable explanation
        """
        overall = health_summary.get("overall_health", "unknown")

        explanations = {
            "healthy": "Your mesh network is operating normally. No significant issues detected.",
            "warning": "Some warnings have been detected. Review the diagnostic panel for details.",
            "degraded": "Network performance is degraded. Multiple errors detected - action recommended.",
            "critical": "Critical issues detected! Immediate attention required.",
        }

        base = explanations.get(overall, "Health status could not be determined.")

        # Add specifics
        symptoms = health_summary.get("symptoms_last_hour", 0)
        if symptoms > 0:
            base += f" ({symptoms} events in the last hour)"

        by_category = health_summary.get("by_category", {})
        if by_category:
            top_category = max(by_category.items(), key=lambda x: x[1])
            base += f" Most issues are {top_category[0]}-related."

        return base

    def clear_conversation(self) -> None:
        """Clear conversation history."""
        with self._conversation_lock:
            self._conversation.clear()

    def get_mode(self) -> AssistantMode:
        """Get current operating mode."""
        return self._mode

    def is_pro_enabled(self) -> bool:
        """Check if PRO mode is available."""
        return self._mode == AssistantMode.PRO and self._api_key is not None


# Convenience functions

def get_assistant(api_key: Optional[str] = None) -> ClaudeAssistant:
    """Get a Claude Assistant instance."""
    return ClaudeAssistant(api_key=api_key)


def quick_ask(question: str) -> str:
    """Quick query without creating persistent instance."""
    assistant = ClaudeAssistant()
    response = assistant.ask(question)
    return response.answer
