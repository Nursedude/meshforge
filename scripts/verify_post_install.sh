#!/bin/bash
#
# MeshForge Post-Install Verification Script
#
# Verifies that MeshForge installation is complete and functional.
# Run after install_noc.sh or anytime to check system health.
#
# Exit codes:
#   0 = All checks passed
#   1 = Critical failures (won't work)
#   2 = Warnings (may work but needs attention)
#
# Usage:
#   sudo bash scripts/verify_post_install.sh
#   sudo bash scripts/verify_post_install.sh --quiet   # Exit code only
#   sudo bash scripts/verify_post_install.sh --json    # Machine-readable output
#

set -e

# Colors (disabled in quiet/json mode)
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Parse arguments
QUIET=false
JSON=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --quiet|-q) QUIET=true; shift ;;
        --json|-j) JSON=true; QUIET=true; shift ;;
        *) shift ;;
    esac
done

# Tracking
CRITICAL_FAILS=0
WARNINGS=0
CHECKS_PASSED=0
RESULTS=()

# Helper functions
log() {
    if ! $QUIET; then
        echo -e "$1"
    fi
}

check_pass() {
    local name="$1"
    local detail="$2"
    CHECKS_PASSED=$((CHECKS_PASSED + 1))
    RESULTS+=("{\"check\":\"$name\",\"status\":\"pass\",\"detail\":\"$detail\"}")
    log "  ${GREEN}[PASS]${NC} $name"
    if [[ -n "$detail" ]]; then
        log "        ${CYAN}$detail${NC}"
    fi
}

check_fail() {
    local name="$1"
    local detail="$2"
    local fix="$3"
    CRITICAL_FAILS=$((CRITICAL_FAILS + 1))
    RESULTS+=("{\"check\":\"$name\",\"status\":\"fail\",\"detail\":\"$detail\",\"fix\":\"$fix\"}")
    log "  ${RED}[FAIL]${NC} $name"
    if [[ -n "$detail" ]]; then
        log "        ${RED}$detail${NC}"
    fi
    if [[ -n "$fix" ]]; then
        log "        ${YELLOW}Fix: $fix${NC}"
    fi
}

check_warn() {
    local name="$1"
    local detail="$2"
    local fix="$3"
    WARNINGS=$((WARNINGS + 1))
    RESULTS+=("{\"check\":\"$name\",\"status\":\"warn\",\"detail\":\"$detail\",\"fix\":\"$fix\"}")
    log "  ${YELLOW}[WARN]${NC} $name"
    if [[ -n "$detail" ]]; then
        log "        ${YELLOW}$detail${NC}"
    fi
    if [[ -n "$fix" ]]; then
        log "        ${CYAN}Fix: $fix${NC}"
    fi
}

check_skip() {
    local name="$1"
    local reason="$2"
    RESULTS+=("{\"check\":\"$name\",\"status\":\"skip\",\"detail\":\"$reason\"}")
    log "  ${CYAN}[SKIP]${NC} $name - $reason"
}

check_info() {
    local name="$1"
    local detail="$2"
    CHECKS_PASSED=$((CHECKS_PASSED + 1))
    RESULTS+=("{\"check\":\"$name\",\"status\":\"info\",\"detail\":\"$detail\"}")
    log "  ${CYAN}[INFO]${NC} $name"
    if [[ -n "$detail" ]]; then
        log "        ${CYAN}$detail${NC}"
    fi
}

# ─────────────────────────────────────────────────────────────────
# Header
# ─────────────────────────────────────────────────────────────────
if ! $QUIET; then
    echo ""
    echo -e "${CYAN}╔═══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║     MeshForge Post-Install Verification                   ║${NC}"
    echo -e "${CYAN}╚═══════════════════════════════════════════════════════════╝${NC}"
    echo ""
fi

# ─────────────────────────────────────────────────────────────────
# Section 1: MeshForge Installation
# ─────────────────────────────────────────────────────────────────
log "${BOLD}[1/6] MeshForge Installation${NC}"

# Check meshforge directory
if [[ -d "/opt/meshforge" ]]; then
    check_pass "MeshForge directory" "/opt/meshforge exists"
else
    check_fail "MeshForge directory" "/opt/meshforge not found" "Run: sudo bash scripts/install_noc.sh"
fi

# Check meshforge command
if command -v meshforge &>/dev/null; then
    check_pass "meshforge command" "$(which meshforge)"
