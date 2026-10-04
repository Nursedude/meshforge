# MeshForge Visual Guide

Screenshots and interface previews for MeshForge.

---

## TUI Interface (SSH/Terminal)

The TUI works over SSH and in any terminal. Recommended for headless Raspberry Pi setups.

```
┌───────────────────────────────────────────────────────────────────┐
│                    MeshForge v0.4.6-beta                          │
├───────────────────────────────────────────────────────────────────┤
│                                                                   │
│   Select an option:                                               │
│                                                                   │
│      GTK4 Desktop Interface                                       │
│      Rich CLI (Terminal Menu)                                     │
│      Web Monitor Dashboard                                        │
│   ──────────── Tools ────────────                                 │
│   >  AI Tools                          <──── NEW! Diagnostics     │
│      System Diagnostics                                           │
│      Network Tools                                                │
│      RF Tools                                                     │
│      Site Planner                                                 │
│      Start Gateway Bridge                                         │
│      Node Monitor                                                 │
│      View Nodes                                                   │
│      Messaging                                                    │
│      Space Weather                                                │
│   ──────────── Config ───────────                                 │
│      Meshtasticd Config                                           │
│      Service Management                                           │
│      Hardware Detection                                           │
│      Settings                                                     │
│   ──────────────────────────────                                  │
│      About MeshForge                                              │
│      Exit                                                         │
│                                                                   │
│                              <Ok>            <Cancel>             │
└───────────────────────────────────────────────────────────────────┘
```

---

## AI Tools Menu

Access intelligent diagnostics, knowledge base, and Claude assistant.

```
┌───────────────────────────────────────────────────────────────────┐
│                         AI Tools                                  │
├───────────────────────────────────────────────────────────────────┤
│                                                                   │
│   AI-powered mesh network assistance:                             │
│                                                                   │
│   >  Intelligent Diagnostics    Analyze symptoms, get fixes       │
│      Knowledge Base Query       Search mesh networking concepts   │
│      Claude Assistant           Natural language Q&A              │
│      Generate Coverage Map      Create interactive map            │
│      Back                                                         │
│                                                                   │
│                              <Ok>            <Cancel>             │
└───────────────────────────────────────────────────────────────────┘
```

---

## Intelligent Diagnostics Output

Example diagnosis result:

```
┌───────────────────────────────────────────────────────────────────┐
│                     Diagnosis Result                              │
├───────────────────────────────────────────────────────────────────┤
│                                                                   │
│   SYMPTOM: Connection refused to meshtasticd on port 4403         │
│                                                                   │
│   LIKELY CAUSE:                                                   │
│     Service not running or port not listening                     │
│                                                                   │
│   CONFIDENCE: 85%                                                 │
│                                                                   │
│   EVIDENCE:                                                       │
│     - TCP connection to localhost:4403 failed                     │
│     - systemctl shows meshtasticd as inactive                     │
│     - No process listening on port 4403                           │
│                                                                   │
│   SUGGESTIONS:                                                    │
│     1. Start the service: sudo systemctl start meshtasticd        │
│     2. Check logs: journalctl -u meshtasticd -n 50                │
│     3. Verify USB device is connected: lsusb | grep -i esp        │
│     4. Check config: cat /etc/meshtasticd/config.yaml             │
│     5. Restart with debug: sudo meshtasticd --debug               │
│                                                                   │
│                                <Ok>                               │
└───────────────────────────────────────────────────────────────────┘
```

---

## Knowledge Base Query

Example knowledge base result:

```
┌───────────────────────────────────────────────────────────────────┐
│                  Knowledge Base Results                           │
├───────────────────────────────────────────────────────────────────┤
│                                                                   │
│   QUERY: What is SNR?                                             │
│                                                                   │
│   --- Result 1: SNR (Signal-to-Noise Ratio) ---                   │
│                                                                   │
│   SNR measures signal strength relative to background noise       │
│   in decibels (dB).                                               │
│                                                                   │
│   Read SNR as MARGIN over your spreading factor's floor:          │
│   • Floors: SF7 -7.5 ... SF11 -17.5, SF12 -20 dB                  │
│   • Margin >=10 dB good · 5-10 fair · 0-5 edge                    │
│   • -9 dB on LongFast (SF11) = 8.5 dB margin: fair                │
│   • Reported SNR tops out near +6 dB: use RSSI above that         │
│                                                                   │
│   Improvement strategies (in this order):                         │
│   • Raise antenna height / improve line of sight                  │
│   • Add a relay node to shorten the hop                           │
│   • Only then more gain or power                                  │
│                                                                   │
│                                <Ok>                               │
└───────────────────────────────────────────────────────────────────┘
```

