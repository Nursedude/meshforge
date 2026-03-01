# MeshForge QTH Hardware Test Checklist

> **Date**: 2026-01-17
> **Updated**: 2026-02-27 — Rewritten for TUI (GTK removed in v0.5.x)
> **Tester**: WH6GXZ
> **Focus**: Messaging, TUI Reliability, Predictive Analytics

---

## Pre-Test Setup

### Environment Verification
- [ ] MeshForge version: `python3 -c "from src.__version__ import __version__; print(__version__)"` → 0.5.4-beta
- [ ] All tests passing: `python3 -m pytest tests/ -v --tb=short` → ~1,986 passed
- [ ] Branch: `git branch` → main (or feature branch)

### Services Running
- [ ] meshtasticd: `systemctl status meshtasticd` or port 4403 open
- [ ] rnsd: `systemctl status rnsd` or UDP port 37428 (check with `ss -ulnp | grep 37428`)
- [ ] MeshForge TUI launches: `sudo python3 src/launcher_tui/main.py`

---

## Test 1: TUI Reliability

**Goal**: Verify TUI launches and operates correctly

### 1.1 Status Consistency (Single Source of Truth)
- [ ] Launch TUI: `sudo python3 src/launcher_tui/main.py`
- [ ] Navigate to System/Service menu
- [ ] Check rnsd status displayed
- [ ] Navigate to RNS menu
- [ ] **VERIFY**: rnsd status consistent across menus
- [ ] Stop rnsd: `sudo systemctl stop rnsd`
- [ ] **VERIFY**: Status updates to "stopped" on next menu refresh
- [ ] Start rnsd: `sudo systemctl start rnsd`
- [ ] **VERIFY**: Status updates to "running"

**Pass Criteria**: Status always consistent via `service_check.py`

### 1.2 TUI Startup Performance
- [ ] Time startup: `time sudo python3 src/launcher_tui/main.py`
- [ ] **VERIFY**: Menu appears within 3 seconds
- [ ] **VERIFY**: No console errors/warnings during startup
- [ ] Navigate through 5+ submenus
- [ ] **VERIFY**: No freezing, no lag

**Pass Criteria**: <3s startup, smooth navigation

### 1.3 Clean Exit
- [ ] Launch TUI
- [ ] Navigate through several menus
- [ ] Exit via Back/Quit
- [ ] **VERIFY**: Clean shutdown (no errors in console)
- [ ] **VERIFY**: No orphan processes: `ps aux | grep meshforge`

**Pass Criteria**: Clean shutdown, no orphans

---

## Test 2: Messaging System (Sprint C)

**Goal**: Test message lifecycle tracking added today

### 2.1 Message Send (TUI)
- [ ] Navigate to Messaging menu in TUI
- [ ] Enter destination node ID
- [ ] Enter test message: "QTH test from WH6GXZ"
- [ ] Send message
- [ ] **RECORD**: Message ID displayed?
- [ ] **RECORD**: Status shown (queued/sent/delivered)?

### 2.2 Message Queue Database
- [ ] Check queue: `sqlite3 ~/.config/meshforge/message_queue.db "SELECT * FROM messages ORDER BY created_at DESC LIMIT 5"`
- [ ] **VERIFY**: Test message appears in database
- [ ] Check lifecycle: `sqlite3 ~/.config/meshforge/message_queue.db "SELECT * FROM message_lifecycle ORDER BY timestamp DESC LIMIT 10"`
- [ ] **VERIFY**: Lifecycle events recorded (CREATED, QUEUED, etc.)

### 2.3 Message Trace (New API)
```python
# Run in Python shell
from gateway.message_queue import PersistentMessageQueue
q = PersistentMessageQueue()
# Get recent message ID from test above
trace = q.get_message_trace("YOUR_MSG_ID")
print(f"States reached: {[e.state.value for e in trace]}")
summary = q.get_message_summary("YOUR_MSG_ID")
print(f"Summary: {summary}")
```
- [ ] **VERIFY**: Trace shows state progression
- [ ] **VERIFY**: Summary includes lifecycle info

**Pass Criteria**: Messages tracked through lifecycle

---

## Test 3: Predictive Analytics (Sprint B)

**Goal**: Test predictive health monitoring added today

### 3.1 Analytics Store
- [ ] Check analytics DB exists: `ls ~/.config/meshforge/analytics.db`
- [ ] Check tables: `sqlite3 ~/.config/meshforge/analytics.db ".tables"`
- [ ] **VERIFY**: Tables include `link_budget_history`, `network_health`

### 3.2 Predictive Analyzer
```python
# Run in Python shell
from utils.analytics import get_predictive_analyzer
analyzer = get_predictive_analyzer()
alerts = analyzer.analyze_all()
print(f"Alerts found: {len(alerts)}")
for a in alerts:
    print(f"  {a.severity}: {a.message}")
```
- [ ] **RECORD**: Number of alerts (may be 0 if insufficient data)
- [ ] **VERIFY**: No errors thrown