else
    check_fail "meshforge command" "Not in PATH" "Check /usr/local/bin/meshforge exists"
fi

# Check venv
if [[ -f "/opt/meshforge/venv/bin/python" ]]; then
    check_pass "Python venv" "/opt/meshforge/venv/bin/python"
else
    check_warn "Python venv" "Venv not found" "Run: python3 -m venv /opt/meshforge/venv"
fi

log ""

# ─────────────────────────────────────────────────────────────────
# Section 2: meshtasticd Installation
# ─────────────────────────────────────────────────────────────────
log "${BOLD}[2/6] meshtasticd Installation${NC}"

MESHTASTICD_INSTALLED=false

# Check for native meshtasticd binary
if command -v meshtasticd &>/dev/null; then
    VERSION=$(meshtasticd --version 2>/dev/null || echo "unknown")
    check_pass "meshtasticd binary" "Version: $VERSION"
    MESHTASTICD_INSTALLED=true
else
    check_warn "meshtasticd binary" "Native daemon not found" "Install: sudo apt install meshtasticd (after adding repo)"
fi

# Check for Python meshtastic CLI (alternative for USB radios)
if command -v meshtastic &>/dev/null; then
    check_pass "meshtastic CLI" "Python CLI available"
elif ! $MESHTASTICD_INSTALLED; then
    check_warn "meshtastic CLI" "Neither native daemon nor Python CLI found" "Install: pip3 install meshtastic"
fi

# Check systemd service file
if [[ -f "/etc/systemd/system/meshtasticd.service" ]]; then
    check_pass "meshtasticd service file" "/etc/systemd/system/meshtasticd.service"
else
    check_warn "meshtasticd service file" "Service file not created" "Will be created when selecting radio type"
fi

log ""

# ─────────────────────────────────────────────────────────────────
# Section 3: meshtasticd Configuration
# ─────────────────────────────────────────────────────────────────
log "${BOLD}[3/6] meshtasticd Configuration${NC}"

CONFIG_DIR="/etc/meshtasticd"
CONFIG_YAML="$CONFIG_DIR/config.yaml"

# Check config directory
if [[ -d "$CONFIG_DIR" ]]; then
    check_pass "Config directory" "$CONFIG_DIR"
else
    check_fail "Config directory" "$CONFIG_DIR not found" "Create: sudo mkdir -p $CONFIG_DIR/{available.d,config.d}"
fi

# Check config.yaml exists
if [[ -f "$CONFIG_YAML" ]]; then
    check_pass "config.yaml exists" "$CONFIG_YAML"

    # Check for Webserver section (CRITICAL for web client)
    if grep -q "Webserver:" "$CONFIG_YAML" 2>/dev/null; then
        PORT=$(grep -A1 "Webserver:" "$CONFIG_YAML" | grep "Port:" | awk '{print $2}' || echo "9443")
        check_pass "Webserver section" "Port: ${PORT:-9443}"
    else
        check_fail "Webserver section" "Missing from config.yaml - web client won't work" \
            "Add: Webserver:\\n  Port: 9443\\n  RootPath: /usr/share/meshtasticd/web"
    fi

    # Check for Lora section
    if grep -q "Lora:" "$CONFIG_YAML" 2>/dev/null; then
        MODULE=$(grep -A1 "Lora:" "$CONFIG_YAML" | grep "Module:" | awk '{print $2}' || echo "auto")
        check_pass "Lora section" "Module: ${MODULE:-auto}"
    else
        check_warn "Lora section" "Missing from config.yaml" "Reinstall meshtasticd package or run MeshForge ensure_structure() to regenerate"
    fi

    # Check for WRONG content (radio parameters that shouldn't be here)
    if grep -qE "Bandwidth:|SpreadFactor:|CodingRate:|TXpower:" "$CONFIG_YAML" 2>/dev/null; then
        check_warn "Radio parameters in config.yaml" \
            "config.yaml should NOT contain Bandwidth/SpreadFactor/TXpower" \
            "These are set via meshtastic CLI, not yaml files"
    fi
else
    check_fail "config.yaml exists" "File not found" "Create minimal config or reinstall meshtasticd"
fi

# Check available.d templates (provided by meshtasticd package)
AVAIL_COUNT=$(ls -1 "$CONFIG_DIR/available.d/"*.yaml 2>/dev/null | wc -l || echo "0")
if [[ "$AVAIL_COUNT" -gt 0 ]]; then
    check_pass "HAT templates available" "$AVAIL_COUNT templates in available.d/"
