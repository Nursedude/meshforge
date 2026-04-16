# RNS Configuration Templates

User-editable templates for Reticulum Network Stack configuration.

**Do NOT overwrite existing configs** - these are starting points only.
Copy to your config location and edit as needed.

## Quick Start

1. Choose a template based on your role:
   - **Server**: `hawaiinet_server.conf` - Hosts the network
   - **Client**: `hawaiinet_client.conf` - Connects to a server
   - **RNode only**: `basic_rnode.conf` - Simple LoRa setup
   - **Meshtastic bridge**: `meshtastic_bridge.conf` - RNS + Meshtastic

2. Copy to your Reticulum config directory:
   ```bash
   # System-wide (preferred, used by rnsd service)
   sudo cp hawaiinet_server.conf /etc/reticulum/config

   # Or user-only
   cp hawaiinet_server.conf ~/.reticulum/config
   ```

3. Edit with your settings:
   ```bash
   nano /etc/reticulum/config
   ```

4. Start Reticulum:
   ```bash
   sudo systemctl restart rnsd
   # or: rnsd
   ```

## Templates

| Template | Use Case |
|----------|----------|
| `hawaiinet_server.conf` | Primary gateway with TCP server + RNode |
| `hawaiinet_client.conf` | Node connecting to HawaiiNet server |
| `basic_rnode.conf` | Simple RNode-only setup |
| `meshtastic_bridge.conf` | Bridge between RNS and Meshtastic |

## All RNS Interface Types

### AutoInterface (zero-config LAN discovery)
```
[[Default Interface]]
    type = AutoInterface
    enabled = Yes
    # devices = wlan0,eth0
    # ignored_devices = tun0
    # group_id = mynet
    # discovery_scope = link
```

### Meshtastic_Interface (RNS over Meshtastic LoRa)
Requires plugin: https://github.com/landandair/RNS_Over_Meshtastic
```
[[Meshtastic Interface]]
    type = Meshtastic_Interface
    enabled = true
    mode = gateway
    tcp_port = 127.0.0.1:4403
    data_speed = 0
    hop_limit = 3
```
Connection options: `port` (USB serial), `ble_port` (Bluetooth LE), `tcp_port` (meshtasticd)

### TCPServerInterface (host entry point)
```
[[My Server]]
    type = TCPServerInterface
    enabled = yes
    listen_ip = 0.0.0.0
    listen_port = 4242
```

### TCPClientInterface (connect to remote)
```
[[Remote Server]]
    type = TCPClientInterface
    enabled = yes
    target_host = 192.168.1.100
    target_port = 4242
```

### BackboneInterface (high-performance, Linux only)
```
[[Backbone]]
    type = BackboneInterface
    enabled = yes
    listen_ip = 0.0.0.0
    listen_port = 4242
```

### RNodeInterface (direct LoRa)
```
[[My RNode]]
    type = RNodeInterface
    interface_enabled = True
    port = /dev/ttyACM0
    frequency = 903625000
    txpower = 22
    bandwidth = 250000
    spreadingfactor = 7
    codingrate = 5
```

### SerialInterface (raw serial link)
```
[[Serial Link]]
    type = SerialInterface
    enabled = yes
    port = /dev/ttyUSB0
    speed = 115200
    databits = 8
    parity = none
    stopbits = 1
```

### KISSInterface (packet radio TNC)
```
[[Packet Radio]]
    type = KISSInterface
    enabled = yes
    port = /dev/ttyUSB1
    speed = 9600
```

### UDPInterface (broadcast over IP)
```
[[UDP Broadcast]]
    type = UDPInterface
    enabled = yes
    listen_ip = 0.0.0.0
    listen_port = 4966
    forward_ip = 255.255.255.255
    forward_port = 4966
```

### I2PInterface (anonymous overlay)
```
[[I2P]]
    type = I2PInterface
    enabled = yes
    peers = destination.b32.i2p
```

## Public RNS Community Nodes

| Host | Port | Region |
|------|------|--------|
| dublin.connect.reticulum.network | 4965 | Ireland (official testnet) |
| reticulum.betweentheborders.com | 4242 | USA |
| rns.acehoss.net | 4242 | USA |
| sydney.reticulum.au | 4242 | Australia |

## US Frequency Slots (902-928 MHz)

| Slot | Frequency (MHz) | Notes |
|------|-----------------|-------|
| 0 | 903.875 | Default |
| 2 | 906.125 | |
| 6 | 915.125 | |
| 8 | 919.625 | |
| 12 | 903.625 | HawaiiNet |

## Modulation Presets

| Preset | SF | BW (kHz) | CR | Use Case |
|--------|----|---------|----|----------|
| LONG_FAST | 7 | 250 | 4/5 | Best range/speed balance |
| LONG_MODERATE | 8 | 125 | 4/5 | Better range, slower |
| LONG_SLOW | 11 | 125 | 4/8 | Maximum range |
| MEDIUM_FAST | 7 | 500 | 4/5 | Higher speed, less range |
| SHORT_TURBO | 6 | 500 | 4/5 | Maximum speed |

## Troubleshooting

### RNode not detected
```bash
ls -la /dev/ttyACM* /dev/ttyUSB*
sudo usermod -a -G dialout $USER
# logout and login
```

### Can't connect to server
```bash
nc -zv 192.0.2.38 4242
sudo ufw allow 4242/tcp
```

### No LoRa traffic
- Verify both nodes use same frequency, SF, BW, CR
- Check antenna connections
- Reduce TX power if very close (< 10m)

## See Also

- [Reticulum Manual](https://reticulum.network/manual/)
- [RNS Interfaces](https://reticulum.network/manual/interfaces.html)
- [NomadNet](https://github.com/markqvist/nomadnet) - Terminal mesh messenger
- [RNS Over Meshtastic](https://github.com/landandair/RNS_Over_Meshtastic) - Meshtastic plugin
- [MeshForge](https://github.com/Nursedude/meshforge) - NOC for mesh networks
