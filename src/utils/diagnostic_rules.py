"""
Diagnostic rules for MeshForge Diagnostic Engine.

This module contains all the built-in diagnostic rules for mesh networking.
Separated from diagnostic_engine.py for maintainability.

These rules are loaded by DiagnosticEngine._load_mesh_rules().
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .diagnostic_engine import DiagnosticEngine

from .diagnostic_engine import (
    Category,
    DiagnosticRule,
    check_meshtasticd_clients,
    check_no_serial_device,
    check_rns_config_missing,
    check_serial_device_exists,
    make_port_check,
    make_port_closed_check,
    make_process_check,
    make_process_not_running_check,
    make_service_active_check,
    make_service_inactive_check,
)


def load_mesh_rules(engine: "DiagnosticEngine") -> None:
    """Load built-in diagnostic rules for mesh networking."""

    # ===== CONNECTIVITY RULES =====

    engine.add_rule(DiagnosticRule(
        name="meshtasticd_connection_refused",
        pattern=r"(?i)connection\s+(refused|rejected).*meshtastic",
        category=Category.CONNECTIVITY,
        cause_template="Another client is likely connected to meshtasticd (single-client limitation)",
        evidence_checks=[
            make_port_check("localhost", 4403),  # Verify port is actually open
            lambda ctx: check_meshtasticd_clients(),  # Check for other clients
            make_service_active_check("meshtasticd"),  # Verify service running
        ],
        suggestions=[
            "Check for other Meshtastic clients: ps aux | grep -E 'nomadnet|meshing|meshtastic'",
            "Restart meshtasticd: sudo systemctl restart meshtasticd",
            "Use --host to connect to a different instance",
        ],
        auto_recoverable=True,
        recovery_action="Will retry connection with exponential backoff",
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="meshtasticd_not_running",
        pattern=r"(?i)(meshtasticd|4403).*(not running|refused|unavailable)",
        category=Category.CONNECTIVITY,
        cause_template="meshtasticd service is not running or not listening on port 4403",
        evidence_checks=[
            make_port_closed_check("localhost", 4403),  # Confirm port is closed
            make_service_inactive_check("meshtasticd"),  # Confirm service not running
            make_process_not_running_check("meshtasticd"),  # Confirm process not running
        ],
        suggestions=[
            "Check service status: sudo systemctl status meshtasticd",
            "Start the service: sudo systemctl start meshtasticd",
            "Check logs: journalctl -u meshtasticd -n 50",
        ],
        confidence_base=0.9,
    ))

    engine.add_rule(DiagnosticRule(
        name="rns_transport_unavailable",
        pattern=r"(?i)(rns|reticulum).*(transport|interface).*(unavailable|failed|error)",
        category=Category.CONNECTIVITY,
        cause_template="RNS transport interface failed to initialize",
        evidence_checks=[
            make_service_inactive_check("rnsd"),  # Check rnsd not running
            lambda ctx: check_rns_config_missing(),  # Check config missing
            lambda ctx: check_no_serial_device(),  # Check for serial devices
        ],
        suggestions=[
            "Check rnsd status: sudo systemctl status rnsd",
            "Verify config: cat ~/.reticulum/config",
            "Check interface availability (serial port, network)",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="mqtt_connection_failed",
        pattern=r"(?i)mqtt.*(connection|connect).*(failed|error|refused|timeout)",
        category=Category.CONNECTIVITY,
        cause_template="MQTT broker connection failed",
        suggestions=[
            "Verify broker address and port",
            "Check network connectivity: ping mqtt.meshtastic.org",
            "Verify credentials if authentication required",
            "Check TLS certificate if using secure connection",
        ],
        auto_recoverable=True,
        recovery_action="Will reconnect with exponential backoff",
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="node_timeout",
        pattern=r"(?i)node.*(timeout|not responding|unreachable)",
        category=Category.CONNECTIVITY,
        cause_template="Remote node is not responding within expected timeframe",
        suggestions=[
            "Node may be out of range or powered off",
            "Check if node appears in mesh: meshtastic --nodes",
            "Verify RF path (line of sight, obstacles)",
            "Node may be in sleep mode for power saving",
        ],
        confidence_base=0.7,
    ))

    # ===== HARDWARE RULES =====

    engine.add_rule(DiagnosticRule(
        name="serial_port_busy",
        pattern=r"(?i)(serial|tty|usb).*(busy|in use|locked|permission)",
        category=Category.HARDWARE,
        cause_template="Serial port is in use by another process or has permission issues",
        evidence_checks=[
            lambda ctx: check_serial_device_exists(),  # Verify device exists
            make_process_check("meshtasticd"),  # meshtasticd might be using it
        ],
        suggestions=[
            "Find process using port: sudo lsof /dev/ttyUSB0",
            "Kill blocking process or use different port",
            "Check permissions: ls -la /dev/ttyUSB*",
            "Add user to dialout group: sudo usermod -aG dialout $USER",
        ],
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="device_disconnected",
        pattern=r"(?i)(device|radio|hardware).*(disconnect|removed|not found|missing)",
        category=Category.HARDWARE,
        cause_template="Hardware device was disconnected or not detected",
        evidence_checks=[
            lambda ctx: check_no_serial_device(),  # Verify no serial devices
        ],
        suggestions=[
            "Check USB connection: lsusb",
            "Check dmesg for device events: dmesg | tail -20",
            "Try different USB port or cable",
            "Device may need power cycle",
        ],
        confidence_base=0.9,
    ))

    # ===== PROTOCOL RULES =====

    engine.add_rule(DiagnosticRule(
        name="encryption_mismatch",
        pattern=r"(?i)(encryption|decrypt|key).*(mismatch|failed|invalid|error)",
        category=Category.PROTOCOL,
        cause_template="Encryption key mismatch between nodes",
        suggestions=[
            "Verify channel encryption key matches on all nodes",
            "Check channel settings: meshtastic --ch-index 0 --info",
            "Reset to default key if needed: meshtastic --ch-set psk default",
        ],
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="protocol_version_mismatch",
        pattern=r"(?i)(protocol|version|firmware).*(mismatch|incompatible|unsupported)",
        category=Category.PROTOCOL,
        cause_template="Protocol or firmware version incompatibility",
        suggestions=[
            "Update firmware on all nodes to same version",
            "Check firmware: meshtastic --info | grep firmware",
            "See https://meshtastic.org/docs/getting-started/flashing-firmware",
        ],
        confidence_base=0.8,
    ))

    # ===== PERFORMANCE RULES =====

    engine.add_rule(DiagnosticRule(
        name="high_channel_utilization",
        pattern=r"(?i)channel\s*utilization.*(high|>50%|warning)",
        category=Category.PERFORMANCE,
        cause_template="Channel utilization is high, may cause message delays or drops",
        suggestions=[
            "Reduce message frequency",
            "Use shorter messages",
            "Consider different channel or modem preset",
            "Spread nodes across multiple channels",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="message_queue_full",
        pattern=r"(?i)(queue|buffer).*(full|overflow|dropping)",
        category=Category.PERFORMANCE,
        cause_template="Message queue is full, new messages may be dropped",
        suggestions=[
            "Reduce outgoing message rate",
            "Check for stuck/slow destination",
            "Increase queue size if persistent",
            "Check for network congestion",
        ],
        auto_recoverable=True,
        recovery_action="Oldest messages will be dropped to make room",
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="low_snr",
        pattern=r"(?i)snr.*(low|weak|poor|<-10|-\d{2})",
        category=Category.PERFORMANCE,
        cause_template="Signal-to-noise ratio is low, indicating weak signal",
        suggestions=[
            "Improve antenna positioning (height, orientation)",
            "Check for obstructions in RF path",
            "Consider higher gain antenna",
            "Reduce distance or add relay node",
        ],
        confidence_base=0.75,
    ))

    # ===== RESOURCE RULES =====

    engine.add_rule(DiagnosticRule(
        name="memory_pressure",
        pattern=r"(?i)(memory|ram|heap).*(low|pressure|warning|exhausted)",
        category=Category.RESOURCE,
        cause_template="System is running low on memory",
        suggestions=[
            "Check memory usage: free -h",
            "Identify memory-heavy processes: top -o %MEM",
            "Restart MeshForge to free memory",
            "Consider increasing system RAM",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="disk_space_low",
        pattern=r"(?i)(disk|storage|space).*(low|full|warning|<\d+%)",
        category=Category.RESOURCE,
        cause_template="Disk space is running low",
        suggestions=[
            "Check disk usage: df -h",
            "Clean log files: sudo journalctl --vacuum-time=7d",
            "Remove old message queue entries",
            "Check for large files: du -sh /* | sort -h",
        ],
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="zombie_process",
        pattern=r"(?i)(zombie|defunct|orphan).*(process|pid)",
        category=Category.RESOURCE,
        cause_template="Zombie or defunct process detected",
        suggestions=[
            "Identify zombie processes: ps aux | grep Z",
            "Kill parent process to clean up zombies",
            "Restart affected service",
        ],
        auto_recoverable=True,
        recovery_action="Will attempt to clean up zombie processes",
        confidence_base=0.9,
    ))

    # ===== CONFIGURATION RULES =====

    engine.add_rule(DiagnosticRule(
        name="alsa_udev_broken_goto",
        pattern=r"(?i)(alsa|udev).*(goto|label).*(missing|broken|no matching|ignoring)",
        category=Category.CONFIGURATION,
        cause_template=(
            "ALSA udev rules have GOTO references to non-existent labels. "
            "This is a known alsa-utils packaging bug on Raspberry Pi OS. "
            "Harmless but produces udev errors on every boot."
        ),
        suggestions=[
            "Fix automatically: sudo python3 -c "
            "\"from utils.udev_fix import fix_broken_udev_rules; "
            "print(fix_broken_udev_rules())\"",
            "Or re-run the installer: sudo bash scripts/install_noc.sh",
            "Manual: copy 90-alsa-restore.rules to /etc/udev/rules.d/ "
            "and add the missing LABEL line",
        ],
        confidence_base=0.95,
    ))

    engine.add_rule(DiagnosticRule(
        name="config_file_missing",
        pattern=r"(?i)(config|configuration).*(missing|not found|does not exist)",
        category=Category.CONFIGURATION,
        cause_template="Configuration file is missing",
        suggestions=[
            "Run setup wizard: python3 src/setup_wizard.py",
            "Copy example config: cp config.example.json ~/.config/meshforge/config.json",
            "Check file permissions",
        ],
        confidence_base=0.9,
    ))

    engine.add_rule(DiagnosticRule(
        name="invalid_config",
        pattern=r"(?i)(config|configuration).*(invalid|error|malformed|parse)",
        category=Category.CONFIGURATION,
        cause_template="Configuration file contains errors",
        suggestions=[
            "Validate JSON syntax: python3 -m json.tool < config.json",
            "Check for missing required fields",
            "Compare with example config",
            "Restore from backup if available",
        ],
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="permission_denied",
        pattern=r"(?i)(permission|access).*(denied|forbidden|not allowed)",
        category=Category.CONFIGURATION,
        cause_template="Insufficient permissions to access resource",
        suggestions=[
            "Check file/device permissions: ls -la <path>",
            "Run with sudo for privileged operations",
            "Add user to required groups: sudo usermod -aG dialout,plugdev $USER",
            "Check SELinux/AppArmor if applicable",
        ],
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="port_conflict",
        pattern=r"(?i)(port|address).*(in use|already|occupied|bind.*fail)",
        category=Category.CONFIGURATION,
        cause_template="Network port is already in use by another process",
        suggestions=[
            "Find process using port: sudo lsof -i :<port>",
            "Kill conflicting process or use different port",
            "Check for duplicate service instances",
            "Verify port configuration in settings",
        ],
        confidence_base=0.9,
    ))

    engine.add_rule(DiagnosticRule(
        name="invalid_frequency_setting",
        pattern=r"(?i)(frequency|freq|region).*(invalid|out of range|illegal|not allowed)",
        category=Category.CONFIGURATION,
        cause_template="Radio frequency or region setting is invalid for the hardware/location",
        suggestions=[
            "Check region setting matches your location: meshtastic --get lora.region",
            "Valid regions: US, EU_868, CN, JP, ANZ, KR, TW, RU, IN, NZ_865, TH",
            "Reset to correct region: meshtastic --set lora.region US",
            "Verify antenna matches frequency band",
        ],
        confidence_base=0.9,
    ))

    engine.add_rule(DiagnosticRule(
        name="duplicate_node_id",
        pattern=r"(?i)(node|device).*(duplicate|conflict|already exists|collision).*id",
        category=Category.CONFIGURATION,
        cause_template="Two or more nodes share the same ID, causing routing conflicts",
        suggestions=[
            "Factory reset the duplicate node: meshtastic --factory-reset",
            "Each node must have unique hardware ID",
            "Check mesh for duplicates: meshtastic --nodes",
            "Firmware reflash may regenerate ID",
        ],
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="wrong_modem_preset",
        pattern=r"(?i)(modem|preset|lora).*(wrong|mismatch|incompatible|different)",
        category=Category.CONFIGURATION,
        cause_template="Nodes are using different LoRa modem presets and cannot communicate",
        suggestions=[
            "All nodes on same channel must use same preset",
            "Check current preset: meshtastic --get lora.modem_preset",
            "Set matching preset: meshtastic --set lora.modem_preset LONG_FAST",
            "Available: LONG_FAST, LONG_SLOW, MEDIUM_FAST, SHORT_FAST, etc.",
        ],
        confidence_base=0.9,
    ))

    # ===== CONNECTIVITY RULES (EXTENDED) =====

    engine.add_rule(DiagnosticRule(
        name="dns_resolution_failed",
        pattern=r"(?i)(dns|resolve|lookup|hostname).*(failed|error|timeout|not found)",
        category=Category.CONNECTIVITY,
        cause_template="DNS resolution failed — cannot resolve hostname",
        suggestions=[
            "Check internet connectivity: ping 8.8.8.8",
            "Verify DNS settings: cat /etc/resolv.conf",
            "Try alternative DNS: echo 'nameserver 8.8.8.8' | sudo tee /etc/resolv.conf",
            "Check if running in offline/air-gapped mode",
        ],
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="network_interface_down",
        pattern=r"(?i)(interface|eth|wlan|wifi|network).*(down|no carrier|link.*lost|disconnected)",
        category=Category.CONNECTIVITY,
        cause_template="Network interface is down or has no link",
        suggestions=[
            "Check interface status: ip link show",
            "Bring interface up: sudo ip link set <iface> up",
            "Check physical cable connection",
            "For WiFi: nmcli device wifi connect <SSID>",
        ],
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="mqtt_subscription_lost",
        pattern=r"(?i)mqtt.*(subscription|subscribe).*(lost|failed|disconnect|error)",
        category=Category.CONNECTIVITY,
        cause_template="MQTT topic subscription was lost, no longer receiving messages",
        suggestions=[
            "Reconnect to broker and resubscribe",
            "Check broker logs for disconnect reason",
            "Verify topic permissions on broker",
            "Check for client ID conflicts (two clients with same ID)",
        ],
        auto_recoverable=True,
        recovery_action="Will resubscribe to topics on reconnect",
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="rns_path_not_found",
        pattern=r"(?i)(rns|reticulum).*(path|route|destination).*(not found|unknown|no path)",
        category=Category.CONNECTIVITY,
        cause_template="No RNS path exists to the destination — node may be unreachable",
        suggestions=[
            "Check if destination is announcing: rnpath <destination_hash>",
            "Verify transport nodes are online",
            "Wait for path discovery (can take minutes on mesh)",
            "Check if destination has changed identity",
        ],
        confidence_base=0.75,
    ))

    engine.add_rule(DiagnosticRule(
        name="tcp_handshake_timeout",
        pattern=r"(?i)(tcp|connection).*(handshake|establish).*(timeout|timed out|slow)",
        category=Category.CONNECTIVITY,
        cause_template="TCP connection handshake is timing out — remote host unreachable or slow",
        suggestions=[
            "Check if remote host is reachable: ping <host>",
            "Verify firewall rules: sudo iptables -L",
            "Check if service is listening on remote: nc -zv <host> <port>",
            "Increase connection timeout if on slow network",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="bridge_reconnecting",
        pattern=r"(?i)(bridge|gateway).*(reconnect|retry|backoff|attempt \d+)",
        category=Category.CONNECTIVITY,
        cause_template="Bridge is repeatedly reconnecting, indicating unstable connection",
        suggestions=[
            "Check underlying transport (serial, TCP, MQTT) stability",
            "Review bridge health metrics for error patterns",
            "Check for resource exhaustion on bridge host",
            "Verify both endpoints are stable and responsive",
        ],
        auto_recoverable=True,
        recovery_action="Exponential backoff reconnection in progress",
        confidence_base=0.7,
    ))

    engine.add_rule(DiagnosticRule(
        name="websocket_disconnected",
        pattern=r"(?i)(websocket|ws).*(disconnect|closed|error|failed)",
        category=Category.CONNECTIVITY,
        cause_template="WebSocket connection was closed unexpectedly",
        suggestions=[
            "Check if server process is still running",
            "Verify network stability between client and server",
            "Check for proxy/firewall WebSocket blocking",
            "Review server logs for disconnect reason",
        ],
        auto_recoverable=True,
        recovery_action="Will attempt WebSocket reconnection",
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="api_endpoint_unreachable",
        pattern=r"(?i)(api|endpoint|http).*(unreachable|503|502|504|unavailable)",
        category=Category.CONNECTIVITY,
        cause_template="API endpoint is unreachable or returning server errors",
        suggestions=[
            "Check if API service is running",
            "Verify network connectivity to API host",
            "Check for rate limiting (429 responses)",
            "Try alternative endpoint or fallback",
        ],
        auto_recoverable=True,
        recovery_action="Will retry with exponential backoff",
        confidence_base=0.8,
    ))

    # ===== HARDWARE RULES (EXTENDED) =====

    engine.add_rule(DiagnosticRule(
        name="usb_power_insufficient",
        pattern=r"(?i)(usb|power).*(insufficient|undervolt|brownout|over.?current)",
        category=Category.HARDWARE,
        cause_template="USB port is not providing sufficient power to the device",
        suggestions=[
            "Use a powered USB hub",
            "Try a different USB port (rear ports often have more power)",
            "Use a shorter, higher-quality USB cable",
            "Check dmesg for USB power warnings: dmesg | grep -i 'over-current'",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="gps_lock_lost",
        pattern=r"(?i)(gps|gnss|position|location).*(lost|no fix|no lock|timeout|unavailable)",
        category=Category.HARDWARE,
        cause_template="GPS receiver has lost satellite lock",
        suggestions=[
            "Ensure clear view of sky (GPS needs satellite visibility)",
            "Check GPS antenna connection",
            "Cold start may take 2-15 minutes for first fix",
            "Verify GPS is enabled: meshtastic --get position.gps_enabled",
        ],
        confidence_base=0.75,
    ))

    engine.add_rule(DiagnosticRule(
        name="radio_reset_detected",
        pattern=r"(?i)(radio|lora|sx127|sx126|chip).*(reset|reboot|reinit|watchdog)",
        category=Category.HARDWARE,
        cause_template="Radio chip has reset unexpectedly — possible power or hardware issue",
        suggestions=[
            "Check power supply stability",
            "Verify SPI/I2C connections to radio chip",
            "Check for overheating (feel the device)",
            "Update firmware — may be a known chip driver bug",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="battery_low",
        pattern=r"(?i)(battery|batt|power).*(low|critical|<\s*2[0-9]%|dying|shutdown)",
        category=Category.HARDWARE,
        cause_template="Device battery is critically low",
        suggestions=[
            "Connect to power source immediately",
            "Enable power-saving mode to extend life",
            "Check charging circuit if connected but not charging",
            "Consider solar panel for remote deployments",
        ],
        confidence_base=0.9,
    ))

    engine.add_rule(DiagnosticRule(
        name="overheating",
        pattern=r"(?i)(temperature|thermal|heat|overheat).*(high|warning|critical|throttl)",
        category=Category.HARDWARE,
        cause_template="Device is overheating — may throttle or shut down",
        suggestions=[
            "Move device to shaded/ventilated location",
            "Reduce TX power to lower heat generation",
            "Check for direct sunlight exposure",
            "Add heatsink or ventilation to enclosure",
        ],
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="spi_bus_error",
        pattern=r"(?i)(spi|i2c|bus).*(error|timeout|nak|collision|stuck)",
        category=Category.HARDWARE,
        cause_template="Communication bus error between processor and peripheral",
        suggestions=[
            "Check wiring/connections to peripheral",
            "Verify bus clock speed is within spec",
            "Check for bus contention (multiple devices on same bus)",
            "Power cycle the device",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="firmware_flash_failed",
        pattern=r"(?i)(firmware|flash|update|ota).*(failed|error|abort|corrupt|verify)",
        category=Category.HARDWARE,
        cause_template="Firmware update/flash operation failed",
        suggestions=[
            "Do NOT power off device — retry the flash",
            "Use wired connection (USB) instead of OTA for reliability",
            "Verify firmware file integrity (checksum)",
            "Try recovery/DFU mode if device is bricked",
        ],
        confidence_base=0.9,
    ))

    # ===== PROTOCOL RULES (EXTENDED) =====

    engine.add_rule(DiagnosticRule(
        name="channel_config_mismatch",
        pattern=r"(?i)(channel|freq|frequency).*(mismatch|wrong|different|hop)",
        category=Category.PROTOCOL,
        cause_template="Nodes are on different channels or frequencies",
        suggestions=[
            "Verify all nodes use same channel config",
            "Check channel index: meshtastic --ch-index 0 --info",
            "Share channel via QR code or URL for consistency",
            "Check for accidental frequency offset",
        ],
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="crc_error_high",
        pattern=r"(?i)(crc|checksum|integrity).*(error|fail|mismatch|corrupt|high rate)",
        category=Category.PROTOCOL,
        cause_template="High CRC error rate indicating RF interference or hardware issue",
        suggestions=[
            "Check for nearby RF interference sources",
            "Verify antenna connection (loose SMA = high CRC)",
            "Try different frequency/channel to avoid interference",
            "Check if nodes are too close (receiver saturation)",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="mesh_routing_loop",
        pattern=r"(?i)(routing|route|mesh).*(loop|circular|infinite|ttl.*expir)",
        category=Category.PROTOCOL,
        cause_template="Message routing loop detected — packets circling without delivery",
        suggestions=[
            "Check hop limit settings (default 3, max 7)",
            "Verify no duplicate node IDs in mesh",
            "Reboot nodes to clear stale routing tables",
            "Check for misconfigured relay/router roles",
        ],
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="beacon_timeout",
        pattern=r"(?i)(beacon|heartbeat|keepalive|announce).*(timeout|missed|expired|lost)",
        category=Category.PROTOCOL,
        cause_template="Node beacon/heartbeat not received — node may be offline or out of range",
        suggestions=[
            "Check if node is powered on and operational",
            "Verify RF path between nodes",
            "Node may have moved out of range",
            "Check node's configured beacon interval",
        ],
        confidence_base=0.7,
    ))

    engine.add_rule(DiagnosticRule(
        name="identity_collision",
        pattern=r"(?i)(identity|hash|address).*(collision|duplicate|conflict)",
        category=Category.PROTOCOL,
        cause_template="Identity hash collision — two nodes resolving to same address",
        suggestions=[
            "This is extremely rare — verify it's a real collision",
            "One node should regenerate identity",
            "For RNS: delete identity file and restart",
            "Check for cloned devices (same keys on multiple nodes)",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="lxmf_delivery_failed",
        pattern=r"(?i)(lxmf|message|delivery).*(failed|timeout|undelivered|expired)",
        category=Category.PROTOCOL,
        cause_template="LXMF message delivery failed — destination unreachable or not accepting",
        suggestions=[
            "Check if destination node is online and reachable",
            "Verify LXMF propagation node is available",
            "Message may be queued for later delivery",
            "Check message size (large messages need more airtime)",
        ],
        auto_recoverable=True,
        recovery_action="Message queued for retry on next contact",
        confidence_base=0.75,
    ))

    engine.add_rule(DiagnosticRule(
        name="packet_decode_error",
        pattern=r"(?i)(packet|frame|message).*(decode|deseriali|unmarshal|parse).*(error|fail)",
        category=Category.PROTOCOL,
        cause_template="Received packet could not be decoded — possible version mismatch or corruption",
        suggestions=[
            "Check firmware versions match across nodes",
            "Verify encryption keys are synchronized",
            "May be interference corrupting packets",
            "Check for mixed Meshtastic protocol versions",
        ],
        confidence_base=0.75,
    ))

    # ===== PERFORMANCE RULES (EXTENDED) =====

    engine.add_rule(DiagnosticRule(
        name="high_packet_loss",
        pattern=r"(?i)(packet|message).*(loss|drop|lost|missing).*(high|>\s*[2-9]\d%|\d{2,3}\s*%)",
        category=Category.PERFORMANCE,
        cause_template="High packet loss rate indicating poor RF link or congestion",
        suggestions=[
            "Check signal strength (SNR should be > -10 dB for reliable links)",
            "Improve antenna height or orientation",
            "Reduce message rate to decrease channel congestion",
            "Consider switching to a longer-range LoRa preset",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="latency_spike",
        pattern=r"(?i)(latency|delay|response time).*(spike|high|excessive|>\s*\d+\s*s)",
        category=Category.PERFORMANCE,
        cause_template="Network latency has spiked — congestion or multi-hop delay",
        suggestions=[
            "Check channel utilization (congestion causes queueing delay)",
            "Reduce hop count if possible (each hop adds ~50-200ms)",
            "Check if relay nodes are overloaded",
            "Consider faster modem preset for lower latency",
        ],
        confidence_base=0.75,
    ))

    engine.add_rule(DiagnosticRule(
        name="tx_duty_cycle_exceeded",
        pattern=r"(?i)(duty cycle|tx time|transmit).*(exceed|limit|restrict|legal|regulat)",
        category=Category.PERFORMANCE,
        cause_template="Transmit duty cycle limit exceeded — legally required quiet period",
        suggestions=[
            "Reduce message frequency",
            "Use shorter messages to reduce airtime",
            "Switch to faster modem preset (less airtime per message)",
            "Duty cycle limits are regulatory — cannot be bypassed",
        ],
        confidence_base=0.9,
    ))

    engine.add_rule(DiagnosticRule(
        name="hop_count_excessive",
        pattern=r"(?i)(hop|relay).*(count|limit).*(exceed|too many|max|>\s*[4-7])",
        category=Category.PERFORMANCE,
        cause_template="Message exceeding practical hop limit — each hop degrades reliability",
        suggestions=[
            "Add relay node closer to destination to reduce hop count",
            "Use longer-range preset to skip intermediate hops",
            "Consider infrastructure node placement (elevated, powered)",
            "Maximum reliable hops is typically 3-4 for Meshtastic",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="retransmission_high",
        pattern=r"(?i)(retransmit|retry|resend).*(rate|count|high|excessive|>\s*\d+)",
        category=Category.PERFORMANCE,
        cause_template="High retransmission rate — ACKs not being received",
        suggestions=[
            "Poor link quality causing lost ACKs — improve antenna",
            "Hidden node problem — nodes can hear base but not each other",
            "Reduce traffic to decrease collision probability",
            "Check if destination node is processing messages fast enough",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="airtime_limit_approaching",
        pattern=r"(?i)(airtime|air time).*(limit|approach|warning|>\s*[5-9]\d%)",
        category=Category.PERFORMANCE,
        cause_template="Channel airtime limit is approaching — risk of message drops",
        suggestions=[
            "Reduce message rate across all nodes on this channel",
            "Switch some nodes to a different channel",
            "Use shorter messages",
            "Consider faster LoRa preset to reduce per-message airtime",
        ],
        confidence_base=0.8,
    ))

    # ===== RESOURCE RULES (EXTENDED) =====

    engine.add_rule(DiagnosticRule(
        name="cpu_overload",
        pattern=r"(?i)(cpu|processor|load).*(high|overload|100%|maxed|throttl)",
        category=Category.RESOURCE,
        cause_template="CPU is overloaded, may cause dropped messages or slow responses",
        suggestions=[
            "Check running processes: top -bn1 | head -20",
            "Identify heavy processes: ps aux --sort=-%cpu | head -10",
            "Reduce monitoring frequency if applicable",
            "Check for runaway processes or infinite loops",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="file_descriptor_limit",
        pattern=r"(?i)(file descriptor|fd|too many open|ulimit|EMFILE|ENFILE)",
        category=Category.RESOURCE,
        cause_template="File descriptor limit reached — cannot open new files or sockets",
        suggestions=[
            "Check current limits: ulimit -n",
            "Increase limit: ulimit -n 65536",
            "Find FD-heavy processes: ls -la /proc/*/fd 2>/dev/null | wc -l",
            "Check for connection leaks in application code",
        ],
        confidence_base=0.9,
    ))

    engine.add_rule(DiagnosticRule(
        name="database_corruption",
        pattern=r"(?i)(database|db|sqlite).*(corrupt|malformed|integrity|damaged)",
        category=Category.RESOURCE,
        cause_template="Database file is corrupted — data may be lost",
        suggestions=[
            "Try SQLite integrity check: sqlite3 <db> 'PRAGMA integrity_check'",
            "Restore from backup if available",
            "Delete and recreate database (data loss)",
            "Check for incomplete writes (power loss during write)",
        ],
        confidence_base=0.9,
    ))

    engine.add_rule(DiagnosticRule(
        name="log_file_too_large",
        pattern=r"(?i)(log|journal).*(large|size|growing|full|>\s*\d+\s*[GM]B)",
        category=Category.RESOURCE,
        cause_template="Log files consuming excessive disk space",
        suggestions=[
            "Rotate logs: sudo journalctl --vacuum-size=100M",
            "Set log retention: sudo journalctl --vacuum-time=7d",
            "Check for excessive debug logging",
            "Configure logrotate for application logs",
        ],
        confidence_base=0.8,
    ))

    # ===== SECURITY RULES =====

    engine.add_rule(DiagnosticRule(
        name="unauthorized_access_attempt",
        pattern=r"(?i)(unauthorized|auth|login).*(attempt|failed|denied|invalid|rejected)",
        category=Category.SECURITY,
        cause_template="Unauthorized access attempt detected",
        suggestions=[
            "Review access logs for source of attempts",
            "Verify credentials are correct",
            "Check for brute-force patterns",
            "Consider enabling fail2ban if repeated",
        ],
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="rogue_node_detected",
        pattern=r"(?i)(rogue|unknown|unexpected|foreign).*(node|device|station).*detect",
        category=Category.SECURITY,
        cause_template="Unknown node detected on mesh network — may be unauthorized",
        suggestions=[
            "Verify node ID against known device inventory",
            "Check if new node was authorized by network admin",
            "Enable encryption on all channels",
            "Monitor node for suspicious activity patterns",
        ],
        confidence_base=0.7,
    ))

    engine.add_rule(DiagnosticRule(
        name="key_rotation_needed",
        pattern=r"(?i)(key|certificate|credential).*(expir|rotat|old|stale|renew)",
        category=Category.SECURITY,
        cause_template="Encryption keys or certificates need rotation",
        suggestions=[
            "Rotate channel encryption keys on all nodes",
            "Update TLS certificates before expiry",
            "Regenerate RNS identity if compromised",
            "Document new key distribution to all operators",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="insecure_channel_detected",
        pattern=r"(?i)(channel|link).*(unencrypted|insecure|plaintext|no.*encrypt)",
        category=Category.SECURITY,
        cause_template="Communication channel is not encrypted — messages visible to anyone",
        suggestions=[
            "Enable encryption: meshtastic --ch-set psk random",
            "Use AES256 for sensitive communications",
            "Share keys only through secure channels",
            "Default PSK 'AQ==' is publicly known — always change it",
        ],
        confidence_base=0.9,
    ))

    # ===== MESHTASTIC WEB CLIENT RULES =====

    engine.add_rule(DiagnosticRule(
        name="mqtt_downlink_queue_flood",
        pattern=r"(?i)(tophone|to.?phone).*(queue|buffer).*(full|overflow|discard)",
        category=Category.PERFORMANCE,
        cause_template=(
            "MQTT downlink is flooding the device radio queue. "
            "When MQTT downlink is enabled, the broker echoes every published "
            "packet back into the device's tophone queue, overwhelming the radio. "
            "This causes the meshtasticd web client to hang and packets to be dropped."
        ),
        evidence_checks=[
            make_service_active_check("meshtasticd"),
        ],
        suggestions=[
            "Disable MQTT downlink on primary channel: "
            "meshtastic --host localhost --ch-index 0 --ch-set downlink_enabled false",
            "Or reduce MaxMessageQueue in /etc/meshtasticd/config.yaml",
            "Downlink is only needed for MQTT→radio injection (remote apps sending to mesh)",
            "For uplink-only monitoring (mesh→MQTT), downlink should be OFF",
        ],
        auto_recoverable=False,
        confidence_base=0.9,
    ))

    engine.add_rule(DiagnosticRule(
        name="web_client_phantom_node_crash",
        pattern=r"(?i)(web.*client|browser).*(crash|error|embarrass|broke|hang)",
        category=Category.CONFIGURATION,
        cause_template=(
            "The meshtasticd web client crashes when clicking on phantom nodes — "
            "nodes heard via MQTT with incomplete data (no user object, missing role). "
            "The React UI accesses undefined properties and triggers an error boundary. "
            "This is an upstream bug (meshtastic/web#862)."
        ),
        evidence_checks=[
            make_port_check("localhost", 9443),
            make_service_active_check("meshtasticd"),
        ],
        suggestions=[
            "Use MeshForge Node DB Cleanup: Meshtasticd > Node DB Cleanup > Scan",
            "Reset node database: meshtastic --host localhost --reset-nodedb",
            "Reduce MaxNodes in config.yaml to limit phantom accumulation",
            "Disable MQTT downlink to stop phantom node ingestion",
            "MeshForge API proxy sanitizes nodes when web client is routed through it",
        ],
        auto_recoverable=False,
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="web_client_fromradio_contention",
        pattern=r"(?i)(web.*client|browser).*(hang|freeze|stuck|loading|spinning)",
        category=Category.CONNECTIVITY,
        cause_template=(
            "Multiple clients competing for meshtasticd's /api/v1/fromradio endpoint. "
            "The HTTP API is single-consumer — only one client gets each packet. "
            "If another client (gateway bridge, Python SDK) is connected, the web client "
            "gets starved and hangs waiting for packets that never arrive."
        ),
        evidence_checks=[
            make_port_check("localhost", 9443),
            make_port_check("localhost", 4403),
            make_service_active_check("meshtasticd"),
        ],
        suggestions=[
            "Route web client through MeshForge API proxy (multiplexes packets to all clients)",
            "Disconnect other meshtasticd clients before using web UI",
            "Stop gateway bridge temporarily: systemctl stop meshforge-gateway",
            "Check for MQTT downlink flooding: look for 'tophone queue is full' in logs",
        ],
        auto_recoverable=False,
        confidence_base=0.75,
    ))

    # ===== RNS / NOMADNET COEXISTENCE RULES =====

    engine.add_rule(DiagnosticRule(
        name="rns_interface_rx_only",
        pattern=(
            r"(?i)(interface|rns).*(rx.?only|no.*tx|"
            r"receive.*only|no.*transmit)"
        ),
        category=Category.CONNECTIVITY,
        cause_template=(
            "RNS interface is receiving packets but not transmitting. "
            "Link establishment (SYN/ACK) is failing, typically because "
            "the shared instance port 37428 is not bound or another "
            "process (NomadNet) is holding it."
        ),
        evidence_checks=[
            make_service_active_check("rnsd"),
        ],
        suggestions=[
            "Check port 37428 owner: sudo ss -ulnp | grep 37428",
            "If NomadNet owns port 37428: stop NomadNet, restart "
            "rnsd, then restart NomadNet",
            "Check share_instance = Yes in Reticulum config",
            "Run rnstatus to see per-interface TX/RX counters",
        ],
        auto_recoverable=False,
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="nomadnet_rnsd_port_conflict",
        pattern=(
            r"(?i)(nomadnet|nomad.?net).*(conflict|port|37428|"
            r"shared.?instance|holding)"
        ),
        category=Category.CONFIGURATION,
        cause_template=(
            "NomadNet and rnsd are competing for the RNS shared "
            "instance port (UDP 37428). Both create their own "
            "Reticulum instance with share_instance=Yes, but only "
            "one can bind the port. Correct startup order: rnsd "
            "first, then NomadNet (as client)."
        ),
        evidence_checks=[
            make_service_active_check("rnsd"),
            make_process_check("nomadnet"),
        ],
        suggestions=[
            "Stop NomadNet: pkill -f nomadnet",
            "Restart rnsd: sudo systemctl restart rnsd",
            "Wait for port 37428 to be listening",
            "Start NomadNet (will connect as client to rnsd)",
            "Correct boot order: rnsd -> NomadNet -> MeshForge",
        ],
        auto_recoverable=False,
        confidence_base=0.9,
    ))

    engine.add_rule(DiagnosticRule(
        name="rns_shared_instance_not_listening",
        pattern=(
            r"(?i)(shared.?instance|37428|port).*(not.?listen|"
            r"not.?bound|waiting|unavailable)"
        ),
        category=Category.CONNECTIVITY,
        cause_template=(
            "The RNS shared instance port (UDP 37428) is not bound. "
            "This prevents client applications (NomadNet, rnstatus, "
            "MeshForge gateway) from connecting to rnsd. Causes: "
            "share_instance not enabled, blocking interface "
            "preventing rnsd initialization, or NomadNet holding "
            "the port."
        ),
        evidence_checks=[
            make_service_active_check("rnsd"),
            make_process_check("nomadnet"),
        ],
        suggestions=[
            "Check share_instance = Yes in [reticulum] config",
            "Check for NomadNet port conflict: pgrep -f nomadnet",
            "Check for blocking interfaces: RNS > Diagnostics in "
            "MeshForge",
            "Restart rnsd: sudo systemctl restart rnsd",
            "Check rnsd logs: sudo journalctl -u rnsd -n 30",
        ],
        auto_recoverable=False,
        confidence_base=0.85,
    ))