---

## RF Tools Menu

```
┌───────────────────────────────────────────────────────────────────┐
│                          RF Tools                                 │
├───────────────────────────────────────────────────────────────────┤
│                                                                   │
│   Radio frequency calculations:                                   │
│                                                                   │
│   >  Frequency Slot Calculator    Calculate slot from channel     │
│      Free Space Path Loss         FSPL at distance/frequency      │
│      Link Budget Calculator       Full link analysis              │
│      Fresnel Zone                 Calculate clearance needed      │
│      EIRP Calculator              Effective radiated power        │
│      Back                                                         │
│                                                                   │
│                              <Ok>            <Cancel>             │
└───────────────────────────────────────────────────────────────────┘
```

---

## Web Dashboard

Access via browser at `http://localhost:8880`

```
┌─────────────────────────────────────────────────────────────────────────┐
│ MeshForge                                              [Dark] [Light]   │
├─────────────────────────────────────────────────────────────────────────┤
│ Dashboard │ Gateway │ Map │ Nodes │ Messages │ Config │ System          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────────┐  ┌─────────────────────┐                       │
│  │ System Status       │  │ Services            │                       │
│  │                     │  │                     │                       │
│  │ CPU:    [████░░] 42%│  │ meshtasticd  ● ON   │                       │
│  │ Memory: [███░░░] 38%│  │ rnsd         ● ON   │                       │
│  │ Disk:   [██░░░░] 24%│  │ mosquitto    ○ OFF  │                       │
│  │ Temp:   52°C        │  │ HamClock     ○ OFF  │                       │
│  │ Uptime: 3d 14h      │  │                     │                       │
│  └─────────────────────┘  └─────────────────────┘                       │
│                                                                         │
│  ┌─────────────────────────────────────────────┐                        │
│  │ Gateway Bridge                              │                        │
│  │                                             │                        │
│  │ Status: ● Running                           │                        │
│  │ Mode:   message_bridge                      │                        │
│  │ Meshtastic nodes: 12                        │                        │
│  │ RNS announces: 3                            │                        │
│  │ Messages bridged: 847                       │                        │
│  │                                             │                        │
│  │ [Start] [Stop] [Restart] [View Logs]        │                        │
│  └─────────────────────────────────────────────┘                        │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Map View

Interactive Leaflet map showing node locations:

```
┌─────────────────────────────────────────────────────────────────────────┐
│ MeshForge - Map                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│    ┌────────────────────────────────────────────────────────────────┐   │
│    │                                                                │   │
│    │                    ● Node-Alpha (Online)                       │   │
│    │                         ╱                                      │   │
│    │                        ╱                                       │   │
│    │           ● Gateway-1 ●────────● Node-Bravo (Stale)            │   │
│    │           (Online)     ╲                                       │   │
│    │                         ╲                                      │   │
│    │                          ● Node-Charlie (Offline)              │   │
│    │                                                                │   │
│    │    [+] [-]              [Layers ▼]                             │   │
│    └────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│    Legend:                                                              │
│    ● Green  = Online (seen < 15 min)                                    │
│    ● Yellow = Stale (seen < 1 hour)                                     │
│    ● Red    = Offline (seen > 1 hour)                                   │
│    ◆ Blue   = Gateway node                                              │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Coverage Map (Generated HTML)

