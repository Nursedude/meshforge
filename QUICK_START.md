# Quick Start Guide

## Choose Your Installation Method

### ⚡ Fastest: One-Liner Install
```bash
curl -sSL https://raw.githubusercontent.com/Nursedude/Meshtasticd_interactive_UI/main/install.sh | sudo bash
```

### 🌐 Easiest: Web Interface
```bash
git clone https://github.com/Nursedude/Meshtasticd_interactive_UI.git
cd Meshtasticd_interactive_UI
sudo python3 web_installer.py
# Visit http://<your-pi-ip>:8080
```

### 🐳 Most Isolated: Docker
```bash
git clone https://github.com/Nursedude/Meshtasticd_interactive_UI.git
cd Meshtasticd_interactive_UI
docker-compose run meshtasticd-installer
```

## After Installation

Run the installer:
```bash
sudo meshtasticd-installer
```

Or use CLI options:
```bash
# Install stable version
sudo meshtasticd-installer --install stable

# Configure device
sudo meshtasticd-installer --configure

# Check system
sudo meshtasticd-installer --check
```

## What Gets Installed

- ✅ Meshtasticd daemon (LoRa mesh networking)
- ✅ Python dependencies (meshtastic, click, rich, etc.)
- ✅ System dependencies (Python 3, Git, etc.)
- ✅ Interactive configuration tools
- ✅ Hardware detection utilities

## Supported Hardware

### Raspberry Pi Models
- Pi Zero 2W, 3, 4, Pi 400, Pi 5

### USB LoRa Modules
- MeshToad (MtnMesh device, 1W)
- MeshTadpole
- MeshStick
- CH340/CH341-based modules
- CP2102-based modules
- FT232-based modules

### SPI LoRa HATs
- MeshAdv-Pi v1.1
- Adafruit RFM9x
- Elecrow LoRa RFM95
- Waveshare SX126X
- PiTx LoRa

## Need Help?

- 📖 [Full Documentation](README.md)
- 🔧 [Installation Options](INSTALL_OPTIONS.md)
- ✅ [Verification Guide](STANDALONE_VERIFICATION.md)
- 🐛 [Debug Guide](DEBUG_VALIDATION.md)
- 💬 [Report Issues](https://github.com/Nursedude/Meshtasticd_interactive_UI/issues)
