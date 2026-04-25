"""Diagnostics, knowledge base, and Claude assistant menus for AIToolsHandler.

Extracted from ai_tools.py for file size compliance (CLAUDE.md #6).

Host class must provide `self.ctx` (TUIContext).
"""

import logging
import os

from utils.safe_import import safe_import

# Optional dependencies (safe_import returns (*attrs, available_bool))
diagnose, Category, Severity, _HAS_DIAGNOSTICS = safe_import(
    'utils.diagnostic_engine', 'diagnose', 'Category', 'Severity'
)
get_knowledge_base, _HAS_KNOWLEDGE = safe_import(
    'utils.knowledge_base', 'get_knowledge_base'
)
ClaudeAssistant, _HAS_ASSISTANT = safe_import(
    'utils.claude_assistant', 'ClaudeAssistant'
)

logger = logging.getLogger(__name__)


class DiagnosticsAndAssistantMixin:
    """Mixin: rule-based diagnostics + knowledge base + Claude assistant menus."""

    def _intelligent_diagnostics(self):
        """Run intelligent diagnostics with symptom analysis."""
        symptom_choices = [
            ("connection", "Connection refused to meshtasticd"),
            ("no_nodes", "No nodes visible in mesh"),
            ("weak_signal", "Weak signal / low SNR"),
            ("timeout", "Message timeouts"),
            ("service", "Service not starting"),
            ("custom", "Describe custom symptom"),
            ("back", "Back"),
        ]

        while True:
            choice = self.ctx.dialog.menu(
                "Intelligent Diagnostics",
                "Select a symptom to diagnose:",
                symptom_choices
            )

            if choice is None or choice == "back":
                break

            symptom_text = None
            if choice == "custom":
                symptom_text = self.ctx.dialog.inputbox(
                    "Custom Symptom",
                    "Describe the issue you're experiencing:"
                )
                if not symptom_text:
                    continue
            else:
                symptom_map = {
                    "connection": "Connection refused to meshtasticd on port 4403",
                    "no_nodes": "No nodes visible in mesh network",
                    "weak_signal": "Weak signal with low SNR values",
                    "timeout": "Message timeouts when sending",
                    "service": "Service meshtasticd failed to start",
                }
                symptom_text = symptom_map.get(choice, choice)

            self._run_diagnosis(symptom_text)

    def _run_diagnosis(self, symptom: str):
        """Run diagnosis on a symptom."""
        self.ctx.dialog.infobox("Analyzing", f"Analyzing: {symptom[:40]}...")

        if not _HAS_DIAGNOSTICS:
            self.ctx.dialog.msgbox(
                "Error",
                "Diagnostic engine not available.\n\n"
                "Ensure you're running from the src/ directory."
            )
            return

        try:
            diagnosis_result = diagnose(
                symptom,
                category=Category.CONNECTIVITY,
                severity=Severity.ERROR
            )

            if diagnosis_result:
                result_lines = [
                    f"SYMPTOM: {symptom}",
                    "",
                    "LIKELY CAUSE:",
                    f"  {diagnosis_result.likely_cause}",
                    "",
                    f"CONFIDENCE: {diagnosis_result.confidence:.0%}",
                    "",
                ]

                if diagnosis_result.evidence:
                    result_lines.append("EVIDENCE:")
                    for ev in diagnosis_result.evidence[:3]:
                        result_lines.append(f"  - {ev}")
                    result_lines.append("")

                if diagnosis_result.suggestions:
                    result_lines.append("SUGGESTIONS:")
                    for i, sug in enumerate(diagnosis_result.suggestions[:5], 1):
                        result_lines.append(f"  {i}. {sug}")
                    result_lines.append("")

                if diagnosis_result.auto_recoverable:
                    result_lines.append(f"AUTO-RECOVERY: {diagnosis_result.recovery_action}")

                self.ctx.dialog.msgbox(
                    "Diagnosis Result",
                    "\n".join(result_lines)
                )
            else:
                self.ctx.dialog.msgbox(
                    "Diagnosis",
                    f"No specific diagnosis found for:\n{symptom}\n\n"
                    "Try the Knowledge Base for general information,\n"
                    "or use Claude Assistant for detailed help."
                )
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Diagnosis failed: {e}")

    def _knowledge_base_query(self):
        """Query the knowledge base for mesh networking concepts."""
        topic_choices = [
            ("snr", "What is SNR?"),
            ("rssi", "What is RSSI?"),
            ("lora", "How does LoRa work?"),
            ("meshtastic", "Meshtastic basics"),
            ("reticulum", "Reticulum basics"),
            ("antenna", "Antenna selection"),
            ("range", "Improving range"),
            ("custom", "Custom query"),
            ("back", "Back"),
        ]

        while True:
            choice = self.ctx.dialog.menu(
                "Knowledge Base",
                "Select a topic or enter custom query:",
                topic_choices
            )

            if choice is None or choice == "back":
                break

            query = None
            if choice == "custom":
                query = self.ctx.dialog.inputbox(
                    "Knowledge Query",
                    "Enter your question about mesh networking:"
                )
                if not query:
                    continue
            else:
                query_map = {
                    "snr": "What is SNR?",
                    "rssi": "What is RSSI?",
                    "lora": "How does LoRa modulation work?",
                    "meshtastic": "What is Meshtastic and how does it work?",
                    "reticulum": "What is Reticulum Network Stack?",
                    "antenna": "How do I choose the right antenna?",
                    "range": "How can I improve my mesh range?",
                }
                query = query_map.get(choice, choice)

            self._query_knowledge(query)

    def _query_knowledge(self, query: str):
        """Query the knowledge base."""
        self.ctx.dialog.infobox("Searching", f"Searching: {query[:40]}...")

        if not _HAS_KNOWLEDGE:
            self.ctx.dialog.msgbox(
                "Error",
                "Knowledge base not available.\n\n"
                "Ensure you're running from the src/ directory."
            )
            return

        try:
            kb = get_knowledge_base()
            results = kb.query(query)

            if results:
                result_lines = [f"QUERY: {query}", ""]

                for i, result in enumerate(results[:3], 1):
                    result_lines.append(f"--- Result {i}: {result.title} ---")
                    content = result.content.strip()
                    if len(content) > 800:
                        content = content[:800] + "..."
                    result_lines.append(content)
                    result_lines.append("")

                self.ctx.dialog.msgbox(
                    "Knowledge Base Results",
                    "\n".join(result_lines)
                )
            else:
                self.ctx.dialog.msgbox(
                    "No Results",
                    f"No knowledge base entries found for:\n{query}\n\n"
                    "Try different keywords or use Claude Assistant."
                )
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Query failed: {e}")

    def _claude_assistant(self):
        """Interactive Claude Assistant for mesh help."""
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        mode = "PRO" if api_key else "Standalone"

        self.ctx.dialog.msgbox(
            "Claude Assistant",
            f"Mode: {mode}\n\n"
            f"{'PRO mode: Full Claude AI capabilities' if api_key else 'Standalone: Rule-based + knowledge base'}\n\n"
            f"{'Set ANTHROPIC_API_KEY for PRO features.' if not api_key else 'API key detected.'}"
        )

        while True:
            question = self.ctx.dialog.inputbox(
                f"Claude Assistant ({mode})",
                "Ask a question about mesh networking:\n(Enter blank to exit)"
            )

            if not question:
                break

            self._ask_assistant(question)

    def _ask_assistant(self, question: str):
        """Ask the Claude assistant."""
        self.ctx.dialog.infobox("Thinking", f"Processing: {question[:40]}...")

        if not _HAS_ASSISTANT:
            self.ctx.dialog.msgbox(
                "Error",
                "Claude assistant not available.\n\n"
                "Ensure you're running from the src/ directory."
            )
            return

        try:
            assistant = ClaudeAssistant()
            response = assistant.ask(question)

            result_lines = [
                f"Q: {question}",
                "",
                "ANSWER:",
                response.answer,
                "",
            ]

            if response.suggested_actions:
                result_lines.append("SUGGESTED ACTIONS:")
                for action in response.suggested_actions[:3]:
                    result_lines.append(f"  - {action}")
                result_lines.append("")

            result_lines.append(f"Mode: {response.mode.value.upper()}")
            if response.confidence > 0:
                result_lines.append(f"Confidence: {response.confidence:.0%}")

            self.ctx.dialog.msgbox(
                "Claude Assistant",
                "\n".join(result_lines)
            )
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Assistant failed: {e}")
