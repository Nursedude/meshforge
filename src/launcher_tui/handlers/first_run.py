"""
First-Run Handler — Initial setup wizard for new MeshForge installations.

Converted from first_run_mixin.py as part of the mixin-to-registry migration (Batch 8).

Guides new users through MeshForge setup:
1. Connection type selection (SPI/USB/Network)
2. Hardware-specific configuration
3. Service status check
4. Basic configuration

Implements LifecycleHandler for on_startup to auto-trigger the wizard on first run.
"""

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, List, Dict, Tuple

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)


def _install_overlay(src: Path, dst: Path) -> list:
    """Install a HAT template into config.d/ — sanitized, never a raw copy.

    An overlay overrides config.yaml, so Webserver/TCP/... must not reach
    config.d (#58: `Webserver: Port: 443` moved the API off :9443).
    Returns the stripped top-level keys.
    """
    from core.meshtasticd_templates import sanitize_hat_overlay
    content, stripped = sanitize_hat_overlay(src.read_text())
    dst.write_text(content)
    if stripped:
        logger.warning(
            "HAT overlay %s: stripped %s before install (belongs in "
            "/etc/meshtasticd/config.yaml)", src.name, ", ".join(stripped))
    return stripped

from utils.paths import get_real_user_home
from utils.safe_import import safe_import

# Import service check
check_service, apply_config_and_restart, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_service', 'apply_config_and_restart'
)

# Import device scanner
from utils.device_scanner import DeviceScanner

# Import startup checker for hardware detection
StartupChecker, _HAS_STARTUP_CHECKER = safe_import('startup_checks', 'StartupChecker')


# =========================================================================
# Hardware Configuration Templates
# =========================================================================

from core.meshtasticd_templates import classify_overlay  # noqa: E402

# How a standalone USB node joins the gateway, per twin (MeshAnchor's
# Meshtastic leg has no serial transport; MeshForge's does — gateway/config.py
# MeshtasticConfig.connection_type). A {tty} placeholder.
USB_NODE_GATEWAY_NOTE = (
    "  - Gateway: the Meshtastic<->RNS message bridge reaches\n"
    "    Meshtastic through meshtasticd (TCP/MQTT) only. A USB node\n"
    "    CAN be a leg of, in ~/.config/meshforge/gateway.json:\n"
    '      mesh_bridge.secondary: connection_type "serial",\n'
    '                             serial_device "{tty}"'
)


# SPI HAT configurations with their config file names
# Legacy fallback — used only when /etc/meshtasticd/available.d/ is empty.
# Filenames must match actual templates in templates/available.d/.
SPI_HARDWARE_CONFIGS = {
    'meshadv-mini': {
        'name': 'MeshAdv-Mini',
        'description': 'MeshAdv-Mini Pi HAT (recommended for Raspberry Pi)',
        'config_file': 'meshadv-mini.yaml',
        'requires_spi': True,
        'requires_overlay': 'spi0-0cs',
    },
    'waveshare-sx1262': {
        'name': 'Waveshare SX1262 HAT',
        'description': 'Waveshare SX1262 868/915M LoRa HAT',
        'config_file': 'waveshare-sx1262.yaml',
        'requires_spi': True,
        'requires_overlay': None,
    },
    'rak-hat': {
        'name': 'RAK WisLink HAT',
        'description': 'RAKwireless WisLink LoRa HAT',
        'config_file': 'rak-hat-spi.yaml',
        'requires_spi': True,
        'requires_overlay': None,
    },
    'ebyte-e22': {
        'name': 'Ebyte E22 Module',
        'description': 'Ebyte E22-900M/E22-400M LoRa Module',
        'config_file': 'ebyte-e22-900m30s.yaml',
        'requires_spi': True,
        'requires_overlay': None,
    },
    'custom-spi': {
        'name': 'Custom SPI Device',
        'description': 'Other SPI-connected LoRa module',
        'config_file': None,  # Manual configuration required
        'requires_spi': True,
        'requires_overlay': None,
    },
}

# Meshtastic regions for frequency configuration
MESHTASTIC_REGIONS = [
    ('US', 'United States (915 MHz)'),
    ('EU_868', 'Europe 868 MHz'),
    ('EU_433', 'Europe 433 MHz'),
    ('CN', 'China (470-510 MHz)'),
    ('JP', 'Japan (920 MHz)'),
    ('ANZ', 'Australia/NZ (915/928 MHz)'),
    ('KR', 'Korea (920 MHz)'),
    ('TW', 'Taiwan (923 MHz)'),
    ('RU', 'Russia (868 MHz)'),
    ('IN', 'India (865-867 MHz)'),
    ('NZ_865', 'New Zealand 865 MHz'),
    ('TH', 'Thailand (920 MHz)'),
    ('LORA_24', 'LoRa 2.4 GHz (worldwide)'),
    ('UA_433', 'Ukraine 433 MHz'),
    ('UA_868', 'Ukraine 868 MHz'),
    ('MY_433', 'Malaysia 433 MHz'),
    ('MY_919', 'Malaysia 919 MHz'),
    ('SG_923', 'Singapore 923 MHz'),
    ('UNSET', 'Unset (configure later)'),
]


