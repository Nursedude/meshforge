"""
RNS Config Handler — Reticulum configuration management.

Converted from rns_config_mixin.py as part of the mixin-to-registry migration.
Also includes _check_meshtastic_plugin and _install_meshtastic_interface_plugin
(moved from rns_menu_mixin.py — config-related, shared by other RNS handlers).
"""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)

from utils.paths import get_real_user_home, ReticulumPaths
from backend import clear_screen


class RNSConfigHandler(BaseHandler):
    """TUI handler for RNS configuration management."""

    handler_id = "rns_config"
    menu_section = "rns"

    def menu_items(self):
        return [
            ("config", "View Reticulum Config", None),
            ("edit", "Edit Reticulum Config", None),
            ("logging", "Configure RNS Logging", None),
            ("check", "Check RNS Setup", None),
        ]

    def execute(self, action):
        dispatch = {
            "config": self._view_rns_config,
            "edit": self._edit_rns_config,
            "logging": self._configure_rns_logging,
            "check": self._check_rns_setup,
        }
        method = dispatch.get(action)
        if method:
            method()

    # ------------------------------------------------------------------
    # Config management methods (from rns_config_mixin.py)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_root_owned_rns_config(config_path: Path) -> bool:
        """Check if the RNS config is in a root-only location (/root/)."""
        try:
            return str(config_path.resolve()).startswith('/root/')
        except OSError:
            return str(config_path).startswith('/root/')

    def _migrate_rns_config_to_etc(self, source: Path) -> bool:
        """Migrate RNS config from root-owned location to /etc/reticulum/config.

        Copies the config to /etc/reticulum/config (system-wide, preferred location),
        sets world-readable permissions, and renames the old file to avoid confusion.
        Also ensures the migrated config carries a valid per-box ``rpc_key`` —
        fleet boxes that pre-date Issue #41 may be missing one (we fill it in
        without overwriting an existing valid value).

        Returns True if migration succeeded.
        """
        target = Path('/etc/reticulum/config')
        if target.exists():
            self.ctx.dialog.msgbox(
                "Cannot Migrate",
                f"Config already exists at:\n  {target}\n\n"
                f"Remove it first if you want to migrate from:\n  {source}"
            )
            return False
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(target))
            target.chmod(0o644)
            self._ensure_rpc_key_on(target)
            # Rename old config so rnsd picks up the /etc/ one
            backup = source.with_suffix('.migrated')
            source.rename(backup)
            return True
        except (OSError, PermissionError) as e:
            self.ctx.dialog.msgbox("Error", f"Failed to migrate config:\n{e}")
            return False

    def _deploy_rns_template(self) -> Optional[Path]:
        """Deploy RNS template to /etc/reticulum/config (system-wide).

        Reads the template into memory, renders it with a freshly-generated
        per-box ``rpc_key`` (so ``__RPC_KEY__`` never lands on disk), and
        writes the rendered text to /etc/reticulum/config.

        Returns the path where the config was deployed, or None on failure.
        """
        from utils.rns_config_setup import render_template

        template = Path(__file__).parent.parent.parent.parent / 'templates' / 'reticulum.conf'
        if not template.exists():
            return None

        target = Path('/etc/reticulum/config')
        if target.exists():
            self.ctx.dialog.msgbox(
                "Config Exists",
                f"Config already exists at:\n  {target}\n\n"
                f"Use 'Edit Reticulum Config' to modify it."
            )
            return None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            rendered = render_template(template.read_text())
            target.write_text(rendered)
            target.chmod(0o644)
            return target
        except (OSError, PermissionError) as e:
            self.ctx.dialog.msgbox("Error", f"Failed to deploy config:\n{e}")
            return None

    def _ensure_rpc_key_on(self, config_path: Path) -> None:
        """Ensure ``config_path`` carries a valid 64-hex per-box ``rpc_key``.

        Thin wrapper around ``utils.rns_config_setup.ensure_rpc_key`` that
        swallows errors and surfaces a dialog only on OSError — the migrate
        and deploy flows call this at config-landing time.
        """
        from utils.rns_config_setup import ensure_rpc_key
        try:
            _key, generated = ensure_rpc_key(config_path)
            if generated:
                logger.info("Pinned fresh rpc_key into %s", config_path)
        except OSError as e:
            logger.warning("rpc_key pin skipped for %s: %s", config_path, e)

    def _view_rns_config(self):
        """View current Reticulum config."""
        clear_screen()
        print("=== Reticulum Configuration ===\n")

        config_path = ReticulumPaths.get_config_file()

        if config_path.exists():
            # Warn if config is in root-only location
            if self._is_root_owned_rns_config(config_path):
                print(f"Config: {config_path}")
                print(f"  ** This config is in /root/ - not editable without sudo **")
                print(f"  ** Use 'Edit Reticulum Config' to migrate to /etc/reticulum/ **\n")
            else:
                print(f"Config: {config_path}\n")
            try:
                content = config_path.read_text()
                print(content)

                # Show validation warnings inline
                issues = self._validate_rns_config_content(content)
                if issues:
                    print("\n--- Config Issues ---")
                    for issue in issues:
                        print(f"  ! {issue}")
            except PermissionError:
                print(f"Permission denied reading {config_path}")
                print(f"Try: sudo cat {config_path}")
        else:
            print(f"No Reticulum config found at: {config_path}")
            user_home = get_real_user_home()
            print(f"\nMeshForge checks (in order):")
            print(f"  1. /etc/reticulum/config  (system-wide, preferred)")
            print(f"  2. {user_home}/.config/reticulum/config")
            print(f"  3. {user_home}/.reticulum/config")
            if os.geteuid() == 0 and os.environ.get('SUDO_USER'):
                print(f"\nNote: rnsd (running as root) uses /root/.reticulum/config")
                print(f"  For shared use, deploy to /etc/reticulum/config")
            print(f"\nTo create: use 'Edit Reticulum Config' to deploy template")
            print(f"Template:  templates/reticulum.conf")

        # Show Meshtastic_Interface plugin status
        plugin_path = ReticulumPaths.get_interfaces_dir() / 'Meshtastic_Interface.py'
        print(f"\n--- Meshtastic Interface Plugin ---")
        if plugin_path.exists():
            print(f"  Installed: {plugin_path}")
            print(f"  Size: {plugin_path.stat().st_size} bytes")
        else:
            print(f"  NOT INSTALLED")
            print(f"  Expected at: {plugin_path}")
            print(f"  Source: https://github.com/landandair/RNS_Over_Meshtastic")
            print(f"  Use 'Install Meshtastic Interface' from the RNS menu to install.")

        self.ctx.wait_for_enter()

    def _edit_rns_config(self):
        """Edit Reticulum config with available editor. Deploys template if no config exists."""
        config_path = ReticulumPaths.get_config_file()

        if not config_path.exists():
            # Offer to deploy from template to /etc/reticulum/config (system-wide)
            template = Path(__file__).parent.parent.parent.parent / 'templates' / 'reticulum.conf'

            if template.exists():
                target = Path('/etc/reticulum/config')
                if self.ctx.dialog.yesno(
                    "Deploy Reticulum Config",
                    f"No Reticulum config found.\n\n"
                    f"Deploy template to:\n  {target}\n\n"
                    f"This sets up RNS with:\n"
                    f"  - share_instance = Yes (required for rnstatus)\n"
                    f"  - AutoInterface (local network discovery)\n"
                    f"  - Meshtastic_Interface on port 4403\n\n"
                    f"You can edit it after deployment."
                ):
                    deployed = self._deploy_rns_template()
                    if deployed:
                        config_path = deployed
                    else:
                        return
                else:  # User said No
                    return
            else:
                self.ctx.dialog.msgbox(
                    "No Config",
                    "No Reticulum config found and template missing.\n\n"
                    "Install RNS first: pipx install rns\n"
                    "Then run rnsd once to generate default config."
                )
                return

        # If config is in /root/, offer to migrate to /etc/reticulum/
        if self._is_root_owned_rns_config(config_path):
            if self.ctx.dialog.yesno(
                "Migrate Config",
                f"Config is at:\n  {config_path}\n\n"
                f"This location requires root access to edit.\n\n"
                f"Migrate to /etc/reticulum/config?\n"
                f"(System-wide location, accessible by rnsd and all users)"
            ):
                if self._migrate_rns_config_to_etc(config_path):
                    config_path = Path('/etc/reticulum/config')
                    self.ctx.dialog.msgbox(
                        "Migrated",
                        f"Config moved to: {config_path}\n\n"
                        f"Restart rnsd to apply:\n"
                        f"  sudo systemctl restart rnsd"
                    )
                # If migration failed, continue with original path

        # In-Domain (MF018): edit the RNS config IN-APP — was a nano/vi spawn
        # that ejected the operator to a shell.
        from config_edit import edit_config_in_app
        edit_config_in_app(self.ctx, config_path, title="Edit RNS config")

        # After editing, check for config divergence between user and root
        self._check_rns_config_divergence(config_path)

    def _check_rns_config_divergence(self, edited_path: Path):
        """Check if edited config differs from root/system config that rnsd actually uses."""
        # Only relevant when running as root/sudo
        if os.geteuid() != 0:
            return

        root_configs = [
            Path('/etc/reticulum/config'),
            Path('/root/.config/reticulum/config'),
            Path('/root/.reticulum/config'),
        ]

        # Skip if edited path is already a root/system path
        edited_str = str(edited_path)
        if edited_str.startswith('/root/') or edited_str.startswith('/etc/'):
            return

        for root_config in root_configs:
            if root_config.exists() and root_config != edited_path:
                try:
                    user_content = edited_path.read_text()
                    root_content = root_config.read_text()

                    if user_content != root_content:
                        if self.ctx.dialog.yesno(
                            "Config Divergence Detected",
                            f"WARNING: Your edited config:\n"
                            f"  {edited_path}\n\n"
                            f"differs from the config rnsd uses:\n"
                            f"  {root_config}\n\n"
                            f"rnsd runs as root and reads {root_config}.\n"
                            f"Your changes won't take effect until synced.\n\n"
                            f"Copy your config to {root_config}?"
                        ):
                            try:
                                backup = root_config.with_suffix('.config.bak')
                                if root_config.exists():
                                    shutil.copy2(str(root_config), str(backup))
                                shutil.copy2(str(edited_path), str(root_config))
                                self.ctx.dialog.msgbox(
                                    "Config Synced",
                                    f"Copied to: {root_config}\n"
                                    f"Backup at: {backup}\n\n"
                                    f"Restart rnsd to apply:\n"
                                    f"  sudo systemctl restart rnsd"
                                )
                            except Exception as e:
                                self.ctx.dialog.msgbox(
                                    "Sync Failed",
                                    f"Could not copy config: {e}\n\n"
                                    f"Manual fix:\n"
                                    f"  sudo cp {edited_path} {root_config}\n"
                                    f"  sudo systemctl restart rnsd"
                                )
                except (OSError, subprocess.SubprocessError) as e:
                    logger.debug("RNS config apply failed: %s", e)
                return  # Only check the first existing root config

    def _validate_rns_config_content(self, content: str) -> list:
        """Validate RNS config content and return list of issues found."""
        issues = []
        content_lower = content.lower()

        # Check [reticulum] section exists
        if '[reticulum]' not in content_lower:
            issues.append("Missing [reticulum] section")

        # Check share_instance
        has_share = False
        for line in content.split('\n'):
            stripped = line.strip().lower()
            if stripped.startswith('#'):
                continue
            if 'share_instance' in stripped:
                if 'yes' in stripped or 'true' in stripped:
                    has_share = True
                break
        if not has_share:
            issues.append("share_instance not set to Yes (rnstatus/client apps won't connect)")

        # Check for at least one active interface
        has_interface = False
        has_meshtastic = False
        for line in content.split('\n'):
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            if stripped.startswith('[[') and stripped.endswith(']]'):
                has_interface = True
            if 'meshtastic_interface' in stripped.lower() and 'type' in stripped.lower():
                has_meshtastic = True

        if not has_interface:
            issues.append("No interfaces configured")

        # Check Meshtastic_Interface status
        plugin_path = ReticulumPaths.get_interfaces_dir() / 'Meshtastic_Interface.py'
        plugin_installed = plugin_path.exists()

        if not has_meshtastic and not plugin_installed:
            issues.append("No Meshtastic_Interface configured (needed for mesh bridging)")
        elif has_meshtastic and not plugin_installed:
            issues.append(
                f"Meshtastic_Interface.py plugin not installed at "
                f"{ReticulumPaths.get_interfaces_dir()}/\n"
                f"    Install from: RNS menu > Install Meshtastic Interface"
            )

        return issues

    def _check_rns_setup(self) -> bool:
        """Check RNS setup and offer to fix common issues."""
        config_path = ReticulumPaths.get_config_file()

        if not config_path.exists():
            template = Path(__file__).parent.parent.parent.parent / 'templates' / 'reticulum.conf'
            if template.exists():
                target = Path('/etc/reticulum/config')
                if self.ctx.dialog.yesno(
                    "RNS Not Configured",
                    f"No Reticulum config found.\n\n"
                    f"RNS tools (rnstatus, rnpath) and the gateway bridge\n"
                    f"require a config file to function.\n\n"
                    f"Deploy MeshForge template to:\n"
                    f"  {target}\n\n"
                    f"(Sets up shared instance + Meshtastic bridge)"
                ):
                    deployed = self._deploy_rns_template()
                    if deployed:
                        self.ctx.dialog.msgbox(
                            "Config Deployed",
                            f"Deployed to: {deployed}\n\n"
                            f"Restart rnsd to apply:\n"
                            f"  sudo systemctl restart rnsd"
                        )
                        config_path = deployed
            return True  # Continue to menu either way

        # Config exists - check if it's in a root-only location
        if self._is_root_owned_rns_config(config_path):
            if self.ctx.dialog.yesno(
                "Config in /root/",
                f"RNS config found at:\n  {config_path}\n\n"
                f"This location requires root access to edit.\n\n"
                f"Migrate to /etc/reticulum/config?\n"
                f"(System-wide location, accessible by all users)"
            ):
                if self._migrate_rns_config_to_etc(config_path):
                    config_path = Path('/etc/reticulum/config')
                    self.ctx.dialog.msgbox(
                        "Migrated",
                        f"Config moved to: {config_path}\n\n"
                        f"Restart rnsd to apply:\n"
                        f"  sudo systemctl restart rnsd"
                    )

        # Config exists - validate it
        try:
            content = config_path.read_text()
            issues = self._validate_rns_config_content(content)
            if issues:
                msg = f"Config: {config_path}\n\nIssues found:\n"
                for issue in issues:
                    msg += f"  - {issue}\n"
                msg += f"\nUse 'Edit Reticulum Config' to fix these issues."
                self.ctx.dialog.msgbox("RNS Config Issues", msg)
        except PermissionError:
            self.ctx.dialog.msgbox(
                "Permission Denied",
                f"Cannot read config at:\n  {config_path}\n\n"
                f"Run MeshForge with sudo to access this file,\n"
                f"or use 'Edit Reticulum Config' to migrate it."
            )

        # Check for Meshtastic_Interface.py plugin
        if not self._check_meshtastic_plugin():
            if self.ctx.dialog.yesno(
                "Meshtastic Interface Plugin Missing",
                "The Meshtastic_Interface.py plugin is not installed.\n\n"
                "This plugin is required for bridging RNS over\n"
                "Meshtastic LoRa mesh networks.\n\n"
                f"Expected at:\n"
                f"  {ReticulumPaths.get_interfaces_dir()}/Meshtastic_Interface.py\n\n"
                "Download and install it now?"
            ):
                self._install_meshtastic_interface_plugin()

        return True

    # ------------------------------------------------------------------
    # Logging configuration
    # ------------------------------------------------------------------

    def _configure_rns_logging(self):
        """Configure RNS loglevel in reticulum config."""
        config_path = ReticulumPaths.get_config_file()
        if not config_path.exists():
            self.ctx.dialog.msgbox(
                "No Config",
                "No Reticulum config found.\n\n"
                "Use 'Check RNS Setup' to deploy a config first."
            )
            return

        # Read current loglevel
        try:
            content = config_path.read_text()
        except (OSError, PermissionError) as e:
            self.ctx.dialog.msgbox("Error", f"Cannot read config:\n{e}")
            return

        current_level = "4"
        level_match = re.search(
            r'^\s*loglevel\s*=\s*(\d+)',
            content, re.MULTILINE
        )
        if level_match:
            current_level = level_match.group(1)

        # Show level picker
        levels = [
            ("0", "Critical"),
            ("1", "Error"),
            ("2", "Warning"),
            ("3", "Notice"),
            ("4", f"Info (default)"),
            ("5", "Verbose"),
            ("6", "Debug — troubleshooting"),
            ("7", "Extreme — very verbose"),
        ]

        choice = self.ctx.dialog.menu(
            "RNS Log Level",
            f"Current loglevel: {current_level}\n\n"
            f"Higher = more verbose. Set to 6 (Debug) or 7\n"
            f"(Extreme) to see interface connection details.\n\n"
            f"Logs visible via: sudo journalctl -u rnsd -f",
            levels,
        )

        if choice is None:
            return

        if choice == current_level:
            self.ctx.dialog.msgbox(
                "No Change",
                f"Loglevel is already {choice}."
            )
            return

        # Update loglevel in config
        if level_match:
            new_content = content[:level_match.start(1)] + choice + content[level_match.end(1):]
        else:
            # No loglevel line found — add one in [logging] section
            logging_match = re.search(r'^\[logging\]\s*$', content, re.MULTILINE)
            if logging_match:
                insert_pos = logging_match.end()
                new_content = (content[:insert_pos]
                               + f"\n  loglevel = {choice}"
                               + content[insert_pos:])
            else:
                # No [logging] section — append one
                new_content = content.rstrip() + f"\n\n[logging]\n  loglevel = {choice}\n"

        # Write with backup
        try:
            backup = config_path.with_suffix('.config.bak')
            if config_path.exists():
                import shutil as _shutil
                _shutil.copy2(str(config_path), str(backup))
            config_path.write_text(new_content)
        except (OSError, PermissionError) as e:
            self.ctx.dialog.msgbox("Error", f"Cannot write config:\n{e}")
            return

        level_name = dict(levels).get(choice, choice)
        # Offer to apply the change in-app via the remediation surface — no
        # shell-escape, and the restart goes through service_check (the SSOT)
        # instead of a raw `sudo systemctl` call. (In-Domain Principle.)
        from remediation import RemediationAction, propose_remediation
        from utils.service_check import restart_service
        propose_remediation(
            self.ctx,
            "Apply RNS Loglevel",
            f"Loglevel set to {choice} ({level_name}). "
            f"rnsd must restart to load the new loglevel.",
            [RemediationAction(
                label="Restart rnsd now",
                description=f"apply loglevel {choice} (systemctl restart rnsd)",
                apply=lambda: restart_service("rnsd"),
                requires_admin=True,
            )],
        )

    # ------------------------------------------------------------------
    # Meshtastic plugin methods (from rns_menu_mixin.py)
    # ------------------------------------------------------------------

    def _check_meshtastic_plugin(self) -> bool:
        """Check if Meshtastic_Interface.py plugin is installed."""
        plugin_path = ReticulumPaths.get_interfaces_dir() / 'Meshtastic_Interface.py'
        return plugin_path.exists()

    def _install_meshtastic_interface_plugin(self):
        """Install or update Meshtastic_Interface.py from vendored template."""
        interfaces_dir = ReticulumPaths.get_interfaces_dir()
        plugin_path = interfaces_dir / 'Meshtastic_Interface.py'

        # Locate vendored source
        vendored = (Path(__file__).parent.parent.parent.parent
                    / 'templates' / 'interfaces' / 'Meshtastic_Interface.py')
        if not vendored.exists():
            self.ctx.dialog.msgbox(
                "Template Missing",
                "Vendored Meshtastic_Interface.py not found.\n\n"
                f"Expected at:\n  {vendored}\n\n"
                "Reinstall MeshForge to restore templates."
            )
            return

        # Check if already installed and up to date
        action = "Install"
        if plugin_path.exists():
            import filecmp
            if filecmp.cmp(str(vendored), str(plugin_path), shallow=False):
                self.ctx.dialog.msgbox(
                    "Up to Date",
                    f"Meshtastic_Interface.py is already current at:\n"
                    f"  {plugin_path}"
                )
                return
            action = "Update"

        if not self.ctx.dialog.yesno(
            f"{action} Meshtastic Interface Plugin",
            f"The Meshtastic_Interface.py plugin bridges RNS over\n"
            f"Meshtastic LoRa mesh networks.\n\n"
            f"{action} to:\n  {plugin_path}\n\n"
            f"Source:\n  {vendored}\n\n"
            f"{action} now?"
        ):
            return

        try:
            interfaces_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(vendored), str(plugin_path))
            plugin_path.chmod(0o644)
            print(f"  {action}d: {plugin_path}")

            # Install meshtastic Python module
            meshtastic_installed = False
            venv_pip = Path('/opt/meshforge/venv/bin/pip')
            if venv_pip.exists():
                print("  Installing meshtastic Python module...")
                pip_result = subprocess.run(
                    [str(venv_pip), 'install', '-q', 'meshtastic'],
                    capture_output=True, text=True, timeout=120
                )
                if pip_result.returncode != 0:
                    err_text = (pip_result.stderr or pip_result.stdout or '').lower()
                    if 'installed by' in err_text or 'externally-managed' in err_text:
                        print("  Debian package conflict, retrying...")
                        pip_result = subprocess.run(
                            [str(venv_pip), 'install', '-q',
                             '--ignore-installed', 'meshtastic'],
                            capture_output=True, text=True, timeout=120
                        )
                meshtastic_installed = pip_result.returncode == 0

            restart_hint = ("Restart rnsd to load the new interface:\n"
                            "  sudo systemctl restart rnsd")
            if not meshtastic_installed:
                restart_hint = (
                    "NOTE: The meshtastic Python module is also required.\n"
                    "Install it: /opt/meshforge/venv/bin/pip install meshtastic"
                    "\n\nThen restart rnsd:\n  sudo systemctl restart rnsd"
                )

            self.ctx.dialog.msgbox(
                f"Plugin {action}d",
                f"Meshtastic_Interface.py {action.lower()}d at:\n"
                f"  {plugin_path}\n\n"
                f"{restart_hint}"
            )

        except (OSError, PermissionError) as e:
            self.ctx.dialog.msgbox(
                f"{action} Failed",
                f"Failed to {action.lower()} plugin:\n{e}\n\n"
                f"Try running with sudo, or manually copy:\n"
                f"  sudo cp {vendored} {interfaces_dir}/"
            )
