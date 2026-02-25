"""
Messaging Mixin — Send/receive messages, view history, conversations.

Wires commands/messaging.py to TUI menus.
Extracted as a mixin to keep main.py under 1,500 lines.
"""

import logging
from backend import clear_screen
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# --- Optional dependencies (module-level) ---
from utils.event_bus import event_bus

(send_message, get_messages, get_conversations, get_stats,
 start_receiving, stop_receiving, get_rx_status,
 diagnose, get_routing_info, clear_messages,
 _HAS_MESSAGING) = safe_import(
    'commands.messaging',
    'send_message', 'get_messages', 'get_conversations', 'get_stats',
    'start_receiving', 'stop_receiving', 'get_rx_status',
    'diagnose', 'get_routing_info', 'clear_messages',
)


class MessagingMixin:
    """TUI mixin for messaging operations."""

    def _messaging_menu(self):
        """Messaging — send, receive, view message history."""
        while True:
            choices = [
                ("send", "Send Message        Send to node or broadcast"),
                ("live", "Live Feed           Real-time message stream"),
                ("messages", "View Messages       Recent message history"),
                ("convos", "Conversations       Message threads by node"),
                ("stats", "Statistics          Message counts & rates"),
                ("rx", "RX Control          Start/stop listener"),
                ("diagnose", "Diagnose            Check messaging health"),
                ("routing", "Routing Info        Hop limits & device role"),
                ("cleanup", "Cleanup             Purge old messages"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Messaging",
                "Mesh network messaging:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "send": ("Send Message", self._messaging_send),
                "live": ("Live Feed", self._messaging_live_feed),
                "messages": ("View Messages", self._messaging_view),
                "convos": ("Conversations", self._messaging_conversations),
                "stats": ("Statistics", self._messaging_stats),
                "rx": ("RX Control", self._messaging_rx_control),
                "diagnose": ("Diagnose", self._messaging_diagnose),
                "routing": ("Routing Info", self._messaging_routing),
                "cleanup": ("Cleanup", self._messaging_cleanup),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _messaging_live_feed(self):
        """Live message feed — event-bus-driven real-time RX display.

        Subscribes to the event bus and displays messages as they arrive.
        Press Enter to stop and return to menu.
        """
        import threading

        clear_screen()
        print("=== Live Message Feed ===")
        print("  Listening for mesh messages via event bus...")
        print("  Press Enter to stop.\n")
        print(f"  {'Time':<10} {'Dir':<4} {'From':<14} {'Ch':<4} {'Net':<6} Message")
        print(f"  {'-'*65}")

        msg_count = [0]  # Mutable counter for closure

        def _on_message(event):
            """Print incoming message event to terminal."""
            direction = getattr(event, 'direction', '?')
            arrow = '<-' if direction == 'rx' else '->'
            ts = getattr(event, 'timestamp', None)
            time_str = ts.strftime("%H:%M:%S") if ts else "??:??:??"
            node = getattr(event, 'node_name', '') or getattr(event, 'node_id', '') or '?'
            if len(node) > 12:
                node = node[:12]
            channel = getattr(event, 'channel', 0)
            network = getattr(event, 'network', '?')
            content = getattr(event, 'content', '')
            if len(content) > 35:
                content = content[:32] + "..."

            print(f"  {time_str:<10} {arrow:<4} {node:<14} {channel:<4} {network:<6} {content}")
            msg_count[0] += 1

        # Subscribe
        event_bus.subscribe('message', _on_message)

        # Clear unread count in status bar
        if hasattr(self, 'status_bar'):
            self.status_bar.clear_unread()

        try:
            # Block until user presses Enter
            input()
        except (EOFError, KeyboardInterrupt):
            pass
        finally:
            event_bus.unsubscribe('message', _on_message)

        print(f"\n  Stopped. {msg_count[0]} messages received during session.")
        print()
        self._wait_for_enter()

    def _messaging_send(self):
        """Send a message via mesh network."""
        if not _HAS_MESSAGING:
            self.dialog.msgbox("Unavailable", "Messaging module not available.\nFile: src/commands/messaging.py")
            return

        # Get destination
        dest = self.dialog.inputbox(
            "Destination",
            "Node ID (e.g. !abcd1234) or leave blank for broadcast:"
        )
        if dest is None:  # Cancelled
            return
        dest = dest.strip() if dest else None

        # Get message text
        text = self.dialog.inputbox("Message", "Enter message text:")
        if not text:
            return

        # Network selection
        net_choices = [
            ("auto", "Auto-detect         Choose best path"),
            ("meshtastic", "Meshtastic          Direct LoRa radio"),
            ("rns", "RNS / Reticulum     Via RNS transport"),
        ]
        network = self.dialog.menu(
            "Network",
            "Send via which network?",
            net_choices
        )
        if not network:
            return

        clear_screen()
        print("=== Sending Message ===\n")
        dest_display = dest if dest else "broadcast"
        print(f"  To:      {dest_display}")
        print(f"  Network: {network}")
        print(f"  Text:    {text[:60]}{'...' if len(text) > 60 else ''}")
        print()

        result = send_message(
            content=text,
            destination=dest,
            network=network,
        )

        if result.success:
            print(f"  \033[0;32mSent\033[0m: {result.message}")
        else:
            print(f"  \033[0;31mFailed\033[0m: {result.message}")
            if result.error:
                print(f"  Error: {result.error}")

        print()
        self._wait_for_enter()

    def _messaging_view(self):
        """View recent messages."""
        # Clear unread count when viewing
        if hasattr(self, 'status_bar'):
            self.status_bar.clear_unread()

        clear_screen()
        print("=== Recent Messages ===\n")

        if not _HAS_MESSAGING:
            print("  Messaging module not available.")
            self._wait_for_enter()
            return

        result = get_messages(limit=20)

        if not result.success:
            print(f"  Error: {result.message}")
            self._wait_for_enter()
            return

        messages = result.data.get('messages', [])
        if not messages:
            print("  No messages recorded yet.")
            print("  Start the RX listener to capture incoming messages.")
            self._wait_for_enter()
            return

        print(f"  Showing {len(messages)} most recent:\n")
        print(f"  {'Time':<20} {'From':<12} {'To':<12} {'Net':<6} Message")
        print(f"  {'-'*70}")

        for msg in messages:
            ts = msg.get('timestamp', '?')[:19]
            from_id = msg.get('from_id', '?')[:10]
            to_id = msg.get('to_id', 'bcast')[:10] if msg.get('to_id') else 'bcast'
            net = msg.get('network', '?')[:5]
            content = msg.get('content', '')
            if len(content) > 30:
                content = content[:27] + "..."
            print(f"  {ts:<20} {from_id:<12} {to_id:<12} {net:<6} {content}")

        print()
        self._wait_for_enter()

    def _messaging_conversations(self):
        """View message threads grouped by conversation partner."""
        clear_screen()
        print("=== Conversations ===\n")

        if not _HAS_MESSAGING:
            print("  Messaging module not available.")
            self._wait_for_enter()
            return

        result = get_conversations()

        if not result.success:
            print(f"  Error: {result.message}")
            self._wait_for_enter()
            return

        convos = result.data.get('conversations', [])
        if not convos:
            print("  No conversations found.")
            self._wait_for_enter()
            return

        print(f"  {'Node':<16} {'Messages':>8} {'Last Message':<20} Network")
        print(f"  {'-'*55}")

        for convo in convos:
            node = convo.get('node_id', '?')[:14]
            count = convo.get('message_count', 0)
            last = convo.get('last_message', '?')[:19]
            net = convo.get('network', '?')
            print(f"  {node:<16} {count:>8} {last:<20} {net}")

        print()
        self._wait_for_enter()

    def _messaging_stats(self):
        """Show messaging statistics."""
        clear_screen()
        print("=== Messaging Statistics ===\n")

        if not _HAS_MESSAGING:
            print("  Messaging module not available.")
            self._wait_for_enter()
            return

        result = get_stats()

        if not result.success:
            print(f"  Error: {result.message}")
            self._wait_for_enter()
            return

        data = result.data
        print(f"  Total messages:       {data.get('total_messages', 0)}")
        print(f"  Sent:                 {data.get('sent', 0)}")
        print(f"  Received:             {data.get('received', 0)}")
        print(f"  Last 24h:             {data.get('last_24h', 0)}")
        print()

        by_net = data.get('by_network', {})
        if by_net:
            print("  By network:")
            for net, count in by_net.items():
                print(f"    {net:<20} {count}")

        print()
        self._wait_for_enter()

    def _messaging_rx_control(self):
        """Start or stop the message RX listener."""
        if not _HAS_MESSAGING:
            self.dialog.msgbox("Unavailable", "Messaging module not available.")
            return

        # Check current status
        status = get_rx_status()
        is_running = status.data.get('running', False) if status.success else False

        if is_running:
            action = self.dialog.yesno(
                "RX Listener Active",
                "Message receiver is running.\n\nStop listening?"
            )
            if action:
                result = stop_receiving()
                self.dialog.msgbox("RX Stopped", result.message)
        else:
            action = self.dialog.yesno(
                "RX Listener Stopped",
                "Message receiver is not running.\n\nStart listening for messages?"
            )
            if action:
                result = start_receiving()
                if result.success:
                    self.dialog.msgbox("RX Started", result.message)
                else:
                    self.dialog.msgbox("RX Failed", f"{result.message}\n\n{result.error or ''}")

    def _messaging_diagnose(self):
        """Run messaging diagnostics."""
        clear_screen()
        print("=== Messaging Diagnostics ===\n")

        if not _HAS_MESSAGING:
            print("  Messaging module not available.")
            self._wait_for_enter()
            return

        result = diagnose()

        if result.success:
            print(f"  Status: \033[0;32mHealthy\033[0m")
        else:
            print(f"  Status: \033[0;31mIssues Found\033[0m")

        print(f"  {result.message}\n")

        data = result.data or {}
        for key, val in data.items():
            label = key.replace('_', ' ').title()
            if isinstance(val, bool):
                indicator = "\033[0;32mYes\033[0m" if val else "\033[0;31mNo\033[0m"
                print(f"  {label:<25} {indicator}")
            else:
                print(f"  {label:<25} {val}")

        print()
        self._wait_for_enter()

    def _messaging_routing(self):
        """Show routing info for messaging."""
        clear_screen()
        print("=== Messaging Routing Info ===\n")

        if not _HAS_MESSAGING:
            print("  Messaging module not available.")
            self._wait_for_enter()
            return

        result = get_routing_info()

        if not result.success:
            print(f"  Error: {result.message}")
            self._wait_for_enter()
            return

        data = result.data or {}
        for key, val in data.items():
            label = key.replace('_', ' ').title()
            print(f"  {label:<25} {val}")

        print()
        self._wait_for_enter()

    def _messaging_cleanup(self):
        """Purge old messages."""
        if not _HAS_MESSAGING:
            self.dialog.msgbox("Unavailable", "Messaging module not available.")
            return

        confirm = self.dialog.yesno(
            "Cleanup Messages",
            "Delete messages older than 30 days?\n\n"
            "This removes old message history from the local database.\n"
            "Recent messages are preserved."
        )

        if not confirm:
            return

        result = clear_messages(older_than_days=30)
        if result.success:
            self.dialog.msgbox("Cleanup Complete", result.message)
        else:
            self.dialog.msgbox("Cleanup Failed", result.message)
