"""NomadNet per-identity submenus (Default vs Interactive).

Extracted from nomadnet.py to keep the main handler file under the
1,500-line cap (CLAUDE.md Issue #6). Each submenu is a `dialog.menu`
loop that dispatches to existing per-identity-scoped methods on
NomadNetHandler. The pattern mirrors rns_tools.py.

Expected host class methods (via MRO):
    self.ctx                       — TUIContext
    self._is_nomadnet_running()    — bool (global)
    self._launch_nomadnet_textui() — default-identity TUI
    self._launch_nomadnet_daemon() — default-identity daemon
    self._launch_nomadnet_interactive() — interactive-identity TUI
    self._stop_nomadnet(config_dir=Path) — identity-scoped stop
    self._view_nomadnet_logs_for(config_dir) — identity-scoped logs
    self._edit_nomadnet_config_for(config_dir) — identity-scoped editor
    self._interactive_config_dir() -> Path
    self._print_interactive_hash(config_dir) — rnid -H lxmf.delivery
"""

import logging
from pathlib import Path

from backend import clear_screen
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


class NomadNetSubmenusMixin:
    """Submenu dispatchers for NomadNet Client → Default / Interactive."""

    # ------------------------------------------------------------------
    # Default Identity submenu
    # ------------------------------------------------------------------

    def _default_identity_config_dir(self) -> Path:
        return get_real_user_home() / ".nomadnetwork"

    def _default_identity_menu(self):
        """Default NomadNet identity lifecycle (~/.nomadnetwork).

        Shows Start (TUI or Daemon) when stopped, Stop when running,
        View Logs + Edit Config always.
        """
        config_dir = self._default_identity_config_dir()

        while True:
            # Running-state is global-ish for the default identity: any
            # nomadnet process without --config is using the default dir.
            running = self._is_default_identity_running()

            subtitle = (
                f"Config dir: {config_dir}\n"
                f"Status: {'RUNNING' if running else 'stopped'}"
            )

            choices = []
            if running:
                choices.append(("stop", "Stop"))
            else:
                choices.append(("textui", "Launch Text UI"))
                choices.append(("daemon", "Start Daemon"))
            choices.append(("logs", "View Logs"))
            choices.append(("edit", "Edit Config"))
            choices.append(("back", "Back"))

            choice = self.ctx.dialog.menu(
                "NomadNet — Default Identity",
                subtitle,
                choices,
            )

            if choice is None or choice == "back":
                return

            terminal_actions = {"textui", "stop"}
            dispatch = {
                "textui": ("Launch Text UI", self._launch_nomadnet_textui),
                "daemon": ("Start Daemon", self._launch_nomadnet_daemon),
                "stop": ("Stop (default identity)",
                         lambda: self._stop_nomadnet(config_dir=config_dir)),
                "logs": ("View Logs",
                         lambda: self._view_nomadnet_logs_for(config_dir)),
                "edit": ("Edit Config",
                         lambda: self._edit_nomadnet_config_for(config_dir)),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)
                if choice in terminal_actions:
                    return

    # ------------------------------------------------------------------
    # Interactive Client submenu
    # ------------------------------------------------------------------

    def _interactive_client_menu(self):
        """Interactive NomadNet identity lifecycle (~/.nomadnetwork-interactive).

        Separate LXMF identity, coexists with a running daemon.
        """
        config_dir = self._interactive_config_dir()

        while True:
            running = self._is_interactive_identity_running()

            subtitle = (
                f"Config dir: {config_dir}\n"
                f"Status: {'RUNNING' if running else 'stopped'}"
            )

            choices = []
            if running:
                choices.append(("stop", "Stop"))
            else:
                choices.append(("start", "Start"))
            choices.append(("logs", "View Logs"))
            choices.append(("edit", "Edit Config"))
            choices.append(("hash", "Show LXMF Hash"))
            choices.append(("back", "Back"))

            choice = self.ctx.dialog.menu(
                "NomadNet — Interactive Client",
                subtitle,
                choices,
            )

            if choice is None or choice == "back":
                return

            terminal_actions = {"start", "stop"}
            dispatch = {
                "start": ("Start Interactive Client",
                          self._launch_nomadnet_interactive),
                "stop": ("Stop Interactive Client",
                         lambda: self._stop_nomadnet(config_dir=config_dir)),
                "logs": ("View Logs",
                         lambda: self._view_nomadnet_logs_for(config_dir)),
                "edit": ("Edit Config",
                         lambda: self._edit_nomadnet_config_for(config_dir)),
                "hash": ("Show LXMF Hash",
                         lambda: self._show_interactive_hash_dialog(config_dir)),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)
                if choice in terminal_actions:
                    return

    # ------------------------------------------------------------------
    # Identity-scoped running detection (proc-walk based)
    # ------------------------------------------------------------------

    def _is_default_identity_running(self) -> bool:
        """True iff a nomadnet process is using the default config dir."""
        from handlers._lxmf_utils import find_competing_clients
        default = str(self._default_identity_config_dir())
        return bool(find_competing_clients(default))

    def _is_interactive_identity_running(self) -> bool:
        """True iff a nomadnet process is using the interactive config dir."""
        from handlers._lxmf_utils import find_competing_clients
        return bool(find_competing_clients(str(self._interactive_config_dir())))

    # ------------------------------------------------------------------
    # Show LXMF hash for interactive identity (out-of-launch)
    # ------------------------------------------------------------------

    def _show_interactive_hash_dialog(self, config_dir: Path):
        """Render the interactive identity's LXMF hash after the client has
        created one. Useful when the operator wants to copy the hash
        without re-launching.
        """
        identity = config_dir / "storage" / "identity"
        if not identity.exists():
            self.ctx.dialog.msgbox(
                "No Identity Yet",
                f"No identity file at:\n  {identity}\n\n"
                f"Start the Interactive Client once — NomadNet creates the\n"
                f"identity on first run.",
            )
            return
        clear_screen()
        print(f"=== Interactive LXMF Hash ===\n")
        print(f"Identity: {identity}\n")
        # _print_interactive_hash already prints formatted hash and label.
        self._print_interactive_hash(config_dir)
        print()
        self.ctx.wait_for_enter()