### 3.3 Network Forecast
```python
from utils.analytics import get_predictive_analyzer
analyzer = get_predictive_analyzer()
forecast = analyzer.get_network_forecast(hours_ahead=24)
print(forecast)
```
- [ ] **RECORD**: Forecast output (may say "insufficient data")
- [ ] **VERIFY**: No errors thrown

### 3.4 Health Dashboard (TUI)
- [ ] Launch TUI
- [ ] Navigate to Dashboard or Analytics menu
- [ ] **VERIFY**: Menu loads without error
- [ ] **VERIFY**: Shows service status for meshtasticd, rnsd
- [ ] **RECORD**: Forecast outlook displayed?

**Pass Criteria**: Analytics infrastructure works, dashboard displays

---

## Test 4: Hardware Detection

### 4.1 Meshtasticd (Primary)
- [ ] Device connected and powered
- [ ] meshtasticd running: `systemctl status meshtasticd`
- [ ] Port reachable: `nc -z localhost 4403 && echo "OK"`
- [ ] TUI Hardware menu shows device info

### 4.2 RAK WisBlock (If Available)
- [ ] Device connected
- [ ] Detected: `lsusb | grep -i rak` or `dmesg | tail -20`
- [ ] **RECORD**: USB VID:PID
- [ ] TUI Hardware menu detects?

### 4.3 Heltec LoRa (If Available)
- [ ] Device connected
- [ ] Detected: `lsusb | grep -i heltec` or CP2102 USB-serial
- [ ] **RECORD**: Serial port `/dev/ttyUSB*` or `/dev/ttyACM*`
- [ ] TUI Hardware menu detects?

### 4.4 Sensors
| Sensor | Detection Command | MeshForge Sees? |
|--------|------------------|-----------------|
| GPS | `ls /dev/ttyUSB* /dev/ttyACM*` | [ ] Yes [ ] No |
| SDR (RTL) | `lsusb \| grep RTL` | [ ] Yes [ ] No |
| WiFi | `iwconfig 2>/dev/null \| grep -v "no wireless"` | [ ] Yes [ ] No |
| LoRa 915 | meshtasticd radio info | [ ] Yes [ ] No |

---

## Test 5: RNS-Meshtastic Gateway

### 5.1 Gateway Status
- [ ] Gateway bridge running? Check logs or status command
- [ ] RNS connected? `rnstatus` shows local identity
- [ ] Meshtastic connected? TUI shows node count

### 5.2 Cross-Network Message (If Both Networks Available)
- [ ] Send message from Meshtastic to RNS destination
- [ ] **RECORD**: Delivery status
- [ ] **RECORD**: Time to deliver (or timeout)

---

## Test Results Summary

### Date/Time: _______________
### Tester: WH6GXZ

| Test Area | Pass | Fail | Notes |
|-----------|------|------|-------|
| TUI Status Consistency | [ ] | [ ] | |
| TUI Startup Performance | [ ] | [ ] | Time: ___s |
| TUI Clean Exit | [ ] | [ ] | |
| Message Send | [ ] | [ ] | |
| Message Lifecycle DB | [ ] | [ ] | |
| Message Trace API | [ ] | [ ] | |
| Predictive Analyzer | [ ] | [ ] | Alerts: ___ |
| Network Forecast | [ ] | [ ] | |
| Health Dashboard | [ ] | [ ] | |
| meshtasticd Detection | [ ] | [ ] | |
| RAK Detection | [ ] | [ ] | N/A if not testing |
| Heltec Detection | [ ] | [ ] | N/A if not testing |
| Sensors | [ ] | [ ] | List working: |
| Gateway | [ ] | [ ] | |

### Issues Found
1.
2.
3.

### Notes for Next Session

---

## Quick Reference

### Start MeshForge
```bash
cd /home/user/meshforge
sudo python3 src/launcher_tui/main.py
```

### Run Tests
```bash
python3 -m pytest tests/ -v --tb=short
```

### Check Service Status
```bash
# Using centralized checker (SINGLE SOURCE OF TRUTH)
python3 -c "from utils.service_check import check_service; print(check_service('rnsd'))"
python3 -c "from utils.service_check import check_service; print(check_service('meshtasticd'))"
```

### Message Queue Queries
```bash
# Recent messages
sqlite3 ~/.config/meshforge/message_queue.db "SELECT id, destination, status, created_at FROM messages ORDER BY created_at DESC LIMIT 10"

# Lifecycle events
sqlite3 ~/.config/meshforge/message_queue.db "SELECT message_id, state, timestamp FROM message_lifecycle ORDER BY timestamp DESC LIMIT 20"
```

---

*Checklist prepared: 2026-01-17*
*For: QTH hardware testing session*
