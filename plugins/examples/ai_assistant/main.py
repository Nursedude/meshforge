"""
AI Development Assistant Plugin for MeshForge.io

Provides AI-powered tools for:
- Code generation with mesh networking context
- Debugging assistance
- Security review
- Code quality analysis
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Pango

import logging
import re
import ast
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from datetime import datetime
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

# Import plugin base if available
try:
    from meshforge.core.plugin_base import Plugin, PluginContext
except ImportError:
    # Fallback for standalone testing
    class Plugin:
        def get_settings(self): return {}
        def update_settings(self, s): pass
    class PluginContext:
        pass


class ReviewSeverity(Enum):
    """Code review finding severity"""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass
class ReviewFinding:
    """A code review finding"""
    line: int
    severity: ReviewSeverity
    category: str
    message: str
    suggestion: Optional[str] = None


@dataclass
class DebugSuggestion:
    """A debugging suggestion"""
    issue: str
    likely_cause: str
    suggested_fix: str
    confidence: str  # high, medium, low


class CodeAnalyzer:
    """
    Static code analysis for mesh networking projects.

    Checks for:
    - Security vulnerabilities
    - Mesh-specific issues
    - Code quality problems
    """

    SECURITY_PATTERNS = {
        r'shell\s*=\s*True': (
            ReviewSeverity.CRITICAL,
            "security",
            "shell=True is dangerous with user input",
            "Use shell=False and pass args as list"
        ),
        r'eval\s*\(': (
            ReviewSeverity.CRITICAL,
            "security",
            "eval() can execute arbitrary code",
            "Use ast.literal_eval() for safe parsing"
        ),
        r'exec\s*\(': (
            ReviewSeverity.CRITICAL,
            "security",
            "exec() can execute arbitrary code",
            "Find alternative approach"
        ),
        r'pickle\.loads?\s*\(': (
            ReviewSeverity.HIGH,
            "security",
            "pickle is unsafe with untrusted data",
            "Use JSON for data serialization"
        ),
        r'(password|api_key|secret)\s*=\s*["\'][^"\']+["\']': (
            ReviewSeverity.CRITICAL,
            "security",
            "Hardcoded credential detected",
            "Use environment variables"
        ),
        r'os\.system\s*\(': (
            ReviewSeverity.HIGH,
            "security",
            "os.system is vulnerable to injection",
            "Use subprocess.run with shell=False"
        ),
    }

    MESH_PATTERNS = {
        r'\.{250,}': (
            ReviewSeverity.HIGH,
            "mesh",
            "String may exceed LoRa payload limit (237 bytes)",
            "Check payload size before transmission"
        ),
        r'time\.sleep\s*\(\s*\d{2,}\s*\)': (
            ReviewSeverity.MEDIUM,
            "mesh",
            "Long sleep may cause missed messages",
            "Use async/event-based waiting"
        ),
        r'while\s+True\s*:(?!.*break)': (
            ReviewSeverity.MEDIUM,
            "mesh",
            "Infinite loop without break condition",
            "Add exit condition for clean shutdown"
        ),
    }

    QUALITY_PATTERNS = {
        r'except\s*:': (
            ReviewSeverity.MEDIUM,
            "quality",
            "Bare except catches all exceptions",
            "Catch specific exceptions"
        ),
        r'# ?TODO': (
            ReviewSeverity.LOW,
            "quality",
            "TODO comment found",
            "Resolve or create tracked issue"
        ),
        r'print\s*\(': (
            ReviewSeverity.LOW,
            "quality",
            "print() in production code",
            "Use logging instead"
        ),
    }

    def analyze(self, code: str) -> List[ReviewFinding]:
        """Analyze code and return findings"""
        findings = []
        lines = code.split('\n')

        all_patterns = {
            **self.SECURITY_PATTERNS,
            **self.MESH_PATTERNS,
            **self.QUALITY_PATTERNS,
        }

        for line_num, line in enumerate(lines, 1):
            for pattern, (severity, category, message, suggestion) in all_patterns.items():
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append(ReviewFinding(
                        line=line_num,
                        severity=severity,
                        category=category,
                        message=message,
                        suggestion=suggestion
                    ))

        return findings

    def analyze_error(self, error_text: str) -> List[DebugSuggestion]:
        """Analyze error message and provide suggestions"""
        suggestions = []

        # Common error patterns
        error_patterns = {
            r'Permission denied.*(/dev/tty|serial)': DebugSuggestion(
                issue="Serial port permission denied",
                likely_cause="User not in dialout group or device busy",
                suggested_fix="Run: sudo usermod -a -G dialout $USER && logout/login",
                confidence="high"
            ),
            r'Connection refused.*4403': DebugSuggestion(
                issue="Cannot connect to meshtasticd API",
                likely_cause="meshtasticd not running or wrong port",
                suggested_fix="Check: systemctl status meshtasticd",
                confidence="high"
            ),
            r'No such file or directory.*config': DebugSuggestion(
                issue="Configuration file not found",
                likely_cause="Config not created or wrong path",
                suggested_fix="Run: meshforge --init-config",
                confidence="medium"
            ),
            r'ModuleNotFoundError.*meshtastic': DebugSuggestion(
                issue="Meshtastic library not installed",
                likely_cause="Missing Python dependency",
                suggested_fix="Run: pip install meshtastic",
                confidence="high"
            ),
            r'RSSI.*-\d{3}': DebugSuggestion(
                issue="Very weak signal (RSSI < -120)",
                likely_cause="Antenna issue or too much distance",
                suggested_fix="Check antenna connection, reduce distance",
                confidence="medium"
            ),
            r'timeout|timed out': DebugSuggestion(
                issue="Operation timed out",
                likely_cause="Device unresponsive or network issue",
                suggested_fix="Check device connection, increase timeout",
                confidence="medium"
            ),
        }

        for pattern, suggestion in error_patterns.items():
            if re.search(pattern, error_text, re.IGNORECASE):
                suggestions.append(suggestion)

        return suggestions


class AIAssistantPanel(Gtk.Box):
    """Main AI Development Assistant panel"""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self.analyzer = CodeAnalyzer()
        self._build_ui()

    def _build_ui(self):
        """Build the assistant UI"""
        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.set_margin_start(15)
        header.set_margin_end(15)
        header.set_margin_top(15)
        header.set_margin_bottom(10)

        icon = Gtk.Image.new_from_icon_name("system-run-symbolic")
        icon.set_pixel_size(32)
        header.append(icon)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label="AI Development Assistant")
        title.add_css_class("title-2")
        title.set_halign(Gtk.Align.START)
        title_box.append(title)

        subtitle = Gtk.Label(label="Code analysis, debugging, and security review")
        subtitle.add_css_class("dim-label")
        subtitle.set_halign(Gtk.Align.START)
        title_box.append(subtitle)

        header.append(title_box)
        self.append(header)

        # Main notebook
        notebook = Gtk.Notebook()
        notebook.set_vexpand(True)

        # Tab 1: Code Review
        review_page = self._build_review_tab()
        notebook.append_page(review_page, Gtk.Label(label="Code Review"))

        # Tab 2: Debug Helper
        debug_page = self._build_debug_tab()
        notebook.append_page(debug_page, Gtk.Label(label="Debug Helper"))

        # Tab 3: Quick Reference
        reference_page = self._build_reference_tab()
        notebook.append_page(reference_page, Gtk.Label(label="Quick Reference"))

        self.append(notebook)

    def _build_review_tab(self) -> Gtk.Box:
        """Build the code review tab"""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.set_margin_start(15)
        page.set_margin_end(15)
        page.set_margin_top(10)
        page.set_margin_bottom(15)

        # Instructions
        info = Gtk.Label(label="Paste code below for security and quality analysis")
        info.add_css_class("dim-label")
        info.set_halign(Gtk.Align.START)
        page.append(info)

        # Code input
        scroll_input = Gtk.ScrolledWindow()
        scroll_input.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll_input.set_min_content_height(200)

        self.code_input = Gtk.TextView()
        self.code_input.set_monospace(True)
        self.code_input.set_wrap_mode(Gtk.WrapMode.NONE)
        self.code_input.get_buffer().set_text(
            "# Paste your code here for analysis\n"
            "# Example:\n"
            "import subprocess\n"
            "def run_command(cmd):\n"
            "    return subprocess.run(cmd, shell=True)\n"
        )
        scroll_input.set_child(self.code_input)
        page.append(scroll_input)

        # Analyze button
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        button_box.set_margin_top(10)

        analyze_btn = Gtk.Button(label="Analyze Code")
        analyze_btn.add_css_class("suggested-action")
        analyze_btn.connect("clicked", self._on_analyze_clicked)
        button_box.append(analyze_btn)

        clear_btn = Gtk.Button(label="Clear")
        clear_btn.connect("clicked", self._on_clear_code)
        button_box.append(clear_btn)

        page.append(button_box)

        # Results
        results_label = Gtk.Label(label="Analysis Results")
        results_label.add_css_class("heading")
        results_label.set_halign(Gtk.Align.START)
        results_label.set_margin_top(15)
        page.append(results_label)

        scroll_results = Gtk.ScrolledWindow()
        scroll_results.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll_results.set_vexpand(True)

        self.results_list = Gtk.ListBox()
        self.results_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.results_list.add_css_class("boxed-list")
        scroll_results.set_child(self.results_list)
        page.append(scroll_results)

        return page

    def _build_debug_tab(self) -> Gtk.Box:
        """Build the debug helper tab"""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.set_margin_start(15)
        page.set_margin_end(15)
        page.set_margin_top(10)
        page.set_margin_bottom(15)

        # Instructions
        info = Gtk.Label(label="Paste error message for debugging suggestions")
        info.add_css_class("dim-label")
        info.set_halign(Gtk.Align.START)
        page.append(info)

        # Error input
        scroll_input = Gtk.ScrolledWindow()
        scroll_input.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll_input.set_min_content_height(150)

        self.error_input = Gtk.TextView()
        self.error_input.set_monospace(True)
        self.error_input.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.error_input.get_buffer().set_text(
            "# Paste your error message here\n"
            "# Example:\n"
            "serial.serialutil.SerialException: [Errno 13] could not open port /dev/ttyACM0: Permission denied"
        )
        scroll_input.set_child(self.error_input)
        page.append(scroll_input)

        # Analyze button
        debug_btn = Gtk.Button(label="Get Debug Suggestions")
        debug_btn.add_css_class("suggested-action")
        debug_btn.set_margin_top(10)
        debug_btn.connect("clicked", self._on_debug_clicked)
        page.append(debug_btn)

        # Suggestions
        suggestions_label = Gtk.Label(label="Debug Suggestions")
        suggestions_label.add_css_class("heading")
        suggestions_label.set_halign(Gtk.Align.START)
        suggestions_label.set_margin_top(15)
        page.append(suggestions_label)

        scroll_suggestions = Gtk.ScrolledWindow()
        scroll_suggestions.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll_suggestions.set_vexpand(True)

        self.suggestions_list = Gtk.ListBox()
        self.suggestions_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.suggestions_list.add_css_class("boxed-list")
        scroll_suggestions.set_child(self.suggestions_list)
        page.append(scroll_suggestions)

        return page

    def _build_reference_tab(self) -> Gtk.Box:
        """Build the quick reference tab"""
        page = Gtk.ScrolledWindow()
        page.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        content.set_margin_start(15)
        content.set_margin_end(15)
        content.set_margin_top(10)
        content.set_margin_bottom(15)

        # Security patterns
        security_group = Adw.PreferencesGroup()
        security_group.set_title("Security Patterns")
        security_group.set_description("Common security issues to avoid")

        patterns = [
            ("shell=True", "Use shell=False with argument list"),
            ("eval()", "Use ast.literal_eval() instead"),
            ("os.system()", "Use subprocess.run()"),
            ("pickle.load()", "Use JSON for untrusted data"),
            ("Hardcoded secrets", "Use environment variables"),
        ]

        for bad, good in patterns:
            row = Adw.ActionRow()
            row.set_title(bad)
            row.set_subtitle(good)
            row.add_css_class("property")
            security_group.add(row)

        content.append(security_group)

        # Mesh limits
        mesh_group = Adw.PreferencesGroup()
        mesh_group.set_title("Mesh Network Limits")
        mesh_group.set_description("Important constraints for LoRa mesh")

        limits = [
            ("Max Payload", "237 bytes (Meshtastic)"),
            ("Duty Cycle", "10% typical (region dependent)"),
            ("Default Timeout", "30 seconds recommended"),
            ("Node ID Format", "8 hex characters (e.g., !a1b2c3d4)"),
            ("Channel Name", "12 characters max"),
        ]

        for name, value in limits:
            row = Adw.ActionRow()
            row.set_title(name)
            row.set_subtitle(value)
            mesh_group.add(row)

        content.append(mesh_group)

        # Debug checklist
        debug_group = Adw.PreferencesGroup()
        debug_group.set_title("Debug Checklist")
        debug_group.set_description("Common troubleshooting steps")

        checks = [
            "Verify serial permissions (dialout group)",
            "Check meshtasticd service status",
            "Confirm antenna is connected",
            "Verify channel settings match",
            "Check for firmware compatibility",
            "Monitor RSSI/SNR values",
        ]

        for check in checks:
            row = Adw.ActionRow()
            row.set_title(check)
            debug_group.add(row)

        content.append(debug_group)

        page.set_child(content)
        return page

    def _on_analyze_clicked(self, button):
        """Handle analyze button click"""
        buffer = self.code_input.get_buffer()
        start, end = buffer.get_bounds()
        code = buffer.get_text(start, end, False)

        findings = self.analyzer.analyze(code)

        # Clear previous results
        while True:
            child = self.results_list.get_first_child()
            if child:
                self.results_list.remove(child)
            else:
                break

        if not findings:
            row = Adw.ActionRow()
            row.set_title("No issues found")
            row.set_subtitle("Code passed all checks")
            row.set_icon_name("emblem-ok-symbolic")
            self.results_list.append(row)
        else:
            for finding in sorted(findings, key=lambda f: f.severity.value):
                row = Adw.ActionRow()

                # Color by severity
                severity_icons = {
                    ReviewSeverity.CRITICAL: "dialog-error-symbolic",
                    ReviewSeverity.HIGH: "dialog-warning-symbolic",
                    ReviewSeverity.MEDIUM: "dialog-information-symbolic",
                    ReviewSeverity.LOW: "dialog-question-symbolic",
                    ReviewSeverity.INFO: "dialog-information-symbolic",
                }

                row.set_icon_name(severity_icons.get(finding.severity, "dialog-information-symbolic"))
                row.set_title(f"[{finding.severity.value.upper()}] Line {finding.line}: {finding.message}")

                if finding.suggestion:
                    row.set_subtitle(f"Fix: {finding.suggestion}")

                self.results_list.append(row)

    def _on_clear_code(self, button):
        """Clear code input"""
        self.code_input.get_buffer().set_text("")

        # Clear results
        while True:
            child = self.results_list.get_first_child()
            if child:
                self.results_list.remove(child)
            else:
                break

    def _on_debug_clicked(self, button):
        """Handle debug button click"""
        buffer = self.error_input.get_buffer()
        start, end = buffer.get_bounds()
        error_text = buffer.get_text(start, end, False)

        suggestions = self.analyzer.analyze_error(error_text)

        # Clear previous suggestions
        while True:
            child = self.suggestions_list.get_first_child()
            if child:
                self.suggestions_list.remove(child)
            else:
                break

        if not suggestions:
            row = Adw.ActionRow()
            row.set_title("No specific suggestions")
            row.set_subtitle("Try providing more error context")
            self.suggestions_list.append(row)
        else:
            for suggestion in suggestions:
                row = Adw.ExpanderRow()
                row.set_title(suggestion.issue)
                row.set_subtitle(f"Confidence: {suggestion.confidence}")

                # Cause row
                cause_row = Adw.ActionRow()
                cause_row.set_title("Likely Cause")
                cause_row.set_subtitle(suggestion.likely_cause)
                row.add_row(cause_row)

                # Fix row
                fix_row = Adw.ActionRow()
                fix_row.set_title("Suggested Fix")
                fix_row.set_subtitle(suggestion.suggested_fix)
                row.add_row(fix_row)

                self.suggestions_list.append(row)


class AIAssistantPlugin(Plugin):
    """Plugin implementation for AI Development Assistant"""

    def activate(self, context: PluginContext) -> None:
        """Called when plugin is enabled"""
        logger.info("AI Development Assistant plugin activated")

        # Register the panel
        context.register_panel(
            panel_id="ai_assistant_panel",
            panel_class=AIAssistantPanel,
            title="AI Assistant",
            icon="system-run-symbolic"
        )

        # Subscribe to code-related events
        context.subscribe("file_opened", self._on_file_opened)

        # Show notification
        context.notify(
            "AI Development Assistant",
            "Code analysis and debugging tools ready"
        )

    def deactivate(self) -> None:
        """Called when plugin is disabled"""
        logger.info("AI Development Assistant plugin deactivated")

    def _on_file_opened(self, data):
        """Handle file opened event for auto-analysis"""
        settings = self.get_settings()
        if settings.get("auto_review", True):
            logger.debug(f"File opened: {data.get('path', 'unknown')}")
            # Could trigger automatic review here
