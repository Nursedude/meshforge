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

# Single source of truth for the PRO-tier Claude API model. A bare model id
# inline at the call site is a stale-hardcode trap: when a model is retired or
# renamed (the agent-side Fable 5 -> Opus lurch has a twin here on the API
# side), messages.create() starts raising and the assistant drops to the
# standalone knowledge base. Naming it here AND honoring
# MESHFORGE_ASSISTANT_MODEL makes a swap a one-env-var change, not a code edit
# -- matching the cadence launcher's MINI_DUDEAI_CADENCE_MODEL pattern.
# Contract on a retired/unknown model: the API call raises -> logged (a witness,
# honest_failure_modes #9) -> ask() degrades to standalone (fail-SAFE: still
# answers, never silently-wrong). See .claude/rules/calibrated_claims.md
# ("behavior shifts across model versions") and ~/.claude/plans/self-audit-qa-arc.md s0.
DEFAULT_ASSISTANT_MODEL = os.environ.get(
    "MESHFORGE_ASSISTANT_MODEL", "claude-opus-5-5"
)


class ExpertiseLevel(Enum):
    """User expertise levels for response adaptation."""
    NOVICE = "novice"      # New to mesh networking
    INTERMEDIATE = "intermediate"  # Familiar with basics
    EXPERT = "expert"      # Deep technical knowledge


# Effort for the in-app chat assistant: short answers to operator questions,
# not agentic work — `low` (thinking stays on; it cannot be disabled on the
# current Opus). Override with MESHFORGE_ASSISTANT_EFFORT.
ASSISTANT_EFFORT = os.environ.get("MESHFORGE_ASSISTANT_EFFORT", "low")

# The API key may come from the environment or from this file (mode 0600).
# The TUI launcher starts under `sudo`, whose default env_reset drops
# ANTHROPIC_API_KEY — a key only in the operator's shell never reaches it.
KEY_FILE_NAME = "anthropic.key"


def _key_file_path():
    from utils.paths import get_real_user_home
    return get_real_user_home() / ".config" / "meshforge" / KEY_FILE_NAME


def resolve_api_key():
    """(key, None) or (None, reason). Env first, then the key file.

    The TUI runs as ROOT under sudo, so the file is opened without following
    a symlink and without blocking (a FIFO planted there froze the banner),
    and must be a small regular file owned by the operator (or root) with no
    group/other bits — never a pointer to some root-only secret that would
    then be sent to api.anthropic.com (reader pair, 2026-09-30).
    """
    import stat as _stat
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key, None
    path = _key_file_path()
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None, ("no API key visible to this process (ANTHROPIC_API_KEY is "
                      f"unset — the TUI runs under sudo, which drops it — and {path} "
                      "does not exist)")
    except OSError as e:
        return None, f"{path} refused ({e.strerror}; a symlink is never followed)"
    try:
        st = os.fstat(fd)
        owner_ok = st.st_uid in (0, path.parent.parent.parent.stat().st_uid)
        if not _stat.S_ISREG(st.st_mode):
            return None, f"{path} is not a regular file — refused"
        if not owner_ok:
            return None, f"{path} is not owned by you or root — refused"
        if st.st_mode & 0o077:
            return None, (f"{path} is readable by other users (mode "
                          f"{oct(st.st_mode & 0o777)}) — refused; chmod 600 it")
        if st.st_size > 512:
            return None, f"{path} is {st.st_size} bytes — not an API key; refused"
        key = os.read(fd, 513).decode("utf-8", "replace").strip()
    except OSError as e:
        return None, f"cannot read {path}: {e}"
    finally:
        os.close(fd)
    if not key:
        return None, f"{path} is empty"
    return key, None


def find_claude_code():
    """Path of the `claude` CLI (Claude Code) for the operator, or None.
    A Claude SUBSCRIPTION reaches the domain through Claude Code run in the
    repo — the API SDK cannot use a subscription. Checked on PATH and in the
    real user's ~/.local/bin (root's PATH under sudo usually lacks it)."""
    import shutil
    found = shutil.which("claude")
    if found:
        return found
    try:
        from utils.paths import get_real_user_home
        cand = get_real_user_home() / ".local" / "bin" / "claude"
        return str(cand) if os.access(cand, os.X_OK) else None
    except OSError:
        return None


@dataclass
class AssistantAvailability:
    """What AI assist this box ACTUALLY has — measured, never assumed."""
    api: str            # "configured" | "no_key" | "no_package"
    detail: str         # one line for the operator
    claude_code: Optional[str] = None


def check_availability() -> "AssistantAvailability":
    key, reason = resolve_api_key()
    cc = find_claude_code()
    if not key:
        return AssistantAvailability("no_key", reason, cc)
    if not _HAS_ANTHROPIC:
        return AssistantAvailability(
            "no_package",
            "API key found, but the `anthropic` package is not installed "
            f"({_pip_hint()}; needs Python >= 3.10)", cc)
    # Key + package found is PRESENCE, not function: a revoked key, no WAN or
    # an unusable model all look the same until a reply comes back.
    return AssistantAvailability(
        "configured",
        f"configured — key + package found, not contacted yet "
        f"({DEFAULT_ASSISTANT_MODEL}, effort {ASSISTANT_EFFORT})", cc)