class FirstRunHandler(BaseHandler):
    """TUI handler for first-run wizard and initial setup."""

    handler_id = "first_run"
    menu_section = "configuration"

    FIRST_RUN_FLAG = ".meshforge_setup_complete"

    def menu_items(self):
        return [
            ("wizard", "Setup Wizard        First-run wizard", None),
        ]

    def execute(self, action):
        if action == "wizard":
            self._run_first_run_wizard()

    # -- LifecycleHandler --------------------------------------------------

    def on_startup(self):
        """Check for first run and offer setup wizard.

        Idempotent per process: main.py calls this explicitly (so the wizard
        fires even in daemon mode, where startup_all() is skipped) AND
        registry.startup_all() calls it again on non-daemon boxes — without
        the guard a user who declined the wizard was re-prompted immediately
        in the same launch (2026-08-14 audit W10).
        """
        if getattr(self, '_startup_ran', False):
            return
        self._startup_ran = True
        if self._check_first_run():
            self._run_first_run_wizard()

    def on_shutdown(self):
        """No cleanup needed for first-run handler."""
        pass

    # -- Template classification -------------------------------------------

    def _classify_templates(self, available_d: Path) -> Tuple[List[Path], List[Path]]:
        """Split available.d overlays into (CH341 USB, SPI) by CONTENT.

        The filename said '-usb' for BOTH ignored `Serial:` files and real
        CH341 `lora-usb-*` configs; an overlay carrying no key meshtasticd
        reads is dropped — it is not a radio config (B7, 2026-09-30).
        """
        usb_templates, spi_templates = [], []
        if not available_d.exists():
            return usb_templates, spi_templates
        for tmpl in sorted(available_d.glob('*.yaml')):
            try:
                kind = classify_overlay(tmpl.read_text(errors='replace'))
            except OSError as e:
                logger.debug("classify %s failed: %s", tmpl, e)
                continue
            if kind == 'ch341':
                usb_templates.append(tmpl)
            elif kind == 'spi':
                spi_templates.append(tmpl)
        return usb_templates, spi_templates

    # -- Existing config check ---------------------------------------------

    def _check_existing_configs(self, config_d: Path, config_type: str) -> bool:
        """Check for existing configs in config.d and ask user if they want to change.

        Args:
            config_d: Path to /etc/meshtasticd/config.d
            config_type: 'USB' or 'SPI HAT' for display purposes

        Returns:
            True if we should proceed with template selection,
            False if user wants to keep existing config.
        """
        if not config_d.exists():
            return True

        existing_configs = list(config_d.glob('*.yaml'))
        if not existing_configs:
            return True

        config_names = ", ".join(f.name for f in existing_configs)
        change = self.ctx.dialog.yesno(
            f"Existing {config_type} Config Found",
            f"You already have hardware configured:\n\n"
            f"  {config_names}\n\n"
            f"Location: {config_d}\n\n"
            "Do you want to change it?",
            default_no=True
        )

        if not change:
            self.ctx.dialog.msgbox(
                "Keeping Existing Config",
                f"Your current config will be kept:\n\n"
                f"  {config_names}\n\n"
                "You can change this later from:\n"
                "  Configuration > meshtasticd Config > Hardware"
            )
            return False

        return True

    # -- Template structure ------------------------------------------------

    def _ensure_template_structure(self):
        """Ensure /etc/meshtasticd directory structure and templates exist."""
        try:
            from core.meshtasticd_config import MeshtasticdConfig
            config_mgr = MeshtasticdConfig()
            config_mgr.ensure_structure()
        except PermissionError:
            logger.debug("Cannot auto-create templates (no root), using existing")
        except ImportError:
            logger.warning("core.meshtasticd_config not available, skipping template setup")
        except (OSError, ValueError) as e:
            logger.warning("Template auto-creation failed: %s", e)

    # -- First-run state ---------------------------------------------------

    def _check_first_run(self) -> bool:
        """Check if this is a first run (no setup flag exists).

        Non-interactive contexts (systemd unit, cron, piped stdin) must
        never get the wizard: nobody can answer the prompt, and under a
        hardened unit the marker write can be impossible — that exact shape
        crash-looped meshforge.service on moc for 26k restarts (2026-06-03,
        fixed in launcher.py; this is the handler-path mirror of that guard).
        """
        try:
            if sys.stdin is None or not sys.stdin.isatty():
                return False
        except (ValueError, OSError):
            # closed/absent stdin (hardened unit, fd 0 missing) — the exact
            # context this guard defends; raising here would reintroduce
            # the crash-loop through its own mirror (review F8)
            return False
        config_dir = get_real_user_home() / ".config" / "meshforge"
        flag_file = config_dir / self.FIRST_RUN_FLAG
        return not flag_file.exists()

    def _mark_setup_complete(self):
        """Mark setup as complete."""
        config_dir = get_real_user_home() / ".config" / "meshforge"
        config_dir.mkdir(parents=True, exist_ok=True)
        flag_file = config_dir / self.FIRST_RUN_FLAG
        flag_file.touch()

    # -- Main wizard flow --------------------------------------------------

    def _run_first_run_wizard(self) -> bool:
        """
        Run the first-run wizard.
        Returns True if completed, False if skipped.

        Enhanced in v0.4.8 with SPI/USB selection first.
        """
        # Welcome
        result = self.ctx.dialog.yesno(
            "Welcome to MeshForge!",
            "It looks like this is your first time running MeshForge.\n\n"
            "Would you like to run the setup wizard?\n\n"
            "The wizard will:\n"
            "• Help you select your connection type (SPI/USB)\n"
            "• Configure your hardware\n"
            "• Set up mesh services\n\n"
            "You can run this wizard again from Configuration > Setup Wizard."
        )

        if not result:
            # User skipped - mark as complete anyway
            skip = self.ctx.dialog.yesno(
                "Skip Setup",
                "Skip the wizard and mark setup as complete?\n\n"
                "You can always run it later from Configuration."
            )
            if skip:
                self._mark_setup_complete()
            return False

        # Step 1: Connection Type Selection (NEW in v0.4.8)
        connection_type = self._wizard_step_connection_type()

        if connection_type == 'skip':
            self._mark_setup_complete()
            return False

        # Step 2: Hardware-specific configuration
        if connection_type == 'spi':
            self._wizard_step_spi_config()
        elif connection_type == 'usb':
            if self._wizard_step_usb_config() == 'node':
                # A standalone node runs without meshtasticd: the region and
                # service steps below both act through meshtasticd.
                self._wizard_complete()
                return True
        elif connection_type == 'network':
            self._wizard_step_network_config()

        # Step 3: Region Selection
        self._wizard_step_region()

        # Step 4: Service Configuration
        self._wizard_step_services()

        # Step 5: Completion
        self._wizard_complete()

        return True

    # -- Step 1: Connection type -------------------------------------------

    def _wizard_step_connection_type(self) -> str:
        """
        Step 1: Select connection type (SPI/USB/Network).

        Returns: 'spi', 'usb', 'network', 'later', or 'skip'
        """
        # Auto-detect available options
        spi_available = len(list(Path('/dev').glob('spidev*'))) > 0
        usb_devices = self._find_usb_serial_devices()
        is_pi = self._is_raspberry_pi()

        # Build description based on detected hardware
        desc = "How is your Meshtastic radio connected?\n\n"

        if spi_available:
            desc += "  SPI interface detected\n"
        elif is_pi:
            desc += "  Raspberry Pi detected (SPI can be enabled)\n"

        if usb_devices:
            # Show specific device names when possible
            device_names = []
            for dev in usb_devices:
                name = dev.get('name', 'Unknown')
                if name and name != 'Unknown':
                    device_names.append(name)

            if device_names:
                desc += f"  USB detected: {', '.join(device_names[:3])}\n"
            else:
                desc += f"  {len(usb_devices)} USB serial device(s) found\n"

        desc += "\nSelect your connection type:"

        choices = [
            ("spi", "SPI HAT         MeshAdv-Mini, Waveshare, etc."),
            ("usb", "USB Serial      T-Beam, Heltec, RAK via USB"),
            ("network", "Network         Remote meshtasticd (TCP)"),
            ("later", "Configure Later Skip hardware setup"),
        ]

        choice = self.ctx.dialog.menu(
            "Step 1: Connection Type",
            desc,
            choices
        )

        if choice is None:
            return 'skip'

        return choice

    # -- Step 2a: SPI config -----------------------------------------------

    def _wizard_step_spi_config(self):
        """Configure SPI HAT hardware."""
        # Check if SPI is enabled
        spi_available = len(list(Path('/dev').glob('spidev*'))) > 0

        if not spi_available:
            # Offer to enable SPI
            if self._is_raspberry_pi():
                if self._offer_enable_spi():
                    self.ctx.dialog.msgbox(
                        "SPI Enabled",
                        "SPI has been enabled.\n\n"
                        "A REBOOT is required for changes to take effect.\n\n"
                        "After reboot, run the wizard again to complete setup."
                    )
                    return
                else:
                    self.ctx.dialog.msgbox(
                        "SPI Required",
                        "SPI HATs require SPI to be enabled.\n\n"
                        "You can enable SPI later using:\n"
                        "  sudo raspi-config\n\n"
                        "Or from System > Hardware in MeshForge."
                    )
                    return
            else:
                self.ctx.dialog.msgbox(
                    "SPI Not Available",
                    "No SPI interface detected on this system.\n\n"
                    "SPI HATs are typically used with Raspberry Pi."
                )
                return

        self._ensure_template_structure()

        config_d = Path('/etc/meshtasticd/config.d')
        available_d = Path('/etc/meshtasticd/available.d')

        # Check for existing config (uses *.yaml, not broken lora-*.yaml)
        if not self._check_existing_configs(config_d, 'SPI HAT'):
            return

        # Build choices from available.d SPI templates
        choices = []
        _, spi_templates = self._classify_templates(available_d)

        if spi_templates:
            active_configs = set()
            if config_d.exists():
                active_configs = {f.name for f in config_d.glob('*.yaml')}

            for tmpl in spi_templates:
                is_active = tmpl.name in active_configs
                status = " [ACTIVE]" if is_active else ""
                display_name = tmpl.stem.replace('-', ' ').title()
                choices.append((tmpl.name, f"{display_name}{status}"))

        # If no templates found in available.d, fall back to hardcoded list
        if not choices:
            for hw_id, hw_info in SPI_HARDWARE_CONFIGS.items():
                choices.append((hw_id, f"{hw_info['name']:<20} {hw_info['description'][:30]}"))

        # Always add manual option
        choices.append(("custom-spi", "Custom/Other SPI Device"))

        choice = self.ctx.dialog.menu(
            "Step 2: Select SPI Hardware",
            "Select your HAT from meshtasticd templates:\n\n"
            f"Templates: {available_d}" if available_d.exists() else
            "Which SPI HAT are you using?",
            choices
        )

        if choice is None or choice == 'custom-spi':
            self.ctx.dialog.msgbox(
                "Manual Configuration",
                "For custom SPI hardware, you'll need to:\n\n"
                "1. Create a config file in /etc/meshtasticd/config.d/\n"
                "2. Configure the SPI pins and radio chip type\n\n"
                "See: Configuration > meshtasticd Config"
            )
            return

        # Check if choice is a filename from available.d or a hardcoded key
        if choice.endswith('.yaml') and available_d.exists():
            # It's a template from available.d
            src = available_d / choice
            if src.exists():
                self._apply_hardware_config_from_file(src, config_d)
        else:
            # It's a hardcoded config key
            hw_config = SPI_HARDWARE_CONFIGS.get(choice)
            if hw_config and hw_config['config_file']:
                self._apply_hardware_config(hw_config)

    # -- Apply config from template file -----------------------------------

    def _apply_hardware_config_from_file(self, src: Path, config_d: Path):
        """Apply a hardware config directly from available.d template."""
        try:
            config_d.mkdir(parents=True, exist_ok=True)
            dst = config_d / src.name

            _install_overlay(src, dst)

            # Restart meshtasticd — honest-signal: gate on the real (ok, msg).
            ok, msg = apply_config_and_restart('meshtasticd')
            self.ctx.report_action(
                ok,
                "Configuration Applied",
                f"Applied HAT configuration:\n\n"
                f"Template: {src.name}\n"
                f"Config: {dst}\n\n"
                f"meshtasticd has been restarted.\n"
                f"Check meshtasticd in Service Control.",
                "Config Applied — Restart FAILED",
                f"The template was copied to:\n  {dst}\n\n"
                f"But meshtasticd did NOT restart cleanly:\n{msg}\n\n"
                f"The radio may be down or still on the old config.\n"
                f"Check meshtasticd in Service Control.",
            )
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to apply config: {e}")

    # -- USB auto-detect ---------------------------------------------------

    def _detect_usb_radio(self) -> 'tuple[Optional[str], Optional[str], Optional[str]]':
        """(kind, device_name, tty) for the attached USB radio, or Nones.

        kind comes from HardwareDetector.usb_radio_kind() where that module
        exists (MeshAnchor has none — then this returns Nones and the wizard
        asks). The two scan lists are JOINED by VID:PID so a device keeps its
        tty (lsusb rows carry none), and a CH341 wins over any tty device —
        a GPS or RNode beside the stick must not hide it (reader pair,
        2026-09-30). The ID never picks an overlay: every CH341 is 1a86:5512.
        """
        try:
            from config.hardware import HardwareDetector
        except ImportError:
            logger.debug("config.hardware not available for USB auto-detect")
            return None, None, None
        try:
            results = DeviceScanner().scan_all()
            ttys, names = {}, {}
            for p in results.get('serial_ports', []):
                vid, pid = getattr(p, 'usb_vendor', ''), getattr(p, 'usb_product', '')
                if vid and pid:
                    key = f"{vid}:{pid}".lower()
                    ttys.setdefault(key, getattr(p, 'device', None))
                    names.setdefault(key, getattr(p, 'description', ''))
            for d in results.get('usb_devices', []):
                vid, pid = getattr(d, 'vendor_id', ''), getattr(d, 'product_id', '')
                if vid and pid:
                    names.setdefault(f"{vid}:{pid}".lower(), getattr(d, 'description', ''))
            kinds = {k: HardwareDetector.usb_radio_kind(k) for k in names}
            for want in ('ch341', 'node'):
                for key, kind in kinds.items():
                    if kind == want:
                        name = (HardwareDetector.get_device_name_for_usb_id(key)
                                or names.get(key) or 'USB device')
                        return kind, name, ttys.get(key)
        except Exception as e:
            logger.debug("USB auto-detect failed: %s", e)
        return None, None, None

    # -- Step 2b: USB config -----------------------------------------------

    def _wizard_step_usb_config(self) -> Optional[str]:
        """USB radio: route by what the device IS, write only what works.

        meshtasticd 2.7.26 has no `Serial:` key, so the overlay this step used
        to write for USB nodes configured nothing (B7, 2026-09-30; measured on
        the binary with --output-yaml). Two kinds:
          - CH341 USB-SPI board: meshtasticd drives it via a `Lora: … spidev:
            ch341` overlay — offer those (the moc5 route), sanitized.
          - standalone node (RAK/Heltec/T-Beam/Station G2): meshtasticd cannot
            drive it; it is reached over serial. Nothing is written.
        A detected CH341 is unambiguous; a tty device is only CONFIRMED by the
        operator — RNodes and GPS receivers use the same USB-UART chips.
        Returns the kind taken ('ch341' / 'node') or None if cancelled.
        """
        kind, device_name, tty = self._detect_usb_radio()
        if kind != 'ch341':
            seen = (f"Detected: {device_name}" + (f" on {tty}" if tty else "")
                    + "\n(a USB serial device: a Meshtastic node, or an\n"
                    "RNode / GPS on the same kind of chip)\n\n") if kind else ""
            kind = self.ctx.dialog.menu(
                "Step 2: USB Radio Type",
                seen + "Which kind of USB radio is this?",
                [("node", "Meshtastic node on USB (RAK, Heltec, T-Beam, G2)"),
                 ("ch341", "USB LoRa stick (CH341: MeshToad, MeshStick...)")])
            if kind is None:
                return None
        if kind == 'node':
            self._explain_usb_node(device_name, tty)
            return 'node'

        self._ensure_template_structure()
        config_d = Path('/etc/meshtasticd/config.d')
        available_d = Path('/etc/meshtasticd/available.d')
        usb_templates, _ = self._classify_templates(available_d)
        if not usb_templates:
            self.ctx.dialog.msgbox(
                "No CH341 Overlays",
                f"No CH341 overlay (Lora: ... spidev: ch341) in {available_d}.\n\n"
                "The meshtasticd package ships them (lora-usb-*.yaml,\n"
                "lora-meshstick-1262.yaml, ...): reinstall or upgrade\n"
                "meshtasticd, then run this step again.\n\n"
                "Nothing was written.")
            return 'ch341'
        if not self._check_existing_configs(config_d, 'USB'):
            return 'ch341'

        active = {f.name for f in config_d.glob('*.yaml')} if config_d.exists() else set()
        choices = [(t.name, t.stem + (" [ACTIVE]" if t.name in active else ""))
                   for t in usb_templates]
        desc = ("Select your CH341 USB LoRa board.\n"
                "(All CH341 boards share one USB ID; pick by board name.)")
        if device_name:
            desc = f"Detected: {device_name}\n\n" + desc
        choice = self.ctx.dialog.menu("Step 2: Select USB Hardware", desc, choices)
        if choice is None:
            return None
        src = available_d / choice
        if src.exists():
            self._apply_hardware_config_from_file(src, config_d)
        else:
            self.ctx.dialog.msgbox("Error", f"Template not found: {src}")
        return 'ch341'

    def _explain_usb_node(self, device_name: Optional[str], tty: Optional[str]):
        """A standalone node needs no meshtasticd config — say how it IS used."""
        dev = tty or "/dev/ttyACM0 or /dev/ttyUSB0"
        self.ctx.dialog.msgbox(
            "USB Meshtastic Node",
            f"{device_name or 'This radio'}, if it is a Meshtastic node, runs its\n"
            "own firmware. meshtasticd cannot drive it (it has no serial-radio\n"
            "mode), so no meshtasticd config is written, and meshtasticd is\n"
            "not needed for it.\n\n"
            "How it is reached:\n"
            "  - Channel Config tools, when meshtasticd is not running,\n"
            "    use the FIRST USB serial device (ttyACM* sort before\n"
            "    ttyUSB*) — make sure that is this one. Other radio menus\n"
            "    expect meshtasticd. Set its region with:\n"
            f"      meshtastic --port {dev.split()[0]} --set lora.region <REGION>\n"
            + USB_NODE_GATEWAY_NOTE.format(tty=tty or "/dev/ttyACM0")
            + "\n\nNothing was written.")

    # -- Step 2c: Network config -------------------------------------------

    def _wizard_step_network_config(self):
        """Configure network connection to remote meshtasticd."""
        host = self.ctx.dialog.inputbox(
            "Step 2: Network Host",
            "Enter the hostname or IP of the meshtasticd server:",
            "localhost"
        )

        if not host:
            return

        # Validate the host up-front so an invalid value never reaches
        # settings.json (the connect target). Honest, actionable signal.
        if not self.ctx.validate_hostname(host):
            self.ctx.dialog.msgbox(
                "Invalid Host",
                f"'{host}' is not a valid hostname or IP."
            )
            return

        port = self.ctx.dialog.inputbox(
            "Network Port",
            "Enter the port number (default 4403):",
            "4403"
        )

        if not port:
            port = "4403"

        # Validate the port up-front so the user gets an actionable message
        # instead of the generic "Failed to save settings: invalid literal
        # for int()" from the bare int(port) below.
        if not self.ctx.validate_port(port):
            self.ctx.dialog.msgbox(
                "Invalid Port",
                "Port must be a number between 1 and 65535."
            )
            return

        # Save network configuration
        try:
            config_dir = get_real_user_home() / ".config" / "meshforge"
            config_dir.mkdir(parents=True, exist_ok=True)

            import json
            settings_file = config_dir / "settings.json"
            settings = {}
            if settings_file.exists():
                settings = json.loads(settings_file.read_text())

            settings['meshtasticd_host'] = host
            settings['meshtasticd_port'] = int(port)

            settings_file.write_text(json.dumps(settings, indent=2))

            self.ctx.dialog.msgbox(
                "Network Configured",
                f"Configured to connect to:\n\n"
                f"  Host: {host}\n"
                f"  Port: {port}\n\n"
                f"Make sure meshtasticd is running on the remote host."
            )
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to save settings: {e}")

    # -- Step 3: Region selection ------------------------------------------

    def _wizard_step_region(self):
        """Step 3: Select regulatory region."""
        choices = [(code, desc) for code, desc in MESHTASTIC_REGIONS]

        choice = self.ctx.dialog.menu(
            "Step 3: Region Selection",
            "Select your regulatory region:\n(This determines allowed frequencies)",
            choices
        )

        if choice and choice != 'UNSET':
            # Save region to settings
            try:
                config_dir = get_real_user_home() / ".config" / "meshforge"
                config_dir.mkdir(parents=True, exist_ok=True)

                import json
                settings_file = config_dir / "settings.json"
                settings = {}
                if settings_file.exists():
                    settings = json.loads(settings_file.read_text())

                settings['region'] = choice

                settings_file.write_text(json.dumps(settings, indent=2))
            except (OSError, ValueError) as e:
                logger.debug("Failed to save region setting: %s", e)
                # The region (regulatory frequencies) silently not persisting
                # then "Setup Complete!" is the silent-failure class — tell the
                # user, like the _wizard_step_network_config sibling does.
                self.ctx.dialog.msgbox(
                    "Region Not Saved",
                    f"Could not save region {choice}:\n{e}\n\n"
                    "Your regulatory region was NOT set — re-run the wizard or "
                    "set the region from Settings before transmitting."
                )

    # -- USB device detection ----------------------------------------------

    def _find_usb_serial_devices(self) -> List[Dict[str, str]]:
        """Find USB serial devices."""
        devices = []

        for pattern in ['ttyUSB*', 'ttyACM*']:
            for path in Path('/dev').glob(pattern):
                device = {'path': str(path), 'name': 'Unknown', 'likely_meshtastic': False}

                try:
                    result = subprocess.run(
                        ['udevadm', 'info', '--query=property', str(path)],
                        capture_output=True, text=True, timeout=5
                    )
                    props = {}
                    for line in result.stdout.splitlines():
                        if '=' in line:
                            key, value = line.split('=', 1)
                            props[key] = value

                    vendor = props.get('ID_VENDOR', '')
                    model = props.get('ID_MODEL', '')
                    if vendor or model:
                        device['name'] = f"{vendor} {model}".strip()

                    device['likely_meshtastic'] = any(
                        kw in (vendor + model).lower()
                        for kw in ['meshtastic', 't-beam', 'heltec', 'rak', 'lilygo', 'cp210', 'ch340']
                    )
                except (subprocess.SubprocessError, OSError) as e:
                    logger.debug("USB device detection for %s failed: %s", path, e)

                devices.append(device)

        return devices

    # -- Apply hardware config (hardcoded) ---------------------------------

    def _apply_hardware_config(self, hw_config: dict):
        """Apply a hardware configuration file."""
        config_file = hw_config.get('config_file')
        if not config_file:
            return

        # Source and destination paths
        available_dir = Path('/etc/meshtasticd/available.d')
        config_d = Path('/etc/meshtasticd/config.d')
        source = available_dir / config_file

        if not source.exists():
            self.ctx.dialog.msgbox(
                "Config Not Found",
                f"Configuration file not found:\n{source}\n\n"
                f"You may need to install or update meshtasticd."
            )
            return

        try:
            config_d.mkdir(parents=True, exist_ok=True)
            dest = config_d / config_file

            _install_overlay(source, dest)

            # Restart meshtasticd — honest-signal: gate on the real (ok, msg).
            ok, msg = apply_config_and_restart('meshtasticd')
            self.ctx.report_action(
                ok,
                "Configuration Applied",
                f"Applied configuration for {hw_config['name']}.\n\n"
                f"Config: {dest}\n\n"
                f"meshtasticd has been restarted.\n"
                f"Check meshtasticd in Service Control.",
                "Config Applied — Restart FAILED",
                f"The config was written to:\n  {dest}\n\n"
                f"But meshtasticd did NOT restart cleanly:\n{msg}\n\n"
                f"The radio may be down or still on the old config.\n"
                f"Check meshtasticd in Service Control.",
            )
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to apply config: {e}")

    # -- Legacy hardware detection step ------------------------------------

    def _wizard_step_hardware(self):
        """Wizard Step 1: Hardware Detection"""
        self.ctx.dialog.infobox("Step 1/4", "Detecting connected hardware...")

        lines = ["Hardware Detection\n"]
        lines.append("=" * 40)

        # Check for SPI devices (HAT-based radios like MeshAdv-Pi-Hat)
        spi_devices = list(Path('/dev').glob('spidev*'))
        is_raspberry_pi = self._is_raspberry_pi()

        if spi_devices:
            lines.append(f"\n✓ SPI Interface Available:")
            for spi in spi_devices[:3]:
                lines.append(f"  • {spi.name}")
            lines.append("  (Supports HAT radios: MeshAdv-Pi-Hat, Waveshare)")
        elif is_raspberry_pi:
            # No SPI but on Pi - offer to enable it
            lines.append("\n✗ SPI Interface Not Enabled")
            lines.append("  HAT radios require SPI to be enabled.")
            self.ctx.dialog.msgbox("Step 1: Hardware", "\n".join(lines))

            # Ask if they want to enable SPI
            if self._offer_enable_spi():
                # Re-check after enable
                spi_devices = list(Path('/dev').glob('spidev*'))
                if spi_devices:
                    self.ctx.dialog.msgbox(
                        "SPI Enabled",
                        "SPI has been enabled!\n\n"
                        "A REBOOT is required for changes to take effect.\n\n"
                        "After reboot, your HAT radio will be detected."
                    )
                    lines = ["Hardware Detection\n", "=" * 40]
                    lines.append("\n✓ SPI Enabled (reboot required)")

        scanner = DeviceScanner()
        results = scanner.scan_all()

        if results['meshtastic_candidates']:
            lines.append(f"\n✓ Found {len(results['meshtastic_candidates'])} Meshtastic-compatible device(s):\n")
            for dev in results['meshtastic_candidates']:
                lines.append(f"  • {dev.description}")
        elif not spi_devices:
            lines.append("\n✗ No Meshtastic devices detected")
            lines.append("\nTo use MeshForge with a radio:")
            lines.append("  1. Connect a Meshtastic device via USB")
            lines.append("  2. Or configure meshtasticd for HAT/SPI")

        if results['serial_ports']:
            compat_ports = [p for p in results['serial_ports'] if p.meshtastic_compatible]
            if compat_ports:
                lines.append(f"\n✓ Serial Ports Available:")
                for port in compat_ports[:3]:  # Show first 3
                    lines.append(f"  • {port.device}")

        if results['recommended_port']:
            lines.append(f"\n→ Recommended port: {results['recommended_port']}")

        # Summary for new users
        if spi_devices or results.get('meshtastic_candidates'):
            lines.append("\n" + "-" * 40)
            lines.append("Hardware detected! Continue to configure.")
        else:
            lines.append("\n" + "-" * 40)
            lines.append("No radio found - you can still explore")
            lines.append("the interface and configure later.")

        self.ctx.dialog.msgbox("Step 1: Hardware", "\n".join(lines))

    # -- Raspberry Pi detection --------------------------------------------

    def _is_raspberry_pi(self) -> bool:
        """Check if running on Raspberry Pi."""
        try:
            # Check /proc/cpuinfo for Raspberry Pi
            cpuinfo = Path('/proc/cpuinfo')
            if cpuinfo.exists():
                content = cpuinfo.read_text()
                if 'Raspberry Pi' in content or 'BCM' in content:
                    return True
            # Check device tree model
            model = Path('/proc/device-tree/model')
            if model.exists():
                if 'Raspberry Pi' in model.read_text():
                    return True
        except OSError as e:
            logger.debug("RPi detection failed: %s", e)
        return False

    # -- SPI enable offer --------------------------------------------------

    def _offer_enable_spi(self) -> bool:
        """Offer to enable SPI on Raspberry Pi. Returns True if enabled."""
        result = self.ctx.dialog.yesno(
            "Enable SPI?",
            "No SPI interface detected.\n\n"
            "HAT-based radios (MeshAdv-Pi-Hat, Waveshare, etc.)\n"
            "require SPI to be enabled.\n\n"
            "Would you like to enable SPI now?\n\n"
            "(Requires reboot to take effect)"
        )

        if not result:
            return False

        self.ctx.dialog.infobox("Enabling SPI", "Configuring SPI interface...")

        try:
            import subprocess

            # Find the boot config file
            boot_config = None
            for path in ['/boot/firmware/config.txt', '/boot/config.txt']:
                if Path(path).exists():
                    boot_config = path
                    break

            if not boot_config:
                self.ctx.dialog.msgbox("Error", "Could not find boot config file.")
                return False

            # Enable SPI using raspi-config if available
            raspi_config = shutil.which('raspi-config')
            if raspi_config:
                subprocess.run(
                    ['raspi-config', 'nonint', 'set_config_var', 'dtparam=spi', 'on', boot_config],
                    timeout=30,
                    check=False
                )

            # Add dtoverlay=spi0-0cs if not present (for HAT compatibility)
            config_content = Path(boot_config).read_text()
            if 'dtoverlay=spi0-0cs' not in config_content:
                # Find dtparam=spi=on line and add overlay after it
                lines = config_content.split('\n')
                new_lines = []
                added = False
                for line in lines:
                    new_lines.append(line)
                    if 'dtparam=spi=on' in line and not added:
                        new_lines.append('dtoverlay=spi0-0cs')
                        added = True

                # If dtparam=spi=on wasn't found, add both at the end
                if not added:
                    new_lines.append('dtparam=spi=on')
                    new_lines.append('dtoverlay=spi0-0cs')

                Path(boot_config).write_text('\n'.join(new_lines))

            return True

        except subprocess.TimeoutExpired:
            self.ctx.dialog.msgbox("Error", "Timeout while configuring SPI.")
            return False
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to enable SPI: {e}")
            return False

    # -- Step 4: Service status --------------------------------------------

    def _wizard_step_services(self):
        """Wizard Step 2: Service Status"""
        self.ctx.dialog.infobox("Step 2/4", "Checking mesh services...")

        services = [
            ('meshtasticd', 'Meshtastic Daemon', 'Required for radio communication'),
            ('rnsd', 'Reticulum Network Stack', 'Optional - enables RNS mesh'),
        ]

        lines = ["Service Status\n"]
        lines.append("=" * 40)

        all_running = True
        for svc_id, svc_name, description in services:
            status = check_service(svc_id)
            if status.available:
                lines.append(f"\n✓ {svc_name}")
                lines.append(f"  Status: running")
            else:
                all_running = False
                lines.append(f"\n✗ {svc_name}")
                lines.append(f"  Status: {status.message}")
                lines.append(f"  ({description})")
                if status.fix_hint:
                    lines.append(f"  Fix: {status.fix_hint}")

        if all_running:
            lines.append("\n" + "-" * 40)
            lines.append("All services are running!")
        else:
            lines.append("\n" + "-" * 40)
            lines.append("Some services need to be started.")
            lines.append("Start them from Mesh Networks > Service Control.")

        self.ctx.dialog.msgbox("Step 2: Services", "\n".join(lines))

    # -- Legacy config step ------------------------------------------------

    def _wizard_step_config(self):
        """Wizard Step 3: Quick Configuration"""
        # Check if basic config exists
        config_dir = get_real_user_home() / ".config" / "meshforge"
        settings_file = config_dir / "settings.json"

        if settings_file.exists():
            self.ctx.dialog.msgbox(
                "Step 3: Configuration",
                "Configuration file found.\n\n"
                "Your settings are preserved from a previous install.\n\n"
                "You can modify settings from:\n"
                "  Main Menu → Settings"
            )
            return

        # Offer basic setup
        result = self.ctx.dialog.yesno(
            "Step 3: Configuration",
            "Would you like to configure basic settings?\n\n"
            "This includes:\n"
            "• Callsign (for ham operators)\n"
            "• Default region\n"
            "• UI preferences"
        )

        if result:
            # Get callsign
            callsign = self.ctx.dialog.inputbox(
                "Callsign",
                "Enter your callsign (optional):",
                ""
            )

            if callsign:
                # Save to settings
                try:
                    from utils.common import SettingsManager
                    settings = SettingsManager("meshforge")
                    settings.set("callsign", callsign.upper())
                    self.ctx.report_action(
                        settings.save(),
                        "Saved", f"Callsign set to: {callsign.upper()}",
                        "Save Failed",
                        f"Callsign {callsign.upper()} could NOT be saved — it won't persist.",
                    )
                except Exception as e:
                    self.ctx.dialog.msgbox("Note", f"Could not save settings: {e}")

    # -- Wizard completion -------------------------------------------------

    def _wizard_complete(self):
        """Wizard completion"""
        self._mark_setup_complete()

        self.ctx.dialog.msgbox(
            "Setup Complete!",
            "MeshForge is ready to use!\n\n"
            "Next Steps:\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "1. Service Manager → Start meshtasticd\n"
            "2. Meshtasticd Config → Configure your radio\n"
            "3. Diagnostics → Verify everything works\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "\nNeed Help?\n"
            "  • Run diagnostics for system health\n"
            "  • Check GitHub issues for known fixes\n"
            "  • HAM community: 73s and good luck!\n\n"
            "Press Enter to continue to main menu."
        )

    # -- Settings menu entry point -----------------------------------------

    def _settings_run_wizard(self):
        """Run wizard from settings menu."""
        result = self.ctx.dialog.yesno(
            "Run Setup Wizard",
            "Run the first-run setup wizard again?\n\n"
            "This will walk through hardware detection,\n"
            "service checks, and basic configuration."
        )

        if result:
            self._run_first_run_wizard()
