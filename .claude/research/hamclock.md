# HamClock Integration Notes

## Web Interface URLs

- **Live View**: `http://<pi-ip>:8081/live.html`
- **REST API**: `http://<pi-ip>:8082/`

## Installation on Headless Pi (arm64)

The pre-built framebuffer packages require `libbcm_host.so` which is unavailable on 64-bit Pi OS. Build from source instead:

```bash
# Install dependencies
sudo apt install -y build-essential libx11-dev

# Download and build web-only version
cd /tmp
wget https://www.clearskyinstitute.com/ham/HamClock/ESPHamClock.zip
unzip ESPHamClock.zip
cd ESPHamClock
make -j4 hamclock-web-1600x960
sudo cp hamclock-web-1600x960 /usr/local/bin/
```

## Systemd Service

```ini
[Unit]
Description=HamClock Web Server
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/hamclock-web-1600x960
Restart=on-failure
User=<your-username>
Environment=HOME=/home/<your-username>

[Install]
WantedBy=multi-user.target
```

**Important:** HamClock needs a valid home directory with write access to `~/.hamclock/` for config storage.

## Common Issues

### Permission denied on ~/.hamclock
```bash
sudo chown -R $USER:$USER ~/.hamclock
chmod -R 755 ~/.hamclock
```

### "basic_string: construction from null" error
Config file corrupted. Remove and let HamClock regenerate:
```bash
rm -f ~/.hamclock/eeprom
```

### libbcm_host.so missing
Use web-only build (see Installation above) or install:
```bash
sudo apt install libraspberrypi0  # may not be available on arm64
```

## MeshForge Integration

- Panel: `src/gtk_ui/panels/hamclock.py`
- Default ports: API=8080, Live=8081 (configurable in panel)
- "Open Web Setup" button opens `http://localhost:8081/live.html`
- REST API fetches solar flux, A/K index, propagation data

## Configuration via Web UI

Access `http://<pi-ip>:8081/live.html` to configure:
- Callsign and QTH (grid square)
- Satellite tracking
- DX cluster connections
- Display panes
- Time zone and units

Config stored in binary format at `~/.hamclock/eeprom`
