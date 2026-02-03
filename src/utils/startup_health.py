"""
MeshForge Startup Health Summary

Provides instant health check on startup showing:
- Service status (meshtasticd, rnsd)
- Hardware detection
- Network status (node count)
- Quick actions

Usage:
    from utils.startup_health import run_health_check, print_health_summary

    health = run_health_check()
    print_health_summary(health)
"""

import logging
import subprocess
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)

# Import service checker
try:
    from utils.service_check import check_service, ServiceState
    HAS_SERVICE_CHECK = True
except ImportError:
    HAS_SERVICE_CHECK = False
    check_service = None
    ServiceState = None

# Import version
try:
    from __version__ import __version__
except ImportError:
    __version__ = "0.5.0-beta"


@dataclass
class ServiceHealth:
    """Health status of a single service."""
    name: str
    running: bool
    port: Optional[int] = None
    status_text: str = ""
    optional: bool = False
    fix_hint: str = ""


@dataclass
class HardwareHealth:
    """Hardware detection status."""
    detected: bool = False
    device_name: str = ""
    device_type: str = ""  # "spi", "usb", "unknown"
    port: str = ""


@dataclass
class NetworkHealth:
    """Network/mesh status."""
    nodes_visible: int = 0
    last_message_ago: str = ""


@dataclass
class HealthSummary:
    """Complete health summary."""
    version: str = __version__
    services: List[ServiceHealth] = field(default_factory=list)
    hardware: HardwareHealth = field(default_factory=HardwareHealth)
    network: NetworkHealth = field(default_factory=NetworkHealth)
    overall_status: str = "unknown"  # "ready", "degraded", "error"

    @property
    def is_ready(self) -> bool:
        """Check if system is ready for operation."""
        # At minimum, meshtasticd should be running
        for svc in self.services:
            if svc.name == "meshtasticd" and svc.running:
                return True
        return False


def check_meshtasticd() -> ServiceHealth:
    """Check meshtasticd service status."""
    if HAS_SERVICE_CHECK:
        status = check_service('meshtasticd')
        return ServiceHealth(
            name="meshtasticd",
            running=status.available,
            port=4403 if status.available else None,
            status_text=status.message,
            optional=False,
            fix_hint=status.fix_hint if not status.available else ""
        )

    # Fallback: try systemctl directly
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', 'meshtasticd'],
            capture_output=True, text=True, timeout=5
        )
        running = result.returncode == 0
        return ServiceHealth(
            name="meshtasticd",
            running=running,
            port=4403 if running else None,
            status_text="running" if running else "not running",
            optional=False,
            fix_hint="" if running else "sudo systemctl start meshtasticd"
        )
    except Exception as e:
        logger.debug(f"meshtasticd check failed: {e}")
        return ServiceHealth(
            name="meshtasticd",
            running=False,
            status_text="check failed",
            optional=False,
            fix_hint="Check if meshtasticd is installed"
        )


def check_rnsd() -> ServiceHealth:
    """Check rnsd service status."""
    if HAS_SERVICE_CHECK:
        status = check_service('rnsd')
        return ServiceHealth(
            name="rnsd",
            running=status.available,
            port=37428 if status.available else None,
            status_text=status.message,
            optional=True,  # rnsd is optional
            fix_hint=status.fix_hint if not status.available else ""
        )

    # Fallback: check process
    try:
        result = subprocess.run(
            ['pgrep', '-x', 'rnsd'],
            capture_output=True, text=True, timeout=5
        )
        running = result.returncode == 0
        return ServiceHealth(
            name="rnsd",
            running=running,
            port=37428 if running else None,
            status_text="running" if running else "not running",
            optional=True,
            fix_hint="" if running else "rnsd (run as user)"
        )
    except Exception as e:
        logger.debug(f"rnsd check failed: {e}")
        return ServiceHealth(
            name="rnsd",
            running=False,
            status_text="check failed",
            optional=True,
            fix_hint="Install Reticulum: pipx install rns"
        )


