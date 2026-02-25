"""
WiFi Access Point Management Module.

Manages hostapd-based WiFi access points for mesh gateway appliances.
Adapted from MeshLinkFoundation/meshlink-wifi-gateway four-service pattern:
  hostapd (AP) + dnsmasq (DHCP/DNS) + iptables (NAT) + systemd (boot).

Architecture:
  wlan0 (AP) -> hostapd -> dnsmasq (DHCP 192.168.50.0/24) -> iptables NAT -> eth0 (WAN)

Requires:
  - hostapd (apt install hostapd)
  - dnsmasq (apt install dnsmasq)
  - iptables (apt install iptables)
  - WiFi adapter supporting AP mode (most USB adapters)

Privilege model:
  - Status/info: Viewer mode (no sudo needed)
  - Setup/control: Admin mode (sudo required, uses service_check helpers)
"""

import ipaddress
import logging
import re
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from utils.service_check import (
    _sudo_cmd, _sudo_write, check_service,
    start_service, stop_service, enable_service,
    daemon_reload,
)

logger = logging.getLogger(__name__)

# Maximum SSID length per 802.11 spec
MAX_SSID_LEN = 32
# WPA2 passphrase range
MIN_PASSPHRASE_LEN = 8
MAX_PASSPHRASE_LEN = 63


@dataclass
class WiFiAPConfig:
    """Configuration for WiFi access point."""
    interface: str = "wlan0"
    ssid: str = "MeshForge"
    passphrase: str = ""          # Empty = open network
    channel: int = 6
    hw_mode: str = "g"            # a/b/g
    subnet: str = "192.168.50.0/24"
    gateway_ip: str = "192.168.50.1"
    dhcp_start: str = "192.168.50.10"
    dhcp_end: str = "192.168.50.100"
    wan_interface: str = "eth0"   # For NAT masquerade
    dns_server: str = "8.8.8.8"
    captive_portal_port: int = 0  # 0 = disabled
    country_code: str = "US"


def validate_ap_config(config: WiFiAPConfig) -> List[str]:
    """
    Validate AP configuration. Returns list of error messages (empty = valid).
    """
    errors = []

    # SSID validation
    if not config.ssid:
        errors.append("SSID cannot be empty")
    elif len(config.ssid) > MAX_SSID_LEN:
        errors.append(f"SSID exceeds {MAX_SSID_LEN} characters")
    elif not all(32 <= ord(c) < 127 for c in config.ssid):
        errors.append("SSID must contain only printable ASCII characters")

    # Passphrase validation (if set)
    if config.passphrase:
        if len(config.passphrase) < MIN_PASSPHRASE_LEN:
            errors.append(f"Passphrase must be at least {MIN_PASSPHRASE_LEN} characters")
        elif len(config.passphrase) > MAX_PASSPHRASE_LEN:
            errors.append(f"Passphrase must be at most {MAX_PASSPHRASE_LEN} characters")

    # Channel validation
    if config.hw_mode in ("b", "g"):
        if not 1 <= config.channel <= 14:
            errors.append(f"Channel {config.channel} out of range for 2.4GHz (1-14)")
    elif config.hw_mode == "a":
        valid_5g = {36, 40, 44, 48, 52, 56, 60, 64,
                    100, 104, 108, 112, 116, 120, 124, 128,
                    132, 136, 140, 149, 153, 157, 161, 165}
        if config.channel not in valid_5g:
            errors.append(f"Channel {config.channel} not valid for 5GHz")

    # IP validation
    for name, val in [("gateway_ip", config.gateway_ip),
                      ("dhcp_start", config.dhcp_start),
                      ("dhcp_end", config.dhcp_end)]:
        try:
            ipaddress.ip_address(val)
        except ValueError:
            errors.append(f"Invalid IP address for {name}: {val}")

    try:
        ipaddress.ip_network(config.subnet, strict=False)
    except ValueError:
        errors.append(f"Invalid subnet: {config.subnet}")

    # Interface name validation (alphanumeric + limited special chars)
    iface_pattern = re.compile(r'^[a-zA-Z0-9_-]+$')
    for name, val in [("interface", config.interface),
                      ("wan_interface", config.wan_interface)]:
        if not iface_pattern.match(val):
            errors.append(f"Invalid interface name for {name}: {val}")

    return errors


def _generate_hostapd_conf(config: WiFiAPConfig) -> str:
    """Generate hostapd.conf content."""
    lines = [
        f"interface={config.interface}",
        f"driver=nl80211",
        f"ssid={config.ssid}",
        f"hw_mode={config.hw_mode}",
        f"channel={config.channel}",
        f"country_code={config.country_code}",
        "wmm_enabled=0",
        "macaddr_acl=0",
        "ieee80211n=1",
    ]

    if config.passphrase:
        lines.extend([
            "auth_algs=1",
            "wpa=2",
            "wpa_key_mgmt=WPA-PSK",
            "wpa_pairwise=CCMP",
            "rsn_pairwise=CCMP",
            f"wpa_passphrase={config.passphrase}",
        ])
    else:
        lines.append("auth_algs=1")

    return "\n".join(lines) + "\n"


