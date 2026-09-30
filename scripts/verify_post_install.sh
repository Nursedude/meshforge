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
# Section 1b: Python Environment & Dependencies
# (the install-hardening verification — pip presence is now first-class,
#  and "installed" is checked against "importable by the consumer")
# ─────────────────────────────────────────────────────────────────
log "${BOLD}[1b] Python Environment & Dependencies${NC}"

VENV_PY="/opt/meshforge/venv/bin/python"

# pip presence — a fresh user once had to install pip by hand because NOTHING
# detected its absence. These probes are read-only (never apt-install in a
# checker). Note: `python3 -c` here runs inside a shell script (allowed); the
# CLI deny-list only covers the interactive Bash tool.
if python3 -m pip --version &>/dev/null; then
    check_pass "pip (system python3)" "$(python3 -m pip --version 2>/dev/null)"
else
    check_fail "pip (system python3)" "python3 -m pip is not available" \
        "sudo apt install -y python3-pip"
fi

if [[ -x "$VENV_PY" ]]; then
    if "$VENV_PY" -m pip --version &>/dev/null; then
        check_pass "pip (venv)" "$("$VENV_PY" -m pip --version 2>/dev/null)"
    else
        check_fail "pip (venv)" "venv python has no pip" \
            "Recreate: python3 -m venv /opt/meshforge/venv --system-site-packages"
    fi
fi

# Import-as-consumer: "installed" is not "importable". Check the launcher's
# core dependency in the interpreter the services actually run.
CONSUMER_PY="python3"
[[ -x "$VENV_PY" ]] && CONSUMER_PY="$VENV_PY"
if "$CONSUMER_PY" -c "import rich" &>/dev/null; then
    check_pass "Core deps importable" "rich imports in $CONSUMER_PY"
else
    check_fail "Core deps importable" "rich not importable in $CONSUMER_PY" \
        "Re-run the Python deps step: sudo bash scripts/install_noc.sh"
fi

# Issue #24: meshtastic must import as ROOT when rnsd's Meshtastic_Interface
# plugin is installed — pipx/--user installs don't reach root's Python.
RNSD_IFACE="/etc/reticulum/interfaces/Meshtastic_Interface.py"
if [[ -f "$RNSD_IFACE" ]]; then
    if sudo python3 -c "import meshtastic" &>/dev/null; then
        check_pass "meshtastic for rnsd (root import)" "importable by root"
    else
        check_fail "meshtastic for rnsd (root import)" \
            "Meshtastic_Interface.py installed but meshtastic not importable by root (rnsd will fail)" \
            "sudo python3 -m pip install --break-system-packages --ignore-installed meshtastic"
    fi
else
    check_skip "meshtastic for rnsd (root import)" "Meshtastic_Interface.py not installed"
fi

# RNS/LXMF fork-pin drift (read-only gate; a box may intentionally lag, so WARN).
if [[ -f /opt/meshforge/scripts/rns_version_check.py ]]; then
    if python3 /opt/meshforge/scripts/rns_version_check.py &>/dev/null; then
        check_pass "RNS/LXMF fork pin" "installed rns/lxmf match requirements/rns.txt"
    else
        check_warn "RNS/LXMF fork pin" "rns/lxmf drifted from the MF-FORK-PIN" \
            "Review + reinstall: pip install -r requirements/rns.txt (see rns_version_check.py)"
    fi
fi

# Designed-absent optional config — make absence an OBSERVED state, not a silent
# gap, and distinguish "off by default" from "misconfigured".
if systemctl show meshforge-gateway 2>/dev/null | grep -qiE "MESHFORGE_ORACLE_ENABLED=(1|true|yes|on)"; then
    check_info "mesh-oracle" "ENABLED via systemd Environment (opt-in)"
else
    check_info "mesh-oracle" "DISABLED (MESHFORGE_ORACLE_ENABLED unset — this is the default)"
fi

