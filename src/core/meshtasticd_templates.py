"""Radio hardware configuration templates for meshtasticd.

Extracted from meshtasticd_config.py for file size compliance (CLAUDE.md #6).

Contains RADIO_TEMPLATES dict (SPI and CH341 USB-SPI radio overlays),
RadioType enum, and RadioConfig dataclass.
"""

from dataclasses import dataclass
from typing import Optional
from enum import Enum

# Top-level keys a HAT overlay in config.d/ must not carry: meshtasticd lets an
# overlay override config.yaml, and `Webserver: Port: 443` moved the API off
# :9443 (#58, moc3 2026-05-18). One constant for the two consumers — the TUI
# activation sanitizer and Config Doctor's audit of what is already active.
HAT_OVERLAY_FORBIDDEN_KEYS = frozenset({
    'Webserver',
    'TCP',
    'Logging',
    'MQTT',
    'Bluetooth',
    'General',
})

# MeshForge's own overlay (written by the TUI config editors). It legitimately
# carries `General: MaxNodes`, so the HAT key rule does not apply to it.
OVERRIDES_NAMES = frozenset({'meshforge-overrides.yaml', 'meshforge-overrides.yml'})


# Top-level keys meshtasticd itself reads — measured, not recalled:
#   grep -oE 'yamlConfig\["[A-Za-z0-9_]+"\]' src/platform/portduino/PortduinoGlue.cpp | sort -u
# on v2.7.26.54e0d8d gives these 12. A key outside this set is silently
# ignored — a `Serial:`-only overlay configures nothing (verified with
# `meshtasticd --output-yaml`: merged config identical to no overlay).
MESHTASTICD_TOP_LEVEL_KEYS = frozenset({
    'Config', 'Display', 'General', 'GPIO', 'GPS', 'HostMetrics', 'I2C',
    'Input', 'Logging', 'Lora', 'Touchscreen', 'Webserver',
})


def classify_overlay(content: str) -> str:
    """What a meshtasticd overlay IS, from its content, never its filename.

    Judged on what survives activation: the keys HAT_OVERLAY_FORBIDDEN_KEYS
    strips (Webserver, Logging, General, …) are removed first, so a
    `Serial:` + `Webserver:` file is 'ignored', not a radio.
      'ch341'   — `Lora:` with `spidev: ch341` (exact, as the firmware
                  compares): a USB-SPI board (MeshToad, MeshStick, …).
      'spi'     — any other `Lora:` overlay (a HAT on the Pi's SPI bus).
      'aux'     — no `Lora:`, but keys meshtasticd reads (Display, GPS,
                  I2C, …): a real overlay, NOT a radio config — activating
                  it as one would replace the radio's overlay.
      'ignored' — nothing meshtasticd reads survives (e.g. `Serial:` only),
                  or not YAML: activating it changes nothing.
    Only 'ch341'/'spi' belong in a radio menu. First YAML document only, as
    yaml-cpp's LoadFile reads. Twins: MeshForge core/meshtasticd_templates.py,
    MeshAnchor utils/meshtasticd_overlay.py — same body.
    """
    import yaml
    try:
        doc = next(yaml.safe_load_all(content), None)
    except yaml.YAMLError:
        return 'ignored'
    if not isinstance(doc, dict):
        return 'ignored'
    keys = (set(doc) - HAT_OVERLAY_FORBIDDEN_KEYS) & MESHTASTICD_TOP_LEVEL_KEYS
    lora = doc.get('Lora')
    if 'Lora' in keys and isinstance(lora, dict):
        return 'ch341' if lora.get('spidev') == 'ch341' else 'spi'
    return 'aux' if keys - {'Lora'} else 'ignored'


def sanitize_hat_overlay(content: str):
    """Strip forbidden top-level blocks from a HAT overlay before activation.

    Returns ``(sanitized_yaml_text, stripped_keys_list)``. If the input
    doesn't parse as YAML or isn't a top-level mapping, returns it
    unchanged with an empty strip list — the caller (and meshtasticd's
    own load) will surface the parse error loudly rather than silently
    mangling the operator's content.

    Every path that copies a template into config.d/ goes through this: the
    radio handler, the first-run wizard and the config file manager.
    """
    import yaml
    try:
        loaded = yaml.safe_load(content)
    except yaml.YAMLError:
        return content, []
    if not isinstance(loaded, dict):
        return content, []
    stripped = sorted(k for k in loaded if k in HAT_OVERLAY_FORBIDDEN_KEYS)
    if not stripped:
        return content, []
    for key in stripped:
        del loaded[key]
    return yaml.safe_dump(loaded, sort_keys=False, default_flow_style=False), stripped


