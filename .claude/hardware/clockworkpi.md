# ClockworkPi Hardware Integration Guide

MeshForge support for ClockworkPi DevTerm and uConsole devices.

**Principle: Don't break code. Safety over features.**

---

## Device Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                    CLOCKWORKPI DEVICE FAMILY                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌─────────────────────┐     ┌─────────────────────┐                │
│  │      DevTerm        │     │      uConsole       │                │
│  │  ┌───────────────┐  │     │  ┌───────────────┐  │                │
│  │  │  1280 x 480   │  │     │  │  1280 x 720   │  │                │
│  │  │   Ultrawide   │  │     │  │   5" Display  │  │                │
│  │  └───────────────┘  │     │  └───────────────┘  │                │
│  │                     │     │                     │                │
│  │  Thermal Printer    │     │  Backlit Keyboard   │                │
│  │  58mm Paper Roll    │     │  Game Pad + Trackball│               │
│  └─────────────────────┘     └─────────────────────┘                │
│                                                                       │
│  Shared Core Modules: A04, A06, CM4, R01 (RISC-V)                   │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

## Core Module Options

| Module | CPU | Cores | RAM | MeshForge Support |
|--------|-----|-------|-----|-------------------|
| **CM4** | BCM2711 | 4× Cortex-A72 @ 1.5GHz | 1-8GB | ✅ Full (same as Pi 4) |
| **A06** | RK3399 | 2× A72 + 4× A53 | 2-4GB | ✅ Full |
| **A04** | RK3566 | 4× Cortex-A55 @ 1.8GHz | 1-2GB | ✅ Full |
| **R01** | Allwinner D1 | 1× RISC-V @ 1GHz | 1GB | ⚠️ Experimental |

### Recommended: CM4 Module

For MeshForge, the **CM4 module** is recommended because:
- Identical to Raspberry Pi 4 (same kernel, drivers, packages)
- Best community support for meshtasticd
- Full GTK4/libadwaita compatibility
- Most tested configuration

---

## Display Considerations

### DevTerm (1280×480 Ultrawide)

```
┌──────────────────────────────────────────────────────────────┐
│ MeshForge │ Dashboard │ Radio │ RNS │ Map │ Tools │ Settings │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  Node List (scrollable)     │  Map View (compact)            │
│  ┌───────────────────────┐  │  ┌─────────────────────────┐   │
│  │ !abc1234  MyNode   2m │  │  │       [map tiles]       │   │
│  │ !def5678  Relay   15m │  │  │                         │   │
│  │ !ghi9012  Base    1h  │  │  │     ● ●    ●           │   │
│  └───────────────────────┘  │  └─────────────────────────┘   │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**UI Adaptations needed:**
- Horizontal tab layout (already implemented)
- Side-by-side panels preferred
- Reduce vertical padding
- Consider `--compact` mode flag

### uConsole (1280×720)

Standard aspect ratio - MeshForge GTK UI works without modification.

```
┌────────────────────────────────┐
│ MeshForge  [tabs...]           │
├────────────────────────────────┤
│                                │
│   Standard panel layout        │
│   works as-is on 720p          │
│                                │
│   GTK4/libadwaita responsive   │
│                                │
└────────────────────────────────┘
```

---

## HackerGadgets All-In-One Expansion Board

The [HackerGadgets uConsole Extension Board](https://hackergadgets.com/products/uconsole-rtl-sdr-lora-gps-rtc-usb-hub-all-in-one-extension-board) is the recommended LoRa solution.

### Specifications

| Component | Chip | Specs |
|-----------|------|-------|
| **LoRa** | Semtech SX1262 | 860-960MHz, 22dBm max |
| **RTL-SDR** | RTL2832U + R860 | 100kHz - 1.74GHz |
| **GPS** | Multi-mode | GPS, BDS, GNSS |
| **RTC** | PCF85063A | CR1220 battery backup |
| **USB Hub** | - | USB-A, USB-C, internal header |

### SPI1 GPIO Pinout

```
┌─────────────────────────────────────────────────────────────────────┐
│                 HACKERGADGETS BOARD - SPI1 PINOUT                    │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  SX1262 LoRa Module                                                  │
│  ─────────────────                                                   │
│  SPI1_MOSI  ────────────────────────────────  GPIO 20 (Pin 38)       │
│  SPI1_MISO  ────────────────────────────────  GPIO 19 (Pin 35)       │
│  SPI1_CLK   ────────────────────────────────  GPIO 21 (Pin 40)       │
│  SPI1_CE0   ────────────────────────────────  GPIO 18 (Pin 12)  CS   │
│  BUSY       ────────────────────────────────  GPIO 24 (Pin 18)       │
│  RESET      ────────────────────────────────  GPIO 25 (Pin 22)       │
│  DIO1/IRQ   ────────────────────────────────  GPIO 23 (Pin 16)       │
│                                                                       │
│  Note: DIO2_AS_RF_SWITCH: true (antenna TX/RX switching)             │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

