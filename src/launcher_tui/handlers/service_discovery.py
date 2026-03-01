"""
Service Discovery Handler — Auto-discover mesh network services.

Converted from service_discovery_mixin.py as part of the mixin-to-registry migration.
"""

import logging
import re
import socket
import subprocess
from dataclasses import dataclass

from handler_protocol import BaseHandler
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

check_service, check_port, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_service', 'check_port'
)

DeviceScanner, _HAS_DEVICE_SCANNER = safe_import(
    'utils.device_scanner', 'DeviceScanner'
)

_check_systemd_service, _HAS_SYSTEMD_CHECK = safe_import(
    'utils.service_check', 'check_systemd_service'
)


@dataclass
class DiscoveredService:
    """A discovered network service."""
    name: str
    status: str
    address: str
    service_type: str
    details: str = ""


class ServiceDiscoveryHandler(BaseHandler):
    """TUI handler for unified service discovery."""

    handler_id = "discover"
    menu_section = "system"

    def menu_items(self):
        return [
            ("discover", "Service Discovery   Auto-discover services", None),
        ]

    def execute(self, action):
        if action == "discover":
            self._service_discovery_menu()

    def _service_discovery_menu(self):
        while True:
            choices = [
                ("scan", "Quick Scan (Local Services)"),
                ("full", "Full Network Scan"),
                ("usb", "USB Device Scan"),
                ("status", "Service Status Overview"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Service Discovery",
                "Auto-discover mesh network services:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "scan": ("Quick Scan", self._quick_scan),
                "full": ("Full Network Scan", self._full_network_scan),
                "usb": ("USB Device Scan", self._usb_device_scan),
                "status": ("Service Status Overview", self._service_status_overview),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _quick_scan(self):
        self.ctx.dialog.infobox("Scanning", "Discovering local services...")

        services = []

        mesh_status = check_service('meshtasticd')
        services.append(DiscoveredService(
            name="meshtasticd",
            status="running" if mesh_status.available else "stopped",
            address="localhost:4403",
            service_type="meshtastic",
            details=mesh_status.message or ""
        ))

        rns_status = check_service('rnsd')
        services.append(DiscoveredService(
            name="rnsd",
            status="running" if rns_status.available else "stopped",
            address="UDP 37428",
            service_type="rns",
            details=rns_status.message or ""
        ))

        aredn_found = self._check_aredn_network()
        services.append(DiscoveredService(
            name="AREDN Network",
            status="detected" if aredn_found else "not found",
            address="10.0.0.0/8",
            service_type="aredn",
            details="AREDN mesh network interface" if aredn_found else ""
        ))

        lines = ["Service Discovery Results\n"]
        lines.append("=" * 40)

        for svc in services:
            status_icon = "+" if svc.status == "running" or svc.status == "detected" else "x"
            lines.append(f"\n{status_icon} {svc.name}")
            lines.append(f"  Status: {svc.status}")
            lines.append(f"  Address: {svc.address}")
            if svc.details:
                lines.append(f"  Info: {svc.details}")

        self.ctx.dialog.msgbox("Discovery Results", "\n".join(lines))

    def _check_aredn_network(self) -> bool:
        try:
            result = subprocess.run(
                ['ip', 'addr'],
                capture_output=True, text=True, timeout=5
            )
            return '10.' in result.stdout and 'inet 10.' in result.stdout
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("AREDN network check failed: %s", e)
            return False

    def _full_network_scan(self):
        network = self._detect_network_range()

        network = self.ctx.dialog.inputbox(
            "Network Scan",
            "Enter network range to scan:",
            network
        )

        if not network:
            return

        if not re.match(r'^[0-9./]+$', network) or len(network) > 18:
            self.ctx.dialog.msgbox("Error", "Invalid network range.")
            return

        self.ctx.dialog.infobox("Scanning", f"Scanning {network} for Meshtastic devices...\nThis may take a minute...")

        nmap_available = subprocess.run(
            ['which', 'nmap'],
            capture_output=True, timeout=5
        ).returncode == 0

        found_devices = []

        if nmap_available:
            try:
                result = subprocess.run(
                    ['nmap', '-p', '4403', '--open', '-oG', '-', network],
                    capture_output=True, text=True, timeout=120
                )
                for line in result.stdout.split('\n'):
                    if '4403/open' in line:
                        parts = line.split()
                        if len(parts) >= 2:
                            found_devices.append(parts[1])
            except (subprocess.SubprocessError, OSError) as e:
                logger.debug("nmap scan failed: %s", e)
        else:
            base = '.'.join(network.split('.')[:3])
            for i in range(1, 255):
                ip = f"{base}.{i}"
                if check_port(ip, 4403, timeout=0.3):
                    found_devices.append(ip)

        if found_devices:
            lines = [f"Found {len(found_devices)} Meshtastic device(s):\n"]
            for ip in found_devices:
                lines.append(f"  - {ip}:4403")
            self.ctx.dialog.msgbox("Network Scan Results", "\n".join(lines))
        else:
            self.ctx.dialog.msgbox("Network Scan", "No Meshtastic devices found on port 4403")

    def _detect_network_range(self) -> str:
        try:
            result = subprocess.run(
                ['ip', 'route', 'show', 'default'],
                capture_output=True, text=True, timeout=5
            )
            parts = result.stdout.split()
            if 'via' in parts:
                gateway_idx = parts.index('via') + 1
                gateway = parts[gateway_idx]
                return '.'.join(gateway.split('.')[:3]) + '.0/24'
        except (subprocess.SubprocessError, OSError, ValueError) as e:
            logger.debug("Network range detection failed: %s", e)
        return "192.168.1.0/24"

    def _usb_device_scan(self):
        if DeviceScanner is None:
            self.ctx.dialog.msgbox("Error", "Device scanner not available")
            return

        self.ctx.dialog.infobox("Scanning", "Scanning USB devices...")

        scanner = DeviceScanner()
        results = scanner.scan_all()

        lines = ["USB Device Scan Results\n"]
        lines.append("=" * 40)

        if results['meshtastic_candidates']:
            lines.append(f"\nMeshtastic-compatible devices ({len(results['meshtastic_candidates'])}):")
            for dev in results['meshtastic_candidates']:
                lines.append(f"  - {dev.description}")
                if dev.notes:
                    lines.append(f"    {dev.notes}")

        if results['serial_ports']:
            lines.append(f"\nSerial Ports ({len(results['serial_ports'])}):")
            for port in results['serial_ports']:
                compat = "+" if port.meshtastic_compatible else " "
                lines.append(f"  [{compat}] {port.device}")
                if port.description:
                    lines.append(f"      {port.description}")

        if results['recommended_port']:
            lines.append(f"\nRecommended port: {results['recommended_port']}")

        if not results['serial_ports'] and not results['meshtastic_candidates']:
            lines.append("\nNo USB serial devices found")
            lines.append("Connect a Meshtastic device and try again")

        self.ctx.dialog.msgbox("USB Scan Results", "\n".join(lines))

    def _service_status_overview(self):
        self.ctx.dialog.infobox("Checking", "Checking service status...")

        services = [
            ('meshtasticd', 'Meshtastic Daemon'),
            ('rnsd', 'Reticulum Network Stack'),
        ]

        lines = ["MeshForge Service Status\n"]
        lines.append("=" * 40)

        warnings = []

        lines.append("\nCore Services:")
        for svc_id, svc_name in services:
            status = check_service(svc_id)
            icon = "+" if status.available else "x"
            state = "running" if status.available else "stopped"
            lines.append(f"\n{icon} {svc_name}")
            lines.append(f"  Status: {state}")

            if status.available and _HAS_SYSTEMD_CHECK:
                _, is_enabled = _check_systemd_service(svc_id)
                if not is_enabled:
                    lines.append(f"  Boot: not enabled (won't start on reboot)")
                    warnings.append(svc_id)
                else:
                    lines.append(f"  Boot: enabled")

            if status.message:
                lines.append(f"  Info: {status.message}")

        lines.append("\n\nOptional Data Sources:")
        lines.append("  Space weather: NOAA SWPC (always active)")
        lines.append("  Configure more in Settings > Propagation Data Sources")

        if warnings:
            lines.append("\n" + "-" * 40)
            lines.append(f"Fix: sudo systemctl enable {' '.join(warnings)}")
        else:
            lines.append("\n" + "-" * 40)

        lines.append("Use Service Manager to start/stop services")

        self.ctx.dialog.msgbox("Service Status", "\n".join(lines))
