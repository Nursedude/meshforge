"""
Knowledge content loaders for MeshForge Knowledge Base.

This module contains all the domain knowledge content that populates
the KnowledgeBase. Separated from knowledge_base.py for maintainability.

These functions are called by KnowledgeBase.__init__() to load content.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .knowledge_base import KnowledgeBase

# Import data classes needed for content creation
from .knowledge_base import (
    KnowledgeEntry,
    KnowledgeTopic,
    TroubleshootingGuide,
    TroubleshootingStep,
)


def load_rf_knowledge(kb: "KnowledgeBase") -> None:
    """Load RF fundamentals knowledge."""

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RF_FUNDAMENTALS,
        title="SNR (Signal-to-Noise Ratio)",
        content="""
SNR measures signal strength relative to background noise in decibels (dB).

For LoRa/Meshtastic:
- SNR > 0 dB: Good signal
- SNR -5 to 0 dB: Acceptable
- SNR -10 to -5 dB: Weak, may have packet loss
- SNR < -15 dB: Very weak, near receive limit

Factors affecting SNR:
1. Distance - Signal strength decreases with distance (inverse square law)
2. Obstacles - Buildings, trees, terrain block/reflect signals
3. Antenna quality - Higher gain antennas improve SNR
4. Interference - Other RF sources on same frequency
5. Antenna orientation - LoRa antennas are usually vertically polarized

Improvement strategies:
- Raise antenna height
- Use higher gain antenna
- Improve line of sight
- Reduce interference sources
- Add relay nodes to shorten hops
""",
        keywords=["snr", "signal", "noise", "weak signal", "reception", "decibels", "db"],
        expertise_level="novice",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RF_FUNDAMENTALS,
        title="RSSI (Received Signal Strength Indicator)",
        content="""
RSSI measures absolute received signal power in dBm.

Typical values for LoRa:
- -50 to -70 dBm: Excellent (very close)
- -70 to -90 dBm: Good
- -90 to -110 dBm: Fair
- -110 to -120 dBm: Weak
- Below -120 dBm: At receiver sensitivity limit

Unlike SNR, RSSI doesn't account for noise floor.
Use both metrics together:
- High RSSI + High SNR = Good link
- Low RSSI + Good SNR = Weak but clean signal
- High RSSI + Low SNR = Strong interference present
""",
        keywords=["rssi", "signal strength", "dbm", "power", "received"],
        related_entries=["SNR (Signal-to-Noise Ratio)"],
        expertise_level="intermediate",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RF_FUNDAMENTALS,
        title="LoRa Spreading Factor",
        content="""
Spreading Factor (SF) is a key LoRa parameter that trades range for speed.

SF7: Fastest, shortest range
SF8-SF11: Intermediate
SF12: Slowest, longest range

Each SF increase roughly doubles airtime and range.

Meshtastic presets map to these SFs:
- SHORT_FAST: SF7 (1-3 km urban)
- SHORT_SLOW: SF8
- MEDIUM_FAST: SF9 (~5 km)
- MEDIUM_SLOW: SF10
- LONG_FAST: SF11 (~10 km) - Default
- LONG_SLOW: SF12 (20+ km line of sight)

Higher SF = Better sensitivity but:
- Longer time on air (more battery)
- Higher channel utilization
- Fewer messages per hour allowed
""",
        keywords=["spreading factor", "sf", "range", "lora", "preset", "speed"],
        expertise_level="intermediate",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RF_FUNDAMENTALS,
        title="Channel Utilization",
        content="""
Channel utilization indicates how busy the radio frequency is.

Measured as percentage of time the channel is in use:
- 0-25%: Light usage, plenty of capacity
- 25-50%: Moderate, still good
- 50-75%: Heavy, delays likely
- >75%: Congested, packet loss expected

Meshtastic enforces duty cycle limits:
- Maximum 10% transmit duty cycle (regulatory)
- Messages queued when channel busy
- Priority given to routing/ACK packets

Reducing channel utilization:
- Send fewer/shorter messages
- Use higher data rate (lower SF)
- Spread across multiple channels
- Use MQTT for non-critical traffic
""",
        keywords=["channel utilization", "duty cycle", "congestion", "busy", "airtime"],
        expertise_level="intermediate",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RF_FUNDAMENTALS,
        title="Fresnel Zone",
        content="""
The Fresnel zone is an elliptical area around the line of sight that must be
clear for optimal RF propagation.

For LoRa at 915 MHz (US), the first Fresnel zone radius at midpoint:
- 1 km link: ~9 meters clearance needed
- 5 km link: ~20 meters clearance needed
- 10 km link: ~28 meters clearance needed

If obstacles intrude into >40% of Fresnel zone, signal loss increases significantly.

Practical implications:
- Antenna height matters more than you think
- A "clear" visual line of sight may not be RF clear
- Lakes/water are excellent reflectors
- Hills mid-path are worse than hills at endpoints

This is why rooftop antennas dramatically outperform ground-level ones,
even with "clear" line of sight.
""",
        keywords=["fresnel", "line of sight", "los", "clearance", "propagation", "height"],
        expertise_level="expert",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RF_FUNDAMENTALS,
        title="Signal Quality Classification",
        content="""
Signal quality is classified based on both SNR and RSSI together:

EXCELLENT (reliable, high margin):
- SNR >= -3 dB AND RSSI >= -100 dBm
- Strong signal, well above noise floor
- Expect near 100% packet delivery

GOOD (normal operation):
- SNR >= -7 dB AND RSSI >= -115 dBm
- Standard quality for reliable mesh operation
- Occasional retransmits may occur

FAIR (usable but weak):
- SNR >= -15 dB AND RSSI >= -126 dBm
- May experience packet loss
- Consider improving antenna/position

BAD (unreliable):
- Below FAIR thresholds
- High packet loss expected
- Link may drop frequently

Link Margin:
The difference between received signal and receiver sensitivity.
- SF11 sensitivity: -134.5 dBm
- SF12 sensitivity: -137 dBm
- 10+ dB margin recommended for reliability

These thresholds are based on the meshtastic-go library and MeshTenna
antenna testing tool.
""",
        keywords=["signal quality", "classification", "good", "bad", "fair", "threshold", "link margin"],
        related_entries=["SNR (Signal-to-Noise Ratio)", "RSSI (Received Signal Strength Indicator)"],
        expertise_level="intermediate",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RF_FUNDAMENTALS,
        title="Antenna Testing",
        content="""
Proper antenna testing ensures your system performs optimally.

Equipment:
- VNA (Vector Network Analyzer) for SWR/impedance
- NanoVNA is affordable (~$50) for hobbyists
- Alternatively: SWR meter inline during TX

Key Measurements:

SWR (Standing Wave Ratio):
- 1.0:1 = Perfect (impossible in practice)
- <1.5:1 = Excellent
- <2.0:1 = Good
- >3.0:1 = Poor, significant power loss

Return Loss:
- >20 dB = Excellent (<1% reflected)
- >14 dB = Good (<4% reflected)
- <10 dB = Poor (>10% reflected)

Resonant Frequency:
- Antenna should resonate at your operating frequency
- Off-resonance = higher SWR, reduced efficiency
- Many cheap antennas are mis-labeled

Cable and Connector Losses (at 915 MHz):
- RG174: ~0.9 dB/m (high loss, avoid for runs >1m)
- RG58: ~0.5 dB/m
- LMR400: ~0.15 dB/m (low loss, recommended)
- SMA connector: ~0.1 dB each
- Every connector/meter of cable reduces your signal

Best Practices:
- Keep cable runs as short as possible
- Use quality low-loss coax for longer runs
- Never close a window on coax cable
- Waterproof outdoor connections
- Mount antenna vertically for LoRa (vertical polarization)