### meshtasticd config.yaml

```yaml
# /etc/meshtasticd/config.yaml
# HackerGadgets uConsole All-In-One Board

Logging:
  LogLevel: info  # debug for troubleshooting

Lora:
  Module: sx1262
  DIO2_AS_RF_SWITCH: true
  CS: 18              # SPI1_CE0
  IRQ: 23             # DIO1
  Busy: 24
  Reset: 25
  spidev: spidev1.0   # Use SPI1, not SPI0

GPS:
  SerialPath: /dev/ttyAMA0  # GPS module

Webserver:
  Port: 443
  RootPath: /usr/share/meshtasticd/web

General:
  MaxNodes: 200
  MaxMessageQueue: 100
```

### Installation Steps

```bash
# 1. Use Rex's Bookworm image (recommended)
#    Download from ClockworkPi forum - has SDR++ and meshtasticd deps

# 2. Stop conflicting SPI1 services (DevTerm printer uses SPI1)
sudo systemctl stop devterm-printer
sudo systemctl disable devterm-printer

# 3. Install meshtasticd dependencies
sudo apt update
sudo apt install -y \
    libgpiod-dev \
    libyaml-cpp-dev \
    libbluetooth-dev \
    libusb-1.0-0-dev \
    libi2c-dev \
    openssl \
    libssl-dev \
    libulfius-dev \
    liborcania-dev

# 4. Enable SPI1
sudo raspi-config nonint do_spi 0
# Or add to /boot/config.txt:
#   dtparam=spi=on
#   dtoverlay=spi1-3cs

# 5. Install meshtasticd
# Follow official Meshtastic Linux Native guide

# 6. Copy config
sudo cp config.yaml /etc/meshtasticd/config.yaml

# 7. Start service
sudo systemctl enable meshtasticd
sudo systemctl start meshtasticd

# 8. Verify
sudo journalctl -u meshtasticd -f
```

---

## MeshForge Installation on ClockworkPi

### Prerequisites

```bash
# GTK4 and libadwaita (should be available on Bookworm)
sudo apt install -y \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-4.0 \
    libadwaita-1-0 \
    gir1.2-adw-1

# Python packages
pip3 install rich textual flask meshtastic --break-system-packages

# Clone MeshForge
git clone https://github.com/Nursedude/meshforge.git
cd meshforge
```

### Launch Options

```bash
# GTK Desktop UI (recommended for uConsole)
python3 src/main_gtk.py

# Web UI (access from another device)
python3 src/main_web.py --port 8080

# Terminal TUI (lowest resource usage)
python3 src/main_tui.py

# DevTerm compact mode (future)
python3 src/main_gtk.py --compact
```

### Performance Tuning for A06

The A06 module has a "Gearbox" system for power/performance trade-offs:

