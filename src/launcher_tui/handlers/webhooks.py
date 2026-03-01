"""
Webhooks Handler — Manage webhook endpoints for external notifications.

Converted from webhooks_mixin.py as part of the mixin-to-registry migration.
"""

import logging

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

get_webhook_manager, WebhookEndpoint, EventType, _HAS_WEBHOOKS = safe_import(
    'utils.webhooks', 'get_webhook_manager', 'WebhookEndpoint', 'EventType'
)


class WebhooksHandler(BaseHandler):
    """TUI handler for webhook management."""

    handler_id = "webhooks"
    menu_section = "configuration"

    def menu_items(self):
        return [
            ("webhooks", "Webhooks            External notifications", None),
        ]

    def execute(self, action):
        if action == "webhooks":
            self._webhooks_menu()

    def _webhooks_menu(self):
        while True:
            choices = [
                ("list", "List Endpoints      Show configured hooks"),
                ("add", "Add Endpoint        Register new webhook"),
                ("remove", "Remove Endpoint     Delete a webhook"),
                ("toggle", "Enable/Disable      Toggle endpoint state"),
                ("test", "Test Webhook        Send test event"),
                ("events", "Event Types         Supported event list"),
                ("back", "Back"),
            ]
            choice = self.ctx.dialog.menu("Webhooks", "External notification management:", choices)
            if choice is None or choice == "back":
                break
            dispatch = {
                "list": ("List Endpoints", self._webhooks_list),
                "add": ("Add Endpoint", self._webhooks_add),
                "remove": ("Remove Endpoint", self._webhooks_remove),
                "toggle": ("Toggle Endpoint", self._webhooks_toggle),
                "test": ("Test Webhook", self._webhooks_test),
                "events": ("Event Types", self._webhooks_event_types),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _webhooks_list(self):
        clear_screen()
        print("=== Webhook Endpoints ===\n")
        if not _HAS_WEBHOOKS:
            print("  Webhooks module not available.")
            print("  File: src/utils/webhooks.py")
            self.ctx.wait_for_enter()
            return
        manager = get_webhook_manager()
        endpoints = manager.list_endpoints()
        if not endpoints:
            print("  No webhook endpoints configured.")
            print("  Use 'Add Endpoint' to register a webhook URL.")
            self.ctx.wait_for_enter()
            return
        for i, ep in enumerate(endpoints, 1):
            enabled = "\033[0;32mON\033[0m" if ep.get('enabled', False) else "\033[0;31mOFF\033[0m"
            name = ep.get('name', 'unnamed')
            url = ep.get('url', '?')
            events = ep.get('events', [])
            event_str = ", ".join(events[:3]) if events else "all events"
            if len(events) > 3:
                event_str += f" +{len(events) - 3} more"
            print(f"  {i}. [{enabled}] {name}")
            print(f"     URL: {url}")
            print(f"     Events: {event_str}")
            print()
        print()
        self.ctx.wait_for_enter()

    def _webhooks_add(self):
        if not _HAS_WEBHOOKS:
            self.ctx.dialog.msgbox("Unavailable", "Webhooks module not available.")
            return
        name = self.ctx.dialog.inputbox("Webhook Name", "Short name for this endpoint:")
        if not name:
            return
        url = self.ctx.dialog.inputbox("Webhook URL", "Full URL (https://...):")
        if not url:
            return
        if not url.startswith(('http://', 'https://')):
            self.ctx.dialog.msgbox("Invalid URL", "URL must start with http:// or https://")
            return
        manager = get_webhook_manager()
        endpoint = WebhookEndpoint(url=url, name=name)
        if manager.add_endpoint(endpoint):
            self.ctx.dialog.msgbox("Added", f"Webhook '{name}' added.\nURL: {url}")
        else:
            self.ctx.dialog.msgbox("Failed", f"Endpoint already exists for URL:\n{url}")

    def _webhooks_remove(self):
        if not _HAS_WEBHOOKS:
            self.ctx.dialog.msgbox("Unavailable", "Webhooks module not available.")
            return
        manager = get_webhook_manager()
        endpoints = manager.list_endpoints()
        if not endpoints:
            self.ctx.dialog.msgbox("No Endpoints", "No webhook endpoints configured.")
            return
        choices = []
        for ep in endpoints:
            name = ep.get('name', 'unnamed')
            url = ep.get('url', '?')
            short_url = url[:40] + "..." if len(url) > 40 else url
            choices.append((ep['url'], f"{name:<16} {short_url}"))
        selected = self.ctx.dialog.menu("Remove Webhook", "Select endpoint to remove:", choices)
        if selected:
            confirm = self.ctx.dialog.yesno("Confirm Remove", f"Remove webhook endpoint?\n\nURL: {selected}")
            if confirm:
                if manager.remove_endpoint(selected):
                    self.ctx.dialog.msgbox("Removed", "Webhook endpoint removed.")
                else:
                    self.ctx.dialog.msgbox("Failed", "Could not remove endpoint.")

    def _webhooks_toggle(self):
        if not _HAS_WEBHOOKS:
            self.ctx.dialog.msgbox("Unavailable", "Webhooks module not available.")
            return
        manager = get_webhook_manager()
        endpoints = manager.list_endpoints()
        if not endpoints:
            self.ctx.dialog.msgbox("No Endpoints", "No webhook endpoints configured.")
            return
        choices = []
        for ep in endpoints:
            name = ep.get('name', 'unnamed')
            state = "ON" if ep.get('enabled', False) else "OFF"
            choices.append((ep['url'], f"[{state}] {name}"))
        selected = self.ctx.dialog.menu("Toggle Webhook", "Select endpoint to toggle:", choices)
        if selected:
            for ep in endpoints:
                if ep['url'] == selected:
                    new_state = not ep.get('enabled', False)
                    manager.update_endpoint(selected, enabled=new_state)
                    state_str = "enabled" if new_state else "disabled"
                    self.ctx.dialog.msgbox("Updated", f"Webhook {state_str}.")
                    break

    def _webhooks_test(self):
        clear_screen()
        print("=== Test Webhook Delivery ===\n")
        if not _HAS_WEBHOOKS:
            print("  Webhooks module not available.")
            self.ctx.wait_for_enter()
            return
        manager = get_webhook_manager()
        endpoints = manager.list_endpoints()
        enabled = [ep for ep in endpoints if ep.get('enabled', False)]
        if not enabled:
            print("  No enabled webhook endpoints.")
            print("  Add and enable an endpoint first.")
            self.ctx.wait_for_enter()
            return
        print(f"  Sending test event to {len(enabled)} endpoint(s)...\n")
        manager.emit(EventType.CUSTOM, {"test": True, "message": "MeshForge webhook test event", "source": "tui_test"})
        for ep in enabled:
            print(f"  -> {ep.get('name', '?')}: {ep.get('url', '?')}")
        print("\n  Test event queued for delivery.")
        print("  Check your webhook receiver for the event.")
        print()
        self.ctx.wait_for_enter()

    def _webhooks_event_types(self):
        clear_screen()
        print("=== Supported Webhook Event Types ===\n")
        if not _HAS_WEBHOOKS:
            print("  Webhooks module not available.")
            self.ctx.wait_for_enter()
            return
        descriptions = {
            "node_online": "Node came online or was first seen",
            "node_offline": "Node went offline or unreachable",
            "message_received": "Text message received from mesh",
            "position_update": "GPS position update from a node",
            "telemetry_update": "Telemetry data (battery, voltage, etc.)",
            "alert_battery_low": "Node battery below threshold",
            "alert_signal_poor": "Link quality degraded",
            "alert_node_unreachable": "Node stopped responding",
            "gateway_status": "Gateway bridge state change",
            "service_status": "Service started/stopped/failed",
            "custom": "Custom user-defined events",
        }
        for evt in EventType:
            desc = descriptions.get(evt.value, "")
            print(f"  {evt.value:<28} {desc}")
        print(f"\n  Total: {len(list(EventType))} event types")
        print("\n  Filter endpoints to specific events when adding,")
        print("  or leave empty to receive all events.")
        print()
        self.ctx.wait_for_enter()