The coverage map generator creates interactive Folium maps:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Coverage Map - MeshForge                          [OpenStreetMap ▼]    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│         ╭─────────────────────────────────────╮                         │
│        ╱   Coverage radius: 10km (LONG_FAST)   ╲                        │
│       ╱                                         ╲                       │
│      │              ┌───────┐                    │                      │
│      │              │ NODE1 │                    │                      │
│      │              │ !abc  │                    │                      │
│      │              │ SNR:5 │                    │                      │
│      │              └───────┘                    │                      │
│       ╲                                         ╱                       │
│        ╲                                       ╱                        │
│         ╰─────────────────────────────────────╯                         │
│                                                                         │
│    Click node for details:                                              │
│    • ID, Name, Hardware                                                 │
│    • SNR, RSSI, Battery                                                 │
│    • Last seen, Position                                                │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           USER INTERFACES                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │   GTK4   │  │   TUI    │  │   Web    │  │   CLI    │  │Standalone│  │
│  │ Desktop  │  │  (SSH)   │  │Dashboard │  │  Menu    │  │   Mode   │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  │
│       │             │             │             │             │        │
│       └─────────────┴──────┬──────┴─────────────┴─────────────┘        │
│                            │                                            │
├────────────────────────────┼────────────────────────────────────────────┤
│                     COMMANDS LAYER                                      │
│                            │                                            │
│  ┌─────────────────────────┴─────────────────────────────────────┐     │
│  │  meshtastic.py │ gateway.py │ rns.py │ service.py │ hardware │     │
│  └─────────────────────────┬─────────────────────────────────────┘     │
│                            │                                            │
├────────────────────────────┼────────────────────────────────────────────┤
│                      UTILS LAYER                                        │
│                            │                                            │
│  ┌─────────────────────────┴─────────────────────────────────────┐     │
│  │ diagnostic_engine │ knowledge_base │ coverage_map │ rf.py    │     │
│  │ claude_assistant  │ settings       │ paths        │ logging  │     │
│  └─────────────────────────┬─────────────────────────────────────┘     │
│                            │                                            │
├────────────────────────────┼────────────────────────────────────────────┤
│                    EXTERNAL SERVICES                                    │
│                            │                                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐               │
│  │meshtastic│  │   rnsd   │  │ HamClock │  │ mosquitto│               │
│  │    d     │  │  (RNS)   │  │  (API)   │  │  (MQTT)  │               │
│  │ :4403    │  │          │  │  :8080   │  │  :1883   │               │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘               │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Gateway Bridge Flow

```
                    ┌─────────────────────────────────────┐
                    │           MESHFORGE GATEWAY          │
                    │                                     │
┌─────────────┐     │  ┌─────────┐       ┌─────────┐    │     ┌─────────────┐
│ MESHTASTIC  │     │  │ Message │       │ Message │    │     │  RETICULUM  │
│   NETWORK   │────►│  │  Queue  │──────►│ Router  │    │────►│   NETWORK   │
│             │     │  │ (SQLite)│       │         │    │     │   (RNS)     │
│ • LoRa mesh │     │  └─────────┘       └─────────┘    │     │             │
│ • !node IDs │◄────│       │                 │         │◄────│ • LXMF      │
│ • Channels  │     │       ▼                 ▼         │     │ • Identities│
└─────────────┘     │  ┌─────────────────────────┐      │     └─────────────┘
                    │  │     Unified Node        │      │
                    │  │       Tracker           │      │
                    │  │                         │      │
                    │  │ • Combines both networks│      │
                    │  │ • Position correlation  │      │
                    │  │ • Health monitoring     │      │
                    │  └─────────────────────────┘      │
                    │                                     │
                    └─────────────────────────────────────┘
```

---

## Quick Command Reference

```bash
# Launch interfaces
sudo meshforge              # Auto-detect best interface
sudo meshforge-gtk          # GTK4 desktop
sudo meshforge-web          # Web dashboard (port 8880)
sudo python3 src/launcher_tui.py   # TUI (SSH-friendly)

# Direct access
python3 src/standalone.py   # Zero-dependency mode

# Configuration
~/.config/meshforge/gateway.json    # Gateway settings
/etc/meshtasticd/config.d/          # Hardware configs

# Logs
journalctl -u meshtasticd -f        # meshtasticd logs
tail -f ~/.local/share/meshforge/meshforge.log

# Testing
python3 -m pytest tests/ -v         # Run tests
python3 scripts/lint.py             # Security linter
```

---

## Color Legend

Throughout the interfaces:

| Color | Meaning |
|-------|---------|
| Green | Online / Success / Running |
| Yellow | Warning / Stale / Pending |
| Red | Error / Offline / Failed |
| Blue | Information / Gateway / Primary |
| Cyan | Highlight / Selected |

---

*Made with aloha for the mesh community | WH6GXZ*