class RadioType(Enum):
    """Type of Meshtastic radio connection."""
    USB_SERIAL = "usb_serial"    # T-Beam, Heltec, etc. via USB
    NATIVE_SPI = "native_spi"    # Meshtoad, RAK HAT via SPI
    NATIVE_I2C = "native_i2c"    # Future: I2C connected radios
    UNKNOWN = "unknown"


@dataclass
class RadioConfig:
    """Configuration for a radio device."""
    name: str
    radio_type: RadioType
    device_path: Optional[str] = None
    chip: Optional[str] = None  # e.g., "sx1262", "sx1276"
    description: str = ""
    enabled: bool = False
    config_file: Optional[str] = None


# Default config templates for all supported radios
# GPIO pins sourced from src/config/hardware.py KNOWN_SPI_HATS / KNOWN_USB_MODULES
RADIO_TEMPLATES = {
    # No USB-serial entries: meshtasticd 2.7.26 parses 11 top-level keys and
    # `Serial:` is not one (PortduinoGlue.cpp) — a standalone USB node runs
    # its own firmware and is reached over serial directly (gateway
    # `meshtastic.connection_type: serial`), never via a meshtasticd overlay.
    # CH341 USB-SPI boards are driven by `Lora: ... spidev: ch341` overlays
    # (upstream `lora-usb-*.yaml`). The 7 `Serial:` entries were removed
    # 2026-09-30.
    # ─────────────────────────────────────────────
    # SPI HATs (GPIO-connected, native meshtasticd)
    # ─────────────────────────────────────────────
    "meshtoad-spi": {
        "name": "meshtoad-spi",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "sx1262",
        "description": "Meshtoad/MeshStick SPI Radio (SX1262 via CH341)",
        "config": """\
# Meshtoad / MeshStick SPI Radio Configuration
# Uses CH341 USB-to-SPI adapter with SX1262

Lora:
  Module: sx1262
  spidev: ch341
  CS: 0
  IRQ: 6
  Reset: 2
  Busy: 4
  DIO2_AS_RF_SWITCH: true
  DIO3_TCXO_VOLTAGE: true
"""
    },
    "meshadv-pi-hat": {
        "name": "meshadv-pi-hat",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "sx1262",
        "description": "MeshAdv-Pi-Hat 1W (SX1262, GPS, high-power)",
        "config": """\
# MeshAdv-Pi-Hat SPI Configuration (1W High-Power)
# Hardware: E22-900M30S/33S (SX1262), +33dBm (1W)
# Features: GPS (ATGM336H), I2C/Qwiic, PPS

Lora:
  Module: sx1262
  CS: 21
  IRQ: 16
  Busy: 20
  Reset: 18
  RXen: 12
  TXen: 13
  DIO2_AS_RF_SWITCH: true
  DIO3_TCXO_VOLTAGE: true

GPS:
  SerialPath: /dev/ttyS0

I2C:
  I2CDevice: /dev/i2c-1
"""
    },
    "meshadv-mini": {
        "name": "meshadv-mini",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "sx1262",
        "description": "MeshAdv-Mini (SX1262, GPS, +22dBm)",
        "config": """\
# MeshAdv-Mini SPI Configuration
# Hardware: SX1262/SX1268, +22dBm
# Features: GPS, Temperature Sensor, PWM Fan, I2C/Qwiic

Lora:
  Module: sx1262
  CS: 8
  IRQ: 16
  Busy: 20
  Reset: 24
  RXen: 12
  DIO2_AS_RF_SWITCH: true
  DIO3_TCXO_VOLTAGE: true

GPS:
  SerialPath: /dev/ttyS0

I2C:
  I2CDevice: /dev/i2c-1
"""
    },
    "meshadv-pi-v1.1": {
        "name": "meshadv-pi-v1.1",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "sx1262",
        "description": "MeshAdv-Pi v1.1 (SX1262)",
        "config": """\
# MeshAdv-Pi v1.1 SPI Configuration
# Hardware: SX1262

Lora:
  Module: sx1262
  CS: 8
  IRQ: 22
  Busy: 23
  Reset: 24
  DIO2_AS_RF_SWITCH: true
"""
    },
    "waveshare-sx1262": {
        "name": "waveshare-sx1262",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "sx1262",
        "description": "Waveshare SX1262 LoRa HAT",
        "config": """\
# Waveshare SX1262 LoRa HAT SPI Configuration

Lora:
  Module: sx1262
  CS: 21
  IRQ: 16
  Busy: 20
  Reset: 18
  DIO2_AS_RF_SWITCH: true
"""
    },
    "rak-hat-spi": {
        "name": "rak-hat-spi",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "sx1262",
        "description": "RAK WisLink / RAK2287 SPI HAT (SX1262)",
        "config": """\
# RAK WisLink / RAK2287 SPI HAT Configuration

Lora:
  Module: sx1262
  CS: 8
  IRQ: 25
  Busy: 24
  Reset: 17
  DIO2_AS_RF_SWITCH: true
"""
    },
    "adafruit-rfm9x": {
        "name": "adafruit-rfm9x",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "sx1276",
        "description": "Adafruit RFM9x LoRa Radio Bonnet (SX1276)",
        "config": """\
# Adafruit RFM9x LoRa Radio Bonnet SPI Configuration
# Hardware: SX1276 (RFM95/RFM96) — no Busy pin

Lora:
  Module: sx1276
  CS: 7
  IRQ: 25
  Reset: 17
"""
    },
    "femtofox": {
        "name": "femtofox",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "sx1262",
        "description": "FemtoFox LoRa Board (compact SX1262)",
        "config": """\
# FemtoFox LoRa Board SPI Configuration

Lora:
  Module: sx1262
  CS: 8
  IRQ: 16
  Busy: 20
  Reset: 24
  DIO2_AS_RF_SWITCH: true
  DIO3_TCXO_VOLTAGE: true
"""
    },
    "ebyte-e22-900m30s": {
        "name": "ebyte-e22-900m30s",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "sx1262",
        "description": "Ebyte E22-900M30S 1W (SX1262, 915MHz)",
        "config": """\
# Ebyte E22-900M30S SPI Configuration (1W, 915MHz)
# WARNING: High-power module — requires adequate power supply.

Lora:
  Module: sx1262
  CS: 21
  IRQ: 16
  Busy: 20
  Reset: 18
  RXen: 12
  TXen: 13
  DIO2_AS_RF_SWITCH: true
  DIO3_TCXO_VOLTAGE: true
"""
    },
    "ebyte-e22-400m30s": {
        "name": "ebyte-e22-400m30s",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "sx1268",
        "description": "Ebyte E22-400M30S 1W (SX1268, 433MHz EU/Asia)",
        "config": """\
# Ebyte E22-400M30S SPI Configuration (1W, 433MHz EU/Asia)
# WARNING: High-power module — requires adequate power supply.

Lora:
  Module: sx1268
  CS: 21
  IRQ: 16
  Busy: 20
  Reset: 18
  RXen: 12
  TXen: 13
  DIO2_AS_RF_SWITCH: true
  DIO3_TCXO_VOLTAGE: true
"""
    },
    "elecrow-rfm95": {
        "name": "elecrow-rfm95",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "sx1276",
        "description": "Elecrow RFM95 LoRa HAT (SX1276)",
        "config": """\
# Elecrow RFM95 LoRa HAT SPI Configuration
# Hardware: SX1276 (RFM95) — no Busy pin

Lora:
  Module: sx1276
  CS: 25
  IRQ: 5
  Reset: 17
"""
    },
    "seeed-sensecap": {
        "name": "seeed-sensecap",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "sx1262",
        "description": "Seeed SenseCAP E5 LoRa HAT (SX1262)",
        "config": """\
# Seeed SenseCAP E5 LoRa HAT SPI Configuration

Lora:
  Module: sx1262
  CS: 8
  IRQ: 25
  Reset: 22
  DIO2_AS_RF_SWITCH: true
"""
    },
    # ─────────────────────────────────────────────
    # USB Radios via CH341 USB-to-SPI (upstream naming)
    # ─────────────────────────────────────────────
    "lora-pinedio-usb-sx1262": {
        "name": "lora-pinedio-usb-sx1262",
        "radio_type": RadioType.USB_SERIAL,
        "chip": "sx1262",
        "description": "Pine64 Pinedio USB (CH341 + SX1262)",
        "config": """\
# Pine64 Pinedio USB LoRa Adapter (CH341 + SX1262)

Lora:
  Module: sx1262
  CS: 0
  IRQ: 10
  spidev: ch341
"""
    },
    "lora-usb-meshtoad-e22": {
        "name": "lora-usb-meshtoad-e22",
        "radio_type": RadioType.USB_SERIAL,
        "chip": "sx1262",
        "description": "MeshToad E22 USB (CH341 + SX1262)",
        "config": """\
# MeshToad E22 USB LoRa Adapter (CH341 + SX1262)

Lora:
  Module: sx1262
  CS: 0
  IRQ: 6
  Reset: 2
  Busy: 4
  RXen: 1
  DIO2_AS_RF_SWITCH: true
  DIO3_TCXO_VOLTAGE: true
  spidev: ch341
  USB_PID: 0x5512
  USB_VID: 0x1A86
"""
    },
    # ─────────────────────────────────────────────
    # SPI HATs — upstream meshtasticd naming (lora-* prefix)
    # ─────────────────────────────────────────────
    "display-waveshare-1-44": {
        "name": "display-waveshare-1-44",
        "radio_type": RadioType.NATIVE_SPI,
        "description": "Waveshare 1.44\" LCD HAT (ST7735S, trackball)",
        "config": """\
# Waveshare 1.44" LCD HAT (ST7735S) Display Configuration

Display:
  Panel: ST7735S
  spidev: spidev0.0
  DC: 25
  Backlight: 24
  Width: 128
  Height: 128
  Reset: 27
  OffsetX: 2
  OffsetY: 1

Input:
  TrackballUp: 6
  TrackballDown: 19
  TrackballLeft: 5
  TrackballRight: 26
  TrackballPress: 13
  TrackballDirection: FALLING
"""
    },
    "display-waveshare-2.8": {
        "name": "display-waveshare-2.8",
        "radio_type": RadioType.NATIVE_SPI,
        "description": "Waveshare 2.8\" LCD + Touchscreen (ST7789)",
        "config": """\
# Waveshare 2.8" RPi LCD Display + Touchscreen

Display:
  Panel: ST7789
  CS: 8
  DC: 22
  Backlight: 18
  Width: 240
  Height: 320
  Reset: 27
  Rotate: true
  Invert: true

Touchscreen:
  Module: XPT2046
  CS: 7
  IRQ: 17
"""
    },
    "lora-Adafruit-RFM9x": {
        "name": "lora-Adafruit-RFM9x",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "RF95",
        "description": "Adafruit RFM9x (upstream naming, RF95/SX1276)",
        "config": """\
# Adafruit RFM9x LoRa Radio Bonnet (upstream naming)

Lora:
  Module: RF95
  Reset: 25
  CS: 7
  IRQ: 22
"""
    },
    "lora-MeshAdv-900M30S": {
        "name": "lora-MeshAdv-900M30S",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "sx1262",
        "description": "MeshAdv-Pi E22-900M30S 1W (SX1262, high-power)",
        "config": """\
# MeshAdv-Pi E22-900M30S SPI Configuration (1W)

Lora:
  Module: sx1262
  CS: 21
  IRQ: 16
  Busy: 20
  Reset: 18
  TXen: 13
  RXen: 12
  DIO3_TCXO_VOLTAGE: true
"""
    },
    "lora-MeshAdv-Mini-900M22S": {
        "name": "lora-MeshAdv-Mini-900M22S",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "sx1262",
        "description": "MeshAdv Mini E22-900M22S (SX1262)",
        "config": """\
# MeshAdv Mini E22-900M22S SPI Configuration

Lora:
  Module: sx1262
  CS: 8
  IRQ: 16
  Busy: 20
  Reset: 24
  RXen: 12
  DIO2_AS_RF_SWITCH: true
  DIO3_TCXO_VOLTAGE: true
"""
    },
    "lora-RAK6421-13300-slot1": {
        "name": "lora-RAK6421-13300-slot1",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "sx1262",
        "description": "RAK6421 Pi-HAT RAK13300 Slot 1 (SX1262)",
        "config": """\
# RAK6421 Pi-HAT with RAK13300 — Slot 1

Lora:
  Module: sx1262
  IRQ: 22
  Reset: 16
  Busy: 24
  DIO3_TCXO_VOLTAGE: true
  DIO2_AS_RF_SWITCH: true
  spidev: spidev0.0
"""
    },
    "lora-RAK6421-13300-slot2": {
        "name": "lora-RAK6421-13300-slot2",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "sx1262",
        "description": "RAK6421 Pi-HAT RAK13300 Slot 2 (SX1262)",
        "config": """\
# RAK6421 Pi-HAT with RAK13300 — Slot 2

Lora:
  Module: sx1262
  IRQ: 18
  Reset: 24
  Busy: 19
  DIO3_TCXO_VOLTAGE: true
  DIO2_AS_RF_SWITCH: true
  spidev: spidev0.1
"""
    },
    "lora-lyra-picocalc-wio-sx1262": {
        "name": "lora-lyra-picocalc-wio-sx1262",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "sx1262",
        "description": "Lyra PicoCalc WIO SX1262 (custom gpiochip)",
        "config": """\
# Lyra PicoCalc WIO SX1262 SPI Configuration

Lora:
  Module: sx1262
  DIO2_AS_RF_SWITCH: true
  DIO3_TCXO_VOLTAGE: true
  SX126X_MAX_POWER: 22
  spidev: spidev1.0
  SPI_Speed: 2000000
"""
    },
    "lora-meshstick-1262": {
        "name": "lora-meshstick-1262",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "sx1262",
        "description": "MeshStick 1262 SPI (CH341 + SX1262)",
        "config": """\
# MeshStick 1262 SPI Configuration (CH341)

Lora:
  Module: sx1262
  CS: 0
  IRQ: 6
  Reset: 2
  Busy: 4
  spidev: ch341
  DIO3_TCXO_VOLTAGE: true
  USB_PID: 0x5512
  USB_VID: 0x1A86
"""
    },
    "lora-piggystick-lr1121": {
        "name": "lora-piggystick-lr1121",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "lr1121",
        "description": "PiggyStick LR1121 (CH341 + LR1121)",
        "config": """\
# PiggyStick LR1121 SPI Configuration (CH341)

Lora:
  Module: lr1121
  CS: 0
  IRQ: 6
  Reset: 2
  Busy: 4
  spidev: ch341
  DIO3_TCXO_VOLTAGE: 1.8
  USB_PID: 0x5512
  USB_VID: 0x1A86
"""
    },
    "lora-raxda-rock2f-starter-edition-hat": {
        "name": "lora-raxda-rock2f-starter-edition-hat",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "sx1262",
        "description": "Radxa Rock 2F Starter Edition HAT (SX1262)",
        "config": """\
# Radxa Rock 2F Starter Edition HAT SPI Configuration

Lora:
  Module: sx1262
  DIO2_AS_RF_SWITCH: true
  DIO3_TCXO_VOLTAGE: 1.8
  spidev: spidev0.1
"""
    },
    "lora-starter-edition-sx1262-i2c": {
        "name": "lora-starter-edition-sx1262-i2c",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "sx1262",
        "description": "Starter Edition SX1262 I2C RPi HAT",
        "config": """\
# Starter Edition SX1262 I2C Raspberry Pi HAT

Lora:
  Module: sx1262
  DIO2_AS_RF_SWITCH: true
  DIO3_TCXO_VOLTAGE: true
  CS: 8
  IRQ: 22
  Busy: 4
  Reset: 18
"""
    },
    "lora-waveshare-sxxx": {
        "name": "lora-waveshare-sxxx",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "sx1262",
        "description": "Waveshare SX126X XXXM LoRa HAT (SX1262)",
        "config": """\
# Waveshare SX126X XXXM LoRa HAT SPI Configuration

Lora:
  Module: sx1262
  DIO2_AS_RF_SWITCH: true
  CS: 21
  IRQ: 16
  Busy: 20
  Reset: 18
  SX126X_ANT_SW: 6
"""
    },
    "lora-ws-raspberry-pi-pico-to-rpi-adapter": {
        "name": "lora-ws-raspberry-pi-pico-to-rpi-adapter",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "sx1262",
        "description": "Waveshare Pico-to-RPi Adapter (SX1262)",
        "config": """\
# Waveshare Raspberry Pi Pico to RPi Adapter

Lora:
  Module: sx1262
  DIO2_AS_RF_SWITCH: true
  DIO3_TCXO_VOLTAGE: true
  CS: 21
  IRQ: 16
  Busy: 20
  Reset: 18
"""
    },
    "lora-ws-raspberry-pico-to-orangepi-03": {
        "name": "lora-ws-raspberry-pico-to-orangepi-03",
        "radio_type": RadioType.NATIVE_SPI,
        "chip": "sx1262",
        "description": "Waveshare SX1262 on Orange Pi Zero3 (gpiochip)",
        "config": """\
# Waveshare SX1262 on Orange Pi Zero3 via Pico Adapter

Lora:
  Module: sx1262
  DIO2_AS_RF_SWITCH: true
  DIO3_TCXO_VOLTAGE: true
  spidev: spidev1.1
"""
    },
}
