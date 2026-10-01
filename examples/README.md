# MeshForge Configuration Examples

This directory contains example configurations to help you get started with MeshForge.

## Quick Setup

```bash
# 1. Copy gateway config to user directory
mkdir -p ~/.config/meshforge
cp examples/configs/gateway-basic.json ~/.config/meshforge/gateway.json

# 2. Radio hardware (requires sudo): a Pi SPI HAT gets an overlay —
#    sudo python3 scripts/sanitize_overlay.py examples/configs/meshtasticd-spi-hat.yaml /etc/meshtasticd/config.d/
#    A USB radio: see "USB radios" below (the setup wizard routes it).

# 3. Start MeshForge
sudo python3 src/launcher.py
```

---

## Configuration Files Overview

### User Configs (`~/.config/meshforge/`)

| File | Purpose |
|------|---------|
| `gateway.json` | Gateway bridge settings (Meshtastic ↔ RNS) |
| `settings.json` | UI preferences (auto-created) |

### System Configs (`/etc/meshtasticd/`)

| File | Purpose |
|------|---------|
| `config.yaml` | Main meshtasticd config |
| `config.d/*.yaml` | Enabled hardware configs |
| `available.d/*.yaml` | Available hardware templates |

---

## Gateway Configurations

### `gateway-basic.json` - Simple Message Bridge
The minimal setup for bridging Meshtastic and RNS messages.

```
Meshtastic (localhost:4403) <---> MeshForge <---> Reticulum
```

**Use when:**
- You have meshtasticd running locally
- You want basic message bridging
- You're just getting started

### `gateway-mqtt.json` - MQTT-Enabled Gateway
Bridge messages while also publishing to an MQTT broker.

```
Meshtastic <---> MeshForge <---> RNS
                    |
                    v
                MQTT Broker
```

**Use when:**
- You want to monitor messages via MQTT
- You're integrating with Home Assistant or similar
- You need message logging/archival

### (removed) `gateway-rns-transport.json`

RNS over Meshtastic was removed on 2026-10-01: the transport never delivered packets to RNS. For RNS over LoRa, add an `RNodeInterface` to rnsd's config.

### USB radios — no example overlay, on purpose
meshtasticd (2.7.x) has no `Serial:` key, so no `config.d/` overlay can point
it at a USB-serial device; an overlay here would configure nothing. Two kinds:

- **CH341 USB LoRa sticks** (MeshToad, MeshStick, uMesh, Pinedio, PiggyStick):
  meshtasticd drives the radio over USB with a `Lora: … spidev: ch341`
  overlay. The meshtasticd package ships them in
  `/etc/meshtasticd/available.d/` (`lora-usb-*.yaml`, `lora-meshstick-1262.yaml`);
  the setup wizard offers exactly these.
- **Standalone Meshtastic nodes** (Heltec, T-Beam, RAK4631, Station G2): they
  run their own firmware; meshtasticd is not used for them. Reach them over
  serial (`meshtastic --port /dev/ttyACM0 ...`).

### `meshtasticd-spi-hat.yaml` - Raspberry Pi SPI HAT
For SPI-connected LoRa HATs on Raspberry Pi GPIO.

**Supported HATs:**
- MeshAdv-Pi-Hat (recommended)
- Waveshare SX1262
- Adafruit RFM9x

**Required setup:**
```bash
# Enable SPI on Raspberry Pi
sudo raspi-config nonint do_spi 0
sudo reboot
```

---

## Configuration Reference

### Gateway Settings

```json
{
  "enabled": true,              // Enable the gateway
  "auto_start": false,          // Start on launch
  "bridge_mode": "message_bridge", // message_bridge | mesh_bridge

  "meshtastic": {
    "host": "localhost",        // meshtasticd host
    "port": 4403,               // meshtasticd port
    "channel": 0,               // Channel to bridge (0 = primary)
    "use_mqtt": false,          // Also publish to MQTT
    "mqtt_topic": ""            // MQTT topic prefix
  },

  "rns": {
    "config_dir": "",           // RNS config (empty = default ~/.reticulum)
    "identity_name": "meshforge_gateway",
    "announce_interval": 300    // Announce every 5 minutes
  },

  "telemetry": {
    "share_position": true,     // Share GPS between networks
    "share_battery": true,      // Share battery status
    "share_environment": true   // Share temperature/humidity
  },

  "log_level": "INFO",          // DEBUG | INFO | WARNING | ERROR
  "ai_diagnostics_enabled": false // Enable AI-powered diagnostics
}
```

### Meshtasticd Hardware Settings

```yaml
Lora:
  CS: 21          # SPI Chip Select GPIO
  IRQ: 16         # Interrupt GPIO
  Busy: 20        # Busy signal GPIO
  Reset: 18       # Reset GPIO
  # Optional radio parameters
  # Bandwidth: 250      # kHz (125, 250, 500)
  # SpreadFactor: 11    # 7-12
  # TXpower: 20         # dBm (0-22 typical)

GPS:
  SerialPath: /dev/ttyS0   # GPS serial port

I2C:
  I2CDevice: /dev/i2c-1    # I2C bus for sensors

# Webserver / TCP / Logging are NOT hardware settings: they belong to
# /etc/meshtasticd/config.yaml. In a config.d/ overlay they OVERRIDE it —
# `Webserver: Port: 443` moves the API off :9443 (Issue #58).
```

---

## Troubleshooting

### Gateway won't connect to meshtasticd

1. Check meshtasticd is running:
   ```bash
   sudo systemctl status meshtasticd
   ```

2. Verify port is listening:
   ```bash
   ss -tlnp | grep 4403
   ```

3. Check logs:
   ```bash
   journalctl -u meshtasticd -f
   ```

### SPI HAT not detected

1. Verify SPI is enabled:
   ```bash
   ls /dev/spidev*
   # Should show: /dev/spidev0.0 /dev/spidev0.1
   ```

2. Check GPIO permissions:
   ```bash
   sudo python3 -c "import spidev; s=spidev.SpiDev(); s.open(0,0); print('SPI OK')"
   ```

3. Verify wiring matches your config GPIO pins

### RNS not connecting

1. Check rnsd is running:
   ```bash
   rnsd --version
   rnstatus
   ```

2. Verify RNS config exists:
   ```bash
   ls ~/.reticulum/config
   ```

---

## More Examples

See the full template library:
- Gateway templates: `src/gateway/templates/`
- Hardware templates: `templates/available.d/`
- Device profiles: `src/gateway/profiles/`
