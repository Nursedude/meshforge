"""Pre-launch user-mismatch and Meshtastic-interface checks for NomadNet.

Extracted from ``_nomadnet_rns_checks.py`` to keep that file under the
300-line cap enforced by ``tests/test_regression_guards.py::
TestNomadNetPrelaunchContract::test_prelaunch_file_size``.

This mixin provides three methods composed onto ``NomadNetHandler``:

    _check_rnsd_user_match()           — warn when rnsd/NomadNet UIDs differ
    _check_mesh_iface_before_launch()  — surface blocking Meshtastic ifaces
    _mesh_iface_subtitle_state()       — short Meshtastic iface summary for
                                         the NomadNet Client menu subtitle

The first two are wired into ``_check_rns_for_nomadnet`` so every launch
path (textui / daemon / interactive) inherits them. The subtitle helper
is called from the top-level menu loop in ``nomadnet.py``.

Expected host class methods (via MRO):
    self.ctx                        — TUIContext
    self._get_rnsd_user()           — Optional[str]
    self._get_rns_diagnostics_handler() — handler or None
"""

import logging
import os

logger = logging.getLogger(__name__)


class NomadNetIfaceChecksMixin:
    """User-match and Meshtastic-interface pre-launch checks."""

    # Case-insensitive match for Meshtastic_Interface entries.
    _MESHTASTIC_IFACE_TOKEN = "meshtastic"

    # ------------------------------------------------------------------
    # RNS user-mismatch detection
    # ------------------------------------------------------------------

    def _check_rnsd_user_match(self) -> bool:
        """Warn the operator when rnsd and NomadNet will run as different users.

        rnsd typically runs as root via systemd; NomadNet launches as the
        real user (SUDO_USER). Divergent UIDs can trip RNS RPC auth and
        surface as ConnectionRefusedError with no obvious cause. We detect
        the mismatch and offer a handoff to RNS Diagnostics, but never
        block the launch — the operator may know what they're doing.

        Returns True to proceed, False only if the user explicitly cancels.
        """
        rnsd_user = self._get_rnsd_user()
        if not rnsd_user:
            logger.info("rnsd not running — skipping user-match check")
            return True

        nomadnet_user = (os.environ.get('SUDO_USER')
                         or os.environ.get('USER')
                         or 'root')

        if rnsd_user == nomadnet_user:
            logger.info(
                "rnsd_user=%s nomadnet_user=%s — match",
                rnsd_user, nomadnet_user,
            )
            return True

        logger.warning(
            "rnsd_user=%s nomadnet_user=%s — mismatch warned",
            rnsd_user, nomadnet_user,
        )
        choice = self.ctx.dialog.menu(
            "RNS User Mismatch",
            f"rnsd is running as '{rnsd_user}', but NomadNet will run\n"
            f"as '{nomadnet_user}'. RNS RPC auth may reject the client\n"
            f"(ConnectionRefusedError / AuthenticationError).\n\n"
            f"Open RNS Diagnostics to align users?",
            [
                ("diagnostics", "Open RNS Diagnostics (recommended)"),
                ("continue", "Launch anyway"),
                ("cancel", "Cancel"),
            ],
        )

        if choice == "diagnostics":
            diag = self._get_rns_diagnostics_handler()
            if diag:
                try:
                    diag._rns_diagnostics()
                except Exception as e:
                    logger.warning("RNS diagnostics failed: %s", e)
            else:
                self.ctx.dialog.msgbox(
                    "Not Available",
                    "RNS Diagnostics handler not found.\n\n"
                    "Use Service Control > Repair RNS alignment to align the\n"
                    f"rnsd user to {nomadnet_user} in-app.",
                )
            return False

        return choice == "continue"

    # ------------------------------------------------------------------
    # Meshtastic RNS-interface pre-launch probe
    # ------------------------------------------------------------------

    def _meshtastic_blockers(self):
        """Return blocking-interface entries whose name/reason mentions Meshtastic."""
        try:
            from handlers._rns_interface_mgr import find_blocking_interfaces
        except ImportError as e:
            logger.debug("Cannot import find_blocking_interfaces: %s", e)
            return []
        try:
            blocking = find_blocking_interfaces()
        except Exception as e:
            logger.debug("find_blocking_interfaces failed: %s", e)
            return []
        return [
            entry for entry in blocking
            if self._MESHTASTIC_IFACE_TOKEN in (entry[0] or '').lower()
            or self._MESHTASTIC_IFACE_TOKEN in (entry[1] or '').lower()
        ]

    def _check_mesh_iface_before_launch(self) -> bool:
        """Surface Meshtastic RNS-interface problems before launching NomadNet.

        Uses the existing ``find_blocking_interfaces`` helper (already
        Meshtastic-aware) and filters its output to Meshtastic entries.
        Non-blocking health issues (RX-only, zero TX) are logged but
        don't gate the launch.

        Returns True to proceed, False only on explicit user cancel.
        """
        mesh_blockers = self._meshtastic_blockers()
        if mesh_blockers:
            name, reason, fix = mesh_blockers[0]
            logger.warning(
                "Meshtastic iface '%s' blocked: %s (fix: %s)",
                name, reason, fix,
            )
            choice = self.ctx.dialog.menu(
                "Meshtastic Interface Blocked",
                f"The RNS Meshtastic_Interface is enabled but unreachable:\n\n"
                f"  {name}\n"
                f"  {reason}\n\n"
                f"Suggested fix:\n  {fix}\n\n"
                f"NomadNet will launch, but RNS traffic over the mesh\n"
                f"will not flow until this is resolved.",
                [
                    ("diagnostics", "Open RNS Diagnostics (recommended)"),
                    ("continue", "Launch anyway"),
                    ("cancel", "Cancel"),
                ],
            )
            if choice == "diagnostics":
                diag = self._get_rns_diagnostics_handler()
                if diag:
                    try:
                        diag._rns_diagnostics()
                    except Exception as e:
                        logger.warning("RNS diagnostics failed: %s", e)
                return False
            return choice == "continue"

        # No blocker — check live health for a soft-warn on RX-only.
        try:
            from handlers._rns_diagnostics_engine import check_rns_interface_health
            health = check_rns_interface_health() or []
        except Exception as e:
            logger.debug("check_rns_interface_health failed: %s", e)
            health = []
        for entry in health:
            name = entry[0] if entry else ''
            healthy = entry[3] if len(entry) > 3 else True
            if (self._MESHTASTIC_IFACE_TOKEN in name.lower()
                    and not healthy):
                logger.warning(
                    "Meshtastic iface '%s' RX-only or no TX — "
                    "link establishment may be failing",
                    name,
                )
                break
        return True

    # ------------------------------------------------------------------
    # Subtitle helper for the top-level NomadNet Client menu
    # ------------------------------------------------------------------

    def _mesh_iface_subtitle_state(self) -> str:
        """One-line Meshtastic RNS interface state for the NomadNet menu subtitle.

        Returns an empty string when the state isn't informative (no RNS
        config, or rnsd entirely offline — callers already surface that).
        """
        try:
            from utils.paths import ReticulumPaths
            config_file = ReticulumPaths.get_config_file()
            if not config_file.exists():
                return ""
            content = config_file.read_text()
        except (OSError, PermissionError) as e:
            logger.debug("Cannot read RNS config for subtitle: %s", e)
            return ""

        import re
        has_mesh_iface = bool(re.search(
            r'^\s*type\s*=\s*Meshtastic_Interface',
            content, re.IGNORECASE | re.MULTILINE,
        ))
        if not has_mesh_iface:
            return "Meshtastic iface: not configured"

        mesh_blockers = self._meshtastic_blockers()
        if mesh_blockers:
            reason = mesh_blockers[0][1]
            short = reason[:50] + ("…" if len(reason) > 50 else "")
            return f"Meshtastic iface: BLOCKED ({short})"

        try:
            from handlers._rns_diagnostics_engine import check_rns_interface_health
            health = check_rns_interface_health() or []
        except Exception:
            health = []

        mesh_entries = [
            e for e in health
            if self._MESHTASTIC_IFACE_TOKEN in (e[0] or '').lower()
        ]
        if not mesh_entries:
            return "Meshtastic iface: DOWN"

        any_healthy = any(len(e) > 3 and e[3] for e in mesh_entries)
        return "Meshtastic iface: UP" if any_healthy else "Meshtastic iface: RX-only"
