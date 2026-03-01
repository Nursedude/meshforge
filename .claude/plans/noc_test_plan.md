# MeshForge NOC Test Plan

> **Operator**: WH6GXZ (Nursedude)
> **Lab**: 8+ Raspberry Pis, 2x Mikrotik routers, cloud DDNS
> **Created**: 2026-01-19
> **Updated**: 2026-02-27 — Aligned with TUI-only interface (GTK removed in v0.5.x)
> **Branch**: `main` (v0.5.4-beta)

---

## Lab Infrastructure

```
                    ┌─────────────────────────────────┐
                    │      CLOUD (DDNS)               │
                    │   wh6gxzhub.ddns.net            │
                    │   └─ AllMon3 (AllStar)          │
                    │   └─ Future: MeshForge Web UI   │
                    └─────────────┬───────────────────┘
                                  │
                    ┌─────────────┴───────────────────┐
                    │        MIKROTIK ROUTERS         │
                    │       (Network backbone)        │
                    └─────────────┬───────────────────┘
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        │                         │                         │
   ┌────┴────┐              ┌────┴────┐              ┌────┴────┐
   │  Pi #1  │              │  Pi #2  │              │  Pi #3  │
   │ CONTROL │              │ TEST-A  │              │ TEST-B  │
   │(current)│              │(fresh)  │              │(stress) │
   └────┬────┘              └────┬────┘              └────┬────┘
        │                        │                        │
        └────────────────────────┴────────────────────────┘
                         MESH NETWORK
                    (Meshtastic + Reticulum)
```

---

## Test Matrix

| Pi | Role | Config | Radio | Purpose |
|----|------|--------|-------|---------|
| #1 | CONTROL | Current (don't touch) | Yes | Known-working reference |
| #2 | TEST-A | Fresh NOC install | Yes | Primary test node |
| #3 | TEST-B | Fresh NOC install | Yes | Stress testing |
| #4 | REMOTE | Client-only mode | No | Remote admin testing |
| #5 | EDGE | No meshtasticd | Yes | Edge case testing |
| #6-8 | SPARE | Clean OS | Varies | Backup / special tests |

---

## Phase 1: Fresh NOC Install (Pi #2)

### Prerequisites
- [ ] Fresh Raspberry Pi OS (64-bit recommended)
- [ ] Network connectivity
- [ ] USB radio attached (T-Beam, RAK, etc.)

### Installation Steps

```bash
# 1. Update system
sudo apt update && sudo apt upgrade -y

# 2. Clone and run NOC installer
git clone https://github.com/Nursedude/meshforge.git /tmp/meshforge
cd /tmp/meshforge
git checkout main
sudo bash scripts/install_noc.sh
```

### Verification Checklist

- [ ] **Installation completes without errors**
- [ ] **Services installed:**
  ```bash
  systemctl list-unit-files | grep -E "meshtasticd|rnsd|meshforge"
  # Expected: meshtasticd.service, rnsd.service, meshforge.service
  ```
- [ ] **Services running:**
  ```bash
  sudo meshforge-noc --status
  # Expected: All green checkmarks
  ```
- [ ] **Radio detected:**
  ```bash
  ls /dev/ttyUSB* /dev/ttyACM*
  # Expected: Device listed
  ```
- [ ] **MeshForge launches:**
  ```bash
  sudo python3 src/launcher_tui/main.py
  # Expected: TUI menu appears (whiptail/dialog)
  ```
- [ ] **Sees mesh network:**
  - Navigate TUI menus
  - Check node count > 0
  - Pi #1 (CONTROL) visible in node list

### Record Results

| Test | Pass/Fail | Notes |
|------|-----------|-------|
| Install completes | | |
| meshtasticd installed | | |
| rnsd installed | | |
| Services start | | |
| Radio detected | | |
| MeshForge launches | | |
| Sees mesh nodes | | |
| Sees Pi #1 | | |

---

## Phase 2: Service Recovery Testing (Pi #2)

### Test: meshtasticd Crash Recovery

```bash
# Terminal 1: Watch orchestrator
sudo journalctl -fu meshforge

# Terminal 2: Kill meshtasticd
sudo systemctl stop meshtasticd
# Wait 30 seconds (health check interval)
# Verify: Orchestrator should restart meshtasticd automatically
```

**Expected**: Orchestrator detects failure, restarts service within 60 seconds.

### Test: rnsd Crash Recovery

```bash
sudo systemctl stop rnsd
# Wait for recovery
```

**Expected**: Same auto-recovery behavior.

### Test: Double Kill (Both Services)

```bash
sudo systemctl stop meshtasticd rnsd
# Watch recovery sequence
```

**Expected**: Orchestrator restarts in correct order (meshtasticd first, then rnsd).

### Record Results