| Gear | CPU Config | GPU | Best For |
|------|------------|-----|----------|
| 1 | 4× LITTLE @ 408MHz | 200MHz | Battery saver |
| 2 | 4× LITTLE @ 816MHz | 400MHz | **Default, good for MeshForge** |
| 3 | 4× LITTLE @ 1.4GHz | 400MHz | Faster UI |
| 4 | 2× big @ 1.0GHz | 600MHz | Mixed workload |
| 5 | 2× big @ 1.8GHz | 800MHz | Performance |
| 6 | 2× big + 4× LITTLE | 800MHz | Maximum |

```bash
# Check current gear
cat /sys/devices/platform/ff650000.i2c/i2c-2/2-0062/gearbox

# Set gear (1-6)
echo 3 | sudo tee /sys/devices/platform/ff650000.i2c/i2c-2/2-0062/gearbox
```

---

## Hardware Detection

MeshForge should auto-detect the HackerGadgets board via:

1. **SPI1 device check**: `/dev/spidev1.0` exists
2. **GPIO chip**: `/dev/gpiochip0` or `/dev/gpiochip4`
3. **USB devices**: RTL-SDR shows in `lsusb`
4. **GPS serial**: `/dev/ttyAMA0` or `/dev/serial0`

### Detection Code (Future Plugin)

```python
# src/config/clockworkpi.py
import os
from pathlib import Path

def detect_clockworkpi_hardware() -> dict:
    """Detect ClockworkPi and HackerGadgets hardware."""
    result = {
        "device": None,
        "module": None,
        "lora": False,
        "gps": False,
        "rtl_sdr": False,
    }

    # Detect uConsole/DevTerm by display resolution
    try:
        # uConsole: 1280x720, DevTerm: 1280x480
        # Check via fbset or Xrandr
        pass
    except Exception:
        pass

    # Detect SPI1 LoRa
    if Path("/dev/spidev1.0").exists():
        result["lora"] = True

    # Detect GPS
    for gps_path in ["/dev/ttyAMA0", "/dev/serial0"]:
        if Path(gps_path).exists():
            result["gps"] = True
            break

    # Detect RTL-SDR via USB
    try:
        import subprocess
        lsusb = subprocess.run(["lsusb"], capture_output=True, text=True)
        if "RTL2838" in lsusb.stdout or "RTL2832" in lsusb.stdout:
            result["rtl_sdr"] = True
    except Exception:
        pass

    return result
```

---

## Known Issues

### SPI1 Conflict with DevTerm Printer

The DevTerm's thermal printer uses SPI1. If you're using the HackerGadgets LoRa board, you must disable the printer service:

```bash
sudo systemctl stop devterm-printer
sudo systemctl disable devterm-printer
```

### Display Rotation

Some ClockworkPi images require display rotation. If the screen is rotated:

```bash
# Wayland (recommended)
# Usually auto-detected

# X11 fallback
xrandr --output DSI-1 --rotate right
```

### GPIO Numbering

The A06 module uses different GPIO chip numbering than CM4:
- CM4: `gpiochip0` (same as Pi 4)
- A06: May need `gpiochip4` in meshtasticd config

---

## Integration Roadmap

### ✅ Currently Supported
- GTK4/libadwaita UI on ARM
- meshtasticd via TCP (localhost:4403)
- Web UI for remote access
- TUI for SSH sessions

### 🔧 Plugin Candidates
- HackerGadgets board auto-detection
- RTL-SDR spectrum display
- GPS position auto-config
- DevTerm thermal printer output

### 📋 Planned
- Compact mode for 1280×480 displays
- Battery status via I2C
- Hardware-accelerated map rendering

---

## Resources

- [ClockworkPi GitHub](https://github.com/clockworkpi)
- [ClockworkPi Forum](https://forum.clockworkpi.com/)
- [HackerGadgets Store](https://hackergadgets.com/)
- [Meshtastic Linux Native Docs](https://meshtastic.org/docs/hardware/devices/linux-native-hardware/)
- [Rex's Bookworm Image](https://forum.clockworkpi.com/) (search for "Rex Bookworm")

---

*Last updated: 2026-01-05*
