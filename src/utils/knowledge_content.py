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
- SF11 sensitivity: -131.5 dBm at 250 kHz (LongFast), -134.5 dBm at 125 kHz
- SF12 sensitivity: -137 dBm at 125 kHz (LongSlow)
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

Network Visualizer:
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

