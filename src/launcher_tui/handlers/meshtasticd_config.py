"""
Meshtasticd Handler — Unified menu for radio, service, and config management.

Single entry point in the Configuration menu. Merges own inline items
(service lifecycle, config files) with sub-handler items (radio, lora,
mqtt, nodedb) from the "meshtasticd" registry section.

Shared module-level utilities (read_overlay, write_overlay, _glob_yaml, etc.)
remain here since sub-handlers import them.
"""

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import yaml
from pathlib import Path

from handler_protocol import BaseHandler
from backend import clear_screen
from utils.service_check import (
    check_service, check_systemd_service,
    apply_config_and_restart,
)
logger = logging.getLogger(__name__)

# Direct imports for first-party modules (MF006: no safe_import for first-party)
from utils.meshtastic_http import get_http_client as _get_http_client

# --- Shared overlay utilities (imported by sub-handlers) ---

OVERLAY_PATH = Path('/etc/meshtasticd/config.d/meshforge-overrides.yaml')
OVERRIDES_NAMES = {'meshforge-overrides.yaml', 'meshforge-overrides.yml'}

# Top-level YAML keys that have no business in a HAT overlay and that MeshForge
# guarantees come from the base /etc/meshtasticd/config.yaml. If an upstream-
# vendored template (e.g. chrismyers2000's lora-MeshAdv-900M30S.yaml in
# meshtasticd 2.7.x available.d/) ships these blocks, they silently override
# operator state — most painfully `Webserver: Port: 443`, which moves the API
# off MeshForge's expected :9443 and breaks every consumer that posts to
# `/api/v1/toradio` (gateway TX SSOT). moc3 ran in this zombie state for 18h
# on 2026-05-18 — meshtasticd "active", but :9443 silently bound to :443.
_HAT_OVERLAY_FORBIDDEN_KEYS = frozenset({
    'Webserver',
    'TCP',
    'Logging',
    'MQTT',
    'Bluetooth',
    'General',
})


def _sanitize_hat_overlay(content: str):
    """Strip non-Lora top-level blocks from a HAT overlay before activation.

    Returns ``(sanitized_yaml_text, stripped_keys_list)``. If the input
    doesn't parse as YAML or isn't a top-level mapping, returns it
    unchanged with an empty strip list — the caller (and meshtasticd's
    own load) will surface the parse error loudly rather than silently
    mangling the operator's content.
    """
    try:
        loaded = yaml.safe_load(content)
    except yaml.YAMLError:
        return content, []
    if not isinstance(loaded, dict):
        return content, []
    stripped = sorted(k for k in loaded if k in _HAT_OVERLAY_FORBIDDEN_KEYS)
    if not stripped:
        return content, []
    for key in stripped:
        del loaded[key]
    return yaml.safe_dump(loaded, sort_keys=False, default_flow_style=False), stripped


def _glob_yaml(directory: Path) -> list:
    """Glob both .yaml and .yml files from a directory."""
    files = list(directory.glob('*.yaml')) + list(directory.glob('*.yml'))
    # Deduplicate by resolved path, preserve order
    seen = set()
    result = []
    for f in files:
        key = f.resolve()
        if key not in seen:
            seen.add(key)
            result.append(f)
    return result


def _is_overrides(path: Path) -> bool:
    """Check if a path is the meshforge overrides file."""
    return path.name in OVERRIDES_NAMES


OVERLAY_HEADER = (
    "# MeshForge configuration overrides\n"
    "# These settings override /etc/meshtasticd/config.yaml\n"
    "# To reset: sudo rm this file and restart meshtasticd\n"
)


def read_overlay() -> dict:
    """Load meshforge-overrides.yaml from config.d/ (or empty dict)."""
    if OVERLAY_PATH.exists():
        try:
            data = yaml.safe_load(OVERLAY_PATH.read_text())
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.debug("Failed to read overlay: %s", e)
    return {}


