# AREDN Mesh Network Integration Research

> Research document for MeshForge AREDN integration
> Date: 2026-01-05

---

## Overview

AREDN (Amateur Radio Emergency Data Network) is firmware based on OpenWrt that enables 802.11 wireless mesh networks for licensed amateur radio operators (Technician Class or higher). It operates under FCC Part 97 allocations adjacent to unlicensed WiFi bands.

### Key Features
- Self-healing mesh network topology
- OLSR + Babel routing (transitioning to Babel-only)
- Device-to-Device (DtD) linking for cross-band routing
- Tunnel connections for internet bridging
- Service advertisement and discovery
- JSON API for node querying

---

## Supported MikroTik Hardware

| Device | Ports | Features | Notes |
|--------|-------|----------|-------|
| hAP ac lite | 5 | Dual-band, PoE | Entry level |
| hAP ac2 | 5 | Dual-band, PoE | Mid-range |
| **hAP ac3** | 5 Gigabit | Quad-core, 256MB RAM, USB | Recommended |
| mANTbox 12-2 | - | 1W output, 12dB gain | Long-range links |
| RBLHG-5HPnD-XL | - | High power | Backbone links |

### hAP ac3 Specifications (RBD53iG-5HacD2HnD)
- 5x Gigabit Ethernet ports
- 256MB RAM, quad-core CPU
- 128MB NAND + full-size USB
- PoE in/out support
- Dual-band 2.4/5GHz radios

---

## VLAN Configuration

### Standard AREDN Port Mapping

| VLAN | Purpose | Description |
|------|---------|-------------|
| Untagged | LAN | Laptops, IP cams, VoIP phones |
| VLAN 1 | WAN | Gateway to home network/internet |
| VLAN 2 | DtDLink | Device-to-device mesh routing |

### hAP ac3 Default Port Assignment

```
Port 1: WAN (vlan0/vlan1/vlan2)
Port 2-4: LAN (vlan0/vlan1)
Port 5: DtD (vlan2 only)
wlan1 (2.4GHz): AREDN Node
wlan2 (5.7GHz): Access Point
```

---

## AREDN API Reference

### Base Endpoint
```
http://<nodename>.local.mesh/a/sysinfo
```

Legacy endpoint (redirects):
```
http://<nodename>.local.mesh/cgi-bin/sysinfo.json
```

### Default Response Fields

```json
{
  "node_details": {
    "firmware_mfg": "AREDN",
    "firmware_version": "3.25.10.0",
    "model": "MikroTik hAP ac3",
    "board_id": "...",
    "description": "Node description"
  },
  "sysinfo": {
    "uptime": "1 day, 2:34:56",
    "loads": [0.5, 0.3, 0.2]
  },
  "meshrf": {
    "ssid": "AREDN-mesh",
    "channel": 177,
    "freq": "5885",
    "chanbw": "10",
    "status": "on"
  },
  "tunnels": {
    "active_tunnel_count": 2
  }
}
```

### Query Parameters

| Parameter | Description |
|-----------|-------------|
| `?hosts=1` | Include all mesh nodes and devices |
| `?services=1` | Include all mesh services |
| `?services_local=1` | Include local services only |
| `?link_info=1` | Include RF/DTD/TUN link details |
| `?lqm=1` | Include Link Quality Manager data |

### Link Info Response

```json
{
  "link_info": {
    "10.x.x.x": {
      "hostname": "KK6XXX-node",
      "linkType": "RF",
      "linkQuality": 0.95,
      "neighborLinkQuality": 0.92,
      "signal": -65,
      "noise": -95,
      "olsrInterface": "wlan0",
      "tx_rate": 130
    }
  }
}
```

---

## Firmware Installation (MikroTik)

### Requirements
- Computer with TFTP server (dnsmasq or Tiny PXE on Windows)
- Ethernet cable
- AREDN firmware files:
  - `.elf` file (RAM boot)
  - `.bin` file (flash update)

### Installation Steps

1. **Prepare Computer**
   - Install dnsmasq (Linux) or Tiny PXE (Windows)
   - Configure static IP: 192.168.1.10/24
   - Set TFTP root to firmware directory

2. **Connect Device**
   - Power off MikroTik
   - Connect Ethernet to Port 1
   - Hold reset button while powering on
   - Release after LEDs flash

3. **TFTP Boot**
   - Device requests .elf file via TFTP
   - Boots into RAM-only AREDN

4. **Flash Firmware**
   - SCP .bin file to /tmp/
   - Run sysupgrade command
   - Device reboots with AREDN

### Known Issue: hAP ac3 RouterOS v7

Newer hAP ac3 units ship with RouterOS v7 which has wireless interface issues. Requires custom AREDN build with decompression fix.

---

## MeshForge Integration Design

### AREDN Panel Features

1. **Node Discovery**
   - Scan local network for AREDN nodes
   - Parse sysinfo API responses
   - Build node inventory

2. **Network Visualization**
   - Display mesh topology
   - Show link quality metrics
   - Real-time signal/noise monitoring

3. **Service Browser**
   - List advertised services
   - Quick access links
   - Service health monitoring

4. **Node Configuration**
   - View/edit node settings
   - Firmware version tracking
   - Tunnel management

5. **MikroTik Router Setup Wizard**
   - Pre-flight checks
   - Firmware download
   - TFTP server integration
   - Installation guidance

### API Integration

```python
class AREDNNode:
    """AREDN mesh node interface"""

    def __init__(self, hostname: str):
        self.hostname = hostname
        self.base_url = f"http://{hostname}.local.mesh"

    def get_sysinfo(self, hosts=False, services=False, link_info=False):
        """Fetch node information via API"""
        params = []
        if hosts: params.append("hosts=1")
        if services: params.append("services=1")
        if link_info: params.append("link_info=1")

        url = f"{self.base_url}/a/sysinfo"
        if params:
            url += "?" + "&".join(params)

        response = requests.get(url, timeout=5)
        return response.json()

    def get_neighbors(self):
        """Get RF neighbors with link quality"""
        data = self.get_sysinfo(link_info=True)
        return data.get('link_info', {})
```

---

## Related Technologies

### Meshtastic + AREDN Integration
- AREDN provides IP backbone
- Meshtastic provides LoRa last-mile
- Gateway bridges both networks
- Unified mesh management in MeshForge

### RNS/Reticulum Bridge
- LXMF messaging over AREDN
- Store-and-forward capability
- Delay-tolerant networking

---

## References

- [AREDN Official Site](https://www.arednmesh.org/)
- [AREDN GitHub](https://github.com/aredn/aredn)
- [AREDN Documentation](https://docs.arednmesh.org/en/latest/)
- [MikroTik Tutorial](https://www.arednmesh.org/content/mikrotik-tutorial)
- [MikroTik Installation Guide](https://www.arednmesh.org/content/installation-instructions-mikrotik-devices)
- [Tools for Integrators](https://docs.arednmesh.org/en/latest/arednHow-toGuides/devtools.html)
- [MeshMap Project](https://github.com/kn6plv/NewMeshMap)
