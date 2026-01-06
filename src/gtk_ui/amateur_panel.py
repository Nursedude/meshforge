"""
Amateur Radio Panel for MeshForge

Provides ham radio specific UI features:
- Callsign management
- ARES/RACES tools
- Part 97 reference
- Station ID reminder
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Pango

from typing import Optional
from datetime import datetime

# Use centralized logging
from utils.logging_config import get_logger
logger = get_logger(__name__)

# Import amateur radio modules with fallback
try:
    from amateur.callsign import CallsignManager, CallsignInfo, StationIDTimer
    from amateur.compliance import Part97Reference, ComplianceChecker, LicenseClass
    from amateur.ares_races import ARESRACESTools, TrafficMessage, MessagePriority
    AMATEUR_AVAILABLE = True
except ImportError:
    AMATEUR_AVAILABLE = False
    logger.warning("Amateur radio modules not available")


class AmateurPanel(Gtk.Box):
    """
    Main Amateur Radio panel with tabbed interface.
    """

    def __init__(self, app=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.app = app

        if not AMATEUR_AVAILABLE:
            self._show_unavailable_message()
            return

        # Initialize managers
        self.callsign_manager = CallsignManager()
        self.ares_tools = ARESRACESTools()
        self.compliance_checker = ComplianceChecker()

        # Station ID timer - use new StationIDTimer class
        self._station_id_timer: Optional[StationIDTimer] = None
        self._ui_update_timer = None

        self._build_ui()
        self._start_id_timer()

        # Connect cleanup to widget destruction
        self.connect("unrealize", self._on_unrealize)

    def _show_unavailable_message(self):
        """Show message when amateur modules aren't available"""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_margin_top(50)
        box.set_margin_bottom(50)

        icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
        icon.set_pixel_size(64)
        box.append(icon)

        label = Gtk.Label(label="Amateur Radio Features Unavailable")
        label.add_css_class("title-2")
        box.append(label)

        desc = Gtk.Label(label="The amateur radio modules could not be loaded.\nCheck your installation.")
        desc.set_wrap(True)
        desc.add_css_class("dim-label")
        box.append(desc)

        self.append(box)

    def _build_ui(self):
        """Build the main UI"""
        # Header with callsign display
        header = self._build_header()
        self.append(header)

        # Main notebook with tabs
        notebook = Gtk.Notebook()
        notebook.set_vexpand(True)

        # Station tab
        station_page = self._build_station_tab()
        notebook.append_page(station_page, Gtk.Label(label="Station"))

        # ARES/RACES tab
        ares_page = self._build_ares_tab()
        notebook.append_page(ares_page, Gtk.Label(label="ARES/RACES"))

        # Part 97 tab
        part97_page = self._build_part97_tab()
        notebook.append_page(part97_page, Gtk.Label(label="Part 97"))

        # Traffic tab
        traffic_page = self._build_traffic_tab()
        notebook.append_page(traffic_page, Gtk.Label(label="Traffic"))

        self.append(notebook)

    def _build_header(self) -> Gtk.Box:
        """Build the header with callsign and ID reminder"""
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.set_margin_top(10)
        header.set_margin_bottom(10)
        header.set_margin_start(10)
        header.set_margin_end(10)

        # Callsign display
        call_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        call_label = Gtk.Label(label="Station Callsign")
        call_label.add_css_class("dim-label")
        call_label.set_halign(Gtk.Align.START)
        call_box.append(call_label)

        self.callsign_display = Gtk.Label()
        self.callsign_display.add_css_class("title-1")
        self.callsign_display.set_halign(Gtk.Align.START)
        self._update_callsign_display()
        call_box.append(self.callsign_display)

        header.append(call_box)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        header.append(spacer)

        # ID Reminder box
        self.id_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.id_box.set_halign(Gtk.Align.END)

        id_label = Gtk.Label(label="Station ID")
        id_label.add_css_class("dim-label")
        self.id_box.append(id_label)

        self.id_status = Gtk.Label(label="Ready")
        self.id_status.add_css_class("success")
        self.id_box.append(self.id_status)

        id_button = Gtk.Button(label="ID Now")
        id_button.connect("clicked", self._on_id_now)
        id_button.add_css_class("suggested-action")
        self.id_box.append(id_button)

        header.append(self.id_box)

        return header

    def _build_station_tab(self) -> Gtk.ScrolledWindow:
        """Build the station configuration tab"""
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        content.set_margin_top(15)
        content.set_margin_bottom(15)
        content.set_margin_start(15)
        content.set_margin_end(15)

        # Callsign entry section
        call_group = Adw.PreferencesGroup()
        call_group.set_title("My Station")
        call_group.set_description("Configure your amateur radio station")

        # Callsign entry
        call_row = Adw.EntryRow()
        call_row.set_title("Callsign")
        if self.callsign_manager.my_callsign:
            call_row.set_text(self.callsign_manager.my_callsign)
        call_row.connect("changed", self._on_callsign_changed)
        self.callsign_entry = call_row
        call_group.add(call_row)

        # Grid square
        grid_row = Adw.EntryRow()
        grid_row.set_title("Grid Square")
        if self.callsign_manager.my_info and self.callsign_manager.my_info.grid_square:
            grid_row.set_text(self.callsign_manager.my_info.grid_square)
        self.grid_entry = grid_row
        call_group.add(grid_row)

        # License class
        license_row = Adw.ComboRow()
        license_row.set_title("License Class")
        license_model = Gtk.StringList.new([
            "Technician",
            "General",
            "Amateur Extra"
        ])
        license_row.set_model(license_model)
        license_row.connect("notify::selected", self._on_license_changed)
        self.license_combo = license_row
        call_group.add(license_row)

        content.append(call_group)

        # Station ID settings
        id_group = Adw.PreferencesGroup()
        id_group.set_title("Station Identification")
        id_group.set_description("Per FCC §97.119")

        # ID interval
        id_row = Adw.SpinRow.new_with_range(1, 10, 1)
        id_row.set_title("ID Reminder Interval")
        id_row.set_subtitle("Minutes (FCC requires every 10 minutes)")
        id_row.set_value(10)
        id_group.add(id_row)

        # Auto ID option
        auto_id_row = Adw.SwitchRow()
        auto_id_row.set_title("Auto ID Reminder")
        auto_id_row.set_subtitle("Show notification when ID is due")
        auto_id_row.set_active(True)
        id_group.add(auto_id_row)

        content.append(id_group)

        # Lookup section
        lookup_group = Adw.PreferencesGroup()
        lookup_group.set_title("Callsign Lookup")

        lookup_row = Adw.EntryRow()
        lookup_row.set_title("Look up callsign")
        lookup_row.connect("apply", self._on_lookup_callsign)
        lookup_group.add(lookup_row)

        self.lookup_result = Gtk.Label()
        self.lookup_result.set_wrap(True)
        self.lookup_result.set_margin_top(10)
        self.lookup_result.set_halign(Gtk.Align.START)
        lookup_group.add(self.lookup_result)

        content.append(lookup_group)

        scroll.set_child(content)
        return scroll

    def _build_ares_tab(self) -> Gtk.ScrolledWindow:
        """Build the ARES/RACES tab"""
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        content.set_margin_top(15)
        content.set_margin_bottom(15)
        content.set_margin_start(15)
        content.set_margin_end(15)

        # Net Control Checklist
        checklist_group = Adw.PreferencesGroup()
        checklist_group.set_title("Net Control Checklist")
        checklist_group.set_description("Standard net operation procedures")

        # Start new checklist button
        new_checklist_btn = Gtk.Button(label="Start New Checklist")
        new_checklist_btn.connect("clicked", self._on_new_checklist)
        new_checklist_btn.add_css_class("suggested-action")
        checklist_group.add(new_checklist_btn)

        # Checklist items container
        self.checklist_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.checklist_box.set_margin_top(10)
        checklist_group.add(self.checklist_box)

        content.append(checklist_group)

        # Tactical Callsigns
        tactical_group = Adw.PreferencesGroup()
        tactical_group.set_title("Tactical Callsigns")
        tactical_group.set_description("Assign tactical IDs to stations")

        # Common tactical buttons
        tactical_box = Gtk.FlowBox()
        tactical_box.set_selection_mode(Gtk.SelectionMode.NONE)
        tactical_box.set_max_children_per_line(4)
        tactical_box.set_column_spacing(5)
        tactical_box.set_row_spacing(5)

        for tactical in ['EOC', 'NET', 'RELAY', 'BASE', 'MOBILE', 'SHELTER']:
            btn = Gtk.Button(label=tactical)
            btn.connect("clicked", self._on_tactical_clicked, tactical)
            tactical_box.append(btn)

        tactical_group.add(tactical_box)

        # Assignment list
        self.tactical_list = Gtk.ListBox()
        self.tactical_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.tactical_list.add_css_class("boxed-list")
        self.tactical_list.set_margin_top(10)
        self._update_tactical_list()
        tactical_group.add(self.tactical_list)

        content.append(tactical_group)

        scroll.set_child(content)
        return scroll

    def _build_part97_tab(self) -> Gtk.ScrolledWindow:
        """Build the Part 97 reference tab"""
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        content.set_margin_top(15)
        content.set_margin_bottom(15)
        content.set_margin_start(15)
        content.set_margin_end(15)

        # Search
        search_group = Adw.PreferencesGroup()
        search_group.set_title("Part 97 Reference")
        search_group.set_description("FCC Amateur Radio Rules")

        search_entry = Adw.EntryRow()
        search_entry.set_title("Search rules")
        search_entry.connect("changed", self._on_rule_search)
        self.rule_search = search_entry
        search_group.add(search_entry)

        content.append(search_group)

        # Rules list
        rules_group = Adw.PreferencesGroup()
        rules_group.set_title("Key Rules")

        self.rules_list = Gtk.ListBox()
        self.rules_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.rules_list.add_css_class("boxed-list")

        # Add key rules
        key_rules = ['97.1', '97.101', '97.113', '97.119', '97.313', '97.403']
        for rule_num in key_rules:
            rule = Part97Reference.get_rule(rule_num)
            if rule:
                row = Adw.ActionRow()
                row.set_title(f"§{rule_num}: {rule['title']}")
                row.set_subtitle(rule['summary'][:100] + "..." if len(rule['summary']) > 100 else rule['summary'])
                row.set_activatable(True)
                row.connect("activated", self._on_rule_clicked, rule_num)
                self.rules_list.append(row)

        rules_group.add(self.rules_list)
        content.append(rules_group)

        # Band privileges
        band_group = Adw.PreferencesGroup()
        band_group.set_title("Band Privileges")
        band_group.set_description("Your authorized frequencies")

        self.band_list = Gtk.ListBox()
        self.band_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.band_list.add_css_class("boxed-list")
        self._update_band_privileges()
        band_group.add(self.band_list)

        content.append(band_group)

        scroll.set_child(content)
        return scroll

    def _build_traffic_tab(self) -> Gtk.ScrolledWindow:
        """Build the traffic handling tab"""
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        content.set_margin_top(15)
        content.set_margin_bottom(15)
        content.set_margin_start(15)
        content.set_margin_end(15)

        # New message
        msg_group = Adw.PreferencesGroup()
        msg_group.set_title("ICS-213 Message")
        msg_group.set_description("Standard emergency message format")

        new_msg_btn = Gtk.Button(label="Create New Message")
        new_msg_btn.connect("clicked", self._on_new_message)
        new_msg_btn.add_css_class("suggested-action")
        msg_group.add(new_msg_btn)

        content.append(msg_group)

        # Traffic stats
        stats_group = Adw.PreferencesGroup()
        stats_group.set_title("Traffic Statistics")

        stats = self.ares_tools.get_traffic_stats()

        total_row = Adw.ActionRow()
        total_row.set_title("Total Messages")
        total_row.set_subtitle(str(stats['total']))
        stats_group.add(total_row)

        content.append(stats_group)

        # Net report
        report_group = Adw.PreferencesGroup()
        report_group.set_title("Net Report")

        gen_report_btn = Gtk.Button(label="Generate Net Report")
        gen_report_btn.connect("clicked", self._on_generate_report)
        report_group.add(gen_report_btn)

        content.append(report_group)

        scroll.set_child(content)
        return scroll

    def _update_callsign_display(self):
        """Update the callsign display"""
        if self.callsign_manager.my_callsign:
            self.callsign_display.set_text(self.callsign_manager.my_callsign)
            if self.callsign_manager.my_info and self.callsign_manager.my_info.grid_square:
                self.callsign_display.set_text(
                    f"{self.callsign_manager.my_callsign} ({self.callsign_manager.my_info.grid_square})"
                )
        else:
            self.callsign_display.set_text("Not Set")
            self.callsign_display.add_css_class("dim-label")

    def _update_tactical_list(self):
        """Update the tactical assignments list"""
        # Clear existing
        while True:
            child = self.tactical_list.get_first_child()
            if child:
                self.tactical_list.remove(child)
            else:
                break

        # Add current assignments
        for tactical, callsign in self.ares_tools.tactical_assignments.items():
            row = Adw.ActionRow()
            row.set_title(tactical)
            row.set_subtitle(callsign)

            # Clear button
            clear_btn = Gtk.Button.new_from_icon_name("edit-clear-symbolic")
            clear_btn.set_valign(Gtk.Align.CENTER)
            clear_btn.connect("clicked", self._on_clear_tactical, tactical)
            row.add_suffix(clear_btn)

            self.tactical_list.append(row)

        if not self.ares_tools.tactical_assignments:
            row = Adw.ActionRow()
            row.set_title("No assignments")
            row.set_subtitle("Click a tactical button above to assign")
            self.tactical_list.append(row)

    def _update_band_privileges(self):
        """Update the band privileges list based on license class"""
        # Clear existing
        while True:
            child = self.band_list.get_first_child()
            if child:
                self.band_list.remove(child)
            else:
                break

        # Get bands for license class
        bands = Part97Reference.get_bands_for_license(self.compliance_checker.license_class)

        for band in bands:
            row = Adw.ActionRow()
            row.set_title(f"{band.band} ({band.frequency_start}-{band.frequency_end} MHz)")
            row.set_subtitle(f"Max {band.max_power_watts}W • {', '.join(band.modes[:3])}")
            if band.notes:
                row.set_tooltip_text(band.notes)
            self.band_list.append(row)

    def _start_id_timer(self):
        """Start the station ID reminder timer using StationIDTimer"""
        callsign = self.callsign_manager.my_callsign or "NOCALL"

        # Callback when ID is due - runs on background thread, use GLib.idle_add for UI
        def on_id_due():
            GLib.idle_add(self._show_id_due_alert)

        # Callback for warning - runs on background thread
        def on_warning(seconds_remaining):
            GLib.idle_add(self._show_id_warning, seconds_remaining)

        # Create and start the timer
        self._station_id_timer = StationIDTimer(
            callsign=callsign,
            interval_minutes=10,
            warning_minutes=1,
            on_id_due=on_id_due,
            on_warning=on_warning,
        )
        self._station_id_timer.start()

        # Start UI update timer (every second for real-time countdown)
        self._ui_update_timer = GLib.timeout_add(1000, self._update_id_display)

    def _update_id_display(self) -> bool:
        """Update the ID countdown display every second"""
        if not self._station_id_timer:
            return False

        time_str = self._station_id_timer.time_until_id_due()
        seconds = self._station_id_timer.seconds_until_id_due()

        # Update display
        self.id_status.set_text(time_str)

        # Update styling based on urgency
        self.id_status.remove_css_class("success")
        self.id_status.remove_css_class("warning")
        self.id_status.remove_css_class("error")

        if seconds <= 0:
            self.id_status.add_css_class("error")
        elif seconds <= 60:
            self.id_status.add_css_class("warning")
        else:
            self.id_status.add_css_class("success")

        return True  # Keep timer running

    def _show_id_due_alert(self):
        """Show alert that station ID is due"""
        self.id_status.set_text("ID NOW!")
        self.id_status.remove_css_class("success")
        self.id_status.remove_css_class("warning")
        self.id_status.add_css_class("error")

        # Show notification if app supports it
        if self.app:
            try:
                self.app.show_notification(
                    "Station ID Required",
                    f"Time to identify as {self._station_id_timer.callsign}"
                )
            except (AttributeError, Exception) as e:
                logger.debug(f"Could not show notification: {e}")

    def _show_id_warning(self, seconds_remaining: int):
        """Show warning that ID is due soon"""
        self.id_status.remove_css_class("success")
        self.id_status.add_css_class("warning")

    # Event handlers
    def _on_id_now(self, button):
        """Handle ID now button - record station identification"""
        if self._station_id_timer:
            self._station_id_timer.record_id()
            callsign = self._station_id_timer.callsign
        else:
            self.callsign_manager.record_identification()
            callsign = self.callsign_manager.my_callsign

        # Update UI immediately
        self.id_status.set_text("ID Complete")
        self.id_status.remove_css_class("error")
        self.id_status.remove_css_class("warning")
        self.id_status.add_css_class("success")

        if self.app:
            try:
                self.app.show_notification(
                    "Station ID",
                    f"Identified as {callsign}"
                )
            except (AttributeError, Exception) as e:
                logger.debug(f"Failed to show notification: {e}")

        logger.info(f"Station ID recorded: {callsign}")

    def _on_callsign_changed(self, entry):
        """Handle callsign entry change"""
        text = entry.get_text().upper().strip()
        if text and self.callsign_manager.validate_callsign(text):
            self.callsign_manager.set_my_callsign(text)
            self._update_callsign_display()
            entry.remove_css_class("error")
        elif text:
            entry.add_css_class("error")

    def _on_license_changed(self, combo, param):
        """Handle license class change"""
        license_classes = [LicenseClass.TECHNICIAN, LicenseClass.GENERAL, LicenseClass.EXTRA]
        self.compliance_checker.license_class = license_classes[combo.get_selected()]
        self._update_band_privileges()

    def _on_lookup_callsign(self, entry):
        """Handle callsign lookup"""
        callsign = entry.get_text().upper().strip()
        if not callsign:
            return

        info = self.callsign_manager.lookup_callsign(callsign)
        if info and info.is_valid():
            self.lookup_result.set_text(
                f"{info.callsign}: {info.name}\n"
                f"{info.city}, {info.state}\n"
                f"Class: {info.license_class}"
            )
        else:
            self.lookup_result.set_text(f"No information found for {callsign}")

    def _on_new_checklist(self, button):
        """Start a new net control checklist"""
        checklist = self.ares_tools.start_new_checklist()

        # Clear existing items
        while True:
            child = self.checklist_box.get_first_child()
            if child:
                self.checklist_box.remove(child)
            else:
                break

        # Add checklist items
        for i, item in enumerate(checklist):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

            check = Gtk.CheckButton()
            check.connect("toggled", self._on_checklist_toggled, i)
            row.append(check)

            label_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            task_label = Gtk.Label(label=item.task)
            task_label.set_halign(Gtk.Align.START)
            task_label.add_css_class("heading")
            label_box.append(task_label)

            desc_label = Gtk.Label(label=item.description)
            desc_label.set_halign(Gtk.Align.START)
            desc_label.add_css_class("dim-label")
            label_box.append(desc_label)

            row.append(label_box)
            self.checklist_box.append(row)

    def _on_checklist_toggled(self, check, index):
        """Handle checklist item toggle"""
        if check.get_active():
            self.ares_tools.complete_checklist_item(
                index,
                operator=self.callsign_manager.my_callsign or "Unknown"
            )

    def _on_tactical_clicked(self, button, tactical):
        """Handle tactical callsign button click"""
        # Show dialog to assign callsign
        dialog = Adw.MessageDialog.new(
            self.get_root(),
            f"Assign {tactical}",
            f"Enter the callsign for {tactical}:"
        )

        entry = Gtk.Entry()
        entry.set_placeholder_text("Callsign")
        dialog.set_extra_child(entry)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("assign", "Assign")
        dialog.set_response_appearance("assign", Adw.ResponseAppearance.SUGGESTED)

        def on_response(dialog, response):
            if response == "assign":
                callsign = entry.get_text().upper().strip()
                if callsign:
                    self.ares_tools.assign_tactical(tactical, callsign)
                    self._update_tactical_list()
            dialog.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def _on_clear_tactical(self, button, tactical):
        """Clear a tactical assignment"""
        self.ares_tools.clear_tactical(tactical)
        self._update_tactical_list()

    def _on_rule_search(self, entry):
        """Handle rule search"""
        query = entry.get_text().strip()
        if not query:
            return

        results = Part97Reference.search_rules(query)
        # Could update a results list here
        logger.info(f"Found {len(results)} rules matching '{query}'")

    def _on_rule_clicked(self, row, rule_num):
        """Handle rule click"""
        rule = Part97Reference.get_rule(rule_num)
        if rule:
            dialog = Adw.MessageDialog.new(
                self.get_root(),
                f"§{rule_num}: {rule['title']}",
                rule['summary']
            )
            dialog.add_response("ok", "OK")
            dialog.present()

    def _on_new_message(self, button):
        """Create a new ICS-213 message"""
        station_id = self.callsign_manager.my_callsign or "UNKNOWN"
        msg = self.ares_tools.create_message(station_id)

        # Show message editor dialog
        dialog = Adw.MessageDialog.new(
            self.get_root(),
            f"New Message: {msg.message_number}",
            "ICS-213 message created. Edit in full message editor."
        )
        dialog.add_response("ok", "OK")
        dialog.present()

    def _on_generate_report(self, button):
        """Generate net report"""
        report = self.ares_tools.generate_net_report(
            net_name="MeshForge Net",
            ncs_callsign=self.callsign_manager.my_callsign or "Unknown",
            frequency="146.520 MHz",
            checkins=list(self.ares_tools.tactical_assignments.values())
        )

        # Show report in dialog
        dialog = Adw.MessageDialog.new(
            self.get_root(),
            "Net Report",
            "Report generated successfully."
        )

        # Add scrollable text view for report
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(300)
        scroll.set_min_content_width(500)

        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_monospace(True)
        text_view.get_buffer().set_text(report)
        scroll.set_child(text_view)

        dialog.set_extra_child(scroll)
        dialog.add_response("close", "Close")
        dialog.add_response("copy", "Copy")
        dialog.set_response_appearance("copy", Adw.ResponseAppearance.SUGGESTED)

        def on_response(dialog, response):
            if response == "copy":
                clipboard = self.get_clipboard()
                clipboard.set(report)
            dialog.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def _on_unrealize(self, widget):
        """Handle widget unrealization - cleanup resources"""
        self.cleanup()

    def cleanup(self):
        """Clean up resources"""
        # Stop the Station ID timer background thread
        if self._station_id_timer:
            self._station_id_timer.stop()
            self._station_id_timer = None

        # Stop the UI update timer
        if self._ui_update_timer:
            GLib.source_remove(self._ui_update_timer)
            self._ui_update_timer = None

        logger.debug("Amateur panel resources cleaned up")
