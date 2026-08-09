# Installing MeshForge

Hardware, install, first run, and upgrades. The short version lives in the [README](../README.md); this is the full path.

## Quick Start

> **Already running MeshForge?** See [Upgrading](#upgrading-meshforge) for upgrade paths.

### Hardware

MeshForge runs on a Raspberry Pi. The tested baseline is a mix of **Pi 3B, Pi 4,
and Pi 5** boards (all 64-bit / `aarch64`) — any of these runs the full NOC stack
(map server, gateway bridge, Reticulum, MQTT) comfortably.

**You don't need a Pi 4 to start.** Small boards are workable — not a deal-breaker —
*because the software is tuned to fit the box*, not because the hardware is roomy.
A **Pi Zero 2 W** (quad-core, 512 MB, ~$15) is the recommended low-cost board for a
lightweight or single-purpose node, and a dedicated bot node has run for weeks on the
original (1st-gen) **Pi Zero W**. On a 512 MB board:

- **Keep the memory-saving defaults on.** The node directory uses a bounding-box
  filter plus a node-count cap, and the large API responses (`/api/nodes/directory`,
  `/api/nodes/geojson`, `/api/network/topology`) are byte-cached. Together these keep
  RAM and the on-disk DB small — on one box the node DB dropped from multiple GB to
  single-digit MB once the bbox filter and cap were set. Don't disable them on a
  constrained board.
- **Expect some swap** under the map service on a large mesh; a high-endurance SD card
  (or a USB SSD) helps latency and reduces card wear.
- **Single-purpose roles do best on Zero-class boards** (a dedicated gateway or bot).
  The full NOC stack is happier on a Pi 3B or larger.
- **1st-gen Pi Zero W (`armv6`):** fine for a dedicated lightweight node, but its single
  core and aging `armv6` Python-package support make a **Pi Zero 2 W or Pi 3B+ the
  better entry choice** for anything new.

### Fresh Install

```bash
git clone https://github.com/Nursedude/meshforge.git
cd meshforge
sudo bash scripts/install_noc.sh    # Full NOC stack install
```

The installer auto-detects your radio hardware (SPI HAT or USB), installs
meshtasticd + Reticulum, and sets up systemd services. It will prompt you
to select your HAT if SPI is detected.

**Installer options:**
```bash
sudo bash scripts/install_noc.sh --skip-meshtasticd   # Don't install meshtasticd
sudo bash scripts/install_noc.sh --skip-rns            # Don't install Reticulum
sudo bash scripts/install_noc.sh --client-only         # MeshForge only (no daemons)
sudo bash scripts/install_noc.sh --force-native        # Force SPI mode
sudo bash scripts/install_noc.sh --force-python        # Force USB mode
```

### MeshAnchor (MeshCore-Primary Sister App)

**[MeshAnchor](https://github.com/Nursedude/meshanchor)** is the sister project
where MeshCore is the primary radio and Meshtastic is an optional gateway — the
mirror image of MeshForge's architecture.

```
MeshForge (this repo)          MeshAnchor (live)
  Primary: Meshtastic            Primary: MeshCore
  Gateway to: MeshCore/RNS       Gateway to: Meshtastic/RNS
```

MeshAnchor was extracted from MeshForge main on 2026-04-01. Both share the same
TUI framework, gateway bridge, and RF tools. They differ in which radio is "home."

```bash
git clone https://github.com/Nursedude/meshanchor.git
cd meshanchor
sudo python3 src/launcher_tui/main.py
```

### Deployment Profiles

MeshForge supports 5 deployment profiles. Install only the dependencies you need:

| Profile | Services Needed | Install | Use Case |
|---------|----------------|---------|----------|
| `radio_maps` | meshtasticd | `pip install -r requirements/core.txt -r requirements/maps.txt` | Radio config + coverage maps |
| `monitor` | (none) | `pip install -r requirements/core.txt -r requirements/mqtt.txt` | MQTT packet analysis |
| `meshcore` | (none) | `pip install -r requirements/core.txt` + meshcore | MeshCore companion radio |
| `gateway` | meshtasticd, rnsd | `pip install -r requirements/core.txt -r requirements/rns.txt -r requirements/mqtt.txt` | Meshtastic <> RNS bridge |
| `full` | meshtasticd, rnsd, mosquitto | `pip install -r requirements.txt` | Everything |

```bash
# Select profile at launch
python3 src/launcher.py --profile gateway

# Auto-detect (default): scans running services and installed packages
python3 src/launcher.py

# Profile is saved to ~/.config/meshforge/deployment.json
```

### Already Have meshtasticd?

```bash
sudo python3 src/launcher_tui/main.py
```

### RF Tools Only (no sudo, no radio)

```bash
python3 src/standalone.py
```

### Upgrade / Reinstall

Already running MeshForge? Pick your path:

```bash
# Option 1: Clean reinstall (recommended)
# Backs up configs → removes code → fresh clone → restores configs
# Your radio, RNS identity, and MQTT broker are NOT touched
sudo bash /opt/meshforge/scripts/reinstall.sh

# Option 2: Quick update (code + service files)
cd /opt/meshforge && sudo bash scripts/update.sh

# Option 3: Manual git pull (developers)
cd /opt/meshforge && sudo git pull origin main
```

**Upgrading from pre-v0.5.4?** The gateway now uses MQTT instead of TCP.
**Upgrading from v0.5.4?** The old in-app MeshChat handler was removed (upstream unmaintained). For a
browser-based LXMF client, **MeshChatX** is now integrated as an opt-in sibling to NomadNet — see
**[docs/MESHCHATX_IN_MESHFORGE.md](MESHCHATX_IN_MESHFORGE.md)** for the full domain setup
(isolated install, propagation node, gateway enrollment, bot round-trips). NomadNet remains the
default LXMF client.
Install mosquitto (`sudo apt install mosquitto`) and configure via
`TUI → Gateway Config → MQTT Bridge Settings → Run Setup Guide`.

After any upgrade, verify:
```bash
sudo bash scripts/verify_post_install.sh
```

### TUI Menu Structure

The TUI uses a raspi-config style interface (whiptail/dialog) designed for SSH and
headless operation. Navigation is keyboard-driven with max 10 items per menu level:

```
Main Menu (MeshForge NOC)
├── 1. Dashboard             Service status, health, alerts, data path check
├── 2. Mesh Networks         Meshtastic, RNS, MeshCore, AREDN, MQTT, Gateway
│       └── RNS submenu      NomadNet Client (install/manage)
├── 3. RF & SDR              Link budget, site planner, frequency slots, SDR
├── 4. Maps & Viz            Live NOC map, coverage, topology, traffic inspector
├── 5. Configuration         Radio, channels, RNS config, services, backup
├── 6. System                Hardware detect, logs, network tools, shell, reboot
├── q. Quick Actions         Common shortcuts (2-tap access)
├── e. Emergency Mode        Field ops, weather/EAS alerts, SOS beacon
├── a. About                 Version, web client, help
└── x. Exit
```

**Design principles** (inspired by
[raspi-config](https://www.raspberrypi.com/documentation/computers/configuration.html)):
- Max 10 items per menu (cognitive load limit)
- Grouped by user task, not technical domain
- 2-tap max for common operations via Quick Actions
- Startup checks detect conflicts, verify services, warn on misconfigs

---

## Hardware

**Minimum:** Raspberry Pi 3B+ or Pi Zero 2W + any Meshtastic radio
**Recommended:** Raspberry Pi 4/5 + SPI HAT (~$90)

| Component | Options |
|-----------|---------|
| **Computer** | Raspberry Pi 4/5 (recommended), Pi 3B+, Pi Zero 2W |
| **OS** | Raspberry Pi OS Bookworm 64-bit, Debian 12+, Ubuntu 22.04+ |
| **Radio (SPI)** | See SPI HATs table below |
| **Radio (USB)** | See USB Radios table below |
| **Optional** | RTL-SDR (spectrum analysis), GPS module, NanoVNA |

### SPI HATs

Native SPI HATs connect directly to the Pi's GPIO header and are managed by `meshtasticd`.
The installer auto-detects SPI and presents a HAT selection menu. Configs live in `/etc/meshtasticd/available.d/`.

| HAT | Radio Module | TX Power | Notes |
|-----|-------------|----------|-------|
| **MeshAdv-Pi HAT** | SX1262 | 33dBm (1W) | High power, GPS, PPS |
| **MeshAdv-Mini** | SX1262/SX1268 | 22dBm | GPS, temp sensor, fan, I2C/Qwiic |
| **MeshAdv-Pi v1.1** | SX1262 | Standard | Standard Pi HAT |
| **PiMesh-1W** ([MeshSmith](https://meshsmith.net/)) | SX1262 (E22-900M30S) | 30dBm (1W) | Commercial MeshAdv-class — TXCO, SMA, PoE/GPS; pin-identical to the `lora-MeshAdv-900M30S` preset (zero-config drop-in); lab-test candidate |
| **Waveshare SX126X** | SX1262 | Standard | DIO2 RF switch |
| **Ebyte E22-900M30S** | SX1262 | 30dBm (1W) | 915MHz high power |
| **Ebyte E22-400M30S** | SX1268 | 30dBm (1W) | 433MHz (EU/Asia) |
| **RAK RAK2287** | SX1262 | Standard | WisBlock HAT |
| **Adafruit RFM9x** | SX1276 | Standard | LoRa Bonnet |
| **Elecrow RFM95** | SX1276 | Standard | LoRa HAT |
| **FemtoFox** | SX1262 | Standard | DIO2/DIO3 support |
| **Seeed SenseCAP E5** | SX1262 | Standard | - |
| **PiTx LoRa** | SX1276 | Standard | - |

### USB Radios

USB radios run their own firmware. `meshtasticd` can manage them, or they work standalone via the `meshtastic` CLI.
The installer auto-detects connected USB devices.

| Device | Chipset | Notes |
|--------|---------|-------|
| **Heltec V3/V4** | ESP32-S3 (CDC) | V4 supports 28dBm TX, gateway capable |
| **Station G2** | CP2102 | Gateway capable, PoE option |
| **LILYGO T-Beam S3** | CH9102 | Built-in GPS, gateway capable |
| **RAK4631** | nRF52840 | Ultra-low power, UF2 flashing |
| **MeshToad / MeshTadpole** | CH340 | MtnMesh devices, 900mA peak draw |
| **MeshStick** | Native USB | Official Meshtastic device |
| **FTDI-based modules** | FT232 | Generic LoRa boards |

### uConsole AIO V2 (Field Unit)

The [HackerGadgets uConsole AIO V2](https://hackergadgets.com/products/uconsole-aio-v2) is a portable all-in-one mesh terminal. MeshForge auto-detects it and generates configs. Hardware arrives Q2 2026.

| Component | Spec |
|-----------|------|
| **Compute** | CM5 8GB |
| **LoRa** | SX1262 on SPI, 860-960MHz, 22dBm |
| **RTL-SDR** | RTL2832U + R860, 100KHz-1.74GHz |
| **GPS/GNSS** | Multi-constellation (GPS/BDS/GLONASS) |
| **RTC** | PCF85063A with battery backup |
| **Ethernet** | RJ45 Gigabit |

---

## Upgrading MeshForge

### Decision Tree

```
Do you need to upgrade?
  │
  ├── Import errors, stale .pyc, major version bump, or something "feels off"
  │   └── Clean Reinstall (recommended)
  │
  └── Small code change, update service files
      └── Quick Update (update.sh)
```

### Clean Reinstall (Recommended)

The safest upgrade path. Guarantees fresh code, correct dependencies, no stale files:

```bash
sudo bash /opt/meshforge/scripts/reinstall.sh
```

**What happens:**
1. Backs up configs to `~/meshforge-backup-<timestamp>/`
2. Stops MeshForge services
3. Removes `/opt/meshforge` (source + venv only)
4. Fresh `git clone` from GitHub
5. Runs `install_noc.sh` to rebuild
6. Restores your configs from backup

**What is preserved (never touched):**

| Preserved | Path | Why |
|-----------|------|-----|
| meshtasticd | apt package + `/etc/meshtasticd/config.yaml` | Separate package, your radio config |
| Radio hardware configs | `/etc/meshtasticd/config.d/` | Backed up + restored |
| Reticulum identity | `~/.reticulum/` | Your RNS address + keys |
| MeshForge user settings | `~/.config/meshforge/` | Backed up + restored |
| MQTT broker | mosquitto service + config | Separate service |
| System packages | pip, apt installs | Not managed by MeshForge |

No need to re-image your Pi. Your radio stays configured.

**Reinstall flags:**
```bash
sudo bash scripts/reinstall.sh --no-confirm    # Skip confirmation prompt
```

### Quick Update

For developers tracking the repo. Updates code, dependencies, and service files:

```bash
cd /opt/meshforge && sudo bash scripts/update.sh
```

**What happens:**
1. Pulls latest code from GitHub
2. Updates Python dependencies if `requirements.txt` changed
3. Updates desktop integration
4. Deploys updated systemd service files (rnsd crash-loop protection, startup ordering)
5. Runs `systemctl daemon-reload`

Or manually (code only — does NOT update service files):
```bash
cd /opt/meshforge && sudo git pull origin main
```

### Post-Upgrade Verification

Run the built-in verification after any upgrade:

```bash
# Automated check (recommended)
sudo python3 src/launcher.py --verify-install

# Manual checks
python3 -c "from src.__version__ import __version__; print(__version__)"
systemctl status meshtasticd rnsd
sudo python3 src/launcher_tui/main.py
```

The `--verify-install` flag checks Python imports, service status, config
file integrity, and radio hardware detection without modifying anything.

### Troubleshooting Upgrades

| Issue | Solution |
|-------|----------|
| Python import errors | `sudo bash scripts/reinstall.sh` (clean reinstall) |
| `Local changes would be overwritten` | `git stash` before pull, or use clean reinstall |
| Service won't start | `journalctl -u meshtasticd -n 50` |
| Config file conflicts | Restore from `~/meshforge-backup-*` or regenerate via TUI |
| `meshtastic` module not found | See "Python Library Conflicts" below |
| Stale `.pyc` files | Clean reinstall handles this automatically |
| Wrong bridge mode after upgrade | As of 0.5.7-beta, `bridge_mode` is an advisory label only — each bridge is gated by its own `.enabled` flag. Legacy configs auto-migrate in-place at startup with a `MIGRATION:` journal warning. If the gateway exits with a `CONFIG ERRORS` block, read the message and fix the named key in `gateway.json`; see `docs/GATEWAY_DEPLOYMENT.md` → "Refusal on inconsistency". |
| `:9443` web UI can't send messages / empty reply | Legacy TCP-mode bridges held the single-client slot on `:4403`. Check `ss -tnp \| grep :4403`. Fix: ensure `mqtt_bridge.enabled=true` and `bridge_mode="mqtt_bridge"` (or run `scripts/configure_gateway.sh` to re-render the config). |
| Gateway exits immediately with `CONFIG ERRORS` | The new refusal-on-inconsistency preflight caught a config bug — e.g. both `mesh_bridge.enabled` and `rns_transport.enabled` true, or `mesh_bridge` primary + secondary sharing a serial device. The error block names the exact key to change. This is by design: the gateway will not silently run a different mode than you asked for. |

#### Python Library Conflicts

On Raspberry Pi OS Bookworm+ (externally-managed Python), the `meshtastic`
library may fail to install. If you see "externally-managed-environment" or
module import failures:

```bash
# Force reinstall (use with caution on managed Python)
pip install meshtastic --break-system-packages --ignore-installed

# Alternative: virtual environment
python3 -m venv ~/.meshforge-venv
source ~/.meshforge-venv/bin/activate
pip install meshtastic
```

The `--break-system-packages` flag bypasses PEP 668 protections. Only use
this if you understand the implications for your system Python.

MeshForge's diagnostics can detect this automatically:
```bash
# TUI: System → Diagnostics → Gateway Pre-flight
# Or directly:
sudo python3 src/launcher_tui/main.py  # Dashboard shows import warnings
```

### Version History

See the full changelog in `src/__version__.py` or run:
```bash
python3 -c "from src.__version__ import show_version_history; show_version_history()"
```

---

