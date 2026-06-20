"""
RNS Interfaces Handler — RNS network interface CRUD management.

Converted from rns_interfaces_mixin.py as part of the mixin-to-registry migration.
"""

import os
import re
import logging
import shutil
import subprocess
from pathlib import Path

from handler_protocol import BaseHandler
from backend import clear_screen
from service_remediation import offer_service_fix
from commands import rns as rns_mod
from utils.paths import get_real_user_home, ReticulumPaths

logger = logging.getLogger(__name__)


class RNSInterfacesHandler(BaseHandler):
    """TUI handler for RNS interface management."""

    handler_id = "rns_interfaces"
    menu_section = "rns"

    def menu_items(self):
        return [
            ("ifaces", "Manage Interfaces", None),
        ]

    def execute(self, action):
        if action == "ifaces":
            self._rns_interfaces_menu()

    # ------------------------------------------------------------------
    # Top-level submenu
    # ------------------------------------------------------------------

    def _rns_interfaces_menu(self):
        """Manage RNS interfaces (add / remove / enable / disable)."""
        while True:
            choices = [
                ("status", "Interface Status (live)"),
                ("list", "List Configured Interfaces"),
                ("add", "Add Interface from Template"),
                ("enable", "Enable Interface"),
                ("disable", "Disable Interface"),
                ("remove", "Remove Interface"),
                ("fix_ownership", "Fix RNS File Ownership"),
                ("plugin", "Install Meshtastic Plugin"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "RNS Interfaces",
                "Manage Reticulum network interfaces:",
                choices,
            )

            if choice is None or choice == "back":
                break

            if choice == "plugin":
                # Cross-handler call to config handler for plugin install
                config_handler = self.ctx.registry.get_handler("rns_config") if self.ctx.registry else None
                if config_handler:
                    self.ctx.safe_call("Install Meshtastic Plugin", config_handler._install_meshtastic_interface_plugin)
                else:
                    self.ctx.dialog.msgbox("Error", "RNS config handler not available.")
                continue

            dispatch = {
                "status": ("Interface Status", self._rns_interface_status),
                "list": ("List Interfaces", self._rns_list_interfaces),
                "add": ("Add Interface", self._rns_add_interface),
                "enable": ("Enable Interface", lambda: self._rns_toggle_interface(enable=True)),
                "disable": ("Disable Interface", lambda: self._rns_toggle_interface(enable=False)),
                "remove": ("Remove Interface", self._rns_remove_interface),
                "fix_ownership": ("Fix Ownership", self._fix_rns_ownership),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    # ------------------------------------------------------------------
    # Interface Status (live)
    # ------------------------------------------------------------------

    def _rns_interface_status(self):
        """Show live interface status: config + blocking reasons + TX/RX."""
        from ._rns_interface_mgr import find_blocking_interfaces
        from ._rns_diagnostics_engine import check_rns_interface_health
        from utils.service_check import (
            check_process_running, get_rns_shared_instance_info,
            get_udp_port_owner,
        )

        clear_screen()
        print("=== Interface Status ===\n")

        # 1. Determine who is running the RNS instance
        rnsd_running = check_process_running('rnsd')
        si_info = get_rns_shared_instance_info()
        si_available = si_info.get('available', False)

        if rnsd_running and si_available:
            print(f"  RNS Instance: rnsd — shared instance available")
        elif rnsd_running:
            # Degraded state: rnsd running but shared instance not responding
            # Check abstract Unix socket for more specific diagnosis
            unix_socket_exists = False
            try:
                from utils.paths import ReticulumPaths
                # rnsd binds @rns/<instance_name> — e.g. @rns/volcano ai rns,
                # NOT @rns/default. /proc/net/unix (like ss) can truncate the
                # spaced name at the first token, so match the leading token,
                # never a hardcoded "default" (which silently never matched on
                # non-default-instance boxes — the #69 hardcode class).
                inst = ReticulumPaths.get_configured_instance_name()
                inst_token = inst.split()[0] if inst.split() else 'default'
                proc_unix = Path('/proc/net/unix').read_text()
                unix_socket_exists = (
                    f'@rns/{inst_token}' in proc_unix
                    or f'rns/{inst_token}' in proc_unix
                )
            except (OSError, PermissionError):
                pass
            if unix_socket_exists:
                print(f"  RNS Instance: rnsd — DEGRADED (socket exists, auth may be stale)")
            else:
                print(f"  RNS Instance: rnsd — DEGRADED (socket missing, may be hung)")
        else:
            # Check if NomadNet or Sideband is serving as shared instance
            port_owner = None
            try:
                port_owner = get_udp_port_owner(37428)
            except Exception:
                pass
            if port_owner:
                proc_name, pid = port_owner
                print(f"  RNS Instance: {proc_name} (PID {pid}) — running its own RNS")
            elif si_available:
                print(f"  RNS Instance: available (unknown process)")
            else:
                print(f"  RNS Instance: NOT RUNNING — no shared instance")
        print()

        # 2. Get configured interfaces from config
        result = self._rns_cmd_list_interfaces()
        if result is None:
            self.ctx.wait_for_enter()
            return

        interfaces = result.data.get('interfaces', [])
        if not interfaces:
            print("  No interfaces configured in Reticulum config.\n")
            print("  Use 'Add Interface from Template' to create one.")
            self.ctx.wait_for_enter()
            return

        # 3. Get blocking info (why interfaces can't connect)
        blocking_map = {}
        try:
            blocking = find_blocking_interfaces()
            for iface_name, reason, fix in blocking:
                blocking_map[iface_name] = (reason, fix)
        except Exception as e:
            logger.debug("Blocking interface check failed: %s", e)

        # 4. Get live health from rnstatus (TX/RX counters)
        health_map = {}
        try:
            health = check_rns_interface_health()
            for entry in health:
                # entry = (display_name, tx_str, rx_str, is_healthy)
                name = entry[0]
                health_map[name] = {
                    'tx': entry[1], 'rx': entry[2], 'healthy': entry[3],
                }
        except Exception as e:
            logger.debug("Interface health check failed: %s", e)

        # 5. Display unified table
        print(f"  {'Name':<26} {'Type':<24} {'Status':<10} Detail")
        print(f"  {'─' * 80}")

        for iface in interfaces:
            name = iface.get('name', '(unnamed)')
            settings = iface.get('settings', {})
            itype = settings.get('type', '?')
            enabled_raw = str(settings.get('enabled', 'no')).lower()
            enabled = enabled_raw in ('yes', 'true', '1')

            if not enabled:
                status = "DISABLED"
                detail = ""
            elif name in blocking_map:
                status = "BLOCKED"
                reason, fix = blocking_map[name]
                detail = reason
            else:
                # Try to match against rnstatus output
                live = self._match_live_health(name, itype, health_map)
                if live:
                    if not live['healthy']:
                        status = "RX-ONLY"
                        detail = f"↑{live['tx']}  ↓{live['rx']} (link establishment failing)"
                    else:
                        status = "UP"
                        detail = f"↑{live['tx']}  ↓{live['rx']}"
                elif rnsd_running and si_available:
                    # rnsd is running and we have shared instance
                    # but no rnstatus data for this interface
                    if itype == 'TCPServerInterface':
                        status = "LISTEN"
                        detail = "waiting for clients"
                    else:
                        status = "UP"
                        detail = "(no traffic data)"
                elif rnsd_running:
                    status = "UNKNOWN"
                    detail = "rnsd running but shared instance not available"
                else:
                    status = "DOWN"
                    detail = "rnsd not running"

            # Color-coded status hint via text markers
            if status == "BLOCKED":
                marker = "!"
            elif status in ("DOWN", "RX-ONLY"):
                marker = "~"
            elif status == "DISABLED":
                marker = "-"
            else:
                marker = " "

            print(f" {marker}{name:<26} {itype:<24} {status:<10} {detail}")

            # Show fix hint for blocked interfaces
            if name in blocking_map:
                _, fix = blocking_map[name]
                print(f"  {' ' * 26} {' ' * 24} {'':10} Fix: {fix}")

        print(f"\n  Total: {len(interfaces)} interface(s)")

        # Summary hints
        if blocking_map:
            print(f"\n  ! = blocked (dependency missing)")
        if not rnsd_running:
            print(f"\n  Start rnsd from Service Control.")
        elif not si_available:
            # Degraded state: show journal tail for immediate visibility
            print(f"\n  rnsd is running but shared instance is NOT responding.")
            print(f"  Common causes: stale auth tokens, config drift, hung interface.")
            try:
                r = subprocess.run(
                    ['journalctl', '-u', 'rnsd', '-n', '5',
                     '--no-pager', '-q', '--no-hostname'],
                    capture_output=True, text=True, timeout=10,
                )
                if r.stdout and r.stdout.strip():
                    print(f"\n  Recent rnsd log:")
                    for line in r.stdout.strip().splitlines():
                        print(f"    {line.strip()[:90]}")
            except (subprocess.SubprocessError, OSError):
                pass

            # Offer repair wizard via cross-handler dispatch
            diag = (self.ctx.registry.get_handler("rns_diagnostics")
                    if self.ctx.registry else None)
            if diag:
                print()
                if self.ctx.dialog.yesno(
                    "Repair Shared Instance",
                    "rnsd is running but the shared instance is not\n"
                    "responding. This prevents NomadNet, rnstatus, and\n"
                    "all RNS tools from connecting.\n\n"
                    "Run the repair wizard?\n"
                    "(Clears stale auth, checks config, restarts rnsd)",
                ):
                    clear_screen()
                    diag._rns_repair_menu()

        # NomadNet connectivity check (only when rnsd is healthy)
        if si_available:
            nn_path, venv_python = self._find_nomadnet_info()
            if nn_path and venv_python:
                print(f"\n  NomadNet Connectivity:")
                print(f"    Binary: {nn_path}")
                status, detail, debug_lines = self._test_nomadnet_connectivity(
                    venv_python,
                )
                if status == 'connected':
                    print(f"    Shared Instance: CONNECTED")
                else:
                    print(f"    Shared Instance: {status.upper()} — {detail}")
                    if debug_lines:
                        print(f"\n    RNS debug log:")
                        for line in debug_lines[-8:]:
                            print(f"      {line[:90]}")
                    print(f"\n    Try: Fix RNS File Ownership (in this menu)")

        self.ctx.wait_for_enter()

    def _match_live_health(self, config_name: str, iface_type: str,
                           health_map: dict) -> dict:
        """Match a config interface name to rnstatus output.

        rnstatus shows interfaces as 'TypeName[DisplayName]'.
        Config names are [[DisplayName]]. Try fuzzy matching.
        """
        # Direct match: TypeName[config_name]
        for key, data in health_map.items():
            if config_name in key:
                return data

        # Type-based partial match
        type_prefix = iface_type.replace('_', '')
        for key, data in health_map.items():
            if key.startswith(type_prefix):
                return data

        return {}

    # ------------------------------------------------------------------
    # NomadNet connectivity helpers
    # ------------------------------------------------------------------

    def _find_nomadnet_info(self):
        """Find NomadNet binary and its venv Python (if pipx-installed).

        Returns (nn_path, venv_python) or (None, None).
        """
        nn_path = shutil.which('nomadnet')
        if not nn_path:
            user_home = get_real_user_home()
            for p in [user_home / '.local' / 'bin' / 'nomadnet',
                      Path('/usr/local/bin/nomadnet')]:
                if p.exists():
                    nn_path = str(p)
                    break
        if not nn_path:
            return None, None
        # Resolve symlink to find venv python3
        try:
            venv_bin = Path(nn_path).resolve().parent
            candidate = venv_bin / 'python3'
            if candidate.exists():
                return nn_path, str(candidate)
            candidate = venv_bin / 'python'
            if candidate.exists():
                return nn_path, str(candidate)
        except (OSError, ValueError):
            pass
        # Fallback to system python
        sys_python = shutil.which('python3')
        return nn_path, sys_python

    def _test_nomadnet_connectivity(self, venv_python):
        """Test if NomadNet's RNS can connect to rnsd shared instance.

        Uses RNS loglevel=6 (Debug) to capture the actual failure reason.

        Returns (status, detail, debug_lines):
            status: 'connected', 'standalone', or 'error'
            detail: human-readable explanation
            debug_lines: list of RNS debug log lines from stderr
        """
        config_dir = '/etc/reticulum'
        if not Path(config_dir).exists():
            config_dir = str(get_real_user_home() / '.reticulum')

        snippet = (
            "import RNS,sys; "
            f"r = RNS.Reticulum(configdir='{config_dir}', loglevel=6); "
            "s = 'connected' if r.is_connected_to_shared_instance else 'standalone'; "
            "print(s)"
        )

        sudo_user = os.environ.get('SUDO_USER')
        try:
            if sudo_user and sudo_user != 'root':
                cmd = ['sudo', '-u', sudo_user, '-H',
                       venv_python, '-c', snippet]
            else:
                cmd = [venv_python, '-c', snippet]

            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15,
            )
            debug_lines = [
                line.strip() for line in r.stderr.splitlines()
                if line.strip()
            ] if r.stderr else []

            if r.returncode == 0 and 'connected' in r.stdout:
                return 'connected', 'shared instance OK', debug_lines
            elif r.returncode == 0 and 'standalone' in r.stdout:
                return 'standalone', 'NOT connected to rnsd', debug_lines
            else:
                err = r.stderr.strip()[:100] if r.stderr else 'unknown'
                return 'error', err, debug_lines
        except subprocess.TimeoutExpired:
            return 'error', 'timed out (15s)', []
        except (subprocess.SubprocessError, OSError) as e:
            return 'error', str(e)[:100], []

    # ------------------------------------------------------------------
    # Fix RNS file ownership
    # ------------------------------------------------------------------

    def _fix_rns_ownership(self):
        """Audit and fix ownership/permissions on all RNS-related paths.

        Addresses the root/user ownership mismatch caused by MeshForge
        running with sudo while rnsd and NomadNet run as the real user.
        """
        import stat

        clear_screen()
        print("=== Fix RNS File Ownership ===\n")

        sudo_user = os.environ.get('SUDO_USER')
        user_home = get_real_user_home()
        issues_found = 0
        issues_fixed = 0

        # 1. Fix /etc/reticulum/ — identity and config world-readable,
        #    storage world-read/write
        etc_rns = ReticulumPaths.ETC_BASE
        if etc_rns.is_dir():
            print(f"  Checking {etc_rns}/...")

            # Identity and config: ensure world-readable
            for fname in ('identity', 'config'):
                fpath = etc_rns / fname
                if fpath.exists():
                    try:
                        mode = fpath.stat().st_mode
                        if not (mode & stat.S_IROTH):
                            issues_found += 1
                            fpath.chmod(mode | stat.S_IROTH | stat.S_IRGRP)
                            issues_fixed += 1
                            print(f"    Fixed: {fname} → world-readable")
                        else:
                            print(f"    OK: {fname}")
                    except (PermissionError, OSError) as e:
                        issues_found += 1
                        print(f"    FAIL: {fname} — {e}")

            # Storage: world-read/write (files 0o666, dirs 0o777)
            try:
                ReticulumPaths._fix_storage_file_permissions()
                print(f"    OK: storage/ — permissions fixed")
            except Exception as e:
                print(f"    FAIL: storage/ — {e}")

        # 2. Fix ~/.reticulum/ — should be owned by user, not root
        user_rns_dirs = [
            user_home / '.reticulum',
            user_home / '.nomadnetwork',
            user_home / '.config' / 'nomadnetwork',
        ]
        for dir_path in user_rns_dirs:
            if dir_path.exists():
                try:
                    st = dir_path.stat()
                    if st.st_uid == 0 and sudo_user and sudo_user != 'root':
                        issues_found += 1
                        subprocess.run(
                            ['chown', '-R', f'{sudo_user}:{sudo_user}',
                             str(dir_path)],
                            capture_output=True, timeout=30,
                        )
                        issues_fixed += 1
                        print(f"    Fixed: {dir_path} → owned by {sudo_user}")
                    else:
                        print(f"    OK: {dir_path}")
                except (PermissionError, OSError, subprocess.SubprocessError) as e:
                    issues_found += 1
                    print(f"    FAIL: {dir_path} — {e}")

        print(f"\n  Issues found: {issues_found}")
        print(f"  Issues fixed: {issues_fixed}")

        if issues_fixed > 0:
            print(f"\n  Restart rnsd to apply (offered next).")
            if self.ctx.dialog.yesno(
                "Restart rnsd?",
                f"Fixed {issues_fixed} permission issue(s).\n\n"
                f"Restart rnsd to regenerate auth tokens\n"
                f"with correct ownership?",
            ):
                from utils.service_check import stop_service, start_service
                import time
                print(f"\n  Restarting rnsd...")
                # stop/start_service return (ok, msg); a discarded return would
                # let a daemon left stopped read as "restarted" (honest-signal
                # #74-#77; mirrors rns_monitor.py:149-153).
                stop_ok, stop_msg = stop_service('rnsd')
                if not stop_ok:
                    # Non-fatal on its own — a clean start below still brings
                    # rnsd up; surface it only as context.
                    print(f"    Note: stop reported: {stop_msg}")
                time.sleep(1)
                start_ok, start_msg = start_service('rnsd')
                time.sleep(2)
                if start_ok:
                    # Fix permissions on the auth tokens rnsd just regenerated.
                    try:
                        ReticulumPaths._fix_storage_file_permissions()
                    except Exception:
                        pass
                    print(f"  rnsd restarted and permissions re-applied")
                else:
                    print(f"  FAILED to restart rnsd: {start_msg}")
                    print(f"  rnsd may be left stopped — re-run this fix or use "
                          f"Diagnose RNS to recover.")
        elif issues_found == 0:
            print(f"\n  All files have correct ownership and permissions.")

        self.ctx.wait_for_enter()

    # ------------------------------------------------------------------
    # List interfaces
    # ------------------------------------------------------------------

    def _rns_list_interfaces(self):
        """Display all configured RNS interfaces."""
        clear_screen()
        print("=== Configured RNS Interfaces ===\n")

        result = self._rns_cmd_list_interfaces()
        if result is None:
            self.ctx.wait_for_enter()
            return

        interfaces = result.data.get('interfaces', [])
        if not interfaces:
            print("No interfaces found in the Reticulum config.\n")
            print("Use 'Add Interface from Template' to create one.")
            self.ctx.wait_for_enter()
            return

        for idx, iface in enumerate(interfaces, 1):
            name = iface.get('name', '(unnamed)')
            settings = iface.get('settings', {})
            itype = settings.get('type', '?')
            enabled = settings.get('enabled', '?')
            print(f"  {idx}. [[{name}]]")
            print(f"     type    = {itype}")
            print(f"     enabled = {enabled}")
            # Show a few key settings per type
            for key, val in settings.items():
                if key in ('type', 'enabled'):
                    continue
                print(f"     {key} = {val}")
            print()

        print(f"Total: {len(interfaces)} interface(s)")
        self.ctx.wait_for_enter()

    # ------------------------------------------------------------------
    # Add interface (template-based)
    # ------------------------------------------------------------------

    def _rns_add_interface(self):
        """Add a new RNS interface by picking a template and customising."""
        cmd_mod = self._import_rns_commands()
        if cmd_mod is None:
            return

        tpl_result = cmd_mod.get_interface_templates()
        if not tpl_result.success:
            self.ctx.dialog.msgbox("Error", f"Could not load templates:\n{tpl_result.message}")
            return

        templates = tpl_result.data.get('templates', {})
        if not templates:
            self.ctx.dialog.msgbox("Error", "No interface templates available.")
            return

        # Build menu of templates
        choices = []
        for key, tpl in templates.items():
            if tpl.get('multi_interface'):
                label = f"Multi - {tpl['description']}"
            else:
                label = f"{tpl['type']} - {tpl['description']}"
            # Truncate long descriptions for whiptail
            if len(label) > 60:
                label = label[:57] + "..."
            choices.append((key, label))
        choices.append(("back", "Back"))

        tpl_choice = self.ctx.dialog.menu(
            "Add Interface",
            "Select an interface template:",
            choices,
        )

        if tpl_choice is None or tpl_choice == "back":
            return

        template = templates[tpl_choice]

        # Multi-interface templates have a different flow
        if template.get('multi_interface'):
            self._rns_add_multi_interface(cmd_mod, tpl_choice, template)
            return

        # Ask for a name
        default_name = template.get('name', tpl_choice)
        iface_name = self.ctx.dialog.inputbox(
            "Interface Name",
            f"Name for the new {template['type']} interface:\n"
            f"(alphanumeric, spaces, dashes allowed)",
            default_name,
        )
        if not iface_name:
            return
        iface_name = iface_name.strip()
        if not iface_name or not re.match(r'^[\w\s\-]+$', iface_name):
            self.ctx.dialog.msgbox(
                "Invalid Name",
                "Name must contain only alphanumeric characters,\n"
                "spaces, and dashes.",
            )
            return

        # Let user customise key settings
        settings = dict(template.get('settings', {}))
        settings = self._rns_edit_interface_settings(template['type'], settings)
        if settings is None:
            return  # user cancelled

        # Apply
        result = cmd_mod.apply_template(tpl_choice, iface_name, settings)
        if result.success:
            self.ctx.dialog.msgbox(
                "Interface Added",
                f"Added [[{iface_name}]] ({template['type']})\n\n"
                f"Restart rnsd to apply the change.",
            )
            offer_service_fix(self.ctx, "rnsd", running=True)
        else:
            self.ctx.dialog.msgbox("Error", f"Failed to add interface:\n{result.message}")

    def _rns_add_multi_interface(self, cmd_mod, tpl_key: str, template: dict):
        """Handle adding a multi-interface template (e.g. dual-radio Meshtastic)."""
        iface_defs = template.get('interfaces', [])
        if not iface_defs:
            self.ctx.dialog.msgbox("Error", "Template has no interface definitions.")
            return

        # Show overview of what will be created
        overview_lines = [f"{template['name']}\n"]
        for i, idef in enumerate(iface_defs, 1):
            overview_lines.append(f"  Radio {i}: {idef['default_name']}")
            overview_lines.append(f"    type = {idef['type']}")
            for k, v in idef['settings'].items():
                overview_lines.append(f"    {k} = {v}")
            overview_lines.append("")
        overview_lines.append("Proceed? (settings can be customised next)")

        if not self.ctx.dialog.yesno(template['name'], "\n".join(overview_lines)):
            return

        # Collect user config for each interface
        interface_configs = []
        for i, idef in enumerate(iface_defs, 1):
            default_name = idef['default_name']
            iface_name = self.ctx.dialog.inputbox(
                f"Radio {i} Name",
                f"Name for radio {i} ({idef['type']}):\n"
                f"(alphanumeric, spaces, dashes allowed)",
                default_name,
            )
            if not iface_name:
                return
            iface_name = iface_name.strip()
            if not iface_name or not re.match(r'^[\w\s\-]+$', iface_name):
                self.ctx.dialog.msgbox(
                    "Invalid Name",
                    "Name must contain only alphanumeric characters,\n"
                    "spaces, and dashes.",
                )
                return

            # Let user customise this radio's settings
            settings = dict(idef.get('settings', {}))
            settings = self._rns_edit_interface_settings(idef['type'], settings)
            if settings is None:
                return  # user cancelled

            interface_configs.append({
                'name': iface_name,
                'overrides': settings,
            })

        # Apply all interfaces
        result = cmd_mod.apply_multi_template(tpl_key, interface_configs)
        if result.success:
            added = result.data.get('added', [])
            names_str = "\n".join(f"  - [[{n}]]" for n in added)
            self.ctx.dialog.msgbox(
                "Interfaces Added",
                f"Added {len(added)} interfaces:\n{names_str}\n\n"
                f"Restart rnsd to apply the change.",
            )
            offer_service_fix(self.ctx, "rnsd", running=True)
        else:
            self.ctx.dialog.msgbox("Error", f"Failed:\n{result.message}")

    def _rns_edit_interface_settings(self, iface_type: str, settings: dict):
        """Let the user edit key settings for an interface template.

        Returns the (possibly modified) settings dict, or None if cancelled.
        """
        if not settings:
            return settings

        # Build a description of defaults
        desc_lines = [f"Current defaults for {iface_type}:\n"]
        for key, val in settings.items():
            desc_lines.append(f"  {key} = {val}")
        desc_lines.append("\nEdit settings? (No keeps defaults)")

        if not self.ctx.dialog.yesno("Customise Settings", "\n".join(desc_lines)):
            return settings

        # Walk through each setting with an inputbox
        updated = {}
        for key, val in settings.items():
            new_val = self.ctx.dialog.inputbox(
                f"Setting: {key}",
                f"Interface type: {iface_type}\n\n"
                f"Enter value for '{key}':",
                str(val),
            )
            if new_val is None:
                # User cancelled mid-edit
                return None
            updated[key] = new_val.strip()

        return updated

    # ------------------------------------------------------------------
    # Enable / Disable interface
    # ------------------------------------------------------------------

    def _rns_toggle_interface(self, enable: bool):
        """Enable or disable a configured interface."""
        cmd_mod = self._import_rns_commands()
        if cmd_mod is None:
            return

        # List interfaces so user can pick one
        iface_name = self._rns_pick_interface(
            "Enable Interface" if enable else "Disable Interface"
        )
        if not iface_name:
            return

        action = "enable" if enable else "disable"
        if not self.ctx.dialog.yesno(
            f"Confirm {action.title()}",
            f"{action.title()} interface [[{iface_name}]]?\n\n"
            f"rnsd restart required to apply.",
        ):
            return

        if enable:
            result = cmd_mod.enable_interface(iface_name)
        else:
            result = cmd_mod.disable_interface(iface_name)

        if result.success:
            self.ctx.dialog.msgbox(
                f"Interface {action.title()}d",
                f"[[{iface_name}]] is now {action}d.\n\n"
                f"Restart rnsd to apply the change.",
            )
            offer_service_fix(self.ctx, "rnsd", running=True)
        else:
            self.ctx.dialog.msgbox("Error", f"Failed to {action} interface:\n{result.message}")

    # ------------------------------------------------------------------
    # Remove interface
    # ------------------------------------------------------------------

    def _rns_remove_interface(self):
        """Remove an interface from the Reticulum config."""
        cmd_mod = self._import_rns_commands()
        if cmd_mod is None:
            return

        iface_name = self._rns_pick_interface("Remove Interface")
        if not iface_name:
            return

        if not self.ctx.dialog.yesno(
            "Confirm Remove",
            f"Remove interface [[{iface_name}]]?\n\n"
            f"A backup of the config will be created.\n"
            f"This cannot be undone without the backup.",
        ):
            return

        result = cmd_mod.remove_interface(iface_name)
        if result.success:
            self.ctx.dialog.msgbox(
                "Interface Removed",
                f"[[{iface_name}]] has been removed.\n\n"
                f"Restart rnsd to apply the change.",
            )
            offer_service_fix(self.ctx, "rnsd", running=True)
        else:
            self.ctx.dialog.msgbox("Error", f"Failed to remove interface:\n{result.message}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _rns_pick_interface(self, title: str):
        """Show a menu of configured interfaces and return the selected name.

        Returns the interface name string, or None if cancelled/empty.
        """
        result = self._rns_cmd_list_interfaces()
        if result is None:
            return None

        interfaces = result.data.get('interfaces', [])
        if not interfaces:
            self.ctx.dialog.msgbox(title, "No interfaces configured.")
            return None

        choices = []
        for iface in interfaces:
            name = iface.get('name', '(unnamed)')
            settings = iface.get('settings', {})
            itype = settings.get('type', '?')
            enabled = settings.get('enabled', '?')
            desc = f"{itype} (enabled={enabled})"
            choices.append((name, desc))
        choices.append(("back", "Back"))

        choice = self.ctx.dialog.menu(title, "Select an interface:", choices)
        if choice is None or choice == "back":
            return None
        return choice

    def _rns_cmd_list_interfaces(self):
        """Call commands.rns.list_interfaces(), returning CommandResult or None on error."""
        cmd_mod = self._import_rns_commands()
        if cmd_mod is None:
            return None

        result = cmd_mod.list_interfaces()
        if not result.success:
            self.ctx.dialog.msgbox("Error", f"Could not read interfaces:\n{result.message}")
            return None
        return result

    def _import_rns_commands(self):
        """Import and return the commands.rns module."""
        return rns_mod