Reference: MeshTenna antenna testing tool
""",
        keywords=["antenna", "testing", "vna", "swr", "return loss", "cable", "connector", "impedance"],
        related_entries=["Fresnel Zone"],
        expertise_level="expert",
    ))


def load_meshtastic_knowledge(kb: "KnowledgeBase") -> None:
    """Load Meshtastic-specific knowledge."""

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.MESHTASTIC,
        title="Meshtastic Node Roles",
        content="""
Meshtastic nodes can have different roles:

CLIENT (default):
- Normal node for sending/receiving messages
- Participates in mesh routing
- Good for mobile/portable use

CLIENT_MUTE:
- Receives all messages
- Does not transmit (stealth mode)
- Does not route for others

ROUTER:
- Optimized for routing/relaying
- Always on, never sleeps
- Higher priority for routing decisions
- Usually solar/mains powered

ROUTER_CLIENT:
- Hybrid router that also uses device
- Routes + normal messaging
- Good for home base stations

REPEATER:
- Pure relay, no user interface
- Minimal protocol overhead
- Ideal for remote hilltop repeaters
- Should be paired with router-role node

TRACKER:
- Optimized for GPS tracking
- Minimal other traffic
- Higher position update rate
""",
        keywords=["role", "router", "client", "repeater", "tracker", "node type"],
        expertise_level="novice",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.MESHTASTIC,
        title="Meshtastic Channels",
        content="""
Meshtastic supports multiple channels for message segregation.

Channel 0: Primary channel
- Required, always exists
- Used for node discovery and routing
- Default encryption key: "AQ==" (LongFast)

Channels 1-7: Secondary channels
- Optional additional channels
- Can have different encryption keys
- Useful for different groups/purposes

Each channel has:
- Name (human readable)
- PSK (Pre-Shared Key) for encryption
- Uplink/Downlink settings for MQTT

MQTT integration:
- Channels can be bridged to MQTT
- Uplink: Send messages to MQTT broker
- Downlink: Receive messages from MQTT
- Enables internet connectivity for mesh
""",
        keywords=["channel", "encryption", "psk", "key", "mqtt", "uplink", "downlink"],
        related_entries=["MQTT for Meshtastic"],
        expertise_level="intermediate",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.MESHTASTIC,
        title="meshtasticd Daemon",
        content="""
meshtasticd is the Linux daemon for Meshtastic radio access.

Purpose:
- Provides TCP/IP interface to Meshtastic radio
- Allows multiple clients (with limitations)
- Runs as system service

Configuration: /etc/meshtasticd/config.yaml
- Serial port settings
- TCP port (default 4403)
- Logging configuration

Common issues:
1. Only ONE client can hold write lock
   - MeshForge, nomadnet, meshtastic CLI compete
   - Solution: Close other clients

2. Serial port permissions
   - User needs dialout group membership
   - Or run as root (not recommended)

3. Device hot-plug
   - Daemon may not detect device changes
   - Restart after connecting/disconnecting radio

Commands:
- sudo systemctl status meshtasticd
- sudo systemctl restart meshtasticd
- journalctl -u meshtasticd -f
""",
        keywords=["meshtasticd", "daemon", "service", "tcp", "4403", "linux"],
        expertise_level="intermediate",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.MESHTASTIC,
        title="Meshtastic Telemetry Sensors",
        content="""
Meshtastic supports various telemetry sensors via I2C bus:

Device Metrics (built-in):
- Battery level and voltage
- Channel utilization (how busy the RF channel is)
- TX airtime (transmit duty cycle)
- Uptime

Environment Sensors (I2C):
- BME280: Temperature, humidity, barometric pressure (~$5-10)
- BME680: Same as BME280 + VOC gas sensor (~$15)
- BMP280: Temperature and pressure only (~$3)
- SHT31: High-accuracy temperature and humidity
- Sensors auto-detected on I2C bus at startup

Air Quality Sensors:
- PMSA003I: Particulate matter (PM1.0, PM2.5, PM10) (~$40)
- SCD4X: CO2 concentration (~$50)
- Good for environmental monitoring stations

Health Sensors:
- MAX30102: Heart rate and SpO2 (blood oxygen)
- Body temperature sensors

Configuration:
- Enable in Meshtastic app: Settings > Module Configuration > Telemetry
- Default broadcast interval: 30 minutes
- Can adjust interval for more/less frequent updates

Use Cases:
- Weather stations at remote locations
- Air quality monitoring network
- Solar-powered environmental sensors
- Garden/greenhouse monitoring
""",
        keywords=["telemetry", "sensor", "bme280", "temperature", "humidity", "air quality", "pm2.5", "environment"],
        expertise_level="intermediate",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.MESHTASTIC,
        title="Meshtastic Detection Sensor Module",
        content="""
The Detection Sensor module monitors GPIO pins for state changes and sends
alerts over the mesh network.

Use Cases:
- Motion detection (PIR sensors like HC-SR501)
- Door/window sensors (reed switches)
- Water leak detection
- Intrusion alerts for remote locations
- Tripwire-style security

Configuration Options:
- Monitor Pin: GPIO pin to watch
- Detection Triggered High: Is HIGH (1) the triggered state?
- Use Pull-up: Enable internal pull-up resistor
- Name: Alert name (e.g., "Motion" -> "Motion detected")
- Min Broadcast Interval: Minimum seconds between alerts
- State Broadcast Interval: Heartbeat interval (0 = only on change)

Hardware Notes:
- HC-SR501 PIR: Requires 5V, may not work on battery
- Reed switches: Work with 3.3V, very low power
- Choose GPIO pins not used by other functions
- Check your board's available GPIO pins

Alert Format:
When triggered, sends message: "{Name} detected" or "{Name} clear"
Example: "Motion detected" or "Door clear"

Requires firmware 2.2.2 or higher.
""",
        keywords=["detection", "sensor", "gpio", "motion", "pir", "reed", "switch", "alert", "security"],
        related_entries=["Meshtastic Telemetry Sensors"],
        expertise_level="intermediate",
    ))


def load_reticulum_knowledge(kb: "KnowledgeBase") -> None:
    """Load Reticulum-specific knowledge."""

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RETICULUM,
        title="Reticulum Network Stack",
        content="""
Reticulum (RNS) is a cryptographic networking stack for reliable communication
over high-latency, low-bandwidth links.

Key concepts:
- Identity-based addressing (no IP addresses)
- End-to-end encryption by default
- Works over any transport (LoRa, TCP, I2P, etc.)
- Automatic routing and path discovery

Components:
- rnsd: Reticulum daemon
- nomadnet: Text-based messaging app
- LXMF: Messaging format
- Sideband: Mobile app

For MeshForge:
- RNS provides the "other" mesh network
- Gateway bridges Meshtastic ↔ RNS
- Different addressing schemes (hash vs node ID)
""",
        keywords=["reticulum", "rns", "cryptographic", "lxmf", "nomadnet", "identity"],
        expertise_level="intermediate",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RETICULUM,
        title="RNS Interfaces",
        content="""
Reticulum supports multiple transport interfaces.

TCPClientInterface:
- Connect to remote RNS node via TCP
- Used for internet bridging
- Config: target_host, target_port

TCPServerInterface:
- Accept incoming TCP connections
- Run as hub for other nodes

SerialInterface:
- Direct serial connection
- For LoRa modems, packet radio

RNodeInterface:
- For RNode hardware (LoRa modem)
- Most common for RF mesh

LocalInterface:
- Loopback for local apps
- Always enabled

AutoInterface:
- Automatic peer discovery
- Uses UDP multicast on LAN
- Great for local testing

Configuration in: ~/.reticulum/config
""",
        keywords=["interface", "tcp", "serial", "rnode", "transport", "config"],
        expertise_level="intermediate",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RETICULUM,
        title="RNS Cryptography",
        content="""
Reticulum uses strong, well-established cryptographic primitives:

Identity & Addressing:
- 512-bit Curve25519 keysets (Ed25519 + X25519)
- No source addresses on packets (initiator anonymity)
- Destination addresses are cryptographic hashes
- Globally unique without central coordination