def _generate_dnsmasq_conf(config: WiFiAPConfig) -> str:
    """Generate dnsmasq configuration for AP interface."""
    network = ipaddress.ip_network(config.subnet, strict=False)
    netmask = str(network.netmask)

    lines = [
        f"interface={config.interface}",
        "bind-interfaces",
        f"dhcp-range={config.dhcp_start},{config.dhcp_end},{netmask},24h",
        f"server={config.dns_server}",
        "no-resolv",
        "log-queries",
        "log-dhcp",
    ]

    return "\n".join(lines) + "\n"


def _generate_systemd_service() -> str:
    """Generate meshforge-ap.service for boot persistence."""
    return """[Unit]
Description=MeshForge WiFi Access Point
After=network.target
Wants=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/usr/sbin/rfkill unblock wifi
ExecStart=/sbin/iptables-restore /etc/iptables/rules.v4

[Install]
WantedBy=multi-user.target
"""


class WiFiAPManager:
    """
    Manages hostapd-based WiFi access point lifecycle.

    Uses utils/service_check.py helpers for all privileged operations.
    """

    HOSTAPD_CONF = "/etc/hostapd/meshforge-ap.conf"
    DNSMASQ_CONF = "/etc/dnsmasq.d/meshforge-ap.conf"
    IPTABLES_RULES = "/etc/iptables/rules.v4"
    SERVICE_NAME = "meshforge-ap"
    SERVICE_FILE = f"/etc/systemd/system/{SERVICE_NAME}.service"

    def __init__(self, config: Optional[WiFiAPConfig] = None):
        self._config = config or WiFiAPConfig()

    @property
    def config(self) -> WiFiAPConfig:
        return self._config

    def setup(self, config: Optional[WiFiAPConfig] = None) -> Tuple[bool, str]:
        """
        Full AP setup: generate configs, set static IP, configure iptables.

        Requires sudo/admin mode.

        Returns:
            (success, message) tuple.
        """
        cfg = config or self._config
        if config:
            self._config = config

        # Validate
        errors = validate_ap_config(cfg)
        if errors:
            return False, f"Config validation failed: {'; '.join(errors)}"

        steps_done = []

        try:
            # 1. Unblock WiFi radio
            result = subprocess.run(
                _sudo_cmd(["rfkill", "unblock", "wifi"]),
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                logger.warning(f"rfkill unblock: {result.stderr.strip()}")
            steps_done.append("rfkill unblock")

            # 2. Set static IP on AP interface
            success, msg = _sudo_write(
                f"/etc/network/interfaces.d/{cfg.interface}-static",
                f"auto {cfg.interface}\n"
                f"iface {cfg.interface} inet static\n"
                f"  address {cfg.gateway_ip}\n"
                f"  netmask {str(ipaddress.ip_network(cfg.subnet, strict=False).netmask)}\n"
            )
            if not success:
                return False, f"Failed to set static IP: {msg}"
            steps_done.append("static IP")

            # 3. Write hostapd config
            hostapd_content = _generate_hostapd_conf(cfg)
            success, msg = _sudo_write(self.HOSTAPD_CONF, hostapd_content)
            if not success:
                return False, f"Failed to write hostapd config: {msg}"
            steps_done.append("hostapd config")

            # 4. Write dnsmasq config
            dnsmasq_content = _generate_dnsmasq_conf(cfg)
            success, msg = _sudo_write(self.DNSMASQ_CONF, dnsmasq_content)
            if not success:
                return False, f"Failed to write dnsmasq config: {msg}"
            steps_done.append("dnsmasq config")

            # 5. Configure iptables NAT rules
            success, msg = self._setup_iptables(cfg)
            if not success:
                return False, f"Failed to setup iptables: {msg}"
            steps_done.append("iptables NAT")

            # 6. Create systemd service for boot persistence
            service_content = _generate_systemd_service()
            success, msg = _sudo_write(self.SERVICE_FILE, service_content)
            if not success:
                return False, f"Failed to create systemd service: {msg}"

            daemon_reload()
            steps_done.append("systemd service")

            logger.info(f"WiFi AP setup complete: {', '.join(steps_done)}")
            return True, f"AP setup complete ({cfg.ssid} on {cfg.interface})"

        except subprocess.TimeoutExpired:
            return False, "Setup timed out"
        except Exception as e:
            return False, f"Setup failed after {steps_done}: {e}"

    def start(self) -> Tuple[bool, str]:
        """Start the AP (hostapd + dnsmasq)."""
        # Start hostapd with our config
        result = subprocess.run(
            _sudo_cmd(["hostapd", "-B", self.HOSTAPD_CONF]),
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return False, f"hostapd start failed: {result.stderr.strip()}"

        # Restart dnsmasq to pick up our config
        success, msg = start_service("dnsmasq")
        if not success:
            return False, f"dnsmasq start failed: {msg}"

        # Enable boot persistence
        enable_service(self.SERVICE_NAME)

        logger.info(f"WiFi AP started: {self._config.ssid}")
        return True, f"AP started ({self._config.ssid})"

    def stop(self) -> Tuple[bool, str]:
        """Stop the AP (kill hostapd, stop dnsmasq)."""
        # Kill hostapd
        subprocess.run(
            _sudo_cmd(["killall", "hostapd"]),
            capture_output=True, text=True, timeout=10,
        )

        # Stop dnsmasq
        stop_service("dnsmasq")

        logger.info("WiFi AP stopped")
        return True, "AP stopped"

    def get_status(self) -> Dict:
        """
        Get AP status (viewer mode — no sudo needed).

        Returns dict with running state and config summary.
        """
        hostapd_status = check_service("hostapd")
        dnsmasq_status = check_service("dnsmasq")

        return {
            "hostapd": {
                "running": hostapd_status.available,
                "message": hostapd_status.message,
            },
            "dnsmasq": {
                "running": dnsmasq_status.available,
                "message": dnsmasq_status.message,
            },
            "config": {
                "ssid": self._config.ssid,
                "interface": self._config.interface,
                "channel": self._config.channel,
                "gateway_ip": self._config.gateway_ip,
                "secured": bool(self._config.passphrase),
            },
        }

    def get_connected_clients(self) -> List[Dict[str, str]]:
        """
        Get list of connected WiFi clients via hostapd_cli.

        Returns list of dicts with 'mac', 'signal', etc.
        """
        clients = []
        try:
            result = subprocess.run(
                _sudo_cmd(["hostapd_cli", "-i", self._config.interface,
                           "all_sta"]),
                capture_output=True, text=True, timeout=10,
            )

            if result.returncode != 0:
                return clients

            # Parse hostapd_cli all_sta output
            current_mac = None
            for line in result.stdout.splitlines():
                line = line.strip()
                # MAC address lines are like "aa:bb:cc:dd:ee:ff"
                if re.match(r'^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$', line):
                    current_mac = line
                    clients.append({"mac": current_mac})
                elif '=' in line and current_mac and clients:
                    key, _, val = line.partition('=')
                    clients[-1][key.strip()] = val.strip()

        except subprocess.TimeoutExpired:
            logger.warning("hostapd_cli timed out")
        except FileNotFoundError:
            logger.debug("hostapd_cli not found")
        except Exception as e:
            logger.debug(f"Error getting connected clients: {e}")

        return clients

    def teardown(self) -> Tuple[bool, str]:
        """Remove AP configuration and restore defaults."""
        self.stop()

        # Remove config files
        for path in [self.HOSTAPD_CONF, self.DNSMASQ_CONF,
                     self.SERVICE_FILE,
                     f"/etc/network/interfaces.d/{self._config.interface}-static"]:
            subprocess.run(
                _sudo_cmd(["rm", "-f", path]),
                capture_output=True, text=True, timeout=10,
            )

        # Flush iptables NAT rules for our interface
        subprocess.run(
            _sudo_cmd(["iptables", "-t", "nat", "-F"]),
            capture_output=True, text=True, timeout=10,
        )

        daemon_reload()
        logger.info("WiFi AP configuration removed")
        return True, "AP teardown complete"

    def _setup_iptables(self, cfg: WiFiAPConfig) -> Tuple[bool, str]:
        """Configure iptables NAT rules."""
        rules = [
            # Enable IP forwarding
            ["sysctl", "-w", "net.ipv4.ip_forward=1"],
            # NAT masquerade from AP to WAN
            ["iptables", "-t", "nat", "-A", "POSTROUTING",
             "-o", cfg.wan_interface, "-j", "MASQUERADE"],
            # Allow forwarding from AP to WAN
            ["iptables", "-A", "FORWARD",
             "-i", cfg.interface, "-o", cfg.wan_interface,
             "-j", "ACCEPT"],
            # Allow established connections back
            ["iptables", "-A", "FORWARD",
             "-i", cfg.wan_interface, "-o", cfg.interface,
             "-m", "state", "--state", "RELATED,ESTABLISHED",
             "-j", "ACCEPT"],
        ]

        # Optional captive portal redirect (REDIRECT, not DNAT)
        if cfg.captive_portal_port > 0:
            rules.append([
                "iptables", "-t", "nat", "-A", "PREROUTING",
                "-i", cfg.interface, "-p", "tcp",
                "--dport", "80",
                "-j", "REDIRECT", "--to-port", str(cfg.captive_portal_port),
            ])

        for cmd in rules:
            result = subprocess.run(
                _sudo_cmd(cmd),
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return False, f"iptables rule failed: {' '.join(cmd)}: {result.stderr.strip()}"

        # Save rules for boot persistence
        try:
            result = subprocess.run(
                ["iptables-save"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                _sudo_write(self.IPTABLES_RULES, result.stdout)
        except Exception as e:
            logger.warning(f"Could not save iptables rules: {e}")

        return True, "iptables configured"