| Test | Recovery Time | Notes |
|------|--------------|-------|
| meshtasticd crash | seconds | |
| rnsd crash | seconds | |
| Both crash | seconds | |

---

## Phase 3: Comparison Test (Pi #1 vs Pi #2)

Run these tests on BOTH nodes and compare:

### Startup Time

```bash
# On each Pi, time the startup
time sudo meshforge --no-services  # Skip service start for fair comparison
```

### Node Discovery

```bash
# Count nodes seen by each
sudo meshforge-cli --nodes | wc -l
```

### Memory Usage

```bash
# Check memory footprint
ps aux | grep -E "meshforge|meshtasticd|rnsd" | awk '{sum += $6} END {print sum/1024 " MB"}'
```

### Connection Stability (1 hour test)

```bash
# Run on each Pi, check every 5 minutes
for i in {1..12}; do
    echo "=== Check $i at $(date) ==="
    sudo meshforge-noc --status
    sleep 300
done
```

### Record Comparison

| Metric | Pi #1 (Control) | Pi #2 (NOC) | Notes |
|--------|-----------------|-------------|-------|
| Startup time | | | |
| Node count | | | |
| Memory usage | | | |
| Connection drops (1hr) | | | |

---

## Phase 4: Stress Testing (Pi #3)

### Chaos Test Script

```bash
#!/bin/bash
# chaos_test.sh - Run for extended period

LOG=/var/log/meshforge_chaos.log

echo "Starting chaos test at $(date)" >> $LOG

while true; do
    # Random delay 30-90 seconds
    DELAY=$((RANDOM % 60 + 30))
    sleep $DELAY

    # Pick random action
    ACTION=$((RANDOM % 3))

    case $ACTION in
        0)
            echo "$(date): Killing meshtasticd" >> $LOG
            sudo systemctl stop meshtasticd
            ;;
        1)
            echo "$(date): Killing rnsd" >> $LOG
            sudo systemctl stop rnsd
            ;;
        2)
            echo "$(date): Killing both" >> $LOG
            sudo systemctl stop meshtasticd rnsd
            ;;
    esac

    # Wait for recovery
    sleep 60

    # Check health
    sudo meshforge-noc --status >> $LOG 2>&1
done
```

Run for 4-8 hours, then analyze log for:
- Recovery success rate
- Average recovery time
- Any permanent failures

---

## Phase 5: Edge Cases (Pi #4, #5)

### Test: Client-Only Mode (Pi #4 - no local radio)

```bash
# Install in client mode
sudo bash scripts/install_noc.sh --client-only

# Configure to connect to Pi #2's meshtasticd
# Edit /etc/meshforge/noc.yaml:
#   mode: client
#   remote:
#     meshtasticd_host: 192.168.x.x  # Pi #2's IP
#     meshtasticd_port: 4403
```

**Verify**: Can see mesh through remote connection.

### Test: Existing meshtasticd (Pi #5)

```bash
# Pre-install meshtasticd manually
pip3 install meshtastic
# Create systemd service manually
# Start it

# Then run NOC installer
sudo bash scripts/install_noc.sh
# Select "Take ownership" when prompted
```

**Verify**: MeshForge takes over existing service.

---

## Success Criteria

### Must Pass (Blockers)
- [ ] Fresh install completes without manual intervention
- [ ] All services start automatically
- [ ] Services recover from crashes
- [ ] Sees mesh network and nodes
- [ ] No regression vs Pi #1 (Control)

### Should Pass (Important)
- [ ] Startup time < 30 seconds
- [ ] Memory usage < 200MB total
- [ ] Zero connection drops in 1-hour test
- [ ] Recovery time < 60 seconds

### Nice to Have
- [ ] Client-only mode works
- [ ] Migration from existing meshtasticd smooth
- [ ] Survives 8-hour chaos test

---

## Post-Test Actions

### If All Pass:
1. Merge branch to main
2. Update main install.sh to use NOC installer
3. Migrate Pi #1 (Control) to NOC
4. Document in release notes

### If Issues Found:
1. Document issue in persistent_issues.md
2. Fix on test Pis
3. Re-run failed tests
4. Keep Pi #1 untouched until resolved

---

## Future Integration Notes

### AllStar / AllMon3
- URL: http://wh6gxzhub.ddns.net/allmon3/
- Potential: Voice announcements for mesh events
- Potential: Text-to-speech for emergency alerts

### Cloud Dashboard
- DDNS: wh6gxzhub.ddns.net
- Potential: Remote MeshForge Web UI
- Potential: Multi-node status aggregation

### Mikrotik Integration
- 2x routers in lab
- Potential: SNMP monitoring from MeshForge
- Potential: VPN tunnel for remote mesh access

---

*73 de WH6GXZ - Made with aloha*