def detect_hardware() -> HardwareHealth:
    """Detect connected LoRa hardware."""
    hardware = HardwareHealth()

    # Check for SPI devices (Meshtoad, Pi HATs)
    spi_devices = list(Path('/dev').glob('spidev*'))
    if spi_devices:
        hardware.device_type = "spi"

        # Check for known HAT identifiers
        try:
            # Check device tree for HAT info
            hat_product = Path('/proc/device-tree/hat/product')
            if hat_product.exists():
                hardware.device_name = hat_product.read_text().strip('\x00')
                hardware.detected = True
        except Exception:
            pass

        # Fallback: check meshtasticd config for device type
        if not hardware.detected:
            config_d = Path('/etc/meshtasticd/config.d')
            if config_d.exists():
                for cfg in config_d.glob('*.yaml'):
                    content = cfg.read_text().lower()
                    if 'meshtoad' in content:
                        hardware.device_name = "Meshtoad SX1262"
                        hardware.detected = True
                        break
                    elif 'meshadvpihat' in content or 'meshadv-pi-hat' in content:
                        hardware.device_name = "MeshAdv-Pi-Hat"
                        hardware.detected = True
                        break
                    elif 'waveshare' in content:
                        hardware.device_name = "Waveshare SX126x"
                        hardware.detected = True
                        break
                    elif 'rak' in content:
                        hardware.device_name = "RAK WisLink"
                        hardware.detected = True
                        break

    # Check for USB serial devices
    usb_patterns = ['/dev/ttyUSB*', '/dev/ttyACM*']
    for pattern in usb_patterns:
        usb_devices = list(Path('/dev').glob(pattern.replace('/dev/', '')))
        if usb_devices:
            hardware.device_type = "usb"
            hardware.port = str(usb_devices[0])
            hardware.device_name = "USB Serial Radio"
            hardware.detected = True
            break

    return hardware


