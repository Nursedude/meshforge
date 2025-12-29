# Meshtasticd Configuration Templates

This directory contains configuration templates for various LoRa HATs and devices supported by meshtasticd.

## Directory Structure

```
/etc/meshtasticd/
├── config.yaml          # Main configuration file
├── available.d/         # Available device configurations
│   ├── meshadv-mini.yaml
│   ├── waveshare-sx1262.yaml
│   └── ...
└── config.d/            # Enabled configurations (symlinks)
    └── meshadv-mini.yaml -> ../available.d/meshadv-mini.yaml
```

## Supported Devices

### MeshAdv-Mini
- **File**: `available.d/meshadv-mini.yaml`
- **Radio**: SX1262 (900 MHz) or SX1268 (400 MHz)
- **Features**: GPS, Temperature Sensor, PWM Fan, I2C/Qwiic
- **GPIO Pins**:
  - CS: GPIO 8
  - IRQ: GPIO 16
  - Busy: GPIO 20
  - Reset: GPIO 24
  - RXen: GPIO 12
- **GPS Serial**: /dev/ttyS0 or /dev/ttyAMA0

### Waveshare SX1262
- **File**: `available.d/waveshare-sx1262.yaml`
- **Radio**: SX1262
- **GPIO Pins**:
  - CS: GPIO 21
  - IRQ: GPIO 16
  - Busy: GPIO 20
  - Reset: GPIO 18

### Adafruit RFM9x
- **File**: `available.d/adafruit-rfm9x.yaml`
- **Radio**: SX1276
- **GPIO Pins**:
  - CS: GPIO 7
  - IRQ: GPIO 25
  - Reset: GPIO 17

## Installation

### Using the Interactive Installer

```bash
sudo meshtasticd-installer --configure
# Select option 7: SPI HAT Configuration
```

### Manual Installation

1. Copy the appropriate template to `/etc/meshtasticd/available.d/`:
   ```bash
   sudo cp templates/available.d/meshadv-mini.yaml /etc/meshtasticd/available.d/
   ```

2. Enable by creating a symlink in `/etc/meshtasticd/config.d/`:
   ```bash
   sudo ln -s ../available.d/meshadv-mini.yaml /etc/meshtasticd/config.d/meshadv-mini.yaml
   ```

3. Copy the main config template (or use the generated one):
   ```bash
   sudo cp templates/config.yaml /etc/meshtasticd/config.yaml
   ```

4. Restart meshtasticd:
   ```bash
   sudo systemctl restart meshtasticd
   ```

## Configuration Options

### LoRa Settings

| Option | Description | Default |
|--------|-------------|---------|
| CS | SPI Chip Select GPIO | Device-specific |
| IRQ | Interrupt Request GPIO | Device-specific |
| Busy | Busy signal GPIO (SX126x) | Device-specific |
| Reset | Reset GPIO | Device-specific |
| RXen | RX Enable GPIO | Optional |
| DIO2_AS_RF_SWITCH | Use DIO2 for RF switch | true (SX126x) |
| DIO3_TCXO_VOLTAGE | Enable TCXO voltage control | Device-specific |
| TXpower | Transmit power (dBm) | 22 |
| Bandwidth | LoRa bandwidth (kHz) | 250 |
| SpreadFactor | Spreading factor (7-12) | 11 |
| CodingRate | Coding rate (5-8) | 8 |

### GPS Settings

| Option | Description | Example |
|--------|-------------|---------|
| SerialPath | GPS serial port | /dev/ttyS0 |
| GPSEnableGpio | GPS enable GPIO | 4 |

### Web Server

| Option | Description | Default |
|--------|-------------|---------|
| Port | Web server port | 443 |
| RootPath | Web UI root path | /usr/share/meshtasticd/web |
