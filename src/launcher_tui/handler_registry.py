"""
Handler Registry — Central dispatch for TUI command handlers.

Manages handler registration, menu-item aggregation, feature-flag
filtering, and action dispatch. Replaces the inline ``dispatch = {}``
dictionaries scattered across MeshForgeLauncher submenu methods.

Phase 0 of the migration: infrastructure only, no existing code changed.

See also:
    handler_protocol.py — TUIContext, CommandHandler, BaseHandler
    handlers/            — Converted handler implementations
"""

import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from handler_protocol import (
    CommandHandler, LifecycleHandler, TUIContext,
    PRIVILEGE_ADMIN, PRIVILEGE_VIEWER,
)

logger = logging.getLogger(__name__)


class HandlerRegistry:
    """Central registry for TUI command handlers.

    Handlers register themselves (or are registered by the launcher).
    Submenu orchestrators call ``get_menu_items(section)`` to build menus
    and ``dispatch(section, tag)`` to execute actions.

    During the migration, submenus try registry dispatch first and fall
    back to legacy mixin methods when no handler is found.
    """

    def __init__(self, ctx: TUIContext):
        self._ctx = ctx
        self._handlers: Dict[str, CommandHandler] = {}
        self._sections: Dict[str, List[CommandHandler]] = defaultdict(list)
        # Tag-to-handler index for O(1) dispatch
        self._tag_index: Dict[str, Dict[str, CommandHandler]] = defaultdict(dict)

    def register(self, handler: CommandHandler) -> None:
        """Register a handler, injecting the shared context.

        Args:
            handler: A CommandHandler instance. Must have a unique handler_id.

        Raises:
            ValueError: If handler_id is already registered.
        """
        hid = handler.handler_id
        if hid in self._handlers:
            raise ValueError(
                f"Handler {hid!r} already registered "
                f"(existing: {type(self._handlers[hid]).__name__}, "
                f"new: {type(handler).__name__})"
            )

        handler.set_context(self._ctx)
        self._handlers[hid] = handler
        self._sections[handler.menu_section].append(handler)

        # Build tag index for this handler
        for tag, _desc, _flag in handler.menu_items():
            if tag in self._tag_index[handler.menu_section]:
                logger.warning(
                    "Duplicate tag %r in section %r — overwriting with %s",
                    tag, handler.menu_section, hid,
                )
            self._tag_index[handler.menu_section][tag] = handler

        logger.debug(
            "Registered handler %s (section=%s, items=%d)",
            hid, handler.menu_section, len(handler.menu_items()),
        )

    def get_handler(self, handler_id: str) -> Optional[CommandHandler]:
        """Look up a handler by its unique ID.

        Returns:
            The handler, or None if not found.
        """
        return self._handlers.get(handler_id)

    @staticmethod
    def _is_admin_item(handler, tag: str) -> bool:
        """Check if a specific menu item requires admin privilege."""
        if hasattr(handler, 'get_item_privilege'):
            return handler.get_item_privilege(tag) == PRIVILEGE_ADMIN
        level = getattr(handler, 'privilege_level', PRIVILEGE_VIEWER)
        return level == PRIVILEGE_ADMIN

    def get_menu_items(self, section: str) -> List[Tuple[str, str]]:
        """Get filtered menu items for a section, respecting feature flags.

        When running in Viewer Mode (no sudo), admin items are annotated
        with ``[sudo]`` in their description.

        Args:
            section: Menu section key (e.g., ``"dashboard"``, ``"rf_sdr"``).

        Returns:
            List of (tag, description) tuples, filtered by feature flags.
        """
        items: List[Tuple[str, str]] = []
        is_admin = self._ctx.is_admin
        for handler in self._sections.get(section, []):
            for tag, desc, flag in handler.menu_items():
                if flag is None or self._ctx.feature_enabled(flag):
                    if not is_admin and self._is_admin_item(handler, tag):
                        desc = desc.rstrip() + "  [sudo]"
                    items.append((tag, desc))
        return items

    def dispatch(self, section: str, tag: str) -> bool:
        """Find and execute the handler for a given section + tag.

        Wraps the handler's ``execute()`` in ``safe_call()`` for
        consistent error handling. Checks privilege before execution
        when running in Viewer Mode.

        Args:
            section: Menu section key.
            tag: The action tag selected by the user.

        Returns:
            True if a handler was found and invoked, False otherwise.
        """
        handler = self._tag_index.get(section, {}).get(tag)
        if handler is None:
            return False

        # Pre-execution privilege check
        if self._is_admin_item(handler, tag):
            if not self._ctx.check_privilege(handler.handler_id, PRIVILEGE_ADMIN):
                return True  # Handled (showed dialog), don't fall through

        self._ctx.safe_call(handler.handler_id, handler.execute, tag)
        return True

    def _build_menu_choices(
        self, section: str, legacy_items: list, ordering: list = None,
    ) -> List[Tuple[str, str]]:
        """Build menu choices by merging registry + legacy items.

        Registry items auto-replace legacy items with the same tag.
        Ordering list controls display order when provided.
        Legacy items whose tags map to admin handlers get ``[sudo]``
        annotation when running in Viewer Mode.

        Args:
            section: Menu section key (e.g., "dashboard", "rf_sdr").
            legacy_items: List of (tag, description) for unconverted items.
            ordering: Optional list of tags defining display order.

        Returns:
            List of (tag, description) tuples with "Back" appended.
        """
        registry_items = self.get_menu_items(section)
        registry_tags = {tag for tag, _ in registry_items}

        # Filter legacy items already handled by registry
        filtered_legacy = [(t, d) for t, d in legacy_items if t not in registry_tags]

        # Annotate legacy items whose tags map to admin handlers
        if not self._ctx.is_admin:
            annotated_legacy = []
            for tag, desc in filtered_legacy:
                handler = self._tag_index.get(section, {}).get(tag)
                if handler and self._is_admin_item(handler, tag):
                    desc = desc.rstrip() + "  [sudo]"
                annotated_legacy.append((tag, desc))
            filtered_legacy = annotated_legacy

        all_map = {tag: desc for tag, desc in registry_items}
        all_map.update({tag: desc for tag, desc in filtered_legacy})

        if ordering:
            result = [(t, all_map[t]) for t in ordering if t in all_map]
            # Append items not in ordering
            ordered_set = set(ordering)
            for tag, desc in list(registry_items) + filtered_legacy:
                if tag not in ordered_set and (tag, desc) not in result:
                    result.append((tag, desc))
        else:
            result = list(registry_items) + filtered_legacy

        result.append(("back", "Back"))
        return result

    def run_section_menu(
        self,
        section: str,
        title: str,
        subtitle: str,
        legacy_items,
        ordering: list,
        cross_dispatch: Optional[Dict[str, Tuple[str, str]]] = None,
    ) -> None:
        """Run a standard section submenu loop.

        Builds menu choices by merging registry items with legacy items,
        displays via dialog, and dispatches to handlers. Eliminates the
        repetitive while/menu/break/dispatch boilerplate in main.py.

        Args:
            section: Registry section key (e.g., "dashboard").
            title: Dialog title.
            subtitle: Dialog subtitle.
            legacy_items: Static list of (tag, desc) or callable returning same.
                When callable, it is invoked each iteration (for dynamic/
                feature-gated items).
            ordering: Tag ordering list for display order.
            cross_dispatch: Optional dict mapping tags to (section, tag) tuples
                for cross-section dispatch fallback.
        """
        while True:
            legacy = legacy_items() if callable(legacy_items) else legacy_items
            choices = self._build_menu_choices(section, legacy, ordering)

            choice = self._ctx.dialog.menu(title, subtitle, choices)

            if choice is None or choice == "back":
                break

            if self.dispatch(section, choice):
                continue

            # Cross-section fallback
            if cross_dispatch and choice in cross_dispatch:
                target_section, target_tag = cross_dispatch[choice]
                self.dispatch(target_section, target_tag)

    # --- Section submenu configurations ---
    # Each entry defines the parameters for run_section_menu().
    # Legacy items are auto-replaced as handlers take over their tags.

    _SECTION_MENUS = {
        "dashboard": {
            "title": "Dashboard",
            "subtitle": "System status and monitoring:",
            "ordering": ["status", "weather", "network", "nodes", "health",
                         "score", "datapath", "metrics", "analytics",
                         "latency", "reports", "alerts"],
            "legacy": [
                ("network", "Network Status      Ports, interfaces, conflicts"),
                ("health", "Node Health         Battery, signal, latency"),
                ("metrics", "Historical Trends   Metrics over time"),
            ],
            "cross_dispatch": {"network": ("system", "network")},
        },
        "mesh_networks": {
            "title": "Mesh Networks",
            "subtitle": "Manage mesh network connections:",
            "ordering": ["meshtastic", "meshcore", "rns", "gateway", "aredn",
                         "messaging", "traffic", "mqtt", "favorites", "ham",
                         "services", "nomadnet", "meshchat"],
            "legacy": "_build_mesh_networks_legacy",
        },
        "rf_sdr": {
            "title": "RF & SDR Tools",
            "subtitle": "Radio frequency tools and monitoring:",
            "ordering": ["link", "site", "freq", "antenna", "weather", "sdr"],
            "legacy": [],
        },
        "maps_viz": {
            "title": "Maps & Visualization",
            "subtitle": "Network visualization tools:",
            "ordering": ["livemap", "coverage", "heatmap", "tiles", "topology",
                         "traffic", "quality", "export", "ai"],
            "legacy": [
                ("quality", "Link Quality        Quality analysis"),
            ],
        },
        "configuration": {
            "title": "Configuration",
            "subtitle": "System and service configuration:",
            "ordering": ["radio", "channels", "rns-config", "rnode", "backup",
                         "updates", "webhooks", "meshforge", "config-api",
                         "wizard"],
            "legacy": [
                ("radio", "Radio Config        meshtasticd settings"),
                ("channels", "Channel Config      Meshtastic channels"),
                ("rns-config", "RNS Config          Reticulum settings"),
                ("backup", "Device Backup       Backup/restore configs"),
                ("updates", "Software Updates    One-click updates"),
                ("webhooks", "Webhooks            External notifications"),
                ("meshforge", "MeshForge Settings  App preferences"),
                ("config-api", "Config API Server   REST config endpoint"),
            ],
            "cross_dispatch": {"rns-config": ("rns", "edit")},
        },
        "system": {
            "title": "System Tools",
            "subtitle": "System administration:",
            "ordering": ["hardware", "logs", "network", "discover", "diagnose",
                         "daemon", "review", "status", "shell", "reboot"],
            "legacy": [
                ("hardware", "Hardware            Detect SPI/I2C/USB"),
                ("logs", "Logs                View/follow logs"),
                ("network", "Network Tools       Ping, ports, interfaces"),
                ("diagnose", "Diagnostics         System health check"),
                ("daemon", "Daemon Mode         Start/stop headless NOC"),
                ("status", "Quick Status        One-shot status display"),
                ("reboot", "Reboot/Shutdown     Safe system control"),
            ],
        },
        "about": {
            "title": "About MeshForge",
            "subtitle": "Information, help, and diagnostics:",
            "ordering": ["version", "changelog", "sysinfo", "deps", "web",
                         "help"],
            "legacy": [
                ("version", "Version Info        MeshForge version"),
                ("changelog", "Changelog           Release history"),
                ("sysinfo", "System Info         OS, Python, disk, uptime"),
                ("deps", "Dependencies        Package status"),
                ("help", "Help                Documentation"),
            ],
        },
    }

    _MAIN_MENU_DISPATCH = {
        "1": "dashboard",
        "2": "mesh_networks",
        "3": "rf_sdr",
        "4": "maps_viz",
        "5": "configuration",
        "6": "system",
        "a": "about",
    }

    def _build_mesh_networks_legacy(self):
        """Build dynamic legacy items for mesh networks (feature-gated)."""
        legacy = []
        if self._ctx.feature_enabled("meshtastic"):
            legacy.append(("meshtastic", "Meshtastic          Radio, channels, CLI"))
        if self._ctx.feature_enabled("meshcore"):
            legacy.append(("meshcore", "MeshCore            Companion radio, config"))
        if self._ctx.feature_enabled("rns"):
            legacy.append(("rns", "RNS / Reticulum     Status, gateway, messaging"))
        if self._ctx.feature_enabled("gateway"):
            legacy.append(("gateway", "Gateway Bridge      RNS-Meshtastic-MeshCore"))
        legacy.append(("aredn", "AREDN Mesh          AREDN integration"))
        legacy.append(("messaging", "Messaging           Send/receive messages"))
        legacy.append(("favorites", "Favorites           Manage favorite nodes"))
        return legacy

    def handle_main_choice(self, choice: str) -> None:
        """Handle main menu selection — dispatch or open section submenu.

        Routes to registry handlers first, then to section submenus
        via run_section_menu() with config from _SECTION_MENUS.
        """
        # Try registry-based dispatch for main-menu handlers
        if self.dispatch("main", choice):
            return

        section = self._MAIN_MENU_DISPATCH.get(choice)
        if section:
            cfg = self._SECTION_MENUS[section]
            legacy = cfg["legacy"]
            # String value = method name for dynamic legacy (feature-gated)
            if isinstance(legacy, str):
                legacy = getattr(self, legacy)
            self._ctx.safe_call(
                cfg["title"],
                self.run_section_menu,
                section, cfg["title"], cfg["subtitle"],
                legacy, cfg["ordering"], cfg.get("cross_dispatch"),
            )

    def startup_all(self) -> None:
        """Call ``on_startup()`` on all handlers that implement LifecycleHandler."""
        for handler in self._handlers.values():
            if isinstance(handler, LifecycleHandler):
                try:
                    handler.on_startup()
                except Exception as e:
                    logger.warning(
                        "Startup hook failed for %s: %s",
                        handler.handler_id, e,
                    )

    def shutdown_all(self) -> None:
        """Call ``on_shutdown()`` on all handlers that implement LifecycleHandler."""
        for handler in self._handlers.values():
            if isinstance(handler, LifecycleHandler):
                try:
                    handler.on_shutdown()
                except Exception as e:
                    logger.warning(
                        "Shutdown hook failed for %s: %s",
                        handler.handler_id, e,
                    )

    @property
    def handler_count(self) -> int:
        """Number of registered handlers."""
        return len(self._handlers)

    @property
    def section_names(self) -> List[str]:
        """List of sections that have at least one handler."""
        return list(self._sections.keys())

    def __repr__(self) -> str:
        return (
            f"HandlerRegistry(handlers={len(self._handlers)}, "
            f"sections={list(self._sections.keys())})"
        )
