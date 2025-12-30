# Usage Guide

## Installation

### Quick Start

```bash
git clone https://github.com/Nursedude/Meshtasticd_interactive_UI.git
cd Meshtasticd_interactive_UI
sudo python3 -m pip install -r requirements.txt
sudo python3 src/main.py
```

### Interactive Mode

The default mode is interactive, providing a menu-driven interface:

```bash
sudo python3 src/main.py
```

You'll be presented with options to:
1. Install meshtasticd
2. Update meshtasticd
3. Configure device
4. Check dependencies
5. Hardware detection
6. Debug & troubleshooting

### Command Line Mode

For automation or scripting, use command line options:

#### Install Stable Version
```bash
sudo python3 src/main.py --install stable
```

#### Install Beta Version
```bash
sudo python3 src/main.py --install beta
```

#### Update Existing Installation
```bash
sudo python3 src/main.py --update
```

#### Configure Device
```bash
sudo python3 src/main.py --configure
```

#### Check Dependencies
```bash
sudo python3 src/main.py --check
```

#### Debug Mode
```bash
sudo python3 src/main.py --debug
```

## Configuration

### LoRa Configuration

The tool provides interactive LoRa configuration including:

- **Region Selection**: Choose your regulatory region (US, EU, etc.)
- **Advanced Settings**: Bandwidth, spreading factor, coding rate, transmit power
- **Presets**: Quick configuration for common use cases

### Device Configuration

Configure your Meshtastic device:

- **Device Name**: Set a friendly name for your node
- **WiFi**: Configure WiFi credentials (if supported)
- **Modules**: Enable/configure MQTT, Serial, Telemetry, etc.

### Hardware Detection

The tool can detect:

- USB LoRa modules
- SPI LoRa HATs
- Raspberry Pi model
- Available serial ports

## Troubleshooting

### Permission Issues

If you encounter permission errors:

```bash
sudo python3 scripts/setup_permissions.sh
```

Then log out and back in for group changes to take effect.

### Service Issues

Check meshtasticd service status:

```bash
systemctl status meshtasticd
```

View logs:

```bash
journalctl -u meshtasticd -f
```

### SPI Not Working

Ensure SPI is enabled:

```bash
# Check config.txt
cat /boot/config.txt | grep spi
# or for newer systems
cat /boot/firmware/config.txt | grep spi
```

You should see `dtparam=spi=on`. If not, the installer should have added it, but a reboot may be required.

### Connection Issues

If you can't connect to your device:

1. **Check hardware**: Ensure LoRa module is properly connected
2. **Check device**: `ls /dev/ttyUSB*` or `ls /dev/ttyACM*`
3. **Check permissions**: User must be in `dialout` group
4. **Check service**: `systemctl status meshtasticd`

## Advanced Usage

### Beta Versions

To install beta versions, use:

```bash
sudo python3 src/main.py --install beta
```

Beta packages are available from the openSUSE Build Service repository.

### Custom Configuration

For advanced users, meshtasticd can be configured directly:

```bash
meshtasticd --help
```

Refer to the official documentation: https://meshtastic.org/docs/software/linux/usage/

### Python API

You can also use the meshtastic Python library directly:

```python
import meshtastic
from meshtastic.serial_interface import SerialInterface

# Connect to device
interface = SerialInterface()

# Get node info
node = interface.getNode('^local')

# Send a message
interface.sendText("Hello Mesh!")
```

## Resources

- [Official Meshtastic Documentation](https://meshtastic.org/docs/)
- [Meshtastic Python Library](https://github.com/meshtastic/python)
- [LoRa Configuration](https://meshtastic.org/docs/configuration/radio/lora/)
- [Module Configuration](https://meshtastic.org/docs/configuration/module/)
- [Linux Usage Guide](https://meshtastic.org/docs/software/linux/usage/)
