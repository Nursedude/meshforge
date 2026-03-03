"""
Meshtastic and hardware knowledge content for MeshForge Knowledge Base.

Extracted from knowledge_content.py for CLAUDE.md #6 compliance (<1,500 lines).
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .knowledge_base import KnowledgeBase

from .knowledge_base import KnowledgeEntry, KnowledgeTopic


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
