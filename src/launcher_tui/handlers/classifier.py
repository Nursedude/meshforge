"""
Classifier Handler — Traffic classification, routing decisions, notification priority.

Converted from classifier_mixin.py as part of the mixin-to-registry migration.
"""

import logging

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

create_routing_system, create_notification_system, _HAS_CLASSIFIER = safe_import(
    'utils.classifier', 'create_routing_system', 'create_notification_system'
)


class ClassifierHandler(BaseHandler):
    """TUI handler for traffic classification display methods."""

    handler_id = "classifier"
    menu_section = "mesh_networks"

    def menu_items(self):
        return [
            ("traffic", "Traffic Classifier  Routing & notification stats", None),
        ]

    def execute(self, action):
        if action == "traffic":
            self._classifier_menu()

    def _classifier_menu(self):
        """Traffic Classification — routing decisions, notifications, audit."""
        while True:
            choices = [
                ("routing", "Routing Stats       Bridge routing decisions"),
                ("notify", "Notification Stats  Event priority breakdown"),
                ("receipts", "Recent Decisions    Last 15 classification receipts"),
                ("bounced", "Bounced Items       Low-confidence items for review"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Traffic Classification",
                "Message routing and event classification:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "routing": ("Routing Stats", self._show_routing_stats),
                "notify": ("Notification Stats", self._show_notification_stats),
                "receipts": ("Recent Decisions", self._show_recent_receipts),
                "bounced": ("Bounced Items", self._show_bounced_items),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _show_routing_stats(self):
        """Show routing classifier statistics."""
        clear_screen()
        print("=== Routing Classification Stats ===\n")

        if not _HAS_CLASSIFIER:
            print("  Classifier module not available.")
            print("  File: src/utils/classifier.py")
            self.ctx.wait_for_enter()
            return

        router = create_routing_system()
        stats = router.get_stats()

        total = stats.get('total', 0)
        if total == 0:
            print("  No routing decisions recorded yet.")
            print("  Data appears when messages are classified for bridging.")
            self.ctx.wait_for_enter()
            return

        print(f"  Total decisions:   {total}")
        print(f"  Avg confidence:    {stats.get('avg_confidence', 0):.1%}")
        print(f"  Bounced:           {stats.get('bounced', 0)}")
        print(f"  Corrected:         {stats.get('corrected', 0)}")

        categories = stats.get('categories', {})
        if categories:
            print(f"\n  {'Category':<22} {'Count':>6}")
            print(f"  {'-'*30}")
            for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
                print(f"  {cat:<22} {count:>6}")

        print()
        self.ctx.wait_for_enter()

    def _show_notification_stats(self):
        """Show notification classifier statistics."""
        clear_screen()
        print("=== Notification Classification Stats ===\n")

        if not _HAS_CLASSIFIER:
            print("  Classifier module not available.")
            self.ctx.wait_for_enter()
            return

        notifier = create_notification_system()
        stats = notifier.get_stats()

        total = stats.get('total', 0)
        if total == 0:
            print("  No notification events classified yet.")
            print("  Events are classified as they occur during operation.")
            self.ctx.wait_for_enter()
            return

        print(f"  Total events:      {total}")
        print(f"  Avg confidence:    {stats.get('avg_confidence', 0):.1%}")
        print(f"  Bounced:           {stats.get('bounced', 0)}")

        categories = stats.get('categories', {})
        if categories:
            severity_colors = {
                'critical': "\033[1;31m",
                'important': "\033[0;33m",
                'info': "\033[0;36m",
                'background': "\033[2m",
            }
            print(f"\n  {'Priority':<16} {'Count':>6}")
            print(f"  {'-'*24}")
            for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
                color = severity_colors.get(cat, "")
                reset = "\033[0m" if color else ""
                print(f"  {color}{cat:<16}{reset} {count:>6}")

        print()
        self.ctx.wait_for_enter()

    def _show_recent_receipts(self):
        """Show recent classification receipts (audit trail)."""
        clear_screen()
        print("=== Recent Classification Decisions ===\n")

        if not _HAS_CLASSIFIER:
            print("  Classifier module not available.")
            self.ctx.wait_for_enter()
            return

        router = create_routing_system()
        receipts = router.get_receipts(limit=15)

        if not receipts:
            print("  No classification receipts yet.")
            print("  Receipts are recorded for each routing decision.")
            self.ctx.wait_for_enter()
            return

        print(f"  {'ID':<14} {'Category':<18} {'Conf':>5} {'Bounced':<8} {'Corrected':<10}")
        print(f"  {'-'*58}")

        for r in receipts:
            bounced = "Yes" if r.bounced else ""
            corrected = "Yes" if r.was_corrected else ""
            input_id = r.input_id[:12] if len(r.input_id) > 12 else r.input_id
            category = r.category[:16] if len(r.category) > 16 else r.category
            print(f"  {input_id:<14} {category:<18} {r.confidence:>4.0%} {bounced:<8} {corrected:<10}")

        if len(receipts) == 15:
            print(f"\n  (showing last 15 receipts)")

        print()
        self.ctx.wait_for_enter()

    def _show_bounced_items(self):
        """Show items bounced due to low confidence."""
        clear_screen()
        print("=== Bounced Items (Low Confidence) ===\n")

        if not _HAS_CLASSIFIER:
            print("  Classifier module not available.")
            self.ctx.wait_for_enter()
            return

        router = create_routing_system()
        bounced = router.bouncer.get_queue() if router.bouncer else []

        if not bounced:
            print("  No bounced items in queue.")
            print("  Items are bounced when classification confidence is too low.")
            self.ctx.wait_for_enter()
            return

        print(f"  {len(bounced)} item(s) awaiting review:\n")
        print(f"  {'ID':<14} {'Category':<18} {'Conf':>5} {'Reason'}")
        print(f"  {'-'*60}")

        for item in bounced:
            input_id = item.input_id[:12] if len(item.input_id) > 12 else item.input_id
            category = item.category[:16] if len(item.category) > 16 else item.category
            reason = item.bounce_reason[:30] if item.bounce_reason else ""
            print(f"  {input_id:<14} {category:<18} {item.confidence:>4.0%} {reason}")

        print()
        self.ctx.wait_for_enter()
