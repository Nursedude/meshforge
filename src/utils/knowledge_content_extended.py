"""
Extended knowledge content loaders for MeshForge Knowledge Base.

Contains AREDN, extended RF fundamentals, and MQTT knowledge.
Separated from knowledge_content.py for maintainability (file size limit).

These functions are called by KnowledgeBase.__init__() to load content.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .knowledge_base import KnowledgeBase

# Import data classes needed for content creation
from .knowledge_base import (
    KnowledgeEntry,
    KnowledgeTopic,
)


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
- Node list: http://node.local.mesh:8080/a/sysinfo?hosts=1
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
   - URL: http://<node>:8080/a/sysinfo
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
        keywords=["aredn", "services", "voip", "video", "emergency"],
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

OMNIDIRECTIONAL (360\u00b0 coverage):
- Stock whip: 2-3 dBi, basic included antenna
- Ground plane: 3-5 dBi, requires ground plane radials
- Collinear: 5-8 dBi, stacked elements, taller
- Good for: Base stations serving all directions

DIRECTIONAL (focused beam):
- Yagi-Uda: 8-15 dBi, traditional beam antenna
- Patch/Panel: 6-12 dBi, flat, low profile
- Sector: 8-15 dBi, 60-120\u00b0 beam width
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
- 3 dBi \u2192 5 dBi \u2192 10 dBi \u2192 15 dBi
- 5 km \u2192 7 km \u2192 14 km \u2192 28 km (ideal conditions)

Installation Tips:
- LoRa uses vertical polarization \u2014 mount vertically
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
  \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
  Received:        -99.4 dBm
  RX Sensitivity:  -134.5 dBm (SF11, BW250kHz)
  Link Margin:     +35.1 dB  \u2190 Excellent!

Sensitivity by Preset:
  SHORT_FAST (SF7):   -124.0 dBm
  MEDIUM_FAST (SF9):  -130.5 dBm
  LONG_FAST (SF11):   -134.5 dBm
  LONG_SLOW (SF12):   -137.0 dBm

Rules of Thumb:
- Every 6 dB margin \u2248 double the reliability
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
- Elevation is king \u2014 get as high as possible
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
- Average 75 mA \u00d7 3.7V \u00d7 24h = 6.7 Wh/day
- Add 50% margin for weather: ~10 Wh/day

Solar Panel Sizing:
- Peak sun hours varies by location
  - Hawaii: 5-6 hours
  - Mainland US: 3-5 hours
  - Northern Europe: 2-3 hours
- Panel watts \u00d7 peak hours \u00d7 0.7 (efficiency) = daily Wh
- For 10 Wh/day in Hawaii: 10 / (5.5 \u00d7 0.7) = 2.6W panel
- Recommended: 5-10W panel for reliability margin

Battery Sizing:
- Want 2-3 days autonomy (cloudy weather)
- 10 Wh/day \u00d7 3 days = 30 Wh storage needed
- 18650 cell = ~10 Wh (3.7V \u00d7 2.6Ah)
- Need 3 cells for 3-day autonomy
- Or 1\u00d7 18650 with daily solar replenishment

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
MQTT Downlink Echo Loop \u2014 the #1 cause of meshtasticd web client hangs.

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
  Device TX \u2192 MQTT broker (uplink publish)
                  \u2193
  MQTT broker \u2192 Device RX queue (downlink subscribe)  \u2190 LOOP

The device subscribes to the same topic it publishes to.
Every outgoing packet comes back in as an incoming packet,
filling the queue faster than the radio can drain it.

Fix:
  # Disable downlink on primary channel
  meshtastic --host localhost --ch-index 0 --ch-set downlink_enabled false

  # Or in MeshForge TUI:
  Meshtasticd > MQTT > Configure Downlink

When to use downlink:
- Only if you need MQTT\u2192radio message injection
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
Phantom Nodes \u2014 why the meshtasticd web client crashes on search.

The Problem:
The meshtasticd web client (React app at :9443) crashes with
"This is a little embarrassing..." when clicking certain nodes
in the search results. The nodes appear in search but clicking
them triggers a JavaScript error.

Root Cause:
Phantom nodes are incomplete entries in the device's node database \u2014
typically received via MQTT from distant nodes. They have a node ID
but are missing required fields:
- No 'user' object (longName, shortName, hwModel missing)
- No 'role' field
- No position data

The React web client tries to render these fields without null checks:
  node.user.longName.replace(...)  \u2192 crashes on undefined

This is upstream bug: https://github.com/meshtastic/web/issues/862

How phantom nodes accumulate:
1. MQTT downlink enabled \u2192 broker sends nodeinfo from entire mesh
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