Encryption:
- AES-256-CBC encryption with PKCS7 padding
- HMAC-SHA256 for authentication
- Forward secrecy via ephemeral ECDH exchanges
- Per-packet keys for privacy

Link Establishment:
- Only 3 packets (297 bytes) to establish encrypted link
- Link overhead: 0.44 bits per second
- Unforgeable delivery confirmations

This means:
- Messages are encrypted end-to-end by default
- No trust in network infrastructure required
- Even relay nodes cannot read message contents
- Identity is provable via cryptographic signatures

For MeshForge gateway:
- Each side maintains its own identity
- Bridge must have valid RNS identity to participate
- Messages re-encrypted across network boundary
""",
        keywords=["cryptography", "encryption", "aes", "curve25519", "ed25519", "identity", "security"],
        related_entries=["Reticulum Network Stack"],
        expertise_level="expert",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RETICULUM,
        title="RNS Node Discovery",
        content="""
Reticulum uses an announce-based discovery system:

Announces:
- Nodes broadcast their identity and destination hash
- Public key shared for others to route to you
- App data can include display name, capabilities
- Without announcing, you are invisible on the network

Path Discovery:
- Automatic multi-hop path finding
- Transport layer maintains path table
- Paths expire and refresh automatically
- No central routing authority

Network Visualizer (like MeshChat):
- Shows announced nodes and their connectivity
- Tracks path hops to each destination
- Displays announce timestamps
- Helps understand network topology

Bootstrap:
- New nodes connect to known peers
- Temporary bootstrap links discover local infrastructure
- System automatically forms stronger direct links
- Bootstrap connections can be discarded after discovery

For MeshForge:
- Use list_known_destinations() to see known nodes
- Use discover_nodes() for active discovery
- Monitor Transport.path_table for topology
- Check Identity.known_destinations for all seen nodes
""",
        keywords=["discovery", "announce", "path", "routing", "bootstrap", "visualizer", "topology"],
        related_entries=["RNS Interfaces"],
        expertise_level="intermediate",
    ))


def load_hardware_knowledge(kb: "KnowledgeBase") -> None:
    """Load hardware-related knowledge."""

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.HARDWARE,
        title="Common Meshtastic Hardware",
        content="""
Popular Meshtastic-compatible devices:

LILYGO T-Beam:
- ESP32 + SX1276/SX1262 LoRa
- Built-in GPS, 18650 battery holder
- Good balance of features
- ~$30-40

Heltec V3:
- ESP32-S3 + SX1262
- Small OLED display
- Compact form factor
- ~$20-25

RAK WisBlock:
- Modular design
- nRF52840 + SX1262
- Low power, long battery life
- Professional quality

Station G2:
- Higher power output (1W)
- Better range
- Larger, not portable
- ~$80-100

For MeshForge as base station:
- Raspberry Pi + USB serial modem
- Or SPI-connected LoRa module
- meshtasticd handles radio access
""",
        keywords=["hardware", "tbeam", "heltec", "rak", "device", "radio", "esp32"],
        expertise_level="novice",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.HARDWARE,
        title="Serial Port Troubleshooting",
        content="""
Serial port issues are common with Meshtastic/RNS devices.

Finding your device:
ls /dev/ttyUSB* /dev/ttyACM*
dmesg | grep -i tty

Permission denied:
sudo usermod -aG dialout $USER
# Then logout/login

Device busy:
lsof /dev/ttyUSB0
# Kill the blocking process

Device not found:
- Check USB cable (data cable, not charge-only)
- Try different USB port
- Check dmesg for errors
- May need CH340/CP2102 driver

For Raspberry Pi:
- Disable Bluetooth to free /dev/ttyAMA0
- Edit /boot/config.txt: dtoverlay=disable-bt
- Reboot

Multiple devices:
- Use /dev/serial/by-id/ for stable names
- Prevents confusion when USB order changes
""",
        keywords=["serial", "tty", "usb", "permission", "port", "device"],
        expertise_level="intermediate",
    ))


def load_troubleshooting_guides(kb: "KnowledgeBase") -> None:
    """Load troubleshooting guides."""

    kb._add_guide(TroubleshootingGuide(
        problem="no_connection_meshtasticd",
        description="Cannot connect to meshtasticd service",
        prerequisites=["meshtasticd installed", "Meshtastic device connected"],
        steps=[
            TroubleshootingStep(
                instruction="Check if meshtasticd is running",
                command="sudo systemctl status meshtasticd",
                expected_result="Active: active (running)",
                if_fail="Start the service: sudo systemctl start meshtasticd",
            ),
            TroubleshootingStep(
                instruction="Check if port 4403 is listening",
                command="ss -tlnp | grep 4403",
                expected_result="LISTEN ... :4403",
                if_fail="Service may have crashed, check logs",
            ),
            TroubleshootingStep(
                instruction="Check for other clients",
                command="ss -tnp | grep 4403",
                expected_result="No established connections or only MeshForge",
                if_fail="Another client is connected, close it first",
            ),
            TroubleshootingStep(
                instruction="Check meshtasticd logs for errors",
                command="journalctl -u meshtasticd -n 50",
                expected_result="No ERROR or CRITICAL messages",
            ),
            TroubleshootingStep(
                instruction="Restart meshtasticd and try again",
                command="sudo systemctl restart meshtasticd",
            ),
        ],
        related_problems=["serial_port_issues", "device_not_found"],
    ))

    kb._add_guide(TroubleshootingGuide(
        problem="weak_signal",
        description="Nodes have weak signal (low SNR/RSSI)",
        prerequisites=["Nodes are powered on", "Basic connectivity exists"],
        steps=[
            TroubleshootingStep(
                instruction="Check current SNR and RSSI values",
                command="meshtastic --nodes",
                expected_result="SNR > -10, RSSI > -110",
            ),
            TroubleshootingStep(
                instruction="Verify antenna is properly connected",
                expected_result="Antenna screwed on tightly, correct frequency band",
                if_fail="Transmitting without antenna can damage radio!",
            ),
            TroubleshootingStep(
                instruction="Check antenna orientation",
                expected_result="Antenna vertical for maximum range",
                if_fail="Horizontal antennas have different pattern",
            ),
            TroubleshootingStep(
                instruction="Increase antenna height if possible",
                expected_result="Even 1-2 meters height can double range",
            ),
            TroubleshootingStep(
                instruction="Check for obstructions in RF path",
                expected_result="Clear line of sight to other node",
                if_fail="Consider relay node or better antenna placement",
            ),
            TroubleshootingStep(
                instruction="Consider changing modem preset for more range",
                command="meshtastic --set lora.modem_preset LONG_SLOW",
                expected_result="Longer range but slower data rate",
            ),
        ],
    ))

    kb._add_guide(TroubleshootingGuide(
        problem="high_channel_utilization",
        description="Channel utilization consistently above 50%",
        steps=[
            TroubleshootingStep(
                instruction="Check current channel utilization",
                command="meshtastic --info | grep -i util",
                expected_result="Channel utilization < 25%",
            ),
            TroubleshootingStep(
                instruction="Identify message sources",
                expected_result="Determine which nodes are sending most traffic",
            ),
            TroubleshootingStep(
                instruction="Reduce position broadcast rate",
                command="meshtastic --set position.position_broadcast_secs 900",
                expected_result="Position updates every 15 minutes instead of default",
            ),
            TroubleshootingStep(
                instruction="Use faster modem preset if range allows",
                command="meshtastic --set lora.modem_preset MEDIUM_FAST",
                expected_result="Shorter air time per message",
            ),
            TroubleshootingStep(
                instruction="Move high-volume traffic to MQTT",
                expected_result="Telemetry via MQTT reduces RF usage",
            ),
        ],
    ))


def load_best_practices(kb: "KnowledgeBase") -> None:
    """Load best practices knowledge."""

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.BEST_PRACTICES,
        title="MeshForge Deployment Best Practices",
        content="""
Recommended practices for MeshForge deployment:

NETWORK DESIGN:
1. Start with minimum viable mesh (3-4 nodes)
2. Test RF links before adding complexity
3. Place router nodes at high points
4. Use MQTT for internet connectivity

GATEWAY CONFIGURATION:
1. Run MeshForge on stable power (not battery)
2. Use wired Ethernet when possible
3. Configure reasonable queue sizes
4. Enable message persistence

MONITORING:
1. Check diagnostic panel regularly
2. Set up alerts for critical issues
3. Monitor channel utilization
4. Track node battery levels

SECURITY:
1. Change default channel keys
2. Use TLS for MQTT connections
3. Don't expose services to internet directly
4. Keep firmware updated

RELIABILITY:
1. Test failover scenarios
2. Have backup power (UPS)
3. Document your configuration
4. Regular backups of config files
""",
        keywords=["best practices", "deployment", "setup", "configuration", "security"],
        expertise_level="intermediate",
    ))


def load_rns_troubleshooting(kb: "KnowledgeBase") -> None:
    """Load RNS troubleshooting knowledge."""

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RETICULUM,
        title="RNS Identity Management",
        content="""
RNS identities are persistent cryptographic keysets stored on disk.

Identity Location:
- Default: ~/.reticulum/storage/identities/
- Each identity is a 512-bit Curve25519 keyset
- Identity hash = first 128 bits of SHA-256 of public key
- This hash IS the network address

Creating Identity:
  import RNS
  identity = RNS.Identity()  # Generates new keypair
  identity.to_file("/path/to/identity")

Loading Identity:
  identity = RNS.Identity.from_file("/path/to/identity")

Common Issues:
1. Lost identity file = lost network address
   - Other nodes can't reach you at old address
   - Must re-announce with new identity
   - Solution: Back up identity files!

2. Duplicate identity (cloned SD card):
   - Two nodes with same keys = routing confusion
   - Delete identity on one and restart rnsd
   - Fresh identity will be generated

3. Identity not announcing:
   - Check destination is registered
   - Verify rnsd is running
   - Check interface connectivity

For MeshForge:
- Gateway bridge needs stable identity
- Back up: ~/.reticulum/storage/
- Identity hash displayed in bridge status
""",
        keywords=["identity", "keys", "address", "hash", "announce", "backup", "cryptographic"],
        related_entries=["RNS Cryptography", "RNS Node Discovery"],
        expertise_level="intermediate",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RETICULUM,
        title="RNS Transport and Routing",
        content="""
RNS Transport handles multi-hop routing across heterogeneous networks.

Transport Nodes:
- Regular node: Only communicates with direct neighbors
- Transport node: Relays traffic between non-adjacent nodes
- Enable transport: transport_enabled = Yes in config

Path Table:
- Maintained automatically by Transport layer
- Entries: destination_hash -> next_hop_interface
- Paths expire after 2 hours (configurable)
- Refreshed by announces and traffic

Routing Process:
1. Source sends packet with destination hash
2. Each transport node checks path table
3. If path known: forward to next hop
4. If unknown: packet is dropped (no flooding)

Path Discovery:
- Passive: Listen for announces
- Active: Transport.request_path(destination_hash)
- Path requests propagate through transport network
- Response contains full path back

Rate Limiting:
- Announces rate-limited to prevent flooding
- Default: 1 announce per 600 seconds per destination
- Can be adjusted but don't set too low

For MeshForge gateway:
- Should run as transport node for better connectivity
- Monitor Transport.path_table for network topology
- High path_table churn = network instability
""",
        keywords=["transport", "routing", "path", "hop", "relay", "table", "forward"],
        related_entries=["RNS Node Discovery", "RNS Interfaces"],
        expertise_level="expert",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RETICULUM,
        title="LXMF Message Protocol",
        content="""
LXMF (Lightweight Extensible Message Format) is the messaging layer on RNS.

Message Types:
- Single packet: Small messages (<500 bytes), delivered directly
- Resource transfer: Larger messages, uses RNS Links for reliable delivery
- Propagation: Messages stored at intermediate nodes for offline recipients

Delivery Modes:
1. Direct: Source → Destination (both must be online)
2. Propagated: Source → Propagation Node → Destination (async delivery)

Propagation Nodes:
- Store messages for offline destinations
- Forward when destination comes online
- Message TTL (time to live) prevents indefinite storage
- Multiple propagation nodes for redundancy

Message Structure:
- Source identity (sender)
- Destination identity (recipient)
- Timestamp
- Content (plaintext or encrypted payload)
- Signature (proves sender authenticity)

For MeshForge bridge:
- Meshtastic messages converted to LXMF format
- DeliveryTracker monitors confirmation callbacks
- Timeout = assume delivery failed
- Queue re-attempts automatically

