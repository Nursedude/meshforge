"""
Hardware Handler — Hardware detection and configuration.

Converted from hardware_menu_mixin.py as part of the mixin-to-registry migration.
"""

import logging
import shutil
import subprocess
from pathlib import Path

from backend import clear_screen
from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)


class HardwareHandler(BaseHandler):
    """TUI handler for hardware detection and configuration."""

    handler_id = "hardware"
    menu_section = "system"

    def menu_items(self):
        return [
            ("hardware", "Hardware            Detect SPI/I2C/USB", None),
        ]

    def execute(self, action):
        if action == "hardware":
            self._hardware_menu()

    def _hardware_menu(self):
        while True:
            choices = [
                ("detect", "Detect Hardware     SPI, I2C, Serial, USB"),
                ("rnode", "RNode Setup         RNode device detection"),
                ("spi", "Enable SPI          For HAT radios"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Hardware",
                "Hardware detection and configuration:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "rnode":
                # Delegate to RNodeHandler via import
                self.ctx.safe_call("RNode Setup", self._rnode_submenu)
                continue

            dispatch = {
                "detect": ("Detect Hardware", self._detect_hardware),
                "spi": ("Enable SPI", self._enable_spi),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _rnode_submenu(self):
        """Delegate to RNodeHandler."""
        from handlers.rnode import RNodeHandler
        handler = RNodeHandler()
        handler.set_context(self.ctx)
        handler._rnode_menu()

    def _detect_hardware(self):
        clear_screen()
        print("=== Hardware Detection ===\n")

        try:
            from commands.hardware import (
                classify_spi_bus, classify_i2c_bus,
                match_config_to_hardware, get_radio_health,
            )
            has_helpers = True
        except ImportError:
            has_helpers = False

        GREEN = "\033[0;32m"
        DIM = "\033[2m"
        YELLOW = "\033[0;33m"
        RESET = "\033[0m"

        # --- SPI ---
        spi_devices = list(Path('/dev').glob('spidev*'))
        if spi_devices:
            spi_labels = []
            for d in spi_devices:
                label = d.name
                if has_helpers:
                    info = classify_spi_bus(d)
                    if info.get('parent_name'):
                        label += f" (via {info['parent_name']})"
                    elif not info.get('is_native'):
                        label += " (USB-bridged)"
                spi_labels.append(label)
            print(f"  {GREEN}●{RESET} SPI: {', '.join(spi_labels)}")
        else:
            print(f"  {DIM}○{RESET} SPI: not enabled")

        # --- I2C ---
        i2c_devices = list(Path('/dev').glob('i2c-*'))
        if i2c_devices:
            i2c_labels = []
            for d in i2c_devices:
                label = d.name
                if has_helpers:
                    info = classify_i2c_bus(d)
                    if info.get('parent_name'):
                        label += f" (via {info['parent_name']})"
                    elif not info.get('is_native'):
                        label += " (USB-bridged)"
                i2c_labels.append(label)
            print(f"  {GREEN}●{RESET} I2C: {', '.join(i2c_labels)}")
        else:
            print(f"  {DIM}○{RESET} I2C: not enabled")

        # --- Serial ---
        serial_ports = list(Path('/dev').glob('ttyUSB*')) + list(Path('/dev').glob('ttyACM*'))
        if serial_ports:
            print(f"  {GREEN}●{RESET} Serial: {', '.join(d.name for d in serial_ports)}")
        else:
            print(f"  {DIM}○{RESET} Serial: no USB serial devices")

        # --- GPIO ---
        gpio_available = Path('/sys/class/gpio').exists()
        marker = f"{GREEN}●{RESET}" if gpio_available else f"{DIM}○{RESET}"
        print(f"  {marker} GPIO: {'available' if gpio_available else 'not available'}")

        # --- USB Devices ---
        print("\nUSB Devices:")
        try:
            from config.hardware import HardwareDetector
            has_detector = True
        except ImportError:
            has_detector = False

        try:
            result = subprocess.run(
                ['lsusb'], capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                root_hub_count = 0
                for line in result.stdout.strip().split('\n'):
                    if not line.strip():
                        continue
                    if 'root hub' in line.lower():
                        root_hub_count += 1
                        continue
                    identified = False
                    if has_detector:
                        for vid_pid in HardwareDetector.KNOWN_USB_MODULES:
                            vid, pid = vid_pid.split(':')
                            if vid.lower() in line.lower() and pid.lower() in line.lower():
                                devices = HardwareDetector.KNOWN_USB_MODULES[vid_pid]
                                device_names = ', '.join(devices.get('common_devices', []))
                                print(f"  {GREEN}●{RESET} {vid_pid} {devices['name']}"
                                      f" → {device_names}")
                                identified = True
                                break
                    if not identified:
                        print(f"  {DIM}  {line.strip()}{RESET}")
                if root_hub_count > 0:
                    print(f"  {DIM}  ({root_hub_count} root hub(s) hidden){RESET}")
        except Exception:
            subprocess.run(['lsusb'], timeout=10)

        # --- meshtasticd config ---
        print("\nmeshtasticd:")
        if has_helpers:
            config_info = match_config_to_hardware()
            configs = config_info.get('configs', [])
            if configs:
                match_marker = f"{GREEN}+ matches hardware{RESET}" if config_info.get('config_match') else ""
                print(f"  {GREEN}●{RESET} config.d/: {', '.join(configs)} {match_marker}")
            else:
                print(f"  {DIM}○{RESET} config.d/: (empty)")
            for warning in config_info.get('warnings', []):
                print(f"  {YELLOW}! {warning}{RESET}")
        else:
            config_d = Path('/etc/meshtasticd/config.d')
            if config_d.exists():
                configs = list(config_d.glob('*.yaml'))
                if configs:
                    for c in configs:
                        print(f"  {c.name}")
                else:
                    print("  (empty)")
            else:
                print("  (not found)")

        # --- Service status ---
        try:
            from utils.service_check import check_service, check_port
            svc = check_service('meshtasticd')
            if svc.available:
                print(f"  {GREEN}●{RESET} service: running (port 4403 OK)")
            else:
                print(f"  {DIM}○{RESET} service: {svc.message}")

            if check_port(9443):
                print(f"  {GREEN}●{RESET} webserver: localhost:9443 responding")
            else:
                print(f"  {DIM}○{RESET} webserver: localhost:9443 not responding")
        except ImportError:
            pass

        # --- Radio Health ---
        if has_helpers:
            print("\nRadio Health:")
            try:
                radio = get_radio_health()
                data = radio.data or {}

                http_nodes = data.get('http_nodes')
                cli_nodes = data.get('cli_node_count')
                if http_nodes is not None or cli_nodes is not None:
                    parts = []
                    if cli_nodes is not None:
                        parts.append(f"{cli_nodes} (CLI)")
                    if http_nodes is not None:
                        parts.append(f"{http_nodes} (HTTP API)")
                    print(f"  {GREEN}●{RESET} Nodes: {' | '.join(parts)}")

                report = data.get('report')
                if report:
                    ch_util = report.get('channel_utilization', 0)
                    tx_util = report.get('tx_utilization', 0)
                    print(f"  {GREEN}●{RESET} Channel util: {ch_util:.1f}%"
                          f" | TX util: {tx_util:.1f}%")
                    freq = report.get('frequency', 0)
                    if freq > 0:
                        print(f"  {GREEN}●{RESET} Frequency: {freq:.3f} MHz")

                snr_stats = data.get('snr_stats')
                if snr_stats:
                    print(f"  {GREEN}●{RESET} SNR range: {snr_stats['min']:.1f}"
                          f" to {snr_stats['max']:.1f} dB"
                          f" ({snr_stats['count']} nodes with data)")

                for warning in data.get('warnings', []):
                    print(f"  {YELLOW}! {warning}{RESET}")

                if not data.get('warnings') and (http_nodes or cli_nodes):
                    print(f"  {GREEN}●{RESET} No issues detected")
            except Exception as e:
                print(f"  {DIM}  (radio health unavailable: {e}){RESET}")

        self.ctx.wait_for_enter()

    def _enable_spi(self):
        spi_devices = list(Path('/dev').glob('spidev*'))
        if spi_devices:
            self.ctx.dialog.msgbox(
                "SPI Status",
                "SPI is already enabled!\n\n"
                f"Devices: {', '.join(d.name for d in spi_devices)}\n\n"
                "Your HAT radio should be detected."
            )
            return

        is_pi = self._is_raspberry_pi()
        if not is_pi:
            self.ctx.dialog.msgbox(
                "Not Raspberry Pi",
                "SPI auto-enable is only available on Raspberry Pi.\n\n"
                "For other systems, consult your board's documentation\n"
                "for enabling SPI interfaces."
            )
            return

        result = self.ctx.dialog.yesno(
            "Enable SPI",
            "This will enable the SPI interface for HAT radios.\n\n"
            "Supported HATs:\n"
            "  - MeshAdv-Pi-Hat\n"
            "  - Waveshare LoRa HAT\n"
            "  - Other SPI-based radios\n\n"
            "A REBOOT is required after enabling.\n\n"
            "Enable SPI now?"
        )

        if not result:
            return

        self.ctx.dialog.infobox("SPI", "Enabling SPI interface...")

        try:
            boot_config = None
            for path in ['/boot/firmware/config.txt', '/boot/config.txt']:
                if Path(path).exists():
                    boot_config = path
                    break

            if not boot_config:
                self.ctx.dialog.msgbox("Error", "Could not find boot config file.")
                return

            raspi_config = shutil.which('raspi-config')
            if raspi_config:
                subprocess.run(
                    ['raspi-config', 'nonint', 'set_config_var', 'dtparam=spi', 'on', boot_config],
                    timeout=30,
                    check=False
                )

            config_content = Path(boot_config).read_text()
            needs_write = False
            lines = config_content.split('\n')
            new_lines = []

            for line in lines:
                new_lines.append(line)
                if 'dtparam=spi=on' in line and 'dtoverlay=spi0-0cs' not in config_content:
                    new_lines.append('dtoverlay=spi0-0cs')
                    needs_write = True

            if 'dtparam=spi=on' not in config_content:
                new_lines.append('dtparam=spi=on')
                new_lines.append('dtoverlay=spi0-0cs')
                needs_write = True

            if needs_write:
                Path(boot_config).write_text('\n'.join(new_lines))

            self.ctx.dialog.msgbox(
                "SPI Enabled",
                "SPI interface has been enabled!\n\n"
                "IMPORTANT: You must REBOOT for changes to take effect.\n\n"
                "After reboot:\n"
                "  1. Your HAT radio will be detected\n"
                "  2. Configure meshtasticd for SPI\n"
                "  3. Start meshtasticd service\n\n"
                "Reboot now with: sudo reboot"
            )

        except subprocess.TimeoutExpired:
            self.ctx.dialog.msgbox("Error", "Timeout while configuring SPI.")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to enable SPI:\n{e}")

    def _is_raspberry_pi(self) -> bool:
        try:
            cpuinfo = Path('/proc/cpuinfo')
            if cpuinfo.exists():
                content = cpuinfo.read_text()
                if 'Raspberry Pi' in content or 'BCM' in content:
                    return True
            model = Path('/proc/device-tree/model')
            if model.exists():
                if 'Raspberry Pi' in model.read_text():
                    return True
        except OSError as e:
            logger.debug("RPi detection failed: %s", e)
        return False
