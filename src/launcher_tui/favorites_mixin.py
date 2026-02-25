"""
Favorites Mixin for MeshForge Launcher TUI.

Provides Meshtastic favorites management (BaseUI 2.7+):
- View favorites list
- Toggle favorite status on nodes
- Filter node views by favorites
- Sync favorites with device
"""

import logging
from typing import Optional, List, Tuple

from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# Connection manager for meshtasticd (respects single-client constraint)
MeshtasticConnection, _HAS_CONN_MGR = safe_import(
    'utils.connection_manager', 'MeshtasticConnection'
)
# Check if meshtastic TCP is available (for capability gating)
_TCPInterface, _HAS_TCP_INTERFACE = safe_import('meshtastic.tcp_interface', 'TCPInterface')


class FavoritesMixin:
    """Mixin providing favorites management for the TUI launcher."""

    def _favorites_menu(self):
        """Favorites management menu."""
        choices = [
            ("list", "View Favorites List"),
            ("all_nodes", "All Nodes (toggle favorites)"),
            ("toggle", "Toggle Favorite by ID"),
            ("sync", "Sync Favorites from Device"),
            ("back", "Back"),
        ]

        while True:
            # Get current favorites count
            fav_count = self._get_favorites_count()
            title = f"Favorites ({fav_count})" if fav_count else "Favorites"

            choice = self.dialog.menu(
                title,
                "Manage node favorites (BaseUI 2.7+):",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "list": ("View Favorites", self._show_favorites_list),
                "all_nodes": ("All Nodes", self._show_all_nodes_with_favorites),
                "toggle": ("Toggle Favorite", self._toggle_favorite_by_id),
                "sync": ("Sync Favorites", self._sync_favorites_from_device),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _get_favorites_count(self) -> int:
        """Get count of favorite nodes."""
        try:
            from gateway.node_tracker import UnifiedNodeTracker
            tracker = self._get_node_tracker()
            if not tracker:
                return 0
            nodes = tracker.get_meshtastic_nodes()
            return sum(1 for n in nodes if getattr(n, 'is_favorite', False))
        except Exception as e:
            logger.debug("Favorites count failed: %s", e)
            return 0

    def _get_node_tracker(self):
        """Get the node tracker instance if available."""
        try:
            # Try the singleton pattern first
            from gateway.node_tracker import get_node_tracker
            return get_node_tracker()
        except (ImportError, AttributeError):
            pass
        # Fallback - check if we have it as an attribute
        if hasattr(self, '_node_tracker'):
            return self._node_tracker
        return None

    def _show_favorites_list(self):
        """Display list of favorite nodes."""
        try:
            tracker = self._get_node_tracker()
            if not tracker:
                self.dialog.msgbox(
                    "Unavailable",
                    "Node tracker not available.\n\n"
                    "Start the gateway or MQTT monitor first."
                )
                return

            nodes = tracker.get_meshtastic_nodes()
            favorites = [n for n in nodes if getattr(n, 'is_favorite', False)]

            if not favorites:
                self.dialog.msgbox(
                    "No Favorites",
                    "No favorite nodes found.\n\n"
                    "Use 'All Nodes' to mark nodes as favorites,\n"
                    "or use the Meshtastic app to set favorites."
                )
                return

            # Sort by name
            favorites.sort(key=lambda n: n.name.lower() if n.name else "zzz")

            # Build menu choices
            node_choices = []
            for node in favorites[:50]:  # Limit for TUI
                name = node.name or node.short_name or node.meshtastic_id or "Unknown"
                name = name[:25]  # Truncate long names
                status = "Online" if node.is_online else "Offline"
                mesh_id = node.meshtastic_id or ""

                # Add description with status
                desc = f"[*] {status}"
                if node.snr is not None:
                    desc += f" | SNR: {node.snr:.1f}dB"

                node_choices.append((mesh_id or node.id, f"{name} {desc}"))

            node_choices.append(("back", "Back"))

            selected = self.dialog.menu(
                f"Favorites ({len(favorites)})",
                "Select a node to view details or toggle favorite:",
                node_choices
            )

            if selected and selected != "back":
                self._show_favorite_node_details(selected)

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to show favorites:\n{e}")

    def _show_all_nodes_with_favorites(self):
        """Show all Meshtastic nodes with favorites toggle."""
        # Use loop instead of recursion to prevent stack overflow
        while True:
            try:
                tracker = self._get_node_tracker()
                if not tracker:
                    self.dialog.msgbox(
                        "Unavailable",
                        "Node tracker not available.\n\n"
                        "Start the gateway or MQTT monitor first."
                    )
                    return

                nodes = tracker.get_meshtastic_nodes()

                if not nodes:
                    self.dialog.msgbox(
                        "No Nodes",
                        "No Meshtastic nodes discovered yet.\n\n"
                        "Connect to meshtasticd or start MQTT monitoring\n"
                        "to discover nodes."
                    )
                    return

                # Sort: favorites first, then by name
                nodes.sort(key=lambda n: (
                    0 if getattr(n, 'is_favorite', False) else 1,
                    n.name.lower() if n.name else "zzz"
                ))

                # Build menu choices
                node_choices = []
                for node in nodes[:75]:  # Limit for TUI
                    is_fav = getattr(node, 'is_favorite', False)
                    star = "[*]" if is_fav else "[ ]"

                    name = node.name or node.short_name or "Unknown"
                    name = name[:20]  # Truncate
                    mesh_id = node.meshtastic_id or node.id

                    status = "+" if node.is_online else "-"

                    node_choices.append((
                        mesh_id,
                        f"{star} {name} ({mesh_id[-8:]}) {status}"
                    ))

                node_choices.append(("back", "Back"))

                selected = self.dialog.menu(
                    f"All Nodes ({len(nodes)})",
                    "[*] = favorite | Select to toggle:",
                    node_choices
                )

                if selected and selected != "back":
                    self._toggle_favorite_on_node(selected)
                    continue  # Loop back to refresh list
                return  # Exit on "back" or cancel

            except Exception as e:
                self.dialog.msgbox("Error", f"Failed to show nodes:\n{e}")
                return

    def _show_favorite_node_details(self, node_id: str):
        """Show details for a favorite node with option to remove."""
        try:
            tracker = self._get_node_tracker()
            if not tracker:
                return

            # Find node by mesh ID or unified ID
            node = None
            for n in tracker.get_meshtastic_nodes():
                if n.meshtastic_id == node_id or n.id == node_id:
                    node = n
                    break

            if not node:
                self.dialog.msgbox("Not Found", f"Node {node_id} not found.")
                return

            # Build details display
            lines = [
                f"NODE: {node.name or 'Unknown'}",
                "=" * 50,
                "",
                f"  Mesh ID:    {node.meshtastic_id or 'N/A'}",
                f"  Short Name: {node.short_name or 'N/A'}",
                f"  Status:     {'Online' if node.is_online else 'Offline'}",
                f"  Favorite:   Yes [*]",
                "",
            ]

            if node.hardware_model:
                lines.append(f"  Hardware:   {node.hardware_model}")
            if node.role:
                lines.append(f"  Role:       {node.role}")

            # Signal info
            if node.snr is not None or node.rssi is not None:
                lines.append("")
                lines.append("SIGNAL:")
                lines.append("-" * 50)
                if node.snr is not None:
                    lines.append(f"  SNR:  {node.snr:.1f} dB")
                if node.rssi is not None:
                    lines.append(f"  RSSI: {node.rssi} dBm")
                if node.hops is not None:
                    lines.append(f"  Hops: {node.hops}")

            # Last seen
            if node.last_seen:
                lines.append("")
                lines.append(f"Last Seen: {node.get_age_string()}")

            # Ask if they want to remove favorite
            lines.append("")
            lines.append("-" * 50)
            lines.append("Remove from favorites?")

            result = self.dialog.yesno(
                "Favorite Node Details",
                "\n".join(lines)
            )

            if result:  # Yes - remove favorite
                self._toggle_favorite_on_node(node.meshtastic_id or node.id, force_remove=True)

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to show details:\n{e}")

    def _toggle_favorite_by_id(self):
        """Toggle favorite status by entering node ID."""
        node_id = self.dialog.inputbox(
            "Toggle Favorite",
            "Enter Meshtastic node ID (e.g., !ba4bf9d0):",
            ""
        )

        if not node_id:
            return

        # Normalize ID format
        if not node_id.startswith("!"):
            node_id = f"!{node_id}"

        self._toggle_favorite_on_node(node_id)

    def _toggle_favorite_on_node(self, node_id: str, force_remove: bool = False):
        """Toggle favorite status on a specific node.

        Args:
            node_id: Meshtastic node ID (e.g., !ba4bf9d0)
            force_remove: If True, only remove (don't toggle to add)
        """
        try:
            # Find current status
            tracker = self._get_node_tracker()
            current_is_favorite = False

            if tracker:
                for n in tracker.get_meshtastic_nodes():
                    if n.meshtastic_id == node_id or n.id == node_id:
                        current_is_favorite = getattr(n, 'is_favorite', False)
                        break

            # Determine action
            if force_remove:
                action = "remove"
            elif current_is_favorite:
                action = "remove"
            else:
                action = "add"

            # Try to update on device
            success = self._set_favorite_on_device(node_id, action == "add")

            if success:
                # Update local tracker
                if tracker:
                    for n in tracker.get_meshtastic_nodes():
                        if n.meshtastic_id == node_id or n.id == node_id:
                            n.is_favorite = (action == "add")
                            from datetime import datetime
                            n.favorite_updated = datetime.now()
                            break

                action_past = "added to" if action == "add" else "removed from"
                self.dialog.msgbox(
                    "Favorite Updated",
                    f"Node {node_id} {action_past} favorites."
                )
            else:
                # Device update failed - show local-only option
                result = self.dialog.yesno(
                    "Device Update Failed",
                    f"Could not update favorite on device.\n\n"
                    f"This may be because:\n"
                    f"- meshtasticd is not running\n"
                    f"- Device is not connected\n"
                    f"- Node is not in device's node database\n\n"
                    f"Update locally only?"
                )
                if result:
                    if tracker:
                        for n in tracker.get_meshtastic_nodes():
                            if n.meshtastic_id == node_id or n.id == node_id:
                                n.is_favorite = (action == "add")
                                break
                    self.dialog.msgbox("Updated", "Local favorite status updated.")

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to toggle favorite:\n{e}")

    def _set_favorite_on_device(self, node_id: str, set_favorite: bool) -> bool:
        """Set or remove favorite status on the Meshtastic device.

        Uses meshtastic CLI (no Python library needed).

        Args:
            node_id: Node ID (e.g., !ba4bf9d0)
            set_favorite: True to set as favorite, False to remove

        Returns:
            True if successful, False otherwise
        """
        import subprocess as sp

        try:
            cli = self._get_meshtastic_cli()
        except Exception:
            logger.warning("meshtastic CLI not found")
            return False

        try:
            action_flag = '--set-favorite' if set_favorite else '--remove-favorite'
            result = sp.run(
                [cli, '--host', 'localhost', action_flag, node_id],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                logger.info(f"{'Set' if set_favorite else 'Removed'} favorite: {node_id}")
                return True
            else:
                logger.warning(f"CLI favorite command failed: {result.stderr}")
                return False
        except (sp.TimeoutExpired, FileNotFoundError) as e:
            logger.warning(f"Failed to update favorite via CLI: {e}")
            return False
        except Exception as e:
            logger.warning(f"Failed to update favorite on device: {e}")
            return False

    def _sync_favorites_from_device(self):
        """Sync favorites from the connected Meshtastic device.

        Note: The isFavorite flag is only available via the TCP/protobuf API.
        The HTTP /json/nodes endpoint does not expose it. Falls back to TCP
        if the meshtastic Python library is installed.
        """
        self.dialog.infobox("Syncing...", "Reading favorites from device...")

        if not _HAS_TCP_INTERFACE:
            self.dialog.msgbox(
                "Favorites Sync Unavailable",
                "The isFavorite flag requires the meshtastic Python library\n"
                "(TCP/protobuf API). The HTTP API does not expose it.\n\n"
                "Install with: pip install meshtastic\n\n"
                "You can still set favorites via the meshtastic CLI."
            )
            return

        try:
            # Use connection manager to respect meshtasticd's single-client
            # TCP constraint (Issue #17). Direct TCPInterface() creation would
            # contend with the gateway bridge and break :9443.
            with MeshtasticConnection() as interface:
                if not interface:
                    self.dialog.msgbox(
                        "Connection Busy",
                        "Another component is using the meshtasticd connection.\n"
                        "Please try again in a moment."
                    )
                    return

                favorites = []
                non_favorites = []

                for node_num, node_info in interface.nodes.items():
                    is_fav = node_info.get('isFavorite', False)
                    user = node_info.get('user', {})
                    name = user.get('longName') or user.get('id', f'!{node_num:08x}')
                    node_id = user.get('id', f'!{node_num:08x}')

                    if is_fav:
                        favorites.append((node_id, name))
                    else:
                        non_favorites.append((node_id, name))

                # Update local tracker
                tracker = self._get_node_tracker()
                if tracker:
                    fav_ids = {f[0] for f in favorites}
                    for n in tracker.get_meshtastic_nodes():
                        if n.meshtastic_id in fav_ids:
                            n.is_favorite = True
                        elif n.meshtastic_id:
                            n.is_favorite = False

                # Show results
                lines = [
                    "FAVORITES SYNC COMPLETE",
                    "=" * 50,
                    "",
                    f"Total nodes in device: {len(interface.nodes)}",
                    f"Favorites: {len(favorites)}",
                    "",
                ]

                if favorites:
                    lines.append("Favorite Nodes:")
                    lines.append("-" * 50)
                    for node_id, name in favorites[:20]:
                        lines.append(f"  [*] {name[:25]} ({node_id})")
                    if len(favorites) > 20:
                        lines.append(f"  ... and {len(favorites) - 20} more")
                else:
                    lines.append("No favorites set on device.")

                self.dialog.msgbox("Sync Complete", "\n".join(lines))

        except ConnectionRefusedError:
            self.dialog.msgbox(
                "Connection Failed",
                "Could not connect to meshtasticd.\n\n"
                "Ensure meshtasticd is running:\n"
                "  sudo systemctl status meshtasticd"
            )
        except Exception as e:
            self.dialog.msgbox("Error", f"Sync failed:\n{e}")

    def _get_favorites_filter(self) -> bool:
        """Get current favorites filter setting.

        Returns:
            True if filtering to favorites only
        """
        return getattr(self, '_favorites_filter_enabled', False)

    def _set_favorites_filter(self, enabled: bool):
        """Set favorites filter setting."""
        self._favorites_filter_enabled = enabled

    def _filter_nodes_by_favorites(self, nodes: List) -> List:
        """Filter node list to only favorites if filter is enabled.

        Args:
            nodes: List of UnifiedNode objects

        Returns:
            Filtered list (or original if filter disabled)
        """
        if not self._get_favorites_filter():
            return nodes
        return [n for n in nodes if getattr(n, 'is_favorite', False)]