Common Issues:
- Message never delivered: Destination offline + no propagation node
- Duplicate messages: Retry logic without deduplication
- Large messages fail: Split into chunks or use resource transfer
""",
        keywords=["lxmf", "message", "delivery", "propagation", "offline", "format"],
        related_entries=["Reticulum Network Stack", "RNS Transport and Routing"],
        expertise_level="intermediate",
    ))

    kb._add_guide(TroubleshootingGuide(
        problem="rnsd_not_starting",
        description="rnsd daemon fails to start or crashes on startup",
        prerequisites=["Reticulum installed", "Python 3 available"],
        steps=[
            TroubleshootingStep(
                instruction="Check rnsd service status",
                command="sudo systemctl status rnsd",
                expected_result="Active: active (running)",
                if_fail="Check error message in status output",
            ),
            TroubleshootingStep(
                instruction="Check for config file errors",
                command="cat ~/.reticulum/config",
                expected_result="Valid YAML/config format with interfaces defined",
                if_fail="Delete config and restart — fresh config will be generated",
            ),
            TroubleshootingStep(
                instruction="Verify Python RNS package is installed",
                command="python3 -c 'import RNS; print(RNS.__version__)'",
                expected_result="Version number printed (e.g., 0.7.3)",
                if_fail="Install: pipx install rns",
            ),
            TroubleshootingStep(
                instruction="Check for port conflicts on AutoInterface",
                command="ss -ulnp | grep 29716",
                expected_result="Nothing or only rnsd using the port",
                if_fail="Kill conflicting process: kill <PID>",
            ),
            TroubleshootingStep(
                instruction="Check interface device exists",
                command="ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null",
                expected_result="Device file exists if using SerialInterface",
                if_fail="Connect device and check dmesg for USB errors",
            ),
            TroubleshootingStep(
                instruction="Try running rnsd in foreground for debug output",
                command="rnsd -v",
                expected_result="Verbose output showing interface initialization",
            ),
        ],
        related_problems=["no_connection_meshtasticd", "serial_port_issues"],
    ))

    kb._add_guide(TroubleshootingGuide(
        problem="rnsd_ratchets_permission",
        description="rnsd crashes with PermissionError on /etc/reticulum/storage/ subdirectories (ratchets, resources, cache)",
        prerequisites=["rnsd installed", "Using system-wide config at /etc/reticulum/"],
        steps=[
            TroubleshootingStep(
                instruction="Check if required storage subdirectories exist",
                command="ls -la /etc/reticulum/storage/{ratchets,resources,cache/announces}",
                expected_result="All directories exist with write permissions",
                if_fail="One or more directories missing — RNS needs ratchets/ (key ratcheting), resources/ (resource storage), cache/announces/ (transport)",
            ),
            TroubleshootingStep(
                instruction="Create all required directories with correct permissions",
                command="sudo mkdir -p /etc/reticulum/storage/{ratchets,resources,cache/announces} && sudo chmod 755 /etc/reticulum/storage/{ratchets,resources,cache/announces}",
                expected_result="Directories created successfully",
                if_fail="Check filesystem is not mounted read-only",
            ),
            TroubleshootingStep(
                instruction="Restart rnsd to verify the fix",
                command="sudo systemctl restart rnsd",
                expected_result="Active: active (running)",
                if_fail="Check journalctl -u rnsd for other errors",
            ),
        ],
        related_problems=["rnsd_not_starting"],
    ))

    kb._add_guide(TroubleshootingGuide(
        problem="rns_path_failure",
        description="Cannot reach RNS destination — path not found",
        prerequisites=["rnsd running", "At least one interface active"],
        steps=[
            TroubleshootingStep(
                instruction="Check if destination has announced recently",
                command="rnpath <destination_hash>",
                expected_result="Path found with hop count",
                if_fail="Destination may be offline or out of range",
            ),
            TroubleshootingStep(
                instruction="Check your interfaces are active",
                command="rnstatus",
                expected_result="Interfaces shown with RX/TX byte counts",
                if_fail="Interface may be misconfigured or disconnected",
            ),
            TroubleshootingStep(
                instruction="Verify transport nodes are available",
                expected_result="At least one transport node should be reachable",
                if_fail="Run a transport node yourself or find one on the network",
            ),
            TroubleshootingStep(
                instruction="Wait for path discovery (especially on mesh)",
                expected_result="Paths can take minutes to propagate on LoRa",
                if_fail="Try requesting path explicitly: rnpath -r <hash>",
            ),
            TroubleshootingStep(
                instruction="Check if announce is reaching network",
                command="rnid -a",
                expected_result="Announce sent successfully",
            ),
        ],
        related_problems=["rnsd_not_starting", "weak_signal"],
    ))

    kb._add_guide(TroubleshootingGuide(
        problem="rns_interface_config",
        description="RNS interface configuration issues",
        prerequisites=["rnsd installed", "Hardware connected"],
        steps=[
            TroubleshootingStep(
                instruction="Generate fresh default config if needed",
                command="rnsd --config-generate",
                expected_result="Config file created at ~/.reticulum/config",
            ),
            TroubleshootingStep(
                instruction="For RNode: verify device detection",
                command="rnodeconf -a /dev/ttyUSB0",
                expected_result="RNode info displayed",
                if_fail="Device may not be an RNode — check firmware",
            ),
            TroubleshootingStep(
                instruction="For TCP interface: check connectivity",
                command="nc -zv <host> <port>",
                expected_result="Connection succeeded",
                if_fail="Check host:port and network/firewall",
            ),
            TroubleshootingStep(
                instruction="Verify config syntax (common YAML errors)",
                expected_result="Correct indentation (2 spaces), no tabs",
                if_fail="YAML is whitespace-sensitive — check indentation",
            ),
            TroubleshootingStep(
                instruction="Check interface enabled flag",
                expected_result="interface_enabled = True for each interface",
                if_fail="Set interface_enabled = True and restart rnsd",
            ),
        ],
    ))

    # --- NomadNet / rnsd coexistence ---

    kb._add_guide(TroubleshootingGuide(
        problem="nomadnet_rnsd_coexistence",
        description=(
            "NomadNet and rnsd competing for shared instance "
            "port 37428"
        ),
        prerequisites=["rnsd installed", "NomadNet installed"],
        steps=[
            TroubleshootingStep(
                instruction="Check who owns port 37428",
                command="sudo ss -ulnp | grep 37428",
                expected_result="rnsd should own the port",
                if_fail=(
                    "If NomadNet owns it, startup order is wrong"
                ),
            ),
            TroubleshootingStep(
                instruction="Stop NomadNet",
                command="pkill -f nomadnet",
                expected_result="NomadNet processes terminated",
            ),
            TroubleshootingStep(
                instruction="Restart rnsd so it claims the port",
                command="sudo systemctl restart rnsd",
                expected_result="Active: active (running)",
                if_fail=(
                    "Check journalctl -u rnsd for errors"
                ),
            ),
            TroubleshootingStep(
                instruction=(
                    "Verify rnsd owns port 37428"
                ),
                command="sudo ss -ulnp | grep 37428",
                expected_result="rnsd shown as port owner",
                if_fail=(
                    "Check share_instance = Yes in config"
                ),
            ),
            TroubleshootingStep(
                instruction=(
                    "Start NomadNet (connects as client)"
                ),
                command="nomadnet --daemon",
                expected_result=(
                    "NomadNet connects to rnsd shared instance"
                ),
                if_fail=(
                    "Check NomadNet logfile: "
                    "~/.nomadnetwork/logfile"
                ),
            ),
        ],
        related_problems=[
            "rnsd_not_starting", "rns_path_failure",
        ],
    ))

    kb._add_guide(TroubleshootingGuide(
        problem="rns_interface_rx_only",
        description=(
            "RNS interfaces show RX traffic but zero TX — "
            "link establishment failing"
        ),
        prerequisites=[
            "rnsd running",
            "At least one interface enabled",
        ],
        steps=[
            TroubleshootingStep(
                instruction=(
                    "Check interface TX/RX counters"
                ),
                command="rnstatus",
                expected_result=(
                    "Both TX and RX byte counts should be "
                    "non-zero"
                ),
                if_fail=(
                    "Interfaces with 0 TX cannot establish "
                    "links"
                ),
            ),
            TroubleshootingStep(
                instruction=(
                    "Check if shared instance port is "
                    "listening"
                ),
                command="sudo ss -ulnp | grep 37428",
                expected_result=(
                    "rnsd bound to port 37428"
                ),
                if_fail=(
                    "rnsd may not have finished initializing, "
                    "or NomadNet may be holding the port"
                ),
            ),
            TroubleshootingStep(
                instruction=(
                    "Check for blocking interfaces in config"
                ),
                expected_result=(
                    "All enabled interfaces have dependencies "
                    "met"
                ),
                if_fail=(
                    "Disable the blocking interface or start "
                    "its dependency"
                ),
            ),
            TroubleshootingStep(
                instruction=(
                    "Restart rnsd to reinitialize interfaces"
                ),
                command="sudo systemctl restart rnsd",
                expected_result=(
                    "Active: active (running), then rnstatus "
                    "shows TX > 0"
                ),
            ),
        ],
        related_problems=[
            "nomadnet_rnsd_coexistence",
            "rnsd_not_starting",
        ],
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RETICULUM,
        title="NomadNet and rnsd Coexistence",
        content="""
NomadNet and rnsd both create Reticulum instances. When both set
share_instance = Yes, they compete for UDP port 37428.

Correct startup order:
1. rnsd starts first, binds port 37428 (shared instance)
2. NomadNet starts second, detects rnsd and connects as client
3. MeshForge gateway connects as another client

If NomadNet starts first:
- NomadNet binds port 37428
- rnsd fails to bind, enters crash loop
- MeshForge gateway may connect to NomadNet instead of rnsd
- Some interfaces (Meshtastic_Interface) only available via rnsd

Diagnosis:
- sudo ss -ulnp | grep 37428 -- shows who owns the port
- RX-only interfaces in rnstatus -- rnsd initialized but can't TX
- NomadNet logfile: ~/.nomadnetwork/logfile

Fix:
- Stop NomadNet: pkill -f nomadnet
- Restart rnsd: sudo systemctl restart rnsd
- Wait for port 37428, then start NomadNet
- For boot: set rnsd to start before NomadNet in systemd
""",
        keywords=[
            "nomadnet", "rnsd", "coexistence", "port", "37428",
            "shared instance", "conflict", "startup order",
        ],
        related_entries=[
            "Reticulum Network Stack",
            "RNS Transport and Routing",
        ],
        expertise_level="intermediate",
    ))


def load_aredn_knowledge(kb: "KnowledgeBase") -> None:
    """Load AREDN (Amateur Radio Emergency Data Network) knowledge."""

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.NETWORKING,
        title="AREDN Network Overview",
        content="""
AREDN (Amateur Radio Emergency Data Network) is a mesh network using
modified WiFi routers on ham radio frequencies.

Key Differences from Meshtastic:
- Uses WiFi hardware (802.11), not LoRa
- Much higher bandwidth (Mbps vs kbps)
- Shorter range per hop (typically 1-5 km)
- Requires ham radio license (Technician or higher)
- Operates on 2.4 GHz, 5.8 GHz, or 3.4 GHz bands