else
    check_warn "HAT templates" "No templates in available.d/" \
        "Templates provided by meshtasticd package - may need reinstall"
fi

# Check config.d (active HAT config)
ACTIVE_COUNT=$(ls -1 "$CONFIG_DIR/config.d/"*.yaml 2>/dev/null | wc -l || echo "0")
if [[ "$ACTIVE_COUNT" -gt 0 ]]; then
    ACTIVE_NAME=$(ls -1 "$CONFIG_DIR/config.d/"*.yaml 2>/dev/null | head -1 | xargs basename)
    check_pass "Active HAT config" "$ACTIVE_NAME in config.d/"
else
    # Check if this is SPI radio (needs HAT config) or USB (doesn't need it)
    if [[ -e /dev/spidev0.0 ]] || [[ -e /dev/spidev0.1 ]]; then
        check_warn "Active HAT config" "SPI detected but no HAT config in config.d/" \
            "Copy your HAT template: sudo cp $CONFIG_DIR/available.d/<your-hat>.yaml $CONFIG_DIR/config.d/"
    else
        check_skip "Active HAT config" "USB radio doesn't require HAT config"
    fi
fi

log ""

# ─────────────────────────────────────────────────────────────────
# Section 4: Service Status
# ─────────────────────────────────────────────────────────────────
log "${BOLD}[4/6] Service Status${NC}"

MESHTASTICD_RUNNING=false

# Check meshtasticd service
if systemctl is-active --quiet meshtasticd 2>/dev/null; then
    check_pass "meshtasticd service" "Running"
    MESHTASTICD_RUNNING=true
elif systemctl is-enabled --quiet meshtasticd 2>/dev/null; then
    check_warn "meshtasticd service" "Enabled but not running" "Start: sudo systemctl start meshtasticd"
else
    check_warn "meshtasticd service" "Not enabled" "Enable: sudo systemctl enable --now meshtasticd"
fi

# Check if port 4403 is listening (meshtasticd TCP)
# Retry when service is running but port hasn't bound yet (startup race)
PORT_4403_OK=false
if ss -tlnp 2>/dev/null | grep -q ":4403 "; then
    PORT_4403_OK=true
elif $MESHTASTICD_RUNNING; then
    for _attempt in 1 2 3 4 5; do
        sleep 2
        if ss -tlnp 2>/dev/null | grep -q ":4403 "; then
            PORT_4403_OK=true
            break
        fi
    done
fi

if $PORT_4403_OK; then
    check_pass "Port 4403 (TCP)" "meshtasticd TCP interface listening"
elif $MESHTASTICD_RUNNING; then
    check_warn "Port 4403 (TCP)" "Not listening yet" \
        "Service is running but TCP port may need more startup time. Check: sudo journalctl -u meshtasticd -f"
else
    check_warn "Port 4403 (TCP)" "Not listening" \
        "meshtasticd is not running. Start: sudo systemctl start meshtasticd"
fi

# Web client is on port 9443 (HTTPS, checked in Section 6)

# Check rnsd service
if systemctl is-active --quiet rnsd 2>/dev/null; then
    check_pass "rnsd service" "Running"
elif command -v rnsd &>/dev/null; then
    check_warn "rnsd service" "Installed but not running" "Start: sudo systemctl start rnsd"
else
    check_warn "rnsd service" "RNS not installed" "Install: pip3 install rns"
fi

log ""

# ─────────────────────────────────────────────────────────────────
# Section 5: Hardware Detection
# ─────────────────────────────────────────────────────────────────
log "${BOLD}[5/6] Hardware Detection${NC}"

RADIO_FOUND=false

# Check for SPI devices
if [[ -e /dev/spidev0.0 ]] || [[ -e /dev/spidev0.1 ]]; then
    check_pass "SPI device" "/dev/spidev0.x present"
    RADIO_FOUND=true

    # Check SPI enabled in boot config
    if grep -q "dtparam=spi=on" /boot/config.txt 2>/dev/null || \
       grep -q "dtparam=spi=on" /boot/firmware/config.txt 2>/dev/null; then
        check_pass "SPI enabled" "In boot config"
    else
        check_warn "SPI in boot config" "dtparam=spi=on not found" \
            "Enable: sudo raspi-config → Interface Options → SPI"
    fi
