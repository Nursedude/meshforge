"""Diagnostics, knowledge base, and Claude assistant menus for AIToolsHandler.

Extracted from ai_tools.py for file size compliance (CLAUDE.md #6).

Host class must provide `self.ctx` (TUIContext).
"""

import logging

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
        """Claude Assistant — first says which AI assist this box ACTUALLY has.

        The old banner said "Mode: PRO — Full Claude AI capabilities" whenever
        ANTHROPIC_API_KEY was set, even with no `anthropic` package (it could
        never answer), and a reply whose first block was a thinking block
        fell back to the knowledge base without a word. Now: measured
        availability, the fallback reason on every answer, and the
        subscription path (Claude Code in the repo) named — an API SDK cannot
        use a Claude subscription (item 5, 2026-09-30).
        """
        if not _HAS_ASSISTANT:
            self.ctx.dialog.msgbox(
                "Error",
                "Claude assistant not available.\n\n"
                "Ensure you're running from the src/ directory."
            )
            return
        from pathlib import Path as _Path
        from utils.claude_assistant import check_availability
        av = check_availability()
        repo = _Path(__file__).resolve().parents[3]
        lines = ["AI assist on this box (checked now):", ""]
        if av.api == "configured":
            lines.append(f"  Claude API: {av.detail}")
        else:
            lines.append("  Claude API: not in use —")
            lines.append(f"    {av.detail}")
        if av.claude_code:
            lines.append("  Claude Code (Claude subscription): installed —")
            lines.append(f"    as your user: cd {repo} && claude")
            lines.append("    (MeshForge's skills, rules and checks load there)")
        else:
            lines.append("  Claude Code (Claude subscription): not found")
            lines.append("    (checked PATH and ~/.local/bin)")
        lines += ["", "Each answer says whether the Claude API or the local",
                  "knowledge base answered it — and why, if not the API."]
        self.ctx.dialog.msgbox("Claude Assistant", "\n".join(lines))

        # The title follows the LAST ACTUAL answerer, never the banner's hope.
        label = "Claude API configured" if av.api == "configured" else "local knowledge base"
        assistant = ClaudeAssistant()      # one per session: history carries over
        while True:
            question = self.ctx.dialog.inputbox(
                f"Claude Assistant ({label})",
                "Ask a question about mesh networking:\n(Enter blank to exit)"
            )
            if not question:
                break
            mode = self._ask_assistant(question, assistant)
            if mode == "pro":
                label = "Claude API"
            elif mode == "standalone":
                label = "local knowledge base"

    def _ask_assistant(self, question: str, assistant=None):
        """Ask the Claude assistant; the answer says who answered, and why not the API."""
        self.ctx.dialog.infobox("Thinking", f"Processing: {question[:40]}...")

        if not _HAS_ASSISTANT:
            self.ctx.dialog.msgbox(
                "Error",
                "Claude assistant not available.\n\n"
                "Ensure you're running from the src/ directory."
            )
            return

        try:
            assistant = assistant or ClaudeAssistant()
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

            if response.mode.value == "pro":
                from utils.claude_assistant import DEFAULT_ASSISTANT_MODEL
                result_lines.append(f"Answered by: Claude API ({DEFAULT_ASSISTANT_MODEL})")
            else:
                result_lines.append("Answered by: local knowledge base")
                if getattr(response, "fallback_reason", ""):
                    result_lines.append(f"Claude API NOT used: {response.fallback_reason}")
            if response.confidence > 0:
                result_lines.append(f"Confidence: {response.confidence:.0%}")

            self.ctx.dialog.msgbox(
                "Claude Assistant",
                "\n".join(result_lines)
            )
            return response.mode.value
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Assistant failed: {e}")
            return None