Network Architecture:
- Nodes are modified WiFi routers (Ubiquiti, Mikrotik, GL.iNet)
- OLSR routing protocol (automatic mesh routing)
- Each node has mesh RF + local LAN ports
- Services hosted on connected computers (chat, VoIP, video)

For MeshForge:
- AREDN is a MONITORING target, not a bridge
- MeshForge discovers AREDN nodes via OLSR data
- Read-only: MeshForge does not inject traffic
- Useful for operators managing both networks

AREDN API:
- Each node has web UI at http://localnode.local.mesh
- OLSR topology: http://node.local.mesh:9090/links
- Node list: http://node.local.mesh:8080/cgi-bin/sysinfo.json
""",
        keywords=["aredn", "amateur radio", "emergency", "wifi", "olsr", "mesh", "ham"],
        expertise_level="intermediate",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.NETWORKING,
        title="AREDN Node Discovery",
        content="""
MeshForge discovers AREDN nodes through the OLSR protocol.

OLSR (Optimized Link State Routing):
- Proactive routing protocol for mobile ad-hoc networks
- Nodes broadcast topology information
- Each node maintains full network map
- Uses Multi-Point Relays (MPR) to reduce flooding

Discovery Methods:

1. OLSR Topology Data:
   - URL: http://<node>:9090/links
   - Returns JSON with link quality, neighbor list
   - Updated every 2-10 seconds

2. Node System Info:
   - URL: http://<node>:8080/cgi-bin/sysinfo.json
   - Returns: hostname, firmware, services, GPS position
   - Rich data for map display

3. Network-wide scan:
   - Query one node's OLSR for all known hosts
   - Walk the topology to discover entire network
   - Typically completes in seconds (IP-based, fast)

MeshForge Integration:
- Polls AREDN nodes periodically (configurable interval)
- Extracts: node names, positions, link quality, services
- Displays on map alongside Meshtastic nodes
- Different icon/color to distinguish network types