OP_USER="${SUDO_USER:-${USER:-$(id -un 2>/dev/null || true)}}"
OP_HOME="$(getent passwd "$OP_USER" 2>/dev/null | cut -d: -f6)"
MINI_ENV="$OP_HOME/.config/meshforge/mini_dudeai.env"
if [[ -z "$OP_HOME" ]]; then
    # UNOBSERVABLE is not "not present". Without a resolvable operator there is
    # no home to look in, and reporting the optional-and-absent case here would
    # be a verifier answering a question it could not ask (hfm #2).
    check_info "mini-dudeai env" "UNKNOWN — no operator home resolved (SUDO_USER='$SUDO_USER' USER='$USER')"
elif [[ -f "$MINI_ENV" ]]; then
    check_info "mini-dudeai env" "present at $MINI_ENV"
else
    check_info "mini-dudeai env" "not present (optional; mini runs on the built-in fleet preset)"
fi

log ""

# ─────────────────────────────────────────────────────────────────
# Section 1c: OS security updates (read-only; the installer's writer half
# is the unattended-upgrades step in install_noc.sh — reader/writer pair,
# honest_failure_modes #4). 2026-09-06: 9 of 10 fleet boxes lacked it and
# NOTHING detected the absence for the life of those boxes.
# ─────────────────────────────────────────────────────────────────
log "${BOLD}[1c] OS Security Updates${NC}"

if command -v dpkg-query &>/dev/null; then
    UU_STATUS="$(dpkg-query -W -f='${Status}' unattended-upgrades 2>/dev/null || true)"
    if [[ "$UU_STATUS" == "install ok installed" ]]; then
        check_pass "unattended-upgrades installed" "$(dpkg-query -W -f='${Version}' unattended-upgrades 2>/dev/null)"
        if grep -qs 'APT::Periodic::Unattended-Upgrade "1"' /etc/apt/apt.conf.d/20auto-upgrades; then
            check_pass "periodic security updates enabled" "/etc/apt/apt.conf.d/20auto-upgrades"
        else
            check_warn "periodic security updates enabled" "20auto-upgrades missing or set to 0 — the package is installed but idle" \
                "sudo dpkg-reconfigure -f noninteractive unattended-upgrades"
        fi
    else
        check_fail "unattended-upgrades installed" "not installed — security updates are not applied automatically" \
            "sudo apt install -y unattended-upgrades"
    fi
else
    check_skip "unattended-upgrades installed" "not a dpkg system"
