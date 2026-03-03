"""
RF fundamentals and MQTT knowledge content for MeshForge Knowledge Base.

Extracted from knowledge_content.py for CLAUDE.md #6 compliance (<1,500 lines).
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .knowledge_base import KnowledgeBase

from .knowledge_base import KnowledgeEntry, KnowledgeTopic


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
