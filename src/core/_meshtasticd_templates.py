"""
Radio configuration templates for meshtasticd.

Extracted from meshtasticd_config.py for CLAUDE.md #6 compliance (<1,500 lines).
Contains RadioType enum and RADIO_TEMPLATES dictionary with all supported
radio hardware configs.
"""

from enum import Enum


class RadioType(Enum):
    """Type of Meshtastic radio connection."""
    USB_SERIAL = "usb_serial"    # T-Beam, Heltec, etc. via USB
    NATIVE_SPI = "native_spi"    # Meshtoad, RAK HAT via SPI
    NATIVE_I2C = "native_i2c"    # Future: I2C connected radios
    UNKNOWN = "unknown"


# Default config templates for all supported radios
# GPIO pins sourced from src/config/hardware.py KNOWN_SPI_HATS / KNOWN_USB_MODULES
RADIO_TEMPLATES = {
    # ─────────────────────────────────────────────
    # USB Radios (run own firmware, managed via serial)
    # ─────────────────────────────────────────────
    "heltec-usb": {
        "name": "heltec-usb",
        "radio_type": RadioType.USB_SERIAL,
        "description": "Heltec V3/V4 USB (ESP32-S3, 28dBm TX, gateway)",
        "config": """\
# Heltec V3/V4 USB Radio Configuration
# Chipset: ESP32-S3 (USB CDC)
# V4 supports 28dBm TX power. Gateway capable.
# Power: 500mA typical, 1A peak (V4 at max TX)

Serial:
  Device: auto

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
"""
    },
    "station-g2-usb": {
        "name": "station-g2-usb",
        "radio_type": RadioType.USB_SERIAL,
        "description": "Station G2 USB (CP2102, gateway, PoE)",
        "config": """\
# Station G2 USB Radio Configuration
# Chipset: CP2102 USB-Serial
# Gateway capable. PoE option available.

Serial:
  Device: auto

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
"""
    },
    "tbeam-usb": {
        "name": "tbeam-usb",
        "radio_type": RadioType.USB_SERIAL,
        "description": "LILYGO T-Beam S3 USB (CH9102, GPS, gateway)",
        "config": """\
# LILYGO T-Beam S3 USB Radio Configuration
# Chipset: CH9102 USB-Serial
# Built-in GPS. Gateway capable.

Serial:
  Device: auto

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
"""
    },
    "rak4631-usb": {
        "name": "rak4631-usb",
        "radio_type": RadioType.USB_SERIAL,
        "description": "RAK4631 USB (nRF52840 + SX1262, ultra-low power)",
        "config": """\
# RAK4631 USB Radio Configuration
# Chipset: nRF52840 + SX1262
# Ultra-low power. Flash via UF2.

Serial:
  Device: auto

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
"""
    },
    "meshtoad-usb": {
        "name": "meshtoad-usb",
        "radio_type": RadioType.USB_SERIAL,
        "description": "MeshToad/MeshTadpole USB (CH340, MtnMesh)",
        "config": """\
# MeshToad / MeshTadpole USB Radio Configuration
# Chipset: CH340/CH341 USB-Serial
# MtnMesh devices. 900mA peak power draw.

Serial:
  Device: auto

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
"""
    },
    "meshstick-usb": {
        "name": "meshstick-usb",
        "radio_type": RadioType.USB_SERIAL,
        "description": "MeshStick USB (official Meshtastic device)",
        "config": """\
# MeshStick USB Radio Configuration
# Official Meshtastic USB device.

Serial:
  Device: auto

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
"""
    },
    "usb-serial-generic": {
        "name": "usb-serial-generic",
        "radio_type": RadioType.USB_SERIAL,
        "description": "Generic USB Serial Radio (FTDI/FT232, fallback)",
        "config": """\
# Generic USB Serial Radio Configuration
# For FTDI (FT232) and other USB-serial LoRa boards.
# Use as fallback when your specific device is not listed.

Serial:
  Device: auto

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
"""
    },
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

TCP:
  Port: 4403

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
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

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
"""
    },
}