fi

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
# The checkout this script belongs to (its overlay_kind.py helper).
VERIFY_SCRIPT_DIR="${VERIFY_SCRIPT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
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

    # Which radio module will meshtasticd ACTUALLY use? Ask meshtasticd:
    # --output-yaml merges config.yaml + config.d/ exactly as the daemon will
    # and exits during config load (private netns, scratch data dir). The old
    # grep read a COMMENTED `# Module: auto` as "Module: Module:", and an
    # unset Module silently runs a SIMULATED radio (measured 2026-09-30).
    # --output-yaml exits BEFORE autoconf, so `auto` is judged here against
    # the hardware autoconf would look for (reader pair, 2026-09-30).
    MT_BIN=$(command -v meshtasticd || true)
    MODULE=""; MT_OUT=""
    if [[ -n "$MT_BIN" ]]; then
        MT_TMP=$(mktemp -d)
        MT_OUT=$(timeout 20 unshare -rn "$MT_BIN" --output-yaml -c "$CONFIG_YAML" -d "$MT_TMP" 2>/dev/null)
        rm -rf "$MT_TMP"
        MODULE=$(printf '%s\n' "$MT_OUT" | awk '/^Lora:/{l=1;next} /^[^ ]/{l=0} l && $1=="Module:"{print $2; exit}')
    fi
    HAVE_CH341=false
    for _d in /sys/bus/usb/devices/*; do
        if [[ "$(cat "$_d/idVendor" 2>/dev/null)" == "1a86" && "$(cat "$_d/idProduct" 2>/dev/null)" == "5512" ]]; then
            HAVE_CH341=true; break
        fi
    done
    MT_WANTED=false
    if systemctl is-enabled --quiet meshtasticd 2>/dev/null || systemctl is-active --quiet meshtasticd 2>/dev/null; then
        MT_WANTED=true
    fi
    # A radio overlay in config.d/ whose Module did not survive the merge
    # (unreadable, or a name meshtasticd does not know, e.g. `sx1276`).
    RADIO_OVERLAY=""
    for _f in "$CONFIG_DIR"/config.d/*.yaml; do
        [[ -f "$_f" ]] || continue
        case "$(python3 -B "$VERIFY_SCRIPT_DIR/overlay_kind.py" "$_f" 2>/dev/null)" in
            spi|ch341) RADIO_OVERLAY="$_f"; break ;;
        esac
    done
    if printf '%s\n' "$MT_OUT" | grep -q '\*\*\* Exception'; then
        check_warn "Radio module" "UNKNOWN — meshtasticd could not load its config: $(printf '%s\n' "$MT_OUT" | grep -m1 '\*\*\* Exception')" \
            "Check file permissions / YAML in $CONFIG_DIR/config.d/"
    else
    case "$MODULE" in
        "")
            check_warn "Radio module" "UNKNOWN — could not ask meshtasticd (--output-yaml)" \
                "Check by hand: sudo meshtasticd --output-yaml -c $CONFIG_YAML -d \$(mktemp -d) | grep -A1 '^Lora:'" ;;
        sim)
            if $MT_WANTED; then
                check_fail "Radio module" "SIMULATED radio (Lora Module unset) — nothing will transmit" \
                    "Select hardware: TUI > Meshtasticd Config > Hardware Config, or set 'Lora: Module: auto' in $CONFIG_YAML"
            else
                check_info "Radio module" "unset (would be a SIMULATED radio) — meshtasticd is not enabled here"
            fi ;;
        auto)
            if [[ -n "$RADIO_OVERLAY" ]]; then
                check_fail "Radio module" "$(basename "$RADIO_OVERLAY") did not set a Module meshtasticd knows — merged config is still 'auto'" \
                    "Valid names: sx1262 sx1268 sx1280 RF95 LLCC68 lr1110 lr1120 lr1121 (not e.g. sx1276)"
            elif $HAVE_CH341; then
                check_pass "Radio module" "auto — a CH341 USB stick is present for autoconf"
            elif [[ -r /proc/device-tree/hat/product ]]; then
                check_pass "Radio module" "auto — a HAT+ EEPROM is present for autoconf"
            elif $MT_WANTED; then
                check_warn "Radio module" "auto, but no CH341 stick or HAT+ EEPROM detected — meshtasticd will exit ('Could not locate any devices')" \
                    "Select hardware: TUI > Meshtasticd Config > Hardware Config"
            else
                check_info "Radio module" "auto — no radio hardware detected; meshtasticd is not enabled here"
            fi ;;
        *)
            check_pass "Radio module" "$MODULE (merged config.yaml + config.d/)" ;;
    esac
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
            "Activate your HAT template (sanitized, never copied raw — #58): sudo python3 /opt/meshforge/scripts/sanitize_overlay.py $CONFIG_DIR/available.d/<your-hat>.yaml $CONFIG_DIR/config.d/"
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
    check_warn "rnsd service" "RNS not installed" "Install: pip3 install -r requirements/rns.txt (MeshForge RNS fork)"
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
# A CH341 USB LoRa stick (MeshToad, MeshStick, ...) runs in SPI mode and
# creates NO tty, so the tty loop below can never see it (reader pair,
# 2026-09-30). Every such stick is USB 1a86:5512.
for _d in /sys/bus/usb/devices/*; do
    if [[ "$(cat "$_d/idVendor" 2>/dev/null)" == "1a86" && "$(cat "$_d/idProduct" 2>/dev/null)" == "5512" ]]; then
        check_pass "USB radio identified" "CH341 USB LoRa stick — meshtasticd drives it via a lora-usb-*.yaml overlay"
        RADIO_FOUND=true
        break
    fi
done

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
                *)
                    # A tty is a standalone node, an RNode or a GPS: meshtasticd
                    # has no serial-radio mode (no `Serial:` key), so no overlay
                    # applies. Reach a Meshtastic node with: meshtastic --port $dev
                    check_info "USB serial device" "$USB_ID on $dev — not a meshtasticd radio; a Meshtastic node is reached with: meshtastic --port $dev" ;;
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
        log "  CH341 USB sticks: a lora-usb-*.yaml overlay from available.d/."
        log "  USB Meshtastic nodes need no meshtasticd config (meshtastic --port <tty>)."
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
