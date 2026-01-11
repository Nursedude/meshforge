"""
MeshForge Native Messaging Panel - GTK4 Interface

Send and receive messages across Meshtastic and RNS networks.
Integrates with commands/messaging.py for SQLite storage.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
import threading
from datetime import datetime


class MessagingPanel(Gtk.Box):
    """Native mesh messaging panel for GTK4"""

    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.main_window = main_window

        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)

        self._build_ui()
        GLib.idle_add(self._load_messages)
        GLib.idle_add(self._load_stats)

    def _build_ui(self):
        """Build the messaging panel UI"""
        # Title
        title = Gtk.Label(label="Mesh Messaging")
        title.add_css_class("title-1")
        title.set_xalign(0)
        self.append(title)

        subtitle = Gtk.Label(label="Send and receive messages across Meshtastic and RNS networks")
        subtitle.add_css_class("dim-label")
        subtitle.set_xalign(0)
        self.append(subtitle)

        # Main paned container (conversations list | messages)
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_vexpand(True)
        paned.set_position(250)

        # Left: Conversations list
        conv_frame = Gtk.Frame()
        conv_frame.set_label("Conversations")

        conv_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)

        # Conversation list
        conv_scrolled = Gtk.ScrolledWindow()
        conv_scrolled.set_vexpand(True)
        conv_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.conv_listbox = Gtk.ListBox()
        self.conv_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.conv_listbox.connect("row-selected", self._on_conversation_selected)
        conv_scrolled.set_child(self.conv_listbox)

        conv_box.append(conv_scrolled)

        # Refresh button
        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.connect("clicked", lambda b: self._load_conversations())
        refresh_btn.set_margin_start(5)
        refresh_btn.set_margin_end(5)
        refresh_btn.set_margin_bottom(5)
        conv_box.append(refresh_btn)

        conv_frame.set_child(conv_box)
        paned.set_start_child(conv_frame)
        paned.set_shrink_start_child(False)

        # Right: Message view and compose
        msg_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        # Stats bar
        stats_frame = Gtk.Frame()
        stats_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        stats_box.set_margin_start(10)
        stats_box.set_margin_end(10)
        stats_box.set_margin_top(5)
        stats_box.set_margin_bottom(5)

        self.stats_total = Gtk.Label(label="Total: --")
        stats_box.append(self.stats_total)

        self.stats_sent = Gtk.Label(label="Sent: --")
        stats_box.append(self.stats_sent)

        self.stats_received = Gtk.Label(label="Received: --")
        stats_box.append(self.stats_received)

        self.stats_24h = Gtk.Label(label="Last 24h: --")
        stats_box.append(self.stats_24h)

        stats_frame.set_child(stats_box)
        msg_box.append(stats_frame)

        # Messages display
        msg_frame = Gtk.Frame()
        msg_frame.set_label("Messages")
        msg_frame.set_vexpand(True)

        msg_scrolled = Gtk.ScrolledWindow()
        msg_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        msg_scrolled.set_vexpand(True)

        self.msg_text = Gtk.TextView()
        self.msg_text.set_editable(False)
        self.msg_text.set_monospace(True)
        self.msg_text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.msg_text.set_margin_start(10)
        self.msg_text.set_margin_end(10)
        self.msg_text.set_margin_top(10)
        self.msg_text.set_margin_bottom(10)
        msg_scrolled.set_child(self.msg_text)

        msg_frame.set_child(msg_scrolled)
        msg_box.append(msg_frame)

        # Compose area
        compose_frame = Gtk.Frame()
        compose_frame.set_label("Send Message")

        compose_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        compose_box.set_margin_start(10)
        compose_box.set_margin_end(10)
        compose_box.set_margin_top(10)
        compose_box.set_margin_bottom(10)

        # Destination row
        dest_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        dest_label = Gtk.Label(label="To:")
        dest_label.set_xalign(1)
        dest_label.set_size_request(60, -1)
        dest_row.append(dest_label)

        self.dest_entry = Gtk.Entry()
        self.dest_entry.set_placeholder_text("!abcd1234 or leave empty for broadcast")
        self.dest_entry.set_hexpand(True)
        dest_row.append(self.dest_entry)

        compose_box.append(dest_row)

        # Network selection row
        net_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        net_label = Gtk.Label(label="Network:")
        net_label.set_xalign(1)
        net_label.set_size_request(60, -1)
        net_row.append(net_label)

        self.network_dropdown = Gtk.DropDown.new_from_strings([
            "Auto (detect from destination)",
            "Meshtastic",
            "Reticulum (RNS)"
        ])
        self.network_dropdown.set_selected(0)
        net_row.append(self.network_dropdown)

        compose_box.append(net_row)

        # Message input row
        msg_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        msg_label = Gtk.Label(label="Message:")
        msg_label.set_xalign(1)
        msg_label.set_size_request(60, -1)
        msg_row.append(msg_label)

        self.msg_entry = Gtk.Entry()
        self.msg_entry.set_placeholder_text("Type your message...")
        self.msg_entry.set_hexpand(True)
        self.msg_entry.connect("activate", lambda e: self._send_message())
        msg_row.append(self.msg_entry)

        # Character count
        self.char_count = Gtk.Label(label="0/160")
        self.char_count.add_css_class("dim-label")
        self.msg_entry.connect("changed", self._on_msg_changed)
        msg_row.append(self.char_count)

        send_btn = Gtk.Button(label="Send")
        send_btn.add_css_class("suggested-action")
        send_btn.connect("clicked", lambda b: self._send_message())
        msg_row.append(send_btn)

        compose_box.append(msg_row)

        compose_frame.set_child(compose_box)
        msg_box.append(compose_frame)

        paned.set_end_child(msg_box)
        paned.set_shrink_end_child(False)

        self.append(paned)

        # Maintenance row
        maint_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        maint_box.set_halign(Gtk.Align.END)

        clear_btn = Gtk.Button(label="Clear Old Messages (30+ days)")
        clear_btn.add_css_class("destructive-action")
        clear_btn.connect("clicked", self._on_clear_messages)
        maint_box.append(clear_btn)

        self.append(maint_box)

    def _on_msg_changed(self, entry):
        """Update character count"""
        text = entry.get_text()
        count = len(text)
        self.char_count.set_label(f"{count}/160")
        if count > 160:
            self.char_count.add_css_class("warning")
        else:
            self.char_count.remove_css_class("warning")

    def _load_stats(self):
        """Load messaging statistics"""
        def fetch():
            try:
                from commands import messaging
                result = messaging.get_stats()
                if result.success:
                    GLib.idle_add(self._update_stats, result.data)
            except Exception as e:
                GLib.idle_add(
                    self.main_window.set_status_message,
                    f"Failed to load stats: {e}"
                )

        thread = threading.Thread(target=fetch, daemon=True)
        thread.start()

    def _update_stats(self, data):
        """Update stats display"""
        self.stats_total.set_label(f"Total: {data.get('total', 0)}")
        self.stats_sent.set_label(f"Sent: {data.get('sent', 0)}")
        self.stats_received.set_label(f"Received: {data.get('received', 0)}")
        self.stats_24h.set_label(f"Last 24h: {data.get('last_24h', 0)}")
        return False

    def _load_conversations(self):
        """Load conversation list"""
        def fetch():
            try:
                from commands import messaging
                result = messaging.get_conversations()
                if result.success:
                    GLib.idle_add(self._update_conversations, result.data.get('conversations', []))
            except Exception as e:
                GLib.idle_add(
                    self.main_window.set_status_message,
                    f"Failed to load conversations: {e}"
                )

        thread = threading.Thread(target=fetch, daemon=True)
        thread.start()

    def _update_conversations(self, conversations):
        """Update conversation list"""
        # Clear existing
        while True:
            row = self.conv_listbox.get_row_at_index(0)
            if row:
                self.conv_listbox.remove(row)
            else:
                break

        # Add "All Messages" option
        all_row = Gtk.ListBoxRow()
        all_row.set_name("")  # Empty name = all messages
        all_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        all_box.set_margin_start(10)
        all_box.set_margin_end(10)
        all_box.set_margin_top(5)
        all_box.set_margin_bottom(5)
        all_label = Gtk.Label(label="All Messages")
        all_label.set_xalign(0)
        all_label.add_css_class("heading")
        all_box.append(all_label)
        all_row.set_child(all_box)
        self.conv_listbox.append(all_row)

        # Add conversations
        for conv in conversations:
            row = Gtk.ListBoxRow()
            row.set_name(conv.get('partner', ''))

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.set_margin_start(10)
            box.set_margin_end(10)
            box.set_margin_top(5)
            box.set_margin_bottom(5)

            partner_label = Gtk.Label(label=conv.get('partner', 'Unknown'))
            partner_label.set_xalign(0)
            partner_label.add_css_class("heading")
            box.append(partner_label)

            info_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            network_label = Gtk.Label(label=conv.get('network', ''))
            network_label.add_css_class("dim-label")
            info_box.append(network_label)

            count_label = Gtk.Label(label=f"{conv.get('message_count', 0)} msgs")
            count_label.add_css_class("dim-label")
            info_box.append(count_label)

            box.append(info_box)
            row.set_child(box)
            self.conv_listbox.append(row)

        # Select first row
        self.conv_listbox.select_row(self.conv_listbox.get_row_at_index(0))
        return False

    def _on_conversation_selected(self, listbox, row):
        """Handle conversation selection"""
        if row:
            partner = row.get_name()
            self._load_messages(conversation_with=partner if partner else None)
            if partner:
                self.dest_entry.set_text(partner)

    def _load_messages(self, conversation_with=None):
        """Load messages"""
        def fetch():
            try:
                from commands import messaging
                kwargs = {'limit': 50}
                if conversation_with:
                    kwargs['conversation_with'] = conversation_with

                result = messaging.get_messages(**kwargs)
                if result.success:
                    GLib.idle_add(
                        self._update_messages,
                        result.data.get('messages', [])
                    )
            except Exception as e:
                GLib.idle_add(
                    self.main_window.set_status_message,
                    f"Failed to load messages: {e}"
                )

        thread = threading.Thread(target=fetch, daemon=True)
        thread.start()

    def _update_messages(self, messages):
        """Update messages display"""
        buffer = self.msg_text.get_buffer()

        if not messages:
            buffer.set_text("No messages yet.\n\nSend a message using the compose area below.")
            return False

        lines = []
        for msg in reversed(messages):  # Show oldest first
            timestamp = msg.get('timestamp', '')
            if timestamp:
                try:
                    dt = datetime.fromisoformat(timestamp)
                    timestamp = dt.strftime("%Y-%m-%d %H:%M")
                except (ValueError, TypeError):
                    pass

            from_id = msg.get('from_id', '?')
            to_id = msg.get('to_id', 'broadcast')
            content = msg.get('content', '')
            network = msg.get('network', '')

            # Format based on direction
            if from_id == 'local':
                direction = f"→ {to_id}"
            else:
                direction = f"← {from_id}"

            lines.append(f"[{timestamp}] [{network}] {direction}")
            lines.append(f"  {content}")
            lines.append("")

        buffer.set_text("\n".join(lines))

        # Scroll to end
        self.msg_text.scroll_to_iter(buffer.get_end_iter(), 0, False, 0, 0)
        return False

    def _send_message(self):
        """Send a message"""
        content = self.msg_entry.get_text().strip()
        if not content:
            self.main_window.set_status_message("Message cannot be empty")
            return

        destination = self.dest_entry.get_text().strip() or None
        network_idx = self.network_dropdown.get_selected()
        network_map = {0: "auto", 1: "meshtastic", 2: "rns"}
        network = network_map.get(network_idx, "auto")

        def send():
            try:
                from commands import messaging
                result = messaging.send_message(
                    content=content,
                    destination=destination,
                    network=network
                )
                GLib.idle_add(self._on_send_complete, result)
            except Exception as e:
                GLib.idle_add(
                    self.main_window.set_status_message,
                    f"Failed to send: {e}"
                )

        thread = threading.Thread(target=send, daemon=True)
        thread.start()

    def _on_send_complete(self, result):
        """Handle send completion"""
        if result.success:
            self.msg_entry.set_text("")
            self.main_window.set_status_message(result.message)
            # Reload messages and stats
            self._load_messages()
            self._load_stats()
            self._load_conversations()
        else:
            self.main_window.set_status_message(f"Send failed: {result.message}")
        return False

    def _on_clear_messages(self, button):
        """Clear old messages"""
        self.main_window.show_confirm_dialog(
            "Clear Old Messages",
            "Delete all messages older than 30 days?",
            self._do_clear_messages
        )

    def _do_clear_messages(self, confirmed):
        """Actually clear messages if confirmed"""
        if not confirmed:
            return

        def clear():
            try:
                from commands import messaging
                result = messaging.clear_messages(older_than_days=30)
                GLib.idle_add(
                    self.main_window.set_status_message,
                    result.message
                )
                if result.success:
                    GLib.idle_add(self._load_messages)
                    GLib.idle_add(self._load_stats)
                    GLib.idle_add(self._load_conversations)
            except Exception as e:
                GLib.idle_add(
                    self.main_window.set_status_message,
                    f"Failed to clear: {e}"
                )

        thread = threading.Thread(target=clear, daemon=True)
        thread.start()