def write_overlay(data: dict, dialog=None) -> bool:
    """Write meshforge-overrides.yaml to config.d/. Never touches config.yaml.

    Uses atomic write (tempfile + rename) to prevent corruption on
    power loss or interruption.
    """
    try:
        OVERLAY_PATH.parent.mkdir(parents=True, exist_ok=True)
        content = OVERLAY_HEADER + "\n" + yaml.dump(
            data, default_flow_style=False, sort_keys=False
        )
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(OVERLAY_PATH.parent), suffix='.tmp'
        )
        try:
            with os.fdopen(tmp_fd, 'w') as f:
                f.write(content)
            os.rename(tmp_path, str(OVERLAY_PATH))
        except BaseException:
            os.unlink(tmp_path)
            raise
        return True
    except PermissionError:
        if dialog:
            dialog.msgbox("Error", "Permission denied. Run with sudo.")  # in-domain-ok: privilege separation — writing the meshtasticd overlay needs root (in_domain_principle.md)
        return False
    except Exception as e:
        if dialog:
            dialog.msgbox("Error", f"Failed to write overlay:\n{e}")
        return False


def activate_hardware_config(config_name: str,
                             available_dir: Path = None,
                             config_d: Path = None) -> bool:
    """Activate a hardware config: remove old configs, copy new one, restart.

    Standalone function usable from both the TUI handler and startup recovery.

    Args:
        config_name: Filename (e.g. 'meshtoad-spi.yaml') in available.d/
        available_dir: Path to available.d/ (default: /etc/meshtasticd/available.d)
        config_d: Path to config.d/ (default: /etc/meshtasticd/config.d)

    Returns:
        True if activation succeeded.

    Raises:
        FileNotFoundError: If source template doesn't exist.
        PermissionError: If lacking write access.
    """
    if available_dir is None:
        available_dir = Path('/etc/meshtasticd/available.d')
    if config_d is None:
        config_d = Path('/etc/meshtasticd/config.d')

    src = available_dir / config_name
    if not src.exists():
        raise FileNotFoundError(f"Template not found: {src}")

    config_d.mkdir(parents=True, exist_ok=True)

    # Remove old hardware configs (preserve meshforge-overrides)
    for old in _glob_yaml(config_d):
        if not _is_overrides(old):
            old.unlink()
            logger.info("Removed old hardware config: %s", old.name)

    dst = config_d / config_name
    sanitized, stripped = _sanitize_hat_overlay(src.read_text())
    dst.write_text(sanitized)
    if stripped:
        logger.warning(
            "HAT overlay %s contained non-Lora blocks (%s) — stripped before "
            "install. HAT templates must not override Webserver/TCP/etc.; "
            "those belong in /etc/meshtasticd/config.yaml.",
            config_name, ', '.join(stripped),
        )
    logger.info("Activated hardware config: %s", config_name)

    # Honest-signal: propagate the real restart result so callers (e.g.
    # MeshtasticdRadioHandler._activate_hardware_config) can surface a failed
    # restart instead of always reporting success. Returning True unconditionally
    # left that handler's error branch dead code (#74-#77 class).
    ok, msg = apply_config_and_restart('meshtasticd')
    if not ok:
        logger.error(
            "meshtasticd restart after activating %s failed: %s", config_name, msg
        )
    return ok


def ensure_meshtasticd_config():
    """Auto-create /etc/meshtasticd structure and templates if missing."""
    try:
        from core.meshtasticd_config import MeshtasticdConfig
        MeshtasticdConfig().ensure_structure()
    except PermissionError:
        logger.debug("Cannot auto-create meshtasticd config (no root)")
    except Exception as e:
        logger.debug("meshtasticd config auto-create failed: %s", e)


# Desired display order for the unified meshtasticd submenu.
# Section headers (_xxx_) and own items are defined inline;
# sub-handler items (owner, presets, hardware, lora, mqtt, cleanup)
# come from the "meshtasticd" registry section.
_MESHTASTICD_ORDERING = [
    "_svc_", "status", "test", "restart", "logs",
    "_radio_", "owner", "presets", "lora",
    "_hw_", "hardware",
    "_dev_", "mqtt", "cleanup",
    "_cfg_", "view", "overlays", "edit",
]