fi

# Check for USB serial devices and identify them
USB_DEVICE_FOUND=false
for dev in /dev/ttyUSB* /dev/ttyACM*; do
    if [[ -e "$dev" ]]; then
        check_pass "USB serial device" "$dev"
        RADIO_FOUND=true
        USB_DEVICE_FOUND=true

        # Try to identify specific device via USB vendor:product ID
        USB_VID=$(udevadm info --query=property "$dev" 2>/dev/null | grep '^ID_VENDOR_ID=' | cut -d= -f2)
        USB_PID=$(udevadm info --query=property "$dev" 2>/dev/null | grep '^ID_MODEL_ID=' | cut -d= -f2)
        if [[ -n "$USB_VID" && -n "$USB_PID" ]]; then
            USB_ID="${USB_VID}:${USB_PID}"
            case "$USB_ID" in
                303a:1001|303a:4001|303a:1002)
                    check_pass "USB radio identified" "Heltec V3/V4 (template: heltec-usb.yaml)" ;;
                1209:0000)
                    check_pass "USB radio identified" "MeshStick (template: meshstick-usb.yaml)" ;;
                1a86:7523|1a86:55d4|1a86:7522)
                    check_pass "USB radio identified" "MeshToad/CH340 (template: meshtoad-usb.yaml)" ;;
                239a:8029|239a:0029|19d2:0016)
                    check_pass "USB radio identified" "RAK4631 (template: rak4631-usb.yaml)" ;;
                10c4:ea60)
                    check_pass "USB radio identified" "Station G2/CP2102 (template: station-g2-usb.yaml)" ;;
                1a86:55d3)
                    check_pass "USB radio identified" "T-Beam S3/CH9102 (template: tbeam-usb.yaml)" ;;
                0403:6001|0403:6015)
                    check_pass "USB radio identified" "FTDI USB-Serial (template: usb-serial-generic.yaml)" ;;
                *)
                    check_warn "USB radio ID" "Unknown USB ID $USB_ID" \
                        "Use generic template: usb-serial-generic.yaml" ;;
            esac
        fi
        break
    fi
done

if ! $RADIO_FOUND; then
    if $MESHTASTICD_RUNNING && [[ "$ACTIVE_COUNT" -gt 0 ]]; then
        # Service is running with active config — hardware not visible but
        # likely working (common in containers or when device managed by daemon)
        check_info "Radio hardware" \
            "No /dev device visible but meshtasticd is running with active config ($ACTIVE_NAME)"
    else
        check_warn "Radio hardware" "No SPI or USB radio detected" \
            "Connect USB radio or enable SPI for HAT"
        log "  Available USB templates: heltec-usb, meshstick-usb, meshtoad-usb,"
        log "    rak4631-usb, station-g2-usb, tbeam-usb, usb-serial-generic"
        log "  Select in TUI: Configuration > Hardware Config"
    fi
fi

# Check udev rules
if [[ -f /etc/udev/rules.d/99-meshtastic.rules ]]; then
    check_pass "udev rules" "/etc/udev/rules.d/99-meshtastic.rules"
else
    check_warn "udev rules" "Meshtastic udev rules not installed" \
        "May cause permission issues with USB radios"
fi

# Check ALSA udev rules for broken GOTO labels (RPi OS packaging bug)
ALSA_RULES="/usr/lib/udev/rules.d/90-alsa-restore.rules"
if [[ -f "$ALSA_RULES" ]]; then
    BROKEN_GOTOS=""
    while IFS= read -r goto_label; do
        if ! grep -q "LABEL=\"$goto_label\"" "$ALSA_RULES"; then
            BROKEN_GOTOS="${BROKEN_GOTOS:+$BROKEN_GOTOS, }$goto_label"
        fi
    done < <(grep -oP 'GOTO="\K[^"]+' "$ALSA_RULES" | sort -u)

    if [[ -n "$BROKEN_GOTOS" ]]; then
        if [[ -f /etc/udev/rules.d/90-alsa-restore.rules ]]; then
            check_pass "ALSA udev rules" "Override exists in /etc/udev/rules.d/"
        else
            check_warn "ALSA udev rules" "Broken GOTO labels: $BROKEN_GOTOS" \
                "Run installer or: sudo python3 -c \"from utils.udev_fix import fix_broken_udev_rules; print(fix_broken_udev_rules())\""
        fi
    else
        check_pass "ALSA udev rules" "No broken GOTO labels"
    fi
