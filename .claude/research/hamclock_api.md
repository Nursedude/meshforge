# HamClock REST API Reference

> Documentation for MeshForge HamClock integration

Reference: [HamClock Official Site](https://www.clearskyinstitute.com/ham/HamClock/) - See FAQ #42 for full API docs

---

## Overview

HamClock provides a RESTful command interface for remote control and querying over a network. Commands can be sent via curl, wget, or any HTTP client.

### Ports

| Port | Purpose |
|------|---------|
| **8080** | REST API (commands and queries) |
| **8081** | Live web view (live.html) |

### Base URL Format

```
http://<hamclock-host>:8080/<command>
```

---

## Query Endpoints (GET)

### System Information

| Endpoint | Description | Returns |
|----------|-------------|---------|
| `get_sys.txt` | System information and health check | Version, uptime, DE/DX info |
| `get_de.txt` | Home (DE) location | Callsign, grid, lat/lon, time |
| `get_dx.txt` | Target (DX) location | Callsign, grid, lat/lon, time |
| `get_config.txt` | Current configuration | Various settings |

### Space Weather

| Endpoint | Description | Returns |
|----------|-------------|---------|
| `get_spacewx.txt` | Space weather conditions | SFI, Kp, A-index, X-ray flux |
| `get_voacap.txt` | VOACAP propagation data | Band predictions |
| `get_bc.txt` | Band conditions | Current HF conditions |

### Satellites & Tracking

| Endpoint | Description | Returns |
|----------|-------------|---------|
| `get_satellite.txt` | Current satellite info | Name, position, next pass |
| `get_satlist.txt` | Available satellites | List of tracked sats |

### DX Cluster

| Endpoint | Description | Returns |
|----------|-------------|---------|
| `get_dxspots.txt` | Recent DX spots | Callsign, freq, time |

---

## Command Endpoints (GET with params)

### Location Commands

| Command | Parameters | Description |
|---------|------------|-------------|
| `set_newdx` | `?call=XX0XX` or `?grid=AA00` | Set DX target by callsign or grid |
| `set_newde` | `?call=XX0XX` or `?grid=AA00` | Set DE location |

### Display Commands

| Command | Parameters | Description |
|---------|------------|-------------|
| `set_title` | `?msg=text` | Set title message |
| `set_mapcolor` | `?color=value` | Change map color scheme |
| `set_screenlock` | `?lock=1` or `?lock=0` | Lock/unlock screen |
| `set_touch` | `?x=N&y=N` | Simulate touch at coordinates |

### Satellite Commands

| Command | Parameters | Description |
|---------|------------|-------------|
| `set_sattle` | `?name=ISS&t1=...&t2=...` | Set satellite TLE |
| `set_satname` | `?name=ISS` | Select satellite by name |

### Image Commands

| Command | Parameters | Description |
|---------|------------|-------------|
| `set_bmp` | `?file=path` | Load BMP into pane |

---

## Response Format

Most `get_*.txt` endpoints return key=value pairs:

```
Key1=Value1
Key2=Value2
...
```

### Example: get_sys.txt

```
Version=4.21
Uptime=12345
DE_call=WH6GXZ
DE_grid=BL10
DE_lat=21.31
DE_lng=-157.86
DX_call=JA1XXX
DX_grid=PM95
```

### Example: get_spacewx.txt

```
SFI=156
Kp=2
A=8
XRay=B5.2
SSN=112
```

---

## MeshForge Integration

### Implemented Methods

**`_parse_sys(data)`** - Parse get_sys.txt response
- Extracts: Version, DE/DX info, grid squares

**`_parse_spacewx(data)`** - Parse get_spacewx.txt response
- Extracts: SFI, Kp, A-index, X-ray flux, sunspot number

**`_fetch_space_weather()`** - Fetch and display weather data
- Calls: get_sys.txt, get_spacewx.txt
- Updates: UI stat labels

### Connection Flow

```
1. User enters HamClock URL (e.g., http://hamclock.local)
2. _on_connect() saves settings, calls check_connection()
3. check_connection() hits get_sys.txt to verify connectivity
4. On success: Load live.html in WebKit, fetch space weather
5. Auto-refresh periodically with _fetch_space_weather()
```

### Error Handling

| Error | Cause | Action |
|-------|-------|--------|
| `Name or service not known` | DNS resolution failed | Check hostname |
| `Connection refused` | HamClock not running | Start HamClock service |
| `Timeout` | Network issue | Check connectivity |

---

## cURL Examples

```bash
# Get system info
curl http://hamclock.local:8080/get_sys.txt

# Get space weather
curl http://hamclock.local:8080/get_spacewx.txt

# Set DX to grid square
curl 'http://hamclock.local:8080/set_newdx?grid=JO62'

# Set DX to callsign
curl 'http://hamclock.local:8080/set_newdx?call=DL1ABC'

# Get VOACAP propagation
curl http://hamclock.local:8080/get_voacap.txt
```

---

## Band Conditions Interpretation

The Kp index indicates geomagnetic activity:

| Kp Value | Condition | HF Impact |
|----------|-----------|-----------|
| 0-2 | Quiet | Good propagation |
| 3-4 | Unsettled | Moderate |
| 5-6 | Active | Degraded |
| 7-9 | Storm | Poor/blackout |

SFI (Solar Flux Index) above 100 generally indicates good HF conditions.

---

## References

- [HamClock Official](https://www.clearskyinstitute.com/ham/HamClock/)
- [HamClock User Guide (PDF)](https://www.clearskyinstitute.com/ham/HamClock/HamClockKey.pdf)
- [GitHub Fork - SmittyHalibut](https://github.com/SmittyHalibut/HamClock)