class MeshtasticdConfigHandler(BaseHandler):
    """TUI handler for meshtasticd — unified radio, service, and config menu."""

    handler_id = "meshtasticd_config"
    menu_section = "configuration"

    def menu_items(self):
        return [
            ("meshtasticd", "meshtasticd          Radio, service, config", "meshtastic"),
        ]

    def execute(self, action):
        if action == "meshtasticd":
            self._meshtasticd_menu()

    # ------------------------------------------------------------------
    # Unified meshtasticd submenu
    # ------------------------------------------------------------------

    def _meshtasticd_menu(self):
        """Unified meshtasticd menu: service, radio, hardware, config files."""
        ensure_meshtasticd_config()

        while True:
            # Own inline items (service lifecycle + config files)
            own_items = [
                ("_svc_", "--- Service ---"),
                ("status", "Service Status"),
                ("test", "Connection Test"),
                ("restart", "Restart Service"),
                ("logs", "Service Logs"),
                ("_radio_", "--- Radio ---"),
                ("_hw_", "--- Radio Hardware ---"),
                ("_dev_", "--- Device Config ---"),
                ("_cfg_", "--- Config Files ---"),
                ("view", "View Active Config"),
                ("overlays", "View config.d/ Overlays"),
                ("edit", "Edit Config Files"),
            ]

            # Merge with registry sub-handler items
            # (owner, presets, hardware from meshtasticd_radio;
            #  lora from meshtasticd_lora; mqtt from meshtasticd_device_mqtt;
            #  cleanup from meshtasticd_nodedb)
            registry_items = []
            if self.ctx.registry:
                registry_items = self.ctx.registry.get_menu_items("meshtasticd")

            registry_tags = {tag for tag, _ in registry_items}
            own_map = {tag: desc for tag, desc in own_items}
            reg_map = {tag: desc for tag, desc in registry_items}
            all_map = {**own_map, **reg_map}

            # Apply ordering
            ordered = []
            for tag in _MESHTASTICD_ORDERING:
                if tag in all_map:
                    ordered.append((tag, all_map[tag]))
            # Append any unordered items
            ordered_set = set(_MESHTASTICD_ORDERING)
            for tag, desc in list(own_items) + list(registry_items):
                if tag not in ordered_set and (tag, desc) not in ordered:
                    ordered.append((tag, desc))

            # Filter out section headers with no items after them
            # (prevents empty groups when sub-handlers fail to register)
            result = []
            for i, (tag, desc) in enumerate(ordered):
                if tag.startswith("_") and tag.endswith("_"):
                    # Look ahead: is there at least one non-header item
                    # before the next header or end of list?
                    has_items = False
                    for j in range(i + 1, len(ordered)):
                        next_tag = ordered[j][0]
                        if next_tag.startswith("_") and next_tag.endswith("_"):
                            break
                        has_items = True
                        break
                    if has_items:
                        result.append((tag, desc))
                else:
                    result.append((tag, desc))

            result.append(("back", "Back"))

            choice = self.ctx.dialog.menu(
                "meshtasticd",
                "Configure meshtasticd daemon:",
                result
            )

            if choice is None or choice == "back":
                break

            # Section headers — just re-display menu
            if choice.startswith("_") and choice.endswith("_"):
                continue

            # Try registry sub-handlers first (owner, presets, hardware,
            # lora, mqtt, cleanup)
            if choice in registry_tags:
                if self.ctx.registry:
                    self.ctx.registry.dispatch("meshtasticd", choice)
                continue

            # Own inline dispatch
            own_dispatch = {
                "status": ("Service Status", self._meshtasticd_status),
                "test": ("Connection Test", self._connection_test),
                "restart": ("Restart Service", self._restart_meshtasticd),
                "logs": ("Service Logs", self._meshtasticd_logs),
                "view": ("View Active Config", self._view_active_config),
                "overlays": ("Config Overlays", self._view_config_overlays),
                "edit": ("Edit Config Files", self._edit_config_menu),
            }
            entry = own_dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    # ------------------------------------------------------------------
    # View methods
    # ------------------------------------------------------------------

    def _view_active_config(self):
        """Show the active meshtasticd config.yaml."""
        clear_screen()
        print("=== meshtasticd config.yaml ===\n")

        config_path = Path('/etc/meshtasticd/config.yaml')

        if not config_path.exists():
            ensure_meshtasticd_config()

        if config_path.exists():
            print(f"File: {config_path}\n")
            try:
                print(config_path.read_text())
            except PermissionError:
                print("Permission denied. Try: sudo cat /etc/meshtasticd/config.yaml")
        else:
            print("config.yaml not found!\n")
            print("Relaunch MeshForge in Admin mode (sudo) — it auto-creates")
            print("the meshtasticd config tree from the bundled templates.")

        self.ctx.wait_for_enter()

    def _view_config_overlays(self):
        """Show config.d/ overlay files (active hardware configs)."""
        clear_screen()
        print("=== config.d/ Active Hardware Configs ===\n")

        config_d = Path('/etc/meshtasticd/config.d')

        if not config_d.exists():
            ensure_meshtasticd_config()

        if not config_d.exists():
            print("config.d/ directory not found.")
            print("\nRelaunch MeshForge in Admin mode (sudo) to auto-create it.")
            self.ctx.wait_for_enter()
            return

        overlays = sorted(_glob_yaml(config_d))
        if not overlays:
            print("No active hardware configs in config.d/\n")
            print("Select your hardware from Device Templates.")
        else:
            print(f"Found {len(overlays)} active config(s):\n")
            for f in overlays:
                size = f.stat().st_size
                print(f"  {f.name} ({size} bytes)")

            print("\n" + "=" * 50)
            for f in overlays:
                print(f"\n--- {f.name} ---")
                try:
                    print(f.read_text())
                except PermissionError:
                    print("  (permission denied)")

        self.ctx.wait_for_enter()

    # ------------------------------------------------------------------
    # Service operations
    # ------------------------------------------------------------------

    def _meshtasticd_status(self):
        """Show meshtasticd service status."""
        self.ctx.dialog.infobox("Status", "Checking meshtasticd status...")

        try:
            status = check_service('meshtasticd')
            is_running = status.available
            _, is_enabled = check_systemd_service('meshtasticd')

            preset_display = "Unknown (select via Radio Presets)"
            region_display = ""
            detection_method = ""
            if is_running:
                try:
                    from utils.lora_presets import detect_meshtastic_settings
                    detection = detect_meshtastic_settings()
                    if detection and detection.get('preset'):
                        preset_display = detection['preset']
                        detection_method = detection.get('detection_method', '')
                        if detection.get('region'):
                            region_display = detection['region']
                except Exception as e:
                    logger.debug("Preset detection failed (service still running): %s", e)

            config_path = Path('/etc/meshtasticd/config.yaml')
            config_exists = config_path.exists()

            if not config_exists:
                ensure_meshtasticd_config()
                config_exists = config_path.exists()

            config_d = Path('/etc/meshtasticd/config.d')
            active_configs = _glob_yaml(config_d) if config_d.exists() else []

            available_d = Path('/etc/meshtasticd/available.d')
            available_count = len(_glob_yaml(available_d)) if available_d.exists() else 0

            text = "Meshtasticd Service Status:\n"
            if is_running:
                text += "\nService: RUNNING"
            else:
                text += "\nService: STOPPED"
                if status.fix_hint:
                    text += f"\n  Hint: {status.fix_hint}"
            text += f"\nBoot:    {'enabled' if is_enabled else 'not enabled (will not start on reboot)'}"
            text += f"\n\nPreset:  {preset_display}"
            if region_display:
                text += f"\nRegion:  {region_display}"
            if detection_method:
                text += f"\n  (detected via {detection_method})"
            elif is_running and preset_display.startswith("Unknown"):
                text += "\n  (CLI detection unavailable — select preset manually)"
            text += f"\n\nConfig File: {config_path}"
            text += f"\nConfig Exists: {'Yes' if config_exists else 'No — relaunch in Admin mode to create'}"
            text += f"\nAvailable Templates: {available_count}"
            text += f"\n\nActive Hardware Configs: {len(active_configs)}"

            for cfg in active_configs[:5]:
                text += f"\n  - {cfg.name}"

            if len(active_configs) > 5:
                text += f"\n  ... and {len(active_configs) - 5} more"

            if not active_configs and available_count > 0:
                text += "\n  (none — select from Device Templates)"

            self.ctx.dialog.msgbox("Meshtasticd Status", text)

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to get status:\n{e}")

    # ------------------------------------------------------------------
    # Edit / restart
    # ------------------------------------------------------------------

    def _offer_restart(self, message: str):
        """Offer to restart meshtasticd after a config change."""
        if self.ctx.dialog.yesno(
            "Restart Service?",
            f"{message}\n\n"
            f"Saved to: {OVERLAY_PATH}\n"
            "(config.yaml unchanged)\n\n"
            "Restart meshtasticd to apply?",
            default_no=False
        ):
            self._restart_meshtasticd()

    def _edit_config_menu(self):
        """Edit config files directly."""
        choices = [
            ("main", "Main Config (/etc/meshtasticd/config.yaml)"),
            ("active", "Active Hardware Configs"),
            ("templates", "Hardware Templates"),
            ("back", "Back"),
        ]

        choice = self.ctx.dialog.menu(
            "Edit Config Files",
            "Edit meshtasticd configuration files:\n\n"
            "Opens in nano editor.\n"
            "Save: Ctrl+O, Exit: Ctrl+X",
            choices
        )

        if choice is None or choice == "back":
            return

        if choice == "main":
            self._edit_file('/etc/meshtasticd/config.yaml')
        elif choice == "active":
            self._edit_config_d()
        elif choice == "templates":
            self._edit_available_d()

    def _edit_file(self, path: str):
        """Edit a file with nano."""
        if not Path(path).exists():
            if '/etc/meshtasticd/' in path:
                try:
                    from core.meshtasticd_config import MeshtasticdConfig
                    config_mgr = MeshtasticdConfig()
                    config_mgr.ensure_structure()
                except Exception as e:
                    logger.debug("Auto-create config failed: %s", e)
            if not Path(path).exists():
                self.ctx.dialog.msgbox("Error", f"File not found:\n{path}")
                return

        # In-Domain (MF018): edit the config IN-APP — was a `nano` spawn that
        # ejected the operator to a shell. Only prompt for a restart on a real
        # saved change (the editbox tells us; nano never could).
        from config_edit import edit_config_in_app
        changed = edit_config_in_app(
            self.ctx, path, title="Edit meshtasticd config")

        if changed and self.ctx.dialog.yesno(
            "Restart Service?",
            "Config file modified.\n\n"
            "Restart meshtasticd to apply changes?",
            default_no=False
        ):
            self._restart_meshtasticd()

    def _edit_config_d(self):
        """Edit files in config.d."""
        config_d = Path('/etc/meshtasticd/config.d')
        if not config_d.exists():
            self.ctx.dialog.msgbox("Error", f"Directory not found:\n{config_d}")
            return

        configs = _glob_yaml(config_d)
        if not configs:
            self.ctx.dialog.msgbox("Info", "No active configs in config.d/")
            return

        choices = [(str(cfg), cfg.name) for cfg in sorted(configs)]
        choices.append(("remove", "Remove a config from config.d/"))

        choice = self.ctx.dialog.menu(
            "Active Configs",
            "Select config to edit or remove (Cancel to go back):",
            choices
        )

        if choice == "remove":
            self._remove_active_hardware_config(config_d, set())
        elif choice:
            self._edit_file(choice)

    def _remove_active_hardware_config(self, config_d: Path, active_names: set):
        """Remove active hardware config(s) from config.d/ (edit menu context)."""
        hw_files = sorted(
            f for f in _glob_yaml(config_d)
            if not _is_overrides(f)
        )
        if not hw_files:
            self.ctx.dialog.msgbox("Info", "No active hardware configs to remove.")
            return

        choices = [(f.name, f.stem) for f in hw_files]
        choices.append(("back", "Back"))

        choice = self.ctx.dialog.menu(
            "Remove Config",
            "Select config to remove from config.d/:",
            choices
        )

        if not choice or choice == "back":
            return

        try:
            target = config_d / choice
            target.unlink()
            logger.info("Removed hardware config: %s", choice)
            self.ctx.dialog.msgbox("Removed", f"Removed: {choice}")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to remove:\n{e}")

    def _edit_available_d(self):
        """Edit files in available.d."""
        available_d = Path('/etc/meshtasticd/available.d')
        if not available_d.exists():
            self.ctx.dialog.msgbox("Error", f"Directory not found:\n{available_d}")
            return

        configs = _glob_yaml(available_d)
        if not configs:
            self.ctx.dialog.msgbox("Info", "No templates in available.d/")
            return

        choices = [(str(cfg), cfg.name) for cfg in sorted(configs)]

        choice = self.ctx.dialog.menu(
            "Hardware Templates",
            "Select template to view (Cancel to go back):",
            choices
        )

        if choice:
            self._edit_file(choice)

    # ------------------------------------------------------------------
    # Connection test & logs
    # ------------------------------------------------------------------

    def _connection_test(self):
        """Quick connectivity test for meshtasticd (service, TCP, HTTP)."""
        import socket

        clear_screen()
        print("=== meshtasticd Connection Test ===\n")

        # 1. Service status
        status = check_service('meshtasticd')
        if status.available:
            print("  [OK]   Service: running")
        else:
            print(f"  [FAIL] Service: {status.message}")
            if status.fix_hint:
                print(f"         Hint: {status.fix_hint}")

        # 2. TCP port 4403
        tcp_ok = False
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(3)
                sock.connect(('127.0.0.1', 4403))
            tcp_ok = True
            print("  [OK]   TCP port 4403: reachable")
        except OSError as e:
            print(f"  [FAIL] TCP port 4403: {e}")

        # 3. HTTP API
        if tcp_ok:
            try:
                http = _get_http_client()
                info = http.get_device_info()
                if info:
                    print("  [OK]   HTTP API: responsive")
                    fw = info.get('firmwareVersion', 'unknown')
                    hw = info.get('hwModel', 'unknown')
                    print(f"         Firmware: {fw} | Hardware: {hw}")
                else:
                    print("  [WARN] HTTP API: no data returned")
            except Exception as e:
                print(f"  [FAIL] HTTP API: {e}")
        else:
            print("  [SKIP] HTTP API: TCP not available")

        print()
        self.ctx.wait_for_enter()

    def _meshtasticd_logs(self):
        """Show recent meshtasticd service logs via journalctl."""
        clear_screen()
        print("=== meshtasticd Service Logs (last 100 lines) ===\n")

        try:
            result = subprocess.run(
                ['journalctl', '-u', 'meshtasticd', '-n', '100',
                 '--no-pager', '--output=short-iso'],
                capture_output=True, text=True, timeout=10
            )
            output = result.stdout.strip()
            if output:
                print(output)
            else:
                print("No log entries found for meshtasticd.")
                print("Service may not have started yet.")
        except FileNotFoundError:
            print("journalctl not available (not a systemd system).")
        except subprocess.TimeoutExpired:
            print("Timed out reading logs.")
        except Exception as e:
            print(f"Failed to read logs: {e}")

        print()
        self.ctx.wait_for_enter()

    def _restart_meshtasticd(self):
        """Restart meshtasticd service and re-apply saved device settings."""
        confirm = self.ctx.dialog.yesno(
            "Restart Service",
            "Restart meshtasticd?\n\n"
            "This will:\n"
            "1. Reload systemd daemon\n"
            "2. Restart meshtasticd service\n"
            "3. Wait for TCP readiness\n"
            "4. Re-apply saved device settings",
            default_no=True
        )

        if not confirm:
            return

        try:
            self.ctx.dialog.infobox("Restarting", "Restarting meshtasticd...")

            success, msg = apply_config_and_restart('meshtasticd')
            if not success:
                self.ctx.dialog.msgbox("Error", f"Restart failed:\n{msg}")
                return

            from utils.device_config_store import load_device_config, apply_saved_config
            saved = load_device_config()

            if not saved:
                self.ctx.dialog.msgbox("Success", f"meshtasticd restarted.\n\n{msg}")
                return

            sections = []
            for section, values in saved.items():
                items = [f"  {k}: {v}" for k, v in values.items()]
                sections.append(f"{section}:\n" + "\n".join(items))
            summary = "\n".join(sections)

            reapply = self.ctx.dialog.yesno(
                "Re-apply Settings?",
                f"meshtasticd restarted.\n\n"
                f"Saved device settings found:\n{summary}\n\n"
                "Re-apply these settings now?\n"
                "(Device config may have reverted to defaults)",
                default_no=False
            )

            if not reapply:
                self.ctx.dialog.msgbox("Info",
                    "Settings NOT re-applied.\n\n"
                    "You can re-apply manually via\n"
                    "Radio Presets or Owner Name.")
                return

            self.ctx.dialog.infobox("Applying", "Re-applying saved device settings...")

            from core.meshtastic_cli import get_cli as _get_cli
            cli = _get_cli()
            all_ok, results = apply_saved_config(cli)

            if all_ok:
                self.ctx.dialog.msgbox("Success",
                    "meshtasticd restarted and settings restored!\n\n"
                    f"{results}")
            else:
                self.ctx.dialog.msgbox("Partial Success",
                    "Some settings could not be restored:\n\n"
                    f"{results}\n\n"
                    "Check the web UI at :9443 to verify.")

        except subprocess.TimeoutExpired:
            self.ctx.dialog.msgbox("Error", "Restart timed out")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Restart failed:\n{e}")