fi

log ""

# ─────────────────────────────────────────────────────────────────
# Section 6: Network Connectivity
# ─────────────────────────────────────────────────────────────────
log "${BOLD}[6/6] Network Connectivity${NC}"

# Test web client (port 9443 - HTTPS)
if ss -tlnp 2>/dev/null | grep -q ":9443 "; then
    # Try to connect to web client
    if curl -sk --max-time 5 "https://localhost:9443" &>/dev/null; then
        check_pass "Web client connection" "https://localhost:9443 responds"
    else
        check_warn "Web client connection" "Port open but not responding" \
            "May still be starting up"
    fi
else
    check_skip "Web client connection" "Port 9443 not listening"
fi

# Test meshtasticd TCP if port is open
if ss -tlnp 2>/dev/null | grep -q ":4403 "; then
    # Quick TCP connect test
    if timeout 2 bash -c "echo -n '' > /dev/tcp/localhost/4403" 2>/dev/null; then
        check_pass "meshtasticd TCP" "localhost:4403 accepts connections"
    else
        check_warn "meshtasticd TCP" "Port open but connection failed"
    fi
else
    check_skip "meshtasticd TCP" "Port 4403 not listening"
fi

# Test internet connectivity (for MQTT, updates)
if ping -c 1 -W 2 8.8.8.8 &>/dev/null; then
    check_pass "Internet connectivity" "Can reach 8.8.8.8"
else
    check_warn "Internet connectivity" "Cannot reach internet" \
        "MQTT and updates won't work"
fi

log ""

# ─────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────

# Calculate totals
TOTAL_CHECKS=$((CHECKS_PASSED + CRITICAL_FAILS + WARNINGS))

if $JSON; then
    # JSON output
    echo "{"
    echo "  \"total_checks\": $TOTAL_CHECKS,"
    echo "  \"passed\": $CHECKS_PASSED,"
    echo "  \"failed\": $CRITICAL_FAILS,"
    echo "  \"warnings\": $WARNINGS,"
    echo "  \"status\": \"$([ $CRITICAL_FAILS -eq 0 ] && echo "ok" || echo "failed")\","
    echo "  \"results\": ["
    for i in "${!RESULTS[@]}"; do
        echo -n "    ${RESULTS[$i]}"
        if [[ $i -lt $((${#RESULTS[@]} - 1)) ]]; then
            echo ","
        else
            echo ""
        fi
    done
    echo "  ]"
    echo "}"
else
    if ! $QUIET; then
        echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
        echo -e "${BOLD}Summary${NC}"
        echo ""
        echo -e "  Total checks: $TOTAL_CHECKS"
        echo -e "  ${GREEN}Passed: $CHECKS_PASSED${NC}"
        if [[ $WARNINGS -gt 0 ]]; then
            echo -e "  ${YELLOW}Warnings: $WARNINGS${NC}"
        fi
        if [[ $CRITICAL_FAILS -gt 0 ]]; then
            echo -e "  ${RED}Failed: $CRITICAL_FAILS${NC}"
        fi
        echo ""

        if [[ $CRITICAL_FAILS -eq 0 ]] && [[ $WARNINGS -eq 0 ]]; then
            echo -e "${GREEN}╔═══════════════════════════════════════════════════════════╗${NC}"
            echo -e "${GREEN}║  Installation verified successfully!                      ║${NC}"
            echo -e "${GREEN}╚═══════════════════════════════════════════════════════════╝${NC}"
        elif [[ $CRITICAL_FAILS -eq 0 ]]; then
            echo -e "${YELLOW}╔═══════════════════════════════════════════════════════════╗${NC}"
            echo -e "${YELLOW}║  Installation OK with warnings - review above             ║${NC}"
            echo -e "${YELLOW}╚═══════════════════════════════════════════════════════════╝${NC}"
        else
            echo -e "${RED}╔═══════════════════════════════════════════════════════════╗${NC}"
            echo -e "${RED}║  Installation needs attention - see failures above        ║${NC}"
            echo -e "${RED}╚═══════════════════════════════════════════════════════════╝${NC}"
        fi
        echo ""
    fi
fi

# Exit code
if [[ $CRITICAL_FAILS -gt 0 ]]; then
    exit 1
elif [[ $WARNINGS -gt 0 ]]; then
    exit 2
else
    exit 0
fi