def _pip_hint():
    """The install command for the interpreter RUNNING this code (the TUI's
    venv, not whatever `pip` is first on PATH — PEP 668 refuses system pip)."""
    import sys as _sys
    return f"{_sys.executable} -m pip install -r requirements/ai.txt"


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
    # Why the Claude API did NOT answer, when it was tried ("" = not tried).
    fallback_reason: str = ""


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
        self._api_key = api_key or resolve_api_key()[0]
        self.last_api_error = ""
        self._expertise_level = expertise_level
        self._mode = (AssistantMode.PRO if self._api_key and _HAS_ANTHROPIC
                      else AssistantMode.STANDALONE)
        if self._api_key and not _HAS_ANTHROPIC:
            # decided here, so say it here — _get_client() never runs now
            self.last_api_error = ("the `anthropic` package is not installed "
                                   f"({_pip_hint()})")

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
                self.last_api_error = ("the `anthropic` package is not installed "
                                       f"({_pip_hint()})")
                logger.warning(self.last_api_error)
                self._mode = AssistantMode.STANDALONE
            else:
                try:
                    self._client = _anthropic_mod.Anthropic(
                        api_key=self._api_key, timeout=90.0, max_retries=1)
                except Exception as e:
                    self.last_api_error = f"client init failed: {type(e).__name__}: {e}"
                    logger.error(self.last_api_error)
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
        # History holds ONLY turns the API took part in, as user/assistant
        # pairs starting on a user turn: a knowledge-base answer replayed as
        # the model's own words, a leading assistant turn after a mid-pair
        # trim, or an orphaned user turn after a fallback all corrupt it
        # (reader pair, 2026-09-30).
        with self._conversation_lock:
            self._conversation.append(ConversationMessage(
                role="user",
                content=question
            ))
            if len(self._conversation) > self.MAX_HISTORY:
                self._conversation = self._conversation[-self.MAX_HISTORY:]
            while self._conversation and self._conversation[0].role != "user":
                self._conversation.pop(0)

        # Try PRO mode first
        reason = ""
        if self._mode == AssistantMode.PRO:
            response = self._ask_claude(question, include_history)
            if response:
                return response
            reason = self.last_api_error or "the Claude API was unavailable"
        elif self._api_key:
            # configured, but out for this session (no package / client init):
            # every later answer says so too, not just the first
            reason = self.last_api_error or "the Claude API is unavailable in this session"

        # The pending user turn got no API answer: drop it from history.
        with self._conversation_lock:
            if self._conversation and self._conversation[-1].role == "user":
                self._conversation.pop()

        # Fall back to standalone — and say why, so a key that is set but
        # broken never passes for a working PRO mode.
        answer = self._ask_standalone(question)
        answer.fallback_reason = reason
        return answer

    def _ask_claude(self, question: str, include_history: bool) -> Optional[AssistantResponse]:
        """Ask using Claude API."""
        self.last_api_error = ""
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
            # max_tokens covers thinking + answer on claude-sonnet-5 (adaptive
            # thinking runs by default when the thinking param is omitted), so
            # the old 1024 cap could truncate a reasoned answer mid-thought.
            response = client.messages.create(
                model=DEFAULT_ASSISTANT_MODEL,
                max_tokens=16000,
                output_config={"effort": ASSISTANT_EFFORT},
                system=self._get_system_prompt(),
                messages=messages,
                cache_control={"type": "ephemeral"},
            )

            # The reply's first block can be a THINKING block (thinking is on
            # by default); `content[0].text` raised there and the broad except
            # below turned every such answer into a silent standalone one.
            if getattr(response, "stop_reason", None) == "refusal":
                self.last_api_error = "the model declined this question (refusal)"
                logger.warning("Claude API: %s", self.last_api_error)
                return None
            answer = "".join(getattr(b, "text", "") for b in (response.content or [])
                             if getattr(b, "type", "") == "text").strip()
            if not answer:
                self.last_api_error = ("no text in the reply (stop_reason="
                                       f"{getattr(response, 'stop_reason', '?')})")
                logger.warning("Claude API: %s", self.last_api_error)
                return None
            if getattr(response, "stop_reason", None) == "max_tokens":
                answer += "\n\n(truncated: the reply hit its length limit)"

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
            self.last_api_error = f"{type(e).__name__}: {e}"
            logger.error(f"Claude API error: {self.last_api_error}")
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

        # NOT added to history: the API must never be shown knowledge-base
        # text as if it were its own earlier answer (reader J6).
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
                mode=AssistantMode.STANDALONE,   # never an API answer
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
            mode=AssistantMode.STANDALONE,   # never an API answer
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
                mode=AssistantMode.STANDALONE,   # never an API answer
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
        return (self._mode == AssistantMode.PRO and self._api_key is not None
                and _HAS_ANTHROPIC)


# Convenience functions

def get_assistant(api_key: Optional[str] = None) -> ClaudeAssistant:
    """Get a Claude Assistant instance."""
    return ClaudeAssistant(api_key=api_key)


def quick_ask(question: str) -> str:
    """Quick query without creating persistent instance."""
    assistant = ClaudeAssistant()
    response = assistant.ask(question)
    return response.answer