Limitations:
- Must be on same network (direct or tunnel)
- AREDN nodes that block API access won't be discovered
- GPS data optional (many AREDN nodes don't have GPS)
""",
        keywords=["aredn", "olsr", "discovery", "topology", "scan", "api"],
        related_entries=["AREDN Network Overview"],
        expertise_level="intermediate",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.NETWORKING,
        title="AREDN Services",
        content="""
AREDN nodes can host and access various network services.

Common Services:
- Chat: MeshChat (web-based group messaging)
- VoIP: Asterisk PBX for voice calls
- Video: IP cameras and streaming
- File sharing: FTP/SFTP servers
- Web: Hosted websites and dashboards

Service Advertisement:
- Nodes advertise services in OLSR data
- Format: protocol://host:port/path
- Other nodes auto-discover available services
- Accessible from any node on the mesh

For Emergency Communications:
- Voice: Multiple VoIP servers for redundancy
- Messaging: MeshChat for text-based coordination
- Situational Awareness: Shared maps and status boards
- Infrastructure: DNS, NTP, monitoring

MeshForge can display:
- Which services are available on which nodes
- Service uptime/availability
- Network paths to service nodes
- This is read-only monitoring

Hardware Needed:
- Ubiquiti NanoStation (sector), Rocket (backbone)
- Mikrotik hAP ac3 (node+services)
- GL.iNet (compact, low power)
- Any device with AREDN firmware support
""",
        keywords=["aredn", "services", "voip", "chat", "meshchat", "video", "emergency"],
        related_entries=["AREDN Network Overview", "AREDN Node Discovery"],
        expertise_level="intermediate",
    ))


def load_rf_fundamentals_extended(kb: "KnowledgeBase") -> None:
    """Load extended RF fundamentals knowledge."""

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RF_FUNDAMENTALS,
        title="Free Space Path Loss (FSPL)",
        content="""
FSPL is the theoretical signal loss over distance in free space.

Formula:
  FSPL(dB) = 20*log10(d_km) + 20*log10(f_MHz) + 32.44

For LoRa at 915 MHz:
  FSPL = 20*log10(d_km) + 91.67

Example losses:
- 1 km: 91.7 dB
- 5 km: 105.6 dB
- 10 km: 111.7 dB
- 50 km: 125.6 dB

Real-World vs FSPL:
- FSPL assumes perfect free space (no obstacles)
- Real world adds 10-40 dB from terrain, foliage, buildings
- Use FSPL as best-case baseline
- Add margin: 10-20 dB for suburban, 20-40 dB for dense urban

Link Budget:
  Received Power = TX Power + TX Antenna Gain + RX Antenna Gain - FSPL - Losses
  Link Margin = Received Power - Receiver Sensitivity

For reliable links:
- 10+ dB link margin recommended
- 20+ dB for critical infrastructure links
""",
        keywords=["fspl", "path loss", "free space", "distance", "formula", "link budget"],
        related_entries=["SNR (Signal-to-Noise Ratio)", "Signal Quality Classification"],
        expertise_level="expert",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RF_FUNDAMENTALS,
        title="Antenna Types for LoRa",
        content="""
Different antenna types for different deployment scenarios.

OMNIDIRECTIONAL (360° coverage):
- Stock whip: 2-3 dBi, basic included antenna
- Ground plane: 3-5 dBi, requires ground plane radials
- Collinear: 5-8 dBi, stacked elements, taller
- Good for: Base stations serving all directions

DIRECTIONAL (focused beam):
- Yagi-Uda: 8-15 dBi, traditional beam antenna
- Patch/Panel: 6-12 dBi, flat, low profile
- Sector: 8-15 dBi, 60-120° beam width
- Good for: Point-to-point links, known direction

Key Trade-offs:
- Higher gain = narrower beam (less coverage area)
- Yagi: Maximum distance, minimum coverage angle
- Omni: Full coverage, moderate distance
- Sector: Compromise between the two

Practical Selection:
- Hilltop relay: Omnidirectional (serve all nodes below)
- Long backhaul link: Yagi-to-Yagi (maximum range)
- Coastal base: Sector aimed at coverage area
- Mobile/portable: Stock whip (compact, omnidirectional)

Gain vs Range (approximate):
- Every 6 dB gain doubles range
- 3 dBi → 5 dBi → 10 dBi → 15 dBi
- 5 km → 7 km → 14 km → 28 km (ideal conditions)

Installation Tips:
- LoRa uses vertical polarization — mount vertically
- Keep antenna away from metal surfaces
- Higher is almost always better
- Weatherproof all outdoor connections
""",
        keywords=["antenna", "yagi", "omnidirectional", "directional", "gain", "beam",
                 "collinear", "sector", "patch", "dbi"],
        related_entries=["Antenna Testing", "Fresnel Zone"],
        expertise_level="intermediate",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RF_FUNDAMENTALS,
        title="RF Propagation Models",
        content="""
Models for predicting signal coverage in real-world environments.

Free Space (Friis):
- Theoretical baseline, no obstacles
- FSPL = 20*log10(d) + 20*log10(f) + 32.44
- Good for: LOS over water, air-to-ground

Two-Ray Ground Reflection:
- Accounts for ground reflection
- More accurate than Friis for long distances
- Breakpoint distance where model transitions
- Good for: Flat terrain, rural

Hata/Okumura:
- Urban propagation model
- Accounts for building clutter
- Classified: urban, suburban, open
- Good for: City deployments

Longley-Rice (ITM):
- Terrain-aware model using elevation data
- Accounts for diffraction over hills
- Used by FCC for broadcast coverage
- Good for: Hilly terrain, mixed environments

Knife-Edge Diffraction:
- Signal bending over obstacles
- Loss depends on how deep into Fresnel zone
- Single obstacle: 6-20 dB additional loss
- Multiple obstacles: losses are cumulative

For MeshForge:
- FSPL for quick estimates
- Terrain model (SRTM) for coverage prediction
- LOSAnalyzer checks Fresnel zone clearance
- Real measurements always trump models
""",
        keywords=["propagation", "model", "friis", "hata", "terrain", "diffraction",
                 "prediction", "coverage"],
        related_entries=["Free Space Path Loss (FSPL)", "Fresnel Zone"],
        expertise_level="expert",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RF_FUNDAMENTALS,
        title="ISM Band Regulations",
        content="""
ISM (Industrial, Scientific, Medical) bands for license-free LoRa use.

US (FCC Part 15):
- 902-928 MHz (915 MHz center)
- Max 1W (30 dBm) conducted power
- Up to 6 dBi antenna without power reduction
- Frequency hopping or digital modulation required
- No duty cycle limit (but fair use applies)

EU (ETSI):
- 863-870 MHz (868 MHz center)
- Max 25 mW (14 dBm) ERP at 868.0-868.6 MHz
- Max 500 mW (27 dBm) at 869.4-869.65 MHz
- STRICT 1% or 10% duty cycle limits
- Duty cycle is legally enforced

Australia/NZ (ANZ):
- 915-928 MHz
- Max 1W (30 dBm) EIRP
- Similar to US but EIRP not conducted

Japan:
- 920-928 MHz
- Max 20 mW (13 dBm)
- Very restrictive power limits

Key Terms:
- Conducted power: Power at antenna connector
- EIRP: Conducted + antenna gain
- ERP: EIRP - 2.15 dB (referenced to dipole)
- Duty cycle: % time transmitting in any hour

For Meshtastic:
- Region set in firmware determines frequency and power
- WRONG region = illegal operation
- Meshtastic enforces regulatory limits in firmware
""",
        keywords=["ism", "regulation", "fcc", "etsi", "power", "duty cycle", "legal",
                 "frequency", "band", "915", "868"],
        expertise_level="intermediate",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RF_FUNDAMENTALS,
        title="LoRa Link Budget Calculation",
        content="""
Link budget determines whether a radio link is viable.

Full Link Budget Equation:
  Received Power = TX Power
                  + TX Antenna Gain
                  - TX Cable Loss
                  - Path Loss (FSPL + extras)
                  + RX Antenna Gain
                  - RX Cable Loss

  Link Margin = Received Power - Receiver Sensitivity

Example (LONG_FAST, 10 km, stock antennas):
  TX Power:        +20 dBm
  TX Antenna:      +2.15 dBi
  TX Cable:        -1.0 dB
  FSPL (10km):     -111.7 dB
  Extra losses:    -10.0 dB (foliage, terrain)
  RX Antenna:      +2.15 dBi
  RX Cable:        -1.0 dB
  ────────────────────────────
  Received:        -99.4 dBm
  RX Sensitivity:  -134.5 dBm (SF11, BW250kHz)
  Link Margin:     +35.1 dB  ← Excellent!

Sensitivity by Preset:
  SHORT_FAST (SF7):   -124.0 dBm
  MEDIUM_FAST (SF9):  -130.5 dBm
  LONG_FAST (SF11):   -134.5 dBm
  LONG_SLOW (SF12):   -137.0 dBm

Rules of Thumb:
- Every 6 dB margin ≈ double the reliability
- Want 10+ dB margin for reliable links
- 20+ dB margin for infrastructure backbone
- 0 dB margin = 50/50 whether packet gets through
""",
        keywords=["link budget", "calculation", "sensitivity", "margin", "power",
                 "received", "transmit"],
        related_entries=["Free Space Path Loss (FSPL)", "LoRa Spreading Factor"],
        expertise_level="expert",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RF_FUNDAMENTALS,
        title="RF Interference and Noise",
        content="""
Sources of RF interference affecting LoRa performance.

Common Interference Sources:
- Other LoRa devices on same channel
- WiFi (2.4 GHz can leak into adjacent bands)
- Microwave ovens (2.45 GHz)
- LED lights (switching noise)
- Solar inverters (switching noise)
- Industrial equipment
- Other ISM band users

Noise Floor:
- Thermal noise: -174 dBm/Hz (fundamental physics)
- LoRa bandwidth noise: -174 + 10*log10(BW)
  - 125 kHz BW: -123 dBm noise floor
  - 250 kHz BW: -120 dBm noise floor
  - 500 kHz BW: -117 dBm noise floor
- Man-made noise adds to this baseline
- Urban: +10-30 dB above thermal
- Rural: +5-10 dB above thermal

Identifying Interference:
- Sudden SNR drop without distance change
- High CRC error rate
- Intermittent connectivity (interference duty-cycled)
- Time-of-day patterns (e.g., worse when neighbors home)

Mitigation:
- Change channel/frequency
- Use higher spreading factor (more processing gain)
- Improve antenna filtering (SAW filter)
- Move antenna away from noise source
- Shield receiver from nearby interference
- Use directional antenna (rejects off-axis noise)
""",
        keywords=["interference", "noise", "noise floor", "rfi", "emi", "spurious",
                 "thermal", "snr degradation"],
        related_entries=["SNR (Signal-to-Noise Ratio)", "Channel Utilization"],
        expertise_level="intermediate",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RF_FUNDAMENTALS,
        title="Terrain Effects on RF Propagation",
        content="""
Terrain significantly affects LoRa signal propagation.

Terrain Types and Losses:
- Open flat: 0-5 dB extra loss (FSPL-like)
- Rolling hills: 5-15 dB (diffraction over ridges)
- Mountains: 15-40 dB (complete blockage possible)
- Forest/dense vegetation: 5-20 dB (absorption)
- Urban/buildings: 10-30 dB (reflection, absorption)
- Water/ocean: -2 to +3 dB (can improve via reflection)

Line of Sight (LOS):
- LOS = unobstructed path between antennas
- Critical for reliable LoRa links
- Check with elevation profile tools
- Earth's curvature matters for long links:
  - Visible horizon at 10m height: ~11 km
  - At 30m height: ~20 km
  - At 100m height: ~36 km

Diffraction:
- Signals bend around obstacles (knife-edge effect)
- Loss depends on clearance ratio to Fresnel zone
- 0% clearance (on obstacle): ~6 dB loss
- -50% clearance (behind obstacle): ~16 dB loss
- Multiple obstacles: losses roughly additive

Practical Tips:
- Elevation is king — get as high as possible
- Hilltop relays can cover entire valleys
- Coastal deployments benefit from water reflection
- Forest links: mount antennas ABOVE tree canopy
- Urban: use rooftop placement, not window
- Check terrain profiles before deploying

MeshForge Coverage Prediction:
- Uses SRTM elevation data (30m resolution)
- LOSAnalyzer checks Fresnel zone clearance
- Coverage grid shows predicted viable areas
- Accounts for Earth curvature and diffraction
""",
        keywords=["terrain", "elevation", "hill", "mountain", "forest", "urban",
                 "line of sight", "los", "diffraction", "srtm"],
        related_entries=["Fresnel Zone", "RF Propagation Models"],
        expertise_level="intermediate",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RF_FUNDAMENTALS,
        title="Solar Power for Remote Nodes",
        content="""
Solar power design for remote mesh nodes (relay stations, repeaters).

Power Budget (typical Meshtastic node):
- Sleep mode: 10-30 mA (most of the time)
- RX active: 50-80 mA
- TX active: 150-400 mA (depends on power level)
- Average: ~50-100 mA at 3.7V = 0.2-0.4W

Daily Energy Need:
- Average 75 mA × 3.7V × 24h = 6.7 Wh/day
- Add 50% margin for weather: ~10 Wh/day

Solar Panel Sizing:
- Peak sun hours varies by location
  - Hawaii: 5-6 hours
  - Mainland US: 3-5 hours
  - Northern Europe: 2-3 hours
- Panel watts × peak hours × 0.7 (efficiency) = daily Wh
- For 10 Wh/day in Hawaii: 10 / (5.5 × 0.7) = 2.6W panel
- Recommended: 5-10W panel for reliability margin

Battery Sizing:
- Want 2-3 days autonomy (cloudy weather)
- 10 Wh/day × 3 days = 30 Wh storage needed
- 18650 cell = ~10 Wh (3.7V × 2.6Ah)
- Need 3 cells for 3-day autonomy
- Or 1× 18650 with daily solar replenishment

Charge Controllers:
- TP4056 module: Simple, cheap, single cell
- CN3065: Solar-optimized, prevents overcharge
- MPPT controller: Maximum efficiency, more expensive
- Most T-Beam boards have built-in charging

Installation Tips:
- Angle panel toward equator at latitude angle
- Keep panel clean (dust = 20-30% loss)
- Weatherproof all connections (marine-grade)
- Mount panel above potential shade paths
- Use anti-corrosion on all contacts
- Consider battery temperature (Li-ion hates heat)
""",
        keywords=["solar", "power", "battery", "remote", "charging", "panel",
                 "18650", "repeater", "off-grid"],
        related_entries=["Common Meshtastic Hardware"],
        expertise_level="intermediate",
    ))


def load_mqtt_knowledge(kb: "KnowledgeBase") -> None:
    """Load MQTT knowledge."""

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.MQTT,
        title="MQTT for Meshtastic",
        content="""
MQTT bridges Meshtastic mesh traffic to the internet.

How it Works:
- Nodes with MQTT enabled publish messages to broker
- Other internet-connected nodes subscribe to same topics
- Effectively extends mesh range via internet backbone
- Bridge between local RF mesh and global MQTT network

Topic Structure:
  msh/{region}/{channel_id}/{app}/{node_id}
  Example: msh/US/2/json/!abc123

Message Format (JSON uplink):
  {
    "from": 1234567890,
    "to": 4294967295,  // broadcast
    "channel": 0,
    "type": "text",
    "payload": "Hello mesh!",
    "sender": "!abc123",
    "timestamp": 1706000000
  }

Common Brokers:
- mqtt.meshtastic.org (default, public)
- Your own Mosquitto instance (private, recommended)
- HiveMQ Cloud (hosted, free tier)

Privacy Considerations:
- Default channel key is public knowledge
- Messages on default key are readable by ANYONE
- Use custom channel key for private communications
- Self-hosted broker for maximum privacy

For MeshForge:
- mqtt_subscriber.py connects to broker
- Parses node positions and telemetry
- Feeds map data service for visualization
- Supports TLS for secure connections
""",
        keywords=["mqtt", "broker", "publish", "subscribe", "topic", "internet",
                 "bridge", "json", "meshtastic"],
        related_entries=["Meshtastic Channels"],
        expertise_level="intermediate",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.MQTT,
        title="MQTT Broker Setup",
        content="""
Setting up your own MQTT broker for mesh privacy and control.

Mosquitto (recommended):
  # Install
  sudo apt install mosquitto mosquitto-clients

  # Config: /etc/mosquitto/mosquitto.conf
  listener 1883
  allow_anonymous true  # For testing only!

  # With authentication:
  listener 1883
  password_file /etc/mosquitto/passwd
  allow_anonymous false

  # Generate password file:
  sudo mosquitto_passwd -c /etc/mosquitto/passwd meshforge

TLS Configuration:
  listener 8883
  cafile /etc/mosquitto/certs/ca.crt
  certfile /etc/mosquitto/certs/server.crt
  keyfile /etc/mosquitto/certs/server.key
  require_certificate false  # Client certs optional

Testing:
  # Subscribe to all Meshtastic traffic:
  mosquitto_sub -h localhost -t 'msh/#' -v

  # Publish test message:
  mosquitto_pub -h localhost -t 'test' -m 'hello'

For MeshForge MQTT subscriber:
  Configure in settings:
  - broker_host: localhost (or remote host)
  - broker_port: 1883 (or 8883 for TLS)
  - username/password if authentication enabled
  - topic_root: msh/US/2/json/#
""",
        keywords=["mqtt", "broker", "mosquitto", "setup", "tls", "authentication",
                 "password", "configuration"],
        related_entries=["MQTT for Meshtastic"],
        expertise_level="intermediate",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.MQTT,
        title="MQTT Downlink Echo Loop",
        content="""
MQTT Downlink Echo Loop — the #1 cause of meshtasticd web client hangs.

The Problem:
When MQTT uplink AND downlink are both enabled on the same channel,
the device publishes packets to the broker (uplink), then the broker
echoes them right back (downlink). This creates a feedback loop that
floods the device's tophone queue.

Symptoms:
- meshtasticd logs: "tophone queue status queue is full, discard oldest"
- Web client at :9443 hangs/freezes after partial load
- Node names appear but UI is unresponsive
- Packet loss on the RF mesh (dropped from full queue)

Root Cause:
  Device TX → MQTT broker (uplink publish)
                  ↓
  MQTT broker → Device RX queue (downlink subscribe)  ← LOOP

The device subscribes to the same topic it publishes to.
Every outgoing packet comes back in as an incoming packet,
filling the queue faster than the radio can drain it.

Fix:
  # Disable downlink on primary channel
  meshtastic --host localhost --ch-index 0 --ch-set downlink_enabled false

  # Or in MeshForge TUI:
  Meshtasticd > MQTT > Configure Downlink

When to use downlink:
- Only if you need MQTT→radio message injection
- Remote apps sending commands to mesh nodes
- Never on a monitoring/broker node that only collects data

When to DISABLE downlink:
- Broker/monitoring nodes (most common)
- Nodes that only publish to MQTT
- Any node experiencing queue overflow
""",
        keywords=["mqtt", "downlink", "echo", "loop", "queue", "full", "overflow",
                 "tophone", "hang", "web client", "freeze", "flood"],
        related_entries=["MQTT for Meshtastic", "Web Client Phantom Nodes"],
        expertise_level="intermediate",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.MESHTASTIC,
        title="Web Client Phantom Nodes",
        content="""
Phantom Nodes — why the meshtasticd web client crashes on search.

The Problem:
The meshtasticd web client (React app at :9443) crashes with
"This is a little embarrassing..." when clicking certain nodes
in the search results. The nodes appear in search but clicking
them triggers a JavaScript error.

Root Cause:
Phantom nodes are incomplete entries in the device's node database —
typically received via MQTT from distant nodes. They have a node ID
but are missing required fields:
- No 'user' object (longName, shortName, hwModel missing)
- No 'role' field
- No position data

The React web client tries to render these fields without null checks:
  node.user.longName.replace(...)  → crashes on undefined

This is upstream bug: https://github.com/meshtastic/web/issues/862

How phantom nodes accumulate:
1. MQTT downlink enabled → broker sends nodeinfo from entire mesh
2. Many nodes on public MQTT have incomplete data
3. Device stores them in nodedb with missing fields
4. MaxNodes: 200 (default) allows hundreds of phantoms

Fixes:
1. MeshForge Node DB Cleanup:
   Meshtasticd > Node DB Cleanup > Scan for Phantom Nodes
   Identifies and removes nodes with no name data.

2. Reset node database (nuclear option):
   meshtastic --host localhost --reset-nodedb
   Clears ALL nodes. Legitimate nodes re-appear within minutes.

3. Reduce MaxNodes in /etc/meshtasticd/config.yaml:
   General:
     MaxNodes: 100  # Down from 200

4. Disable MQTT downlink (prevents new phantoms):
   meshtastic --host localhost --ch-index 0 --ch-set downlink_enabled false

5. MeshForge API proxy sanitization:
   When web client is routed through MeshForge's proxy, the
   _sanitize_nodes_json() method fills in missing fields with
   safe defaults, preventing the React crash entirely.
""",
        keywords=["phantom", "ghost", "node", "crash", "web client", "search",
                 "embarrassing", "react", "undefined", "missing", "user",
                 "longName", "role", "M3GO", "nodedb", "cleanup"],
        related_entries=["MQTT Downlink Echo Loop", "MQTT for Meshtastic"],
        expertise_level="intermediate",
    ))
