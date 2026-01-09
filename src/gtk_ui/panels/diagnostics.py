"""
Diagnostics Panel - Comprehensive network monitoring and troubleshooting

Provides user-visible diagnostics including:
- Real-time health status dashboard
- Live event log with filtering
- Network connectivity tests
- Diagnostic report generation
- Audit trail viewer
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Pango
import threading
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

# Import diagnostic system
try:
    from utils.network_diagnostics import (
        get_diagnostics, NetworkDiagnostics,
        EventCategory, EventSeverity, HealthStatus,
        DiagnosticEvent, HealthCheck, DIAG_DIR
    )
    HAS_DIAGNOSTICS = True
except ImportError:
    HAS_DIAGNOSTICS = False

# Import logging
try:
    from utils.logging_utils import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


class DiagnosticsPanel(Gtk.Box):
    """Comprehensive diagnostics and monitoring panel."""

    # Status colors
    STATUS_COLORS = {
        HealthStatus.HEALTHY: "#4caf50",    # Green
        HealthStatus.DEGRADED: "#ff9800",   # Orange
        HealthStatus.UNHEALTHY: "#f44336",  # Red
        HealthStatus.UNKNOWN: "#9e9e9e",    # Gray
    }

    SEVERITY_COLORS = {
        EventSeverity.DEBUG: "#9e9e9e",
        EventSeverity.INFO: "#2196f3",
        EventSeverity.WARNING: "#ff9800",
        EventSeverity.ERROR: "#f44336",
        EventSeverity.CRITICAL: "#9c27b0",
    }

    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.main_window = main_window

        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)

        # Initialize diagnostics system
        if HAS_DIAGNOSTICS:
            self.diag = get_diagnostics()
            self.diag.register_event_callback(self._on_new_event)
            self.diag.register_health_callback(self._on_health_change)
        else:
            self.diag = None

        self._build_ui()

        # Start periodic updates
        GLib.timeout_add_seconds(5, self._update_health_display)

    def _build_ui(self):
        """Build the diagnostics panel UI."""
        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        title = Gtk.Label(label="Network Diagnostics")
        title.add_css_class("title-1")
        title.set_xalign(0)
        header.append(title)

        # Overall status indicator
        self.overall_status = Gtk.Label(label="Checking...")
        self.overall_status.add_css_class("title-2")
        header.append(Gtk.Box())  # Spacer
        header.append(self.overall_status)

        self.append(header)

        # Subtitle
        subtitle = Gtk.Label(
            label="Monitor network health, view events, and troubleshoot issues"
        )
        subtitle.set_xalign(0)
        subtitle.add_css_class("dim-label")
        self.append(subtitle)

        # Main content in notebook
        notebook = Gtk.Notebook()
        notebook.set_vexpand(True)
        notebook.set_margin_top(15)

        # Tab 1: Health Dashboard
        notebook.append_page(
            self._create_health_tab(),
            Gtk.Label(label="Health Status")
        )

        # Tab 2: Event Log
        notebook.append_page(
            self._create_events_tab(),
            Gtk.Label(label="Event Log")
        )

        # Tab 3: Network Tests
        notebook.append_page(
            self._create_tests_tab(),
            Gtk.Label(label="Network Tests")
        )

        # Tab 4: Reports
        notebook.append_page(
            self._create_reports_tab(),
            Gtk.Label(label="Reports")
        )

        self.append(notebook)

    def _create_health_tab(self) -> Gtk.Widget:
        """Create health status dashboard tab."""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Health cards container
        self.health_cards = Gtk.FlowBox()
        self.health_cards.set_selection_mode(Gtk.SelectionMode.NONE)
        self.health_cards.set_max_children_per_line(3)
        self.health_cards.set_column_spacing(10)
        self.health_cards.set_row_spacing(10)
        box.append(self.health_cards)

        # Create initial health cards
        self._health_card_widgets = {}
        subsystems = [
            ("meshtasticd", "Meshtastic Daemon", "network-wireless-symbolic"),
            ("rns", "Reticulum (RNS)", "network-transmit-receive-symbolic"),
            ("internet", "Internet", "network-wired-symbolic"),
            ("disk", "Disk Space", "drive-harddisk-symbolic"),
            ("memory", "Memory", "computer-symbolic"),
        ]

        for sys_id, name, icon in subsystems:
            card = self._create_health_card(sys_id, name, icon)
            self.health_cards.append(card)

        # Recommendations section
        rec_frame = Gtk.Frame()
        rec_frame.set_label("Recommendations")
        rec_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        rec_box.set_margin_start(10)
        rec_box.set_margin_end(10)
        rec_box.set_margin_top(10)
        rec_box.set_margin_bottom(10)

        self.recommendations_list = Gtk.ListBox()
        self.recommendations_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.recommendations_list.add_css_class("boxed-list")
        rec_box.append(self.recommendations_list)

        rec_frame.set_child(rec_box)
        box.append(rec_frame)

        scrolled.set_child(box)
        return scrolled

    def _create_health_card(self, sys_id: str, name: str, icon: str) -> Gtk.Widget:
        """Create a single health status card."""
        frame = Gtk.Frame()
        frame.set_size_request(200, 120)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Header with icon
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        icon_widget = Gtk.Image.new_from_icon_name(icon)
        icon_widget.set_icon_size(Gtk.IconSize.LARGE)
        header.append(icon_widget)

        name_label = Gtk.Label(label=name)
        name_label.add_css_class("heading")
        name_label.set_xalign(0)
        header.append(name_label)
        box.append(header)

        # Status indicator
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        status_icon = Gtk.Label(label="●")
        status_icon.add_css_class("dim-label")
        status_box.append(status_icon)

        status_label = Gtk.Label(label="Checking...")
        status_label.set_xalign(0)
        status_box.append(status_label)
        box.append(status_box)

        # Message
        message_label = Gtk.Label(label="")
        message_label.set_xalign(0)
        message_label.add_css_class("dim-label")
        message_label.set_wrap(True)
        message_label.set_max_width_chars(25)
        box.append(message_label)

        frame.set_child(box)

        # Store references for updates
        self._health_card_widgets[sys_id] = {
            "frame": frame,
            "status_icon": status_icon,
            "status_label": status_label,
            "message_label": message_label
        }

        return frame

    def _create_events_tab(self) -> Gtk.Widget:
        """Create event log tab."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Filter controls
        filter_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        # Category filter
        filter_box.append(Gtk.Label(label="Category:"))
        self.category_filter = Gtk.DropDown.new_from_strings([
            "All", "Network", "Security", "Performance", "System", "Error", "Audit"
        ])
        self.category_filter.set_selected(0)
        self.category_filter.connect("notify::selected", self._on_filter_changed)
        filter_box.append(self.category_filter)

        # Severity filter
        filter_box.append(Gtk.Label(label="Severity:"))
        self.severity_filter = Gtk.DropDown.new_from_strings([
            "All", "Debug", "Info", "Warning", "Error", "Critical"
        ])
        self.severity_filter.set_selected(0)
        self.severity_filter.connect("notify::selected", self._on_filter_changed)
        filter_box.append(self.severity_filter)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        filter_box.append(spacer)

        # Refresh button
        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Refresh Events")
        refresh_btn.connect("clicked", lambda b: self._refresh_events())
        filter_box.append(refresh_btn)

        # Clear button
        clear_btn = Gtk.Button()
        clear_btn.set_icon_name("edit-clear-symbolic")
        clear_btn.set_tooltip_text("Clear Display")
        clear_btn.connect("clicked", lambda b: self._clear_events_display())
        filter_box.append(clear_btn)

        box.append(filter_box)

        # Event list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        self.events_list = Gtk.ListBox()
        self.events_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.events_list.add_css_class("boxed-list")
        scrolled.set_child(self.events_list)
        box.append(scrolled)

        # Event details
        details_frame = Gtk.Frame()
        details_frame.set_label("Event Details")
        self.event_details = Gtk.Label(label="Select an event to view details")
        self.event_details.set_xalign(0)
        self.event_details.set_margin_start(10)
        self.event_details.set_margin_end(10)
        self.event_details.set_margin_top(10)
        self.event_details.set_margin_bottom(10)
        self.event_details.set_wrap(True)
        self.event_details.set_selectable(True)
        details_frame.set_child(self.event_details)
        box.append(details_frame)

        # Initial load
        GLib.idle_add(self._refresh_events)

        return box

    def _create_tests_tab(self) -> Gtk.Widget:
        """Create network tests tab."""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Quick tests section
        tests_frame = Gtk.Frame()
        tests_frame.set_label("Quick Diagnostics")
        tests_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        tests_box.set_margin_start(15)
        tests_box.set_margin_end(15)
        tests_box.set_margin_top(10)
        tests_box.set_margin_bottom(10)

        # Test buttons
        tests = [
            ("test_meshtastic", "Test Meshtastic Connection", "Test connection to meshtasticd on port 4403"),
            ("test_rns", "Test RNS Connection", "Check Reticulum daemon and network"),
            ("test_internet", "Test Internet", "Check DNS and internet connectivity"),
            ("test_ports", "Scan Network Ports", "Check which services are listening"),
            ("test_all", "Run All Tests", "Execute comprehensive diagnostic suite"),
        ]

        self.test_buttons = {}
        for test_id, label, tooltip in tests:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

            btn = Gtk.Button(label=label)
            btn.set_tooltip_text(tooltip)
            btn.connect("clicked", self._on_test_clicked, test_id)
            self.test_buttons[test_id] = btn
            row.append(btn)

            tests_box.append(row)

        tests_frame.set_child(tests_box)
        box.append(tests_frame)

        # Test results
        results_frame = Gtk.Frame()
        results_frame.set_label("Test Results")
        results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        results_box.set_margin_start(10)
        results_box.set_margin_end(10)
        results_box.set_margin_top(10)
        results_box.set_margin_bottom(10)

        self.test_results = Gtk.TextView()
        self.test_results.set_editable(False)
        self.test_results.set_monospace(True)
        self.test_results.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.test_results.set_vexpand(True)

        results_scroll = Gtk.ScrolledWindow()
        results_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        results_scroll.set_min_content_height(200)
        results_scroll.set_child(self.test_results)
        results_box.append(results_scroll)

        results_frame.set_child(results_box)
        box.append(results_frame)

        scrolled.set_child(box)
        return scrolled

    def _create_reports_tab(self) -> Gtk.Widget:
        """Create reports tab."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Generate report section
        gen_frame = Gtk.Frame()
        gen_frame.set_label("Generate Report")
        gen_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        gen_box.set_margin_start(15)
        gen_box.set_margin_end(15)
        gen_box.set_margin_top(10)
        gen_box.set_margin_bottom(10)

        gen_box.append(Gtk.Label(
            label="Generate a comprehensive diagnostic report including health status,\n"
                  "recent events, and recommendations.",
            xalign=0
        ))

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        gen_btn = Gtk.Button(label="Generate Report")
        gen_btn.add_css_class("suggested-action")
        gen_btn.connect("clicked", self._on_generate_report)
        btn_box.append(gen_btn)

        open_dir_btn = Gtk.Button(label="Open Reports Folder")
        open_dir_btn.connect("clicked", self._on_open_reports_dir)
        btn_box.append(open_dir_btn)

        gen_box.append(btn_box)
        gen_frame.set_child(gen_box)
        box.append(gen_frame)

        # Report viewer
        viewer_frame = Gtk.Frame()
        viewer_frame.set_label("Report Viewer")
        viewer_frame.set_vexpand(True)

        self.report_view = Gtk.TextView()
        self.report_view.set_editable(False)
        self.report_view.set_monospace(True)
        self.report_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.report_view.set_left_margin(10)
        self.report_view.set_right_margin(10)
        self.report_view.set_top_margin(10)
        self.report_view.set_bottom_margin(10)

        viewer_scroll = Gtk.ScrolledWindow()
        viewer_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        viewer_scroll.set_child(self.report_view)
        viewer_frame.set_child(viewer_scroll)
        box.append(viewer_frame)

        # Previous reports list
        prev_frame = Gtk.Frame()
        prev_frame.set_label("Previous Reports")

        self.reports_list = Gtk.ListBox()
        self.reports_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.reports_list.connect("row-selected", self._on_report_selected)

        reports_scroll = Gtk.ScrolledWindow()
        reports_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        reports_scroll.set_max_content_height(150)
        reports_scroll.set_child(self.reports_list)
        prev_frame.set_child(reports_scroll)
        box.append(prev_frame)

        # Load existing reports
        GLib.idle_add(self._load_reports_list)

        return box

    # ==================== Event Handlers ====================

    def _on_new_event(self, event: 'DiagnosticEvent'):
        """Handle new diagnostic event."""
        GLib.idle_add(self._add_event_to_list, event)

    def _on_health_change(self, name: str, check: 'HealthCheck'):
        """Handle health status change."""
        GLib.idle_add(self._update_health_card, name, check)

    def _on_filter_changed(self, dropdown, param):
        """Handle filter change."""
        self._refresh_events()

    def _on_test_clicked(self, button, test_id: str):
        """Run a diagnostic test."""
        button.set_sensitive(False)
        self.test_results.get_buffer().set_text(f"Running {test_id}...\n")

        def run_test():
            results = []
            try:
                if test_id == "test_meshtastic":
                    results = self._test_meshtastic()
                elif test_id == "test_rns":
                    results = self._test_rns()
                elif test_id == "test_internet":
                    results = self._test_internet()
                elif test_id == "test_ports":
                    results = self._test_ports()
                elif test_id == "test_all":
                    results = self._test_all()
            except Exception as e:
                results = [f"ERROR: {e}"]

            GLib.idle_add(self._show_test_results, results, button)

        threading.Thread(target=run_test, daemon=True).start()

    def _on_generate_report(self, button):
        """Generate diagnostic report."""
        if not self.diag:
            self.report_view.get_buffer().set_text("Diagnostics not available")
            return

        button.set_sensitive(False)

        def generate():
            try:
                report_path = self.diag.save_report()
                report = self.diag.generate_report()
                import json
                report_text = json.dumps(report, indent=2)
                GLib.idle_add(self._show_report, report_text, report_path, button)
            except Exception as e:
                GLib.idle_add(
                    lambda: self.report_view.get_buffer().set_text(f"Error: {e}")
                )
                GLib.idle_add(lambda: button.set_sensitive(True))

        threading.Thread(target=generate, daemon=True).start()

    def _on_open_reports_dir(self, button):
        """Open reports directory."""
        if DIAG_DIR.exists():
            subprocess.Popen(['xdg-open', str(DIAG_DIR)])

    def _on_report_selected(self, listbox, row):
        """Load selected report."""
        if not row:
            return

        report_file = row.get_child().report_path
        try:
            with open(report_file, 'r') as f:
                self.report_view.get_buffer().set_text(f.read())
        except Exception as e:
            self.report_view.get_buffer().set_text(f"Error loading report: {e}")

    # ==================== UI Updates ====================

    def _update_health_display(self) -> bool:
        """Update health status display."""
        if not self.diag:
            return True

        # Update overall status
        overall = self.diag.get_overall_health()
        status_text = {
            HealthStatus.HEALTHY: "● System Healthy",
            HealthStatus.DEGRADED: "● System Degraded",
            HealthStatus.UNHEALTHY: "● System Unhealthy",
            HealthStatus.UNKNOWN: "● Status Unknown",
        }.get(overall, "● Unknown")

        self.overall_status.set_label(status_text)

        # Update health cards
        health = self.diag.get_health()
        for name, check in health.items():
            self._update_health_card(name, check)

        # Update recommendations
        self._update_recommendations()

        return True  # Continue timer

    def _update_health_card(self, name: str, check: 'HealthCheck'):
        """Update a single health card."""
        if name not in self._health_card_widgets:
            return

        widgets = self._health_card_widgets[name]

        # Update status indicator
        color = self.STATUS_COLORS.get(check.status, "#9e9e9e")
        widgets["status_icon"].set_markup(f'<span foreground="{color}">●</span>')

        # Update status text
        status_text = check.status.value.title()
        widgets["status_label"].set_label(status_text)

        # Update message
        widgets["message_label"].set_label(check.message[:50])

    def _update_recommendations(self):
        """Update recommendations list."""
        if not self.diag:
            return

        # Clear existing
        while True:
            row = self.recommendations_list.get_row_at_index(0)
            if row:
                self.recommendations_list.remove(row)
            else:
                break

        # Get recommendations from report
        report = self.diag.generate_report()
        recs = report.get("recommendations", [])

        if not recs:
            label = Gtk.Label(label="No issues detected")
            label.set_margin_start(10)
            label.set_margin_top(5)
            label.set_margin_bottom(5)
            label.add_css_class("dim-label")
            self.recommendations_list.append(label)
        else:
            for rec in recs[:5]:  # Limit to 5
                label = Gtk.Label(label=rec)
                label.set_xalign(0)
                label.set_wrap(True)
                label.set_margin_start(10)
                label.set_margin_end(10)
                label.set_margin_top(5)
                label.set_margin_bottom(5)
                self.recommendations_list.append(label)

    def _refresh_events(self):
        """Refresh events list with current filters."""
        if not self.diag:
            return

        # Clear existing
        while True:
            row = self.events_list.get_row_at_index(0)
            if row:
                self.events_list.remove(row)
            else:
                break

        # Get filter values
        cat_idx = self.category_filter.get_selected()
        sev_idx = self.severity_filter.get_selected()

        category = None
        if cat_idx > 0:
            categories = [None, EventCategory.NETWORK, EventCategory.SECURITY,
                         EventCategory.PERFORMANCE, EventCategory.SYSTEM,
                         EventCategory.ERROR, EventCategory.AUDIT]
            category = categories[cat_idx] if cat_idx < len(categories) else None

        severity = None
        if sev_idx > 0:
            severities = [None, EventSeverity.DEBUG, EventSeverity.INFO,
                         EventSeverity.WARNING, EventSeverity.ERROR, EventSeverity.CRITICAL]
            severity = severities[sev_idx] if sev_idx < len(severities) else None

        # Get events
        events = self.diag.get_events(category=category, severity=severity, limit=100)

        for event in events:
            self._add_event_to_list(event)

    def _add_event_to_list(self, event: 'DiagnosticEvent'):
        """Add event to the events list."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_margin_start(10)
        row.set_margin_end(10)
        row.set_margin_top(5)
        row.set_margin_bottom(5)

        # Timestamp
        ts = event.timestamp.strftime("%H:%M:%S")
        ts_label = Gtk.Label(label=ts)
        ts_label.add_css_class("dim-label")
        ts_label.set_size_request(70, -1)
        row.append(ts_label)

        # Severity indicator
        color = self.SEVERITY_COLORS.get(event.severity, "#9e9e9e")
        sev_label = Gtk.Label()
        sev_label.set_markup(f'<span foreground="{color}">●</span>')
        row.append(sev_label)

        # Category
        cat_label = Gtk.Label(label=event.category.value[:4].upper())
        cat_label.set_size_request(40, -1)
        cat_label.add_css_class("dim-label")
        row.append(cat_label)

        # Source
        src_label = Gtk.Label(label=event.source[:12])
        src_label.set_size_request(80, -1)
        row.append(src_label)

        # Message
        msg_label = Gtk.Label(label=event.message[:60])
        msg_label.set_xalign(0)
        msg_label.set_hexpand(True)
        msg_label.set_ellipsize(Pango.EllipsizeMode.END)
        row.append(msg_label)

        # Store event reference
        row.event = event

        self.events_list.prepend(row)

    def _clear_events_display(self):
        """Clear events display."""
        while True:
            row = self.events_list.get_row_at_index(0)
            if row:
                self.events_list.remove(row)
            else:
                break

    # ==================== Tests ====================

    def _test_meshtastic(self) -> List[str]:
        """Test meshtastic connection."""
        results = ["=== Meshtastic Connection Test ===\n"]

        # Check port
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        result = sock.connect_ex(('localhost', 4403))
        sock.close()

        if result == 0:
            results.append("[PASS] Port 4403 is open")
        else:
            results.append("[FAIL] Port 4403 not responding")
            results.append("  FIX: Start meshtasticd: sudo systemctl start meshtasticd")

        # Check service
        try:
            proc = subprocess.run(
                ['systemctl', 'is-active', 'meshtasticd'],
                capture_output=True, text=True, timeout=5
            )
            if proc.stdout.strip() == 'active':
                results.append("[PASS] meshtasticd service is active")
            else:
                results.append(f"[WARN] meshtasticd service: {proc.stdout.strip()}")
        except Exception as e:
            results.append(f"[WARN] Could not check service: {e}")

        return results

    def _test_rns(self) -> List[str]:
        """Test RNS connection."""
        results = ["=== RNS Connection Test ===\n"]

        # Check if RNS module available
        try:
            import RNS
            results.append("[PASS] RNS module installed")
        except ImportError:
            results.append("[FAIL] RNS module not installed")
            results.append("  FIX: pip install rns")
            return results

        # Check rnsd
        try:
            proc = subprocess.run(['pgrep', '-f', 'rnsd'], capture_output=True, timeout=5)
            if proc.returncode == 0:
                results.append("[PASS] rnsd daemon is running")
            else:
                results.append("[WARN] rnsd daemon not running")
                results.append("  FIX: Start with: rnsd")
        except Exception as e:
            results.append(f"[WARN] Could not check rnsd: {e}")

        # Check config - use real user home for sudo compatibility
        try:
            from utils.paths import get_real_user_home
            user_home = get_real_user_home()
        except ImportError:
            import os as os_mod
            sudo_user = os_mod.environ.get('SUDO_USER')
            user_home = Path(f'/home/{sudo_user}') if sudo_user and sudo_user != 'root' else Path.home()
        rns_config = user_home / '.reticulum' / 'config'
        if rns_config.exists():
            results.append(f"[PASS] RNS config exists: {rns_config}")
        else:
            results.append("[WARN] RNS config not found")
            results.append("  FIX: Run rnsd once to create default config")

        return results

    def _test_internet(self) -> List[str]:
        """Test internet connectivity."""
        results = ["=== Internet Connectivity Test ===\n"]

        # DNS test
        import socket
        try:
            socket.gethostbyname('google.com')
            results.append("[PASS] DNS resolution working")
        except socket.gaierror:
            results.append("[FAIL] DNS resolution failed")
            results.append("  FIX: Check /etc/resolv.conf")

        # Connectivity test
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        result = sock.connect_ex(('8.8.8.8', 53))
        sock.close()

        if result == 0:
            results.append("[PASS] Internet connectivity OK")
        else:
            results.append("[FAIL] Cannot reach internet")
            results.append("  FIX: Check network configuration")

        return results

    def _test_ports(self) -> List[str]:
        """Scan network ports."""
        results = ["=== Port Scan ===\n"]

        ports = [
            (4403, "meshtasticd"),
            (22, "SSH"),
            (80, "HTTP"),
            (443, "HTTPS"),
            (1883, "MQTT"),
            (8883, "MQTT-TLS"),
        ]

        import socket
        for port, name in ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            result = sock.connect_ex(('localhost', port))
            sock.close()

            if result == 0:
                results.append(f"[OPEN] Port {port} ({name})")
            else:
                results.append(f"[CLOSED] Port {port} ({name})")

        return results

    def _test_all(self) -> List[str]:
        """Run all tests."""
        results = []
        results.extend(self._test_meshtastic())
        results.append("")
        results.extend(self._test_rns())
        results.append("")
        results.extend(self._test_internet())
        results.append("")
        results.extend(self._test_ports())
        return results

    def _show_test_results(self, results: List[str], button):
        """Display test results."""
        self.test_results.get_buffer().set_text('\n'.join(results))
        button.set_sensitive(True)

    def _show_report(self, report_text: str, report_path: Path, button):
        """Display generated report."""
        self.report_view.get_buffer().set_text(report_text)
        button.set_sensitive(True)
        self._load_reports_list()

    def _load_reports_list(self):
        """Load list of existing reports."""
        # Clear existing
        while True:
            row = self.reports_list.get_row_at_index(0)
            if row:
                self.reports_list.remove(row)
            else:
                break

        if not DIAG_DIR.exists():
            return

        # Find report files
        reports = sorted(DIAG_DIR.glob("diag_report_*.json"), reverse=True)

        for report_file in reports[:10]:  # Limit to 10
            label = Gtk.Label(label=report_file.name)
            label.set_xalign(0)
            label.set_margin_start(10)
            label.set_margin_top(5)
            label.set_margin_bottom(5)
            label.report_path = report_file
            self.reports_list.append(label)