def get_node_count() -> int:
    """Get count of visible nodes."""
    try:
        from utils.cli import find_meshtastic_cli
        cli_path = find_meshtastic_cli()
        if not cli_path:
            return 0

        result = subprocess.run(
            [cli_path, '--host', 'localhost', '--info'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            # Count lines containing node info
            lines = result.stdout.split('\n')
            node_lines = [l for l in lines if '!' in l and 'node' in l.lower()]
            return len(node_lines) if node_lines else 0
    except Exception as e:
        logger.debug(f"Node count failed: {e}")

    return 0


def run_health_check() -> HealthSummary:
    """Run complete health check and return summary."""
    summary = HealthSummary()

    # Check services
    summary.services.append(check_meshtasticd())
    summary.services.append(check_rnsd())

    # Detect hardware
    summary.hardware = detect_hardware()

    # Get network status (only if meshtasticd running)
    meshtasticd_running = any(
        s.name == "meshtasticd" and s.running
        for s in summary.services
    )
    if meshtasticd_running:
        summary.network.nodes_visible = get_node_count()

    # Determine overall status
    critical_ok = all(
        s.running for s in summary.services if not s.optional
    )
    optional_ok = all(
        s.running for s in summary.services if s.optional
    )

    if critical_ok and optional_ok:
        summary.overall_status = "ready"
    elif critical_ok:
        summary.overall_status = "degraded"
    else:
        summary.overall_status = "error"

    return summary


def print_health_summary(summary: HealthSummary, use_color: bool = True) -> str:
    """
    Generate health summary text for display.

    Args:
        summary: HealthSummary from run_health_check()
        use_color: Whether to use ANSI colors

    Returns:
        Formatted string for display
    """
    # Color codes
    if use_color:
        GREEN = "\033[92m"
        YELLOW = "\033[93m"
        RED = "\033[91m"
        RESET = "\033[0m"
        BOLD = "\033[1m"
    else:
        GREEN = YELLOW = RED = RESET = BOLD = ""

    lines = []

    # Header
    lines.append(f"{BOLD}MeshForge v{summary.version}{RESET}")
    lines.append("")

    # Services section
    lines.append("Services:")
    for svc in summary.services:
        if svc.running:
            icon = f"{GREEN}✓{RESET}"
            port_info = f" (port {svc.port})" if svc.port else ""
            lines.append(f"  {icon} {svc.name}: running{port_info}")
        elif svc.optional:
            icon = f"{YELLOW}⚠{RESET}"
            lines.append(f"  {icon} {svc.name}: not running (optional)")
        else:
            icon = f"{RED}✗{RESET}"
            lines.append(f"  {icon} {svc.name}: {svc.status_text}")
            if svc.fix_hint:
                lines.append(f"    Fix: {svc.fix_hint}")

    # Hardware section
    if summary.hardware.detected:
        lines.append(f"  {GREEN}✓{RESET} Hardware: {summary.hardware.device_name} detected")
    else:
        lines.append(f"  {YELLOW}⚠{RESET} Hardware: No radio detected")

    # Network section
    lines.append("")
    lines.append("Network:")
    if summary.network.nodes_visible > 0:
        lines.append(f"  {GREEN}✓{RESET} Nodes visible: {summary.network.nodes_visible}")
    else:
        lines.append(f"  {YELLOW}⚠{RESET} Nodes visible: 0 (check connection)")

    # Overall status
    lines.append("")
    if summary.overall_status == "ready":
        lines.append(f"{GREEN}Ready!{RESET} [Continue] [Configure] [Troubleshoot]")
    elif summary.overall_status == "degraded":
        lines.append(f"{YELLOW}Degraded{RESET} - Some services not running")
        lines.append("[Continue] [Configure] [Troubleshoot]")
    else:
        lines.append(f"{RED}Not Ready{RESET} - Critical services down")
        lines.append("[Configure] [Troubleshoot] [Exit]")

    return "\n".join(lines)


def get_health_dict(summary: HealthSummary) -> Dict[str, Any]:
    """Convert health summary to dictionary for JSON/API use."""
    return {
        'version': summary.version,
        'overall_status': summary.overall_status,
        'is_ready': summary.is_ready,
        'services': [
            {
                'name': s.name,
                'running': s.running,
                'port': s.port,
                'status': s.status_text,
                'optional': s.optional
            }
            for s in summary.services
        ],
        'hardware': {
            'detected': summary.hardware.detected,
            'name': summary.hardware.device_name,
            'type': summary.hardware.device_type
        },
        'network': {
            'nodes_visible': summary.network.nodes_visible
        }
    }


def get_traffic_light(summary: HealthSummary, use_color: bool = True) -> str:
    """
    Get a simple traffic light status indicator.

    Returns:
        String like "● READY" or "● DEGRADED" with color
    """
    if use_color:
        GREEN = "\033[92m"
        YELLOW = "\033[93m"
        RED = "\033[91m"
        RESET = "\033[0m"
    else:
        GREEN = YELLOW = RED = RESET = ""

    if summary.overall_status == "ready":
        return f"{GREEN}●{RESET} READY"
    elif summary.overall_status == "degraded":
        return f"{YELLOW}●{RESET} DEGRADED"
    else:
        return f"{RED}●{RESET} NOT READY"


def get_compact_status(summary: HealthSummary, use_color: bool = True) -> str:
    """
    Get compact one-line status for status bars.

    Returns:
        String like "● meshtasticd ● rnsd ○ 3 nodes"
    """
    if use_color:
        GREEN = "\033[92m"
        YELLOW = "\033[93m"
        RED = "\033[91m"
        DIM = "\033[2m"
        RESET = "\033[0m"
    else:
        GREEN = YELLOW = RED = DIM = RESET = ""

    parts = []

    # Service indicators
    for svc in summary.services:
        if svc.running:
            parts.append(f"{GREEN}●{RESET}{svc.name}")
        elif svc.optional:
            parts.append(f"{DIM}○{svc.name}{RESET}")
        else:
            parts.append(f"{RED}●{RESET}{svc.name}")

    # Node count
    if summary.network.nodes_visible > 0:
        parts.append(f"{GREEN}●{RESET}{summary.network.nodes_visible} nodes")
    else:
        parts.append(f"{DIM}○0 nodes{RESET}")

    return " ".join(parts)


# CLI entry point
if __name__ == "__main__":
    summary = run_health_check()
    print(print_health_summary(summary))
    print()
    print(f"Traffic Light: {get_traffic_light(summary)}")
    print(f"Compact: {get_compact_status(summary)}")
