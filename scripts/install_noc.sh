#!/bin/bash
#
# MeshForge NOC Stack Installer
#
# Installs the complete NOC stack:
#   - meshtasticd (Meshtastic daemon) - auto-detects USB or SPI radio
#   - Reticulum/RNS (rnsd)
#   - MeshForge (orchestrates everything)
#
# Supports:
#   - USB Serial radios (T-Beam, Heltec, RAK USB) → Python CLI
#   - Native SPI radios (Meshtoad, RAK HAT) → Native meshtasticd binary
#
# Usage:
#   sudo bash scripts/install_noc.sh
#
# Options:
#   --skip-meshtasticd    Don't install meshtasticd
#   --skip-rns            Don't install Reticulum
#   --client-only         Only install MeshForge as client (no daemons)
#   --force-native        Force native meshtasticd (for SPI radios)
#   --force-python        Force Python meshtastic CLI (for USB radios)
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Defaults
INSTALL_MESHTASTICD=true
INSTALL_RNS=true
INSTALL_DIR="/opt/meshforge"
VENV_DIR="$INSTALL_DIR/venv"
MESHTASTICD_CONFIG_DIR="/etc/meshtasticd"
FORCE_NATIVE=false
FORCE_PYTHON=false

# Architecture detection
ARCH=$(dpkg --print-architecture 2>/dev/null || uname -m)
case $ARCH in
    aarch64|arm64) ARCH="arm64" ;;
    armv7l|armhf) ARCH="armhf" ;;
    x86_64|amd64) ARCH="amd64" ;;
esac

# OpenSUSE Build Service repo for native meshtasticd
# Supports: Debian_12, Debian_13, Debian_Testing, Raspbian_12, Ubuntu_24.04, etc.
OBS_BASE_URL="https://download.opensuse.org/repositories/network:/Meshtastic:/beta"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-meshtasticd)
            INSTALL_MESHTASTICD=false
            shift
            ;;
        --skip-rns)
            INSTALL_RNS=false
            shift
            ;;
        --client-only)
            INSTALL_MESHTASTICD=false
            INSTALL_RNS=false
            shift
            ;;
        --force-native)
            FORCE_NATIVE=true
            shift
            ;;
        --force-python)
            FORCE_PYTHON=true
            shift
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

echo -e "${CYAN}"
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║        MeshForge NOC Stack Installer                      ║"
echo "║        Network Operations Center for Mesh Networks        ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Check root
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}Error: This script must be run as root${NC}"
   echo "Please run: sudo bash $0"
   exit 1
fi

# Resolve operator login for systemd User= placeholders.
# Refuses root and users not in /etc/passwd — loud-fail rather than
# silently install a unit that drifts from the TUI's $SUDO_USER home.
resolve_operator_user() {
    local u="${SUDO_USER:-$USER}"
    if [[ -z "$u" || "$u" == "root" ]]; then
        echo -e "${RED}✗ Cannot resolve operator user (SUDO_USER='$SUDO_USER' USER='$USER')${NC}" >&2
        echo -e "${YELLOW}  Run via: sudo bash scripts/install_noc.sh${NC}" >&2
        exit 1
    fi
    if ! getent passwd "$u" >/dev/null 2>&1; then
        echo -e "${RED}✗ User '$u' not in /etc/passwd${NC}" >&2
        exit 1
    fi
    echo "$u"
}

# ─────────────────────────────────────────────────────────────────
# Radio Type Detection Functions
# ─────────────────────────────────────────────────────────────────

detect_hardware_hints() {
    # Detect available hardware and report findings (does NOT decide radio type)
    # Sets: HW_HAS_SPI, HW_HAS_USB, HW_HAS_CH341, HW_USB_DEVS
    HW_HAS_SPI=false
    HW_HAS_USB=false
    HW_HAS_CH341=false
    HW_USB_DEVS=""

    # Check for CH341 (Meshtoad USB-to-SPI adapter)
    if dmesg 2>/dev/null | grep -qi "ch341.*spi\|ch341-spi"; then
        HW_HAS_CH341=true
    fi

    # Check for SPI devices
    if [[ -e /dev/spidev0.0 ]] || [[ -e /dev/spidev0.1 ]]; then
        HW_HAS_SPI=true
    fi

    # Check for USB serial devices
    HW_USB_DEVS=$(ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null | tr '\n' ' ')
    if [[ -n "$HW_USB_DEVS" ]]; then
        HW_HAS_USB=true
    fi
}

ask_radio_type() {
    # Ask user what radio they have, showing detected hardware as hints
    # Returns (stdout): "spi", "usb", or "none"
    # All display output goes to stderr so it doesn't pollute the return value

    detect_hardware_hints

    echo "" >&2
    echo -e "  ${BOLD}Hardware detected:${NC}" >&2
    if $HW_HAS_SPI; then
        echo -e "    ${GREEN}✓${NC} SPI bus available (/dev/spidev0.*)" >&2
    fi
    if $HW_HAS_CH341; then
        echo -e "    ${GREEN}✓${NC} CH341 USB-to-SPI adapter (Meshtoad)" >&2
    fi
    if $HW_HAS_USB; then
        echo -e "    ${GREEN}✓${NC} USB serial ports: $HW_USB_DEVS" >&2
    fi
    if ! $HW_HAS_SPI && ! $HW_HAS_USB && ! $HW_HAS_CH341; then
        echo -e "    ${YELLOW}!${NC} No LoRa hardware detected yet" >&2
    fi
    echo "" >&2

    # Try whiptail first (raspi-config style)
    if command -v whiptail &>/dev/null; then
        local DEFAULT_ITEM="none"
        if $HW_HAS_SPI || $HW_HAS_CH341; then
            DEFAULT_ITEM="spi"
        elif $HW_HAS_USB; then
            DEFAULT_ITEM="usb"
        fi

        local CHOICE
        flush_terminal_input
        CHOICE=$(whiptail --title "Radio Type" \
            --default-item "$DEFAULT_ITEM" \
            --menu \
            "What type of LoRa radio is connected to this device?\n\nSelect your hardware:" \
            15 60 3 \
            -- \
            "spi"  "SPI HAT (MeshAdv, Waveshare, RAK, Meshtoad)" \
            "usb"  "USB Serial (T-Beam, Heltec, RAK USB)" \
            "none" "No radio connected / install later" \
            3>&1 1>&2 2>&3) || CHOICE="none"

        echo "$CHOICE"
        return
    fi

    # Fallback: simple text menu
    echo -e "  ${BOLD}What type of LoRa radio is connected?${NC}" >&2
    echo "    1) SPI HAT (MeshAdv, Waveshare, RAK, Meshtoad)" >&2
    echo "    2) USB Serial (T-Beam, Heltec, RAK USB)" >&2
    echo "    3) No radio / install later" >&2
    echo "" >&2
    read -rp "  Select [1/2/3]: " radio_choice
    case "$radio_choice" in
        1) echo "spi" ;;
        2) echo "usb" ;;
        *) echo "none" ;;
    esac
}

get_usb_device() {
    # Find the first USB serial device
    for dev in /dev/ttyUSB* /dev/ttyACM*; do
        if [[ -e "$dev" ]]; then
            echo "$dev"
            return
        fi
    done
    echo ""
}

load_ch341_driver() {
    # Load CH341 kernel module if needed
    if ! lsmod | grep -q ch341; then
        echo -e "  ${CYAN}Loading CH341 driver...${NC}"
        modprobe ch341 2>/dev/null || true
        sleep 1
    fi
}

flush_terminal_input() {
    # Drain stale keystrokes before launching whiptail dialogs.
    # Without this, buffered input (from user typing during apt install etc.)
    # can cause whiptail to immediately process Enter/Escape and close.
    # Mirrors termios.tcflush() in src/launcher_tui/backend.py:106-109
    read -t 0.1 -n 10000 discard < /dev/tty 2>/dev/null || true
}

classify_template() {
    # Classify a hardware template as "spi", "usb", or "display".
    # Mirrors src/launcher_tui/handlers/meshtasticd_radio.py:277-296
    local file="$1"
    local stem
    stem=$(basename "$file" .yaml)

    # Display configs (not radio hardware)
    [[ "$stem" == display-* ]] && { echo "display"; return; }

    # Name-based USB detection (covers MeshForge-named templates)
    case "$stem" in
        *-usb|usb-*) echo "usb"; return ;;
    esac

    # Content-based: Serial: section without spidev -> USB serial radio
    local content
    content=$(head -20 "$file" 2>/dev/null) || content=""
    if echo "$content" | grep -q "^Serial:" && ! echo "$content" | grep -qi "spidev"; then
        echo "usb"; return
    fi

    # CH341 USB-to-SPI adapters used as USB devices
    if [[ "$stem" == *usb* ]] && echo "$content" | grep -qi "spidev:.*ch341"; then
        echo "usb"; return
    fi

    echo "spi"
}

get_template_description() {
    # Return human-readable description for a hardware template.
    # Built-in lookup from RADIO_TEMPLATES (src/core/meshtasticd_templates.py),
    # then fallback to first comment line, then filename.
    # Mirrors src/launcher_tui/handlers/meshtasticd_radio.py:298-321
    local file="$1"
    local stem
    stem=$(basename "$file" .yaml)

    case "$stem" in
        # USB radios
        heltec-usb)          echo "Heltec V3/V4 USB (ESP32-S3, 28dBm)" ;;
        station-g2-usb)      echo "Station G2 USB (CP2102, gateway, PoE)" ;;
        tbeam-usb)           echo "T-Beam S3 USB (CH9102, GPS, gateway)" ;;
        rak4631-usb)         echo "RAK4631 USB (nRF52840 + SX1262)" ;;
        meshtoad-usb)        echo "MeshToad/MeshTadpole USB (CH340)" ;;
        meshstick-usb)       echo "MeshStick USB (official Meshtastic)" ;;
        usb-serial-generic)  echo "Generic USB Serial (FTDI, fallback)" ;;
        lora-pinedio-usb-sx1262)  echo "Pinedio USB (CH341 + SX1262)" ;;
        lora-usb-meshtoad-e22)    echo "MeshToad E22 USB (CH341 + SX1262)" ;;
        # SPI radios
        meshtoad-spi)        echo "Meshtoad/MeshStick SPI (SX1262 CH341)" ;;
        meshadv-pi-hat)      echo "MeshAdv-Pi-Hat 1W (SX1262, GPS)" ;;
        meshadv-mini)        echo "MeshAdv-Mini (SX1262, GPS, +22dBm)" ;;
        meshadv-pi-v1.1)     echo "MeshAdv-Pi v1.1 (SX1262)" ;;
        waveshare-sx1262)    echo "Waveshare SX1262 LoRa HAT" ;;
        rak-hat-spi)         echo "RAK WisLink / RAK2287 SPI HAT" ;;
        adafruit-rfm9x)      echo "Adafruit RFM9x Bonnet (SX1276)" ;;
        femtofox)            echo "FemtoFox LoRa Board (SX1262)" ;;
        ebyte-e22-900m30s)   echo "Ebyte E22-900M30S 1W (915MHz)" ;;
        ebyte-e22-400m30s)   echo "Ebyte E22-400M30S 1W (433MHz)" ;;
        elecrow-rfm95)       echo "Elecrow RFM95 HAT (SX1276)" ;;
        seeed-sensecap)      echo "Seeed SenseCAP E5 HAT (SX1262)" ;;
        # Upstream-named SPI templates
        lora-Adafruit-RFM9x)       echo "Adafruit RFM9x (upstream, SX1276)" ;;
        lora-MeshAdv-900M30S)       echo "MeshAdv E22-900M30S 1W (SX1262)" ;;
        lora-MeshAdv-Mini-900M22S)  echo "MeshAdv Mini E22-900M22S (SX1262)" ;;
        lora-RAK6421-13300-slot1)   echo "RAK6421 Pi-HAT RAK13300 Slot 1" ;;
        lora-RAK6421-13300-slot2)   echo "RAK6421 Pi-HAT RAK13300 Slot 2" ;;
        lora-meshstick-1262)        echo "MeshStick 1262 SPI (CH341)" ;;
        lora-piggystick-lr1121)     echo "PiggyStick LR1121 (CH341)" ;;
        lora-lyra-picocalc-wio-sx1262) echo "Lyra PicoCalc WIO SX1262" ;;
        lora-raxda-rock2f-starter-edition-hat) echo "Radxa Rock 2F Starter HAT" ;;
        lora-starter-edition-sx1262-i2c) echo "Starter Edition SX1262 I2C HAT" ;;
        lora-waveshare-sxxx)        echo "Waveshare SX126X XXXM HAT" ;;
        lora-ws-raspberry-pi-pico-to-rpi-adapter) echo "Waveshare Pico-to-RPi Adapter" ;;
        lora-ws-raspberry-pico-to-orangepi-03) echo "Waveshare SX1262 Orange Pi Zero3" ;;
        # Displays
        display-waveshare-1-44)  echo "Waveshare 1.44in LCD HAT (ST7735S)" ;;
        display-waveshare-2.8)   echo "Waveshare 2.8in LCD + Touch (ST7789)" ;;
        *)
            # Fallback: first comment line from file (strip all leading #)
            local desc
            desc=$(grep "^#" "$file" 2>/dev/null | head -1 | sed 's/^#[# ]*//' | head -c 55)
            if [[ -n "$desc" ]]; then
                echo "$desc"
            else
                echo "$stem"
            fi
            ;;
    esac
}

deploy_meshforge_templates() {
    # Deploy MeshForge's curated hardware templates to available.d/.
    # Uses cp -f to overwrite system templates (which have garbage descriptions)
    # with MeshForge's curated versions (which have proper # Description headers).
    # Must be called AFTER apt install meshtasticd to overwrite dpkg's copies.
    if [[ -d "$INSTALL_DIR/templates/available.d" ]]; then
        mkdir -p "$MESHTASTICD_CONFIG_DIR/available.d"
        cp -f "$INSTALL_DIR/templates/available.d/"*.yaml \
            "$MESHTASTICD_CONFIG_DIR/available.d/" 2>/dev/null || true
        local deployed
        deployed=$(ls -1 "$MESHTASTICD_CONFIG_DIR/available.d/"*.yaml 2>/dev/null | wc -l)
        echo -e "  ${GREEN}✓ Deployed ${deployed} hardware templates to available.d/${NC}"
    else
        echo -e "  ${YELLOW}⚠ templates/available.d/ not found in $INSTALL_DIR${NC}"
    fi
}

detect_os_repo() {
    # Detect OS and return the correct OpenSUSE Build Service repo name
    # Returns: Debian_12, Debian_13, Raspbian_12, Ubuntu_24.04, etc.

    if [[ ! -f /etc/os-release ]]; then
        echo "Debian_12"  # Fallback
        return
    fi

    # shellcheck source=/dev/null
    source /etc/os-release

    local os_name="${ID}"
    local version="${VERSION_ID:-}"
    local version_codename="${VERSION_CODENAME:-}"

    case "$os_name" in
        debian)
            case "$version" in
                12) echo "Debian_12" ;;
                13) echo "Debian_13" ;;
                *)
                    # Use codename for sid/testing
                    case "$version_codename" in
                        bookworm) echo "Debian_12" ;;
                        trixie)   echo "Debian_13" ;;
                        sid)      echo "Debian_Unstable" ;;
                        *)        echo "Debian_Testing" ;;
                    esac
                    ;;
            esac
            ;;
        raspbian)
            case "$version" in
                12) echo "Raspbian_12" ;;
                11) echo "Raspbian_11" ;;
                *)  echo "Raspbian_12" ;;  # Fallback to latest supported
            esac
            ;;
        ubuntu)
            case "$version" in
                24.04|24.10) echo "xUbuntu_24.04" ;;
                22.04)       echo "xUbuntu_22.04" ;;
                20.04)       echo "xUbuntu_20.04" ;;
                *)           echo "xUbuntu_24.04" ;;  # Fallback to latest
            esac
            ;;
        *)
            # Fallback: try Debian version if it's a Debian derivative
            if [[ -n "$version" ]]; then
                echo "Debian_${version%%.*}"
            else
                echo "Debian_12"
            fi
            ;;
    esac
}

add_meshtastic_repo() {
    # Add OpenSUSE Build Service meshtastic repo for the detected OS
    local os_repo
    os_repo=$(detect_os_repo)

    echo -e "  ${CYAN}Adding Meshtastic repo for ${BOLD}${os_repo}${NC}"

    local repo_url="${OBS_BASE_URL}/${os_repo}/"
    local key_url="${OBS_BASE_URL}/${os_repo}/Release.key"

    # Add repo
    echo "deb ${repo_url} /" > /etc/apt/sources.list.d/meshtastic.list

    # Add GPG key
    curl -fsSL "$key_url" | gpg --dearmor > /etc/apt/trusted.gpg.d/meshtastic.gpg 2>/dev/null

    # Update apt cache
    if ! apt-get update -qq 2>/dev/null; then
        echo -e "  ${YELLOW}Warning: apt update had errors (repo may be unavailable)${NC}"
        return 1
    fi

    return 0
}

# ─────────────────────────────────────────────────────────────────
# Detect existing installations
# ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}[1/8] Checking existing installations...${NC}"

MESHTASTICD_EXISTS=false
RNS_EXISTS=false
MESHFORGE_EXISTS=false

if systemctl list-unit-files | grep -q meshtasticd; then
    MESHTASTICD_EXISTS=true
    echo -e "  ${YELLOW}⚡ meshtasticd already installed${NC}"
fi

if command -v rnsd &> /dev/null; then
    RNS_EXISTS=true
    echo -e "  ${YELLOW}⚡ Reticulum (RNS) already installed${NC}"
fi

if [[ -d "$INSTALL_DIR" ]]; then
    MESHFORGE_EXISTS=true
    echo -e "  ${YELLOW}⚡ MeshForge already installed${NC}"
fi

if ! $MESHTASTICD_EXISTS && ! $RNS_EXISTS && ! $MESHFORGE_EXISTS; then
    echo -e "  ${GREEN}✓ Fresh installation${NC}"
fi

# ─────────────────────────────────────────────────────────────────
# Handle existing meshtasticd
# ─────────────────────────────────────────────────────────────────
if $MESHTASTICD_EXISTS && $INSTALL_MESHTASTICD; then
    echo ""
    echo -e "${YELLOW}═══ Existing meshtasticd Detected ═══${NC}"
    echo ""
    echo "  MeshForge can work in different modes:"
    echo ""
    echo -e "  ${BOLD}1)${NC} Take ownership ${GREEN}(Recommended)${NC}"
    echo "     MeshForge manages meshtasticd as part of NOC stack"
    echo ""
    echo -e "  ${BOLD}2)${NC} Connect as client"
    echo "     Use existing meshtasticd without managing it"
    echo ""
    echo -e "  ${BOLD}3)${NC} Skip meshtasticd setup"
    echo "     Install MeshForge only, configure later"
    echo ""
    echo -e "  ${BOLD}q)${NC} Quit installer"
    echo ""

    if [[ -c /dev/tty ]]; then
        read -p "  Select mode [1/2/3/q] (default: 1): " -n 1 -r mode_choice < /dev/tty
        echo ""
        case $mode_choice in
            2)
                INSTALL_MESHTASTICD=false
                echo -e "  ${CYAN}→ Client mode selected${NC}"
                ;;
            3)
                INSTALL_MESHTASTICD=false
                echo -e "  ${CYAN}→ Skipping meshtasticd setup${NC}"
                ;;
            q|Q|0)
                echo -e "  ${YELLOW}→ Installation cancelled${NC}"
                exit 0
                ;;
            *)
                echo -e "  ${GREEN}→ Taking ownership of meshtasticd${NC}"
                ;;
        esac
    fi
fi

# ─────────────────────────────────────────────────────────────────
# System dependencies
# ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}[2/8] Installing system dependencies...${NC}"

# Show detected OS
OS_REPO=$(detect_os_repo)
if [[ -f /etc/os-release ]]; then
    # shellcheck source=/dev/null
    source /etc/os-release
    echo -e "  ${CYAN}Detected: ${BOLD}${PRETTY_NAME:-$ID}${NC} → repo: ${BOLD}${OS_REPO}${NC}"
fi

apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    python3-msgpack \
    git wget curl gnupg \
    libusb-1.0-0 \
    mosquitto mosquitto-clients \
    tmux \
    &>/dev/null
# python3-msgpack is needed for NomadNet peer cache reading in
# src/utils/_map_collector_rns.py. Without it, meshforge-map logs
# "msgpack not available for NomadNet peer reading" every collect
# cycle and silently skips the peer-cache code path. Installed via
# apt (not pip) so the venv — which uses include-system-site-packages
# — picks it up without needing --break-system-packages in the venv.

echo -e "  ${GREEN}✓ System dependencies installed${NC}"

# ─────────────────────────────────────────────────────────────────
# Detect PEP 668 (externally-managed-environment)
# ─────────────────────────────────────────────────────────────────
PIP_ARGS="--timeout 60"
# Check for EXTERNALLY-MANAGED file (Debian Bookworm, RPi OS)
if ls /usr/lib/python3*/EXTERNALLY-MANAGED 1>/dev/null 2>&1; then
    echo -e "${YELLOW}  Detected: Externally managed Python (PEP 668)${NC}"
    PIP_ARGS="--break-system-packages --timeout 60"
fi

# ─────────────────────────────────────────────────────────────────
# Fix known ALSA udev packaging bug (RPi OS)
# 90-alsa-restore.rules may have GOTO targets with no matching LABEL
# ─────────────────────────────────────────────────────────────────
ALSA_RULES="/usr/lib/udev/rules.d/90-alsa-restore.rules"
if [[ -f "$ALSA_RULES" ]] && [[ ! -f /etc/udev/rules.d/90-alsa-restore.rules ]]; then
    NEEDS_FIX=false
    while IFS= read -r goto_label; do
        if ! grep -q "LABEL=\"$goto_label\"" "$ALSA_RULES"; then
            NEEDS_FIX=true
            break
        fi
    done < <(grep -oP 'GOTO="\K[^"]+' "$ALSA_RULES" | sort -u)

    if $NEEDS_FIX; then
        echo -e "  ${YELLOW}Fixing broken ALSA udev rules (RPi OS packaging bug)...${NC}"
        cp "$ALSA_RULES" /etc/udev/rules.d/90-alsa-restore.rules
        # Append missing LABEL declarations before EOF
        while IFS= read -r goto_label; do
            if ! grep -q "LABEL=\"$goto_label\"" "$ALSA_RULES"; then
                echo "LABEL=\"$goto_label\"" >> /etc/udev/rules.d/90-alsa-restore.rules
                echo "    Added missing LABEL=\"$goto_label\""
            fi
        done < <(grep -oP 'GOTO="\K[^"]+' "$ALSA_RULES" | sort -u)
        udevadm control --reload-rules 2>/dev/null || true
        echo -e "  ${GREEN}ALSA udev rules fixed${NC}"
    fi
fi

# ─────────────────────────────────────────────────────────────────
# Install meshtasticd (auto-detect USB vs SPI)
# ─────────────────────────────────────────────────────────────────
if $INSTALL_MESHTASTICD; then
    echo -e "${CYAN}[3/8] Installing meshtasticd...${NC}"

    # Create udev rules first (needed for detection)
    if [[ ! -f /etc/udev/rules.d/99-meshtastic.rules ]]; then
        echo "  Creating udev rules for radio devices..."
        cat > /etc/udev/rules.d/99-meshtastic.rules << 'UDEV_RULES'
# Meshtastic USB devices
# T-Beam, Heltec, RAK, etc.

# Silicon Labs CP210x
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", MODE="0666", GROUP="dialout"

# CH340/CH341 (USB serial AND USB-to-SPI for Meshtoad)
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", MODE="0666", GROUP="dialout"
SUBSYSTEM=="usb", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="5512", MODE="0666", GROUP="dialout"

# FTDI
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6001", MODE="0666", GROUP="dialout"

# ESP32 native USB
SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", ATTRS{idProduct}=="1001", MODE="0666", GROUP="dialout"

# SPI device permissions (for native meshtasticd)
SUBSYSTEM=="spidev", MODE="0666", GROUP="spi"
UDEV_RULES

        udevadm control --reload-rules
        udevadm trigger
        sleep 1
    fi

    # Load CH341 driver if present (Meshtoad)
    load_ch341_driver

    # Determine radio type - ask user with hardware hints
    if $FORCE_NATIVE; then
        RADIO_TYPE="spi"
        echo -e "  ${CYAN}Radio type forced: ${BOLD}spi${NC} (--force-native)"
    elif $FORCE_PYTHON; then
        RADIO_TYPE="usb"
        echo -e "  ${CYAN}Radio type forced: ${BOLD}usb${NC} (--force-python)"
    else
        RADIO_TYPE=$(ask_radio_type)
    fi

    echo -e "  ${CYAN}Selected radio type: ${BOLD}${RADIO_TYPE}${NC}"

    # Create meshtasticd config directory structure (if not exists)
    echo "  Creating /etc/meshtasticd/ structure..."
    mkdir -p "$MESHTASTICD_CONFIG_DIR"/{available.d,config.d,ssl}
    chmod 700 "$MESHTASTICD_CONFIG_DIR/ssl"

    # MeshForge hardware templates are deployed AFTER apt install meshtasticd
    # (via deploy_meshforge_templates) so they overwrite dpkg's copies which
    # lack proper description comments. See Issue #33: whiptail non-interactive.

    # Deploy config.yaml if missing
    if [[ ! -f "$MESHTASTICD_CONFIG_DIR/config.yaml" ]]; then
        if [[ -f "$INSTALL_DIR/templates/config.yaml" ]]; then
            cp "$INSTALL_DIR/templates/config.yaml" "$MESHTASTICD_CONFIG_DIR/config.yaml"
            echo -e "  ${GREEN}✓ Created config.yaml${NC}"
        fi
    else
        echo -e "  ${GREEN}✓ config.yaml already exists${NC}"
    fi

    echo -e "  ${CYAN}Available hardware configs:${NC}"
    if ls "$MESHTASTICD_CONFIG_DIR/available.d/"*.yaml 2>/dev/null | head -5 >/dev/null; then
        ls -1 "$MESHTASTICD_CONFIG_DIR/available.d/"*.yaml 2>/dev/null | head -10 | xargs -I {} basename {} | sed 's/^/    - /'
        AVAIL_COUNT=$(ls -1 "$MESHTASTICD_CONFIG_DIR/available.d/"*.yaml 2>/dev/null | wc -l)
        if [[ "$AVAIL_COUNT" -gt 10 ]]; then
            echo "    ... and $((AVAIL_COUNT - 10)) more"
        fi
    else
        echo -e "  ${YELLOW}⚠ No templates deployed — TUI will generate from built-in list${NC}"
    fi

    # Install appropriate daemon based on radio type
    case "$RADIO_TYPE" in
        spi)
            echo -e "  ${CYAN}Installing native meshtasticd for SPI radio...${NC}"

            NATIVE_INSTALLED=false

            # Check if already installed
            if command -v meshtasticd &>/dev/null; then
                INSTALLED_VERSION=$(meshtasticd --version 2>/dev/null || echo "unknown")
                echo -e "  ${GREEN}✓ Native meshtasticd already installed (${INSTALLED_VERSION})${NC}"
                NATIVE_INSTALLED=true
                deploy_meshforge_templates
            else
                # Add OpenSUSE Build Service repo and install via apt
                if add_meshtastic_repo; then
                    echo -e "  ${CYAN}Installing meshtasticd via apt (this may take a minute)...${NC}"
                    if apt-get install -y -qq meshtasticd >/dev/null 2>&1; then
                        # Verify it actually installed
                        if command -v meshtasticd &>/dev/null; then
                            INSTALLED_VERSION=$(meshtasticd --version 2>/dev/null || echo "unknown")
                            echo -e "  ${GREEN}✓ Native meshtasticd installed (${INSTALLED_VERSION})${NC}"
                            NATIVE_INSTALLED=true
                            deploy_meshforge_templates
                        else
                            echo -e "  ${RED}Package installed but binary not found${NC}"
                        fi
                    else
                        echo -e "  ${RED}Failed to install meshtasticd via apt${NC}"
                    fi
                else
                    echo -e "  ${RED}Failed to add Meshtastic repo${NC}"
                fi

                # If native install failed, fall back to Python CLI
                if ! $NATIVE_INSTALLED; then
                    echo -e "  ${YELLOW}Native meshtasticd required for SPI radios${NC}"
                    echo -e "  ${YELLOW}Install from: https://meshtastic.org/docs/software/linux-native/${NC}"
                    pip3 install $PIP_ARGS --ignore-installed -q meshtastic paho-mqtt 'cryptography>=45.0.7,<47' 'pyopenssl>=25.3.0'

                    # Create placeholder service explaining the requirement
                    cat > /etc/systemd/system/meshtasticd.service << 'SPI_NEEDS_NATIVE'
[Unit]
Description=Meshtastic (Native daemon required for SPI)
Documentation=https://meshtastic.org/docs/software/linux-native/

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/echo "SPI radios require native meshtasticd. Install from meshtastic.org then run: meshforge"

[Install]
WantedBy=multi-user.target
SPI_NEEDS_NATIVE
                    DAEMON_TYPE="spi-pending"
                    RADIO_TYPE="spi"  # Mark as SPI mode needing native daemon

                    # Only create config.yaml if it doesn't exist (meshtasticd package provides it)
                    if [[ ! -f "$MESHTASTICD_CONFIG_DIR/config.yaml" ]]; then
                        cat > "$MESHTASTICD_CONFIG_DIR/config.yaml" << 'FALLBACK_CONFIG'
---
Lora:
  # Module: auto  # Disabled — select hardware via TUI or copy template to config.d/

Logging:
  LogLevel: info

Webserver:
  Port: 9443
  RootPath: /usr/share/meshtasticd/web

General:
  MaxNodes: 200
  MaxMessageQueue: 100
  ConfigDirectory: /etc/meshtasticd/config.d/
  AvailableDirectory: /etc/meshtasticd/available.d/
FALLBACK_CONFIG
                        echo -e "  ${GREEN}✓ Created placeholder config.yaml${NC}"
                    fi

                    # User needs to install native meshtasticd manually
                    echo -e "  ${YELLOW}After installing meshtasticd, run 'meshforge' to select your HAT${NC}"
                fi
            fi

            # Only configure SPI if native binary is installed
            if $NATIVE_INSTALLED; then
                MESHTASTICD_BIN=$(command -v meshtasticd)
                echo -e "  ${GREEN}✓ Binary at: ${MESHTASTICD_BIN}${NC}"

                # ── Step 1: Check SPI bus is enabled ──
                echo -e "  ${CYAN}Checking SPI bus...${NC}"
                SPI_ENABLED=false
                SPI_NEEDS_REBOOT=false

                if [[ -e /dev/spidev0.0 ]] || [[ -e /dev/spidev0.1 ]]; then
                    echo -e "  ${GREEN}✓ SPI bus active (/dev/spidev0.*)${NC}"
                    SPI_ENABLED=true
                else
                    # SPI device not present - check boot config
                    BOOT_CONFIG=""
                    if [[ -f /boot/firmware/config.txt ]]; then
                        BOOT_CONFIG="/boot/firmware/config.txt"
                    elif [[ -f /boot/config.txt ]]; then
                        BOOT_CONFIG="/boot/config.txt"
                    fi

                    if [[ -n "$BOOT_CONFIG" ]]; then
                        if grep -q "^dtparam=spi=on" "$BOOT_CONFIG" 2>/dev/null; then
                            echo -e "  ${YELLOW}⚠ SPI enabled in config but device not loaded${NC}"
                            echo -e "  ${YELLOW}  A reboot is required to activate SPI${NC}"
                            SPI_NEEDS_REBOOT=true
                        else
                            echo -e "  ${YELLOW}⚠ SPI not enabled in ${BOOT_CONFIG}${NC}"
                            echo -e "  ${CYAN}  Enabling SPI...${NC}"

                            # Enable SPI in boot config
                            if grep -q "^#dtparam=spi=on" "$BOOT_CONFIG" 2>/dev/null; then
                                # Uncomment existing line
                                sed -i 's/^#dtparam=spi=on/dtparam=spi=on/' "$BOOT_CONFIG"
                            else
                                # Add to [all] section or end of file
                                echo "" >> "$BOOT_CONFIG"
                                echo "# SPI enabled by MeshForge for LoRa HAT" >> "$BOOT_CONFIG"
                                echo "dtparam=spi=on" >> "$BOOT_CONFIG"
                            fi
                            echo -e "  ${GREEN}✓ SPI enabled in ${BOOT_CONFIG}${NC}"
                            SPI_NEEDS_REBOOT=true
                        fi
                    else
                        echo -e "  ${YELLOW}⚠ Cannot find boot config to enable SPI${NC}"
                        echo -e "  ${YELLOW}  Run: sudo raspi-config → Interface Options → SPI${NC}"
                    fi

                    if $SPI_NEEDS_REBOOT; then
                        echo ""
                        echo -e "  ${YELLOW}╔════════════════════════════════════════════════════╗${NC}"
                        echo -e "  ${YELLOW}║  REBOOT REQUIRED to activate SPI bus              ║${NC}"
                        echo -e "  ${YELLOW}║  After reboot, re-run this installer to continue  ║${NC}"
                        echo -e "  ${YELLOW}╚════════════════════════════════════════════════════╝${NC}"
                        echo ""
                        echo -e "  ${CYAN}Run: sudo reboot${NC}"
                        echo -e "  ${CYAN}Then: sudo bash /opt/meshforge/scripts/install_noc.sh${NC}"
                        echo ""

                        # Still create minimal config and service so re-run picks up where we left off
                        if [[ ! -f "$MESHTASTICD_CONFIG_DIR/config.yaml" ]]; then
                            cat > "$MESHTASTICD_CONFIG_DIR/config.yaml" << 'REBOOT_CONFIG'
---
Lora:
  # Module: auto  # Disabled — select hardware via TUI or copy template to config.d/

Logging:
  LogLevel: info

Webserver:
  Port: 9443
  RootPath: /usr/share/meshtasticd/web

General:
  MaxNodes: 200
  MaxMessageQueue: 100
  ConfigDirectory: /etc/meshtasticd/config.d/
  AvailableDirectory: /etc/meshtasticd/available.d/
REBOOT_CONFIG
                        fi
                        DAEMON_TYPE="spi-reboot-needed"
                        # Skip HAT selection and service start until after reboot
                        SPI_ENABLED=false
                    fi
                fi

                # ── Step 2: HAT selection (only if SPI is active) ──
                if $SPI_ENABLED; then
                    echo -e "  ${CYAN}Selecting SPI HAT configuration...${NC}"

                    AVAIL_DIR="$MESHTASTICD_CONFIG_DIR/available.d"
                    HAT_SELECTED=false

                    # Check if a HAT config is already in config.d/
                    EXISTING_HAT=""
                    if [[ -d "$MESHTASTICD_CONFIG_DIR/config.d" ]]; then
                        EXISTING_HAT=$(ls -1 "$MESHTASTICD_CONFIG_DIR/config.d/"*.yaml 2>/dev/null | head -1)
                    fi

                    if [[ -n "$EXISTING_HAT" ]]; then
                        HAT_NAME=$(basename "$EXISTING_HAT")
                        echo -e "  ${GREEN}✓ HAT config already active: ${HAT_NAME}${NC}"
                        HAT_SELECTED=true
                    elif [[ -d "$AVAIL_DIR" ]] && ls -1 "$AVAIL_DIR/"*.yaml &>/dev/null; then
                        # Build HAT menu from available.d/ — SPI templates only
                        declare -a HAT_OPTIONS=()
                        declare -A HAT_TAG_MAP=()
                        while IFS= read -r hat_file; do
                            # Filter: only show SPI templates (skip USB, display)
                            [[ "$(classify_template "$hat_file")" != "spi" ]] && continue
                            hat_base=$(basename "$hat_file" .yaml)
                            hat_desc=$(get_template_description "$hat_file")
                            # Sanitize tag: prefix if starts with '-' (whiptail flag confusion)
                            tag="$hat_base"
                            [[ "$tag" == -* ]] && tag="f:$tag"
                            HAT_TAG_MAP["$tag"]="$hat_base"
                            HAT_OPTIONS+=("$tag" "$hat_desc")
                        done < <(ls -1 "$AVAIL_DIR/"*.yaml 2>/dev/null | sort)

                        if [[ ${#HAT_OPTIONS[@]} -gt 0 ]]; then
                            HAT_COUNT=$((${#HAT_OPTIONS[@]} / 2))
                            SELECTED_HAT=""

                            if command -v whiptail &>/dev/null; then
                                MENU_H=$((HAT_COUNT + 7))
                                [[ $MENU_H -lt 12 ]] && MENU_H=12
                                [[ $MENU_H -gt 22 ]] && MENU_H=22
                                # Cap list-height to available dialog space
                                LIST_H=$((MENU_H - 8))
                                [[ $LIST_H -gt $HAT_COUNT ]] && LIST_H=$HAT_COUNT
                                [[ $LIST_H -lt 1 ]] && LIST_H=$HAT_COUNT

                                flush_terminal_input
                                SELECTED_HAT=$(whiptail --title "SPI HAT Selection" \
                                    --menu \
                                    "Which LoRa HAT is connected to this Pi?\n\nConfigs from: ${AVAIL_DIR}/" \
                                    $MENU_H 70 $LIST_H \
                                    -- \
                                    "${HAT_OPTIONS[@]}" \
                                    3>&1 1>&2 2>&3) || SELECTED_HAT=""

                                # Resolve tag back to original filename stem
                                if [[ -n "$SELECTED_HAT" ]] && [[ -n "${HAT_TAG_MAP[$SELECTED_HAT]+x}" ]]; then
                                    SELECTED_HAT="${HAT_TAG_MAP[$SELECTED_HAT]}"
                                fi
                            fi

                            # Fallback: text menu (if whiptail unavailable or failed)
                            if [[ -z "$SELECTED_HAT" ]]; then
                                if command -v whiptail &>/dev/null; then
                                    echo -e "  ${YELLOW}Dialog failed or cancelled — trying text menu...${NC}" >&2
                                fi
                                echo "" >&2
                                echo -e "  ${BOLD}Select your SPI HAT:${NC}" >&2
                                i=1
                                for ((idx=0; idx<${#HAT_OPTIONS[@]}; idx+=2)); do
                                    echo "    $i) ${HAT_OPTIONS[$((idx+1))]}" >&2
                                    ((i++))
                                done
                                echo "" >&2
                                read -rp "  Select [1-${HAT_COUNT}]: " hat_choice || true
                                if [[ "$hat_choice" =~ ^[0-9]+$ ]] && [[ "$hat_choice" -ge 1 ]] && [[ "$hat_choice" -le "$HAT_COUNT" ]]; then
                                    idx=$(( (hat_choice - 1) * 2 ))
                                    sel_tag="${HAT_OPTIONS[$idx]}"
                                    SELECTED_HAT="${HAT_TAG_MAP[$sel_tag]:-${sel_tag#f:}}"
                                fi
                            fi

                            if [[ -n "$SELECTED_HAT" ]]; then
                                # Copy selected HAT config to config.d/
                                mkdir -p "$MESHTASTICD_CONFIG_DIR/config.d"
                                cp "$AVAIL_DIR/${SELECTED_HAT}.yaml" "$MESHTASTICD_CONFIG_DIR/config.d/"
                                echo -e "  ${GREEN}✓ HAT config installed: ${SELECTED_HAT}.yaml${NC}"
                                HAT_SELECTED=true
                            else
                                echo -e "  ${YELLOW}⚠ No HAT selected - meshtasticd may not start correctly${NC}"
                                echo -e "  ${YELLOW}  Fix: cp /etc/meshtasticd/available.d/<your-hat>.yaml /etc/meshtasticd/config.d/${NC}"
                            fi
                        else
                            echo -e "  ${YELLOW}⚠ No SPI HAT templates found (all templates are USB/display)${NC}"
                            echo -e "  ${YELLOW}  Copy a SPI template to /etc/meshtasticd/config.d/ manually${NC}"
                        fi
                    else
                        echo -e "  ${YELLOW}⚠ No HAT templates found in ${AVAIL_DIR}/${NC}"
                        echo -e "  ${YELLOW}  Templates are provided by the meshtasticd package${NC}"
                        echo -e "  ${YELLOW}  Create config manually in /etc/meshtasticd/config.d/${NC}"
                    fi

                    # ── Step 3: Verify config.yaml exists (meshtasticd package provides it) ──
                    if [[ -f "$MESHTASTICD_CONFIG_DIR/config.yaml" ]]; then
                        if grep -q "Webserver:" "$MESHTASTICD_CONFIG_DIR/config.yaml" 2>/dev/null; then
                            echo -e "  ${GREEN}✓ config.yaml valid (Webserver section present)${NC}"
                        else
                            echo -e "  ${YELLOW}⚠ config.yaml missing Webserver section - adding${NC}"
                            cat >> "$MESHTASTICD_CONFIG_DIR/config.yaml" << 'ADD_WEBSERVER'

Webserver:
  Port: 9443
  RootPath: /usr/share/meshtasticd/web
ADD_WEBSERVER
                            echo -e "  ${GREEN}✓ Added Webserver section to config.yaml${NC}"
                        fi
                    else
                        echo -e "  ${CYAN}Creating fallback config.yaml...${NC}"
                        cat > "$MESHTASTICD_CONFIG_DIR/config.yaml" << 'SPI_CONFIG'
---
Lora:
  # Module: auto  # Disabled — select hardware via TUI or copy template to config.d/

Logging:
  LogLevel: info

Webserver:
  Port: 9443
  RootPath: /usr/share/meshtasticd/web

General:
  MaxNodes: 200
  MaxMessageQueue: 100
  ConfigDirectory: /etc/meshtasticd/config.d/
  AvailableDirectory: /etc/meshtasticd/available.d/
SPI_CONFIG
                        echo -e "  ${GREEN}✓ Created config.yaml${NC}"
                    fi

                    # ── Step 4: Create systemd service ──
                    if [[ -f "$INSTALL_DIR/templates/systemd/meshtasticd-native.service" ]]; then
                        sed "s|@MESHTASTICD_BIN@|${MESHTASTICD_BIN}|g" \
                            "$INSTALL_DIR/templates/systemd/meshtasticd-native.service" \
                            > /etc/systemd/system/meshtasticd.service
                    else
                        cat > /etc/systemd/system/meshtasticd.service << NATIVE_SERVICE
[Unit]
Description=Meshtastic Daemon (Native SPI)
Documentation=https://meshtastic.org
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/etc/meshtasticd
ExecStart=${MESHTASTICD_BIN} -c /etc/meshtasticd/config.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
NATIVE_SERVICE
                    fi

                    systemctl daemon-reload

                    # ── Step 5: Start meshtasticd ──
                    echo -e "  ${CYAN}Starting meshtasticd...${NC}"
                    systemctl enable meshtasticd 2>/dev/null
                    systemctl restart meshtasticd

                    # ── Step 6: Verify service is running ──
                    echo -e "  ${CYAN}Verifying meshtasticd...${NC}"
                    sleep 3  # Give it time to start

                    if systemctl is-active --quiet meshtasticd; then
                        echo -e "  ${GREEN}✓ meshtasticd is running${NC}"

                        # Check TCP port 4403
                        SPI_VERIFY_OK=false
                        for attempt in 1 2 3; do
                            if timeout 2 bash -c "echo >/dev/tcp/localhost/4403" 2>/dev/null; then
                                echo -e "  ${GREEN}✓ TCP port 4403 responding${NC}"
                                SPI_VERIFY_OK=true
                                break
                            fi
                            sleep 2
                        done

                        if ! $SPI_VERIFY_OK; then
                            echo -e "  ${YELLOW}⚠ Port 4403 not responding yet (may need more time)${NC}"
                            echo -e "  ${YELLOW}  Check: sudo journalctl -u meshtasticd -f${NC}"
                        fi

                        # Web client on port 9443 (HTTPS)
                        echo -e "  ${GREEN}✓ Web client available on port 9443${NC}"
                    else
                        echo -e "  ${RED}✗ meshtasticd failed to start${NC}"
                        echo -e "  ${YELLOW}  Check logs: sudo journalctl -u meshtasticd --no-pager -n 20${NC}"
                        if ! $HAT_SELECTED; then
                            echo -e "  ${YELLOW}  Likely cause: No HAT config in /etc/meshtasticd/config.d/${NC}"
                        fi
                    fi

                    DAEMON_TYPE="native"
                else
                    # SPI not enabled / needs reboot - service not started
                    DAEMON_TYPE=${DAEMON_TYPE:-"spi-pending"}
                fi
            fi
            ;;

        usb)
            echo -e "  ${CYAN}Installing for USB serial radio...${NC}"

            # Install meshtastic Python package for CLI tools
            pip3 install $PIP_ARGS --ignore-installed -q meshtastic paho-mqtt 'cryptography>=45.0.7,<47' 'pyopenssl>=25.3.0'

            USB_DEV=$(get_usb_device)
            echo -e "  ${GREEN}✓ Python meshtastic CLI installed${NC}"
            if [[ -n "$USB_DEV" ]]; then
                echo -e "  ${GREEN}  USB device: $USB_DEV${NC}"
            fi

            NATIVE_INSTALLED=false

            # Check if native meshtasticd is already installed
            if command -v meshtasticd &>/dev/null; then
                INSTALLED_VERSION=$(meshtasticd --version 2>/dev/null || echo "unknown")
                echo -e "  ${GREEN}✓ Native meshtasticd already installed (${INSTALLED_VERSION})${NC}"
                NATIVE_INSTALLED=true
                deploy_meshforge_templates
            else
                # Install native meshtasticd via apt (same as SPI path)
                if add_meshtastic_repo; then
                    echo -e "  ${CYAN}Installing meshtasticd via apt...${NC}"
                    if apt-get install -y -qq meshtasticd >/dev/null 2>&1; then
                        if command -v meshtasticd &>/dev/null; then
                            INSTALLED_VERSION=$(meshtasticd --version 2>/dev/null || echo "unknown")
                            echo -e "  ${GREEN}✓ Native meshtasticd installed (${INSTALLED_VERSION})${NC}"
                            NATIVE_INSTALLED=true
                            deploy_meshforge_templates
                        else
                            echo -e "  ${RED}Package installed but binary not found${NC}"
                        fi
                    else
                        echo -e "  ${YELLOW}⚠ apt install meshtasticd failed — will use USB templates only${NC}"
                        deploy_meshforge_templates
                    fi
                else
                    echo -e "  ${YELLOW}⚠ Could not add Meshtastic repo — will use USB templates only${NC}"
                    deploy_meshforge_templates
                fi
            fi

            if $NATIVE_INSTALLED; then
                MESHTASTICD_BIN=$(command -v meshtasticd)

                # Let user select their USB hardware template from available.d
                AVAIL_DIR="$MESHTASTICD_CONFIG_DIR/available.d"
                USB_TEMPLATES=()
                for tmpl in "$AVAIL_DIR/"*.yaml; do
                    [[ -f "$tmpl" ]] || continue
                    [[ "$(classify_template "$tmpl")" == "usb" ]] && USB_TEMPLATES+=("$tmpl")
                done

                if [[ ${#USB_TEMPLATES[@]} -gt 0 ]]; then
                    # Check if a USB config is already in config.d/
                    EXISTING_USB=""
                    if [[ -d "$MESHTASTICD_CONFIG_DIR/config.d" ]]; then
                        EXISTING_USB=$(ls -1 "$MESHTASTICD_CONFIG_DIR/config.d/"*.yaml 2>/dev/null | head -1)
                    fi

                    if [[ -n "$EXISTING_USB" ]]; then
                        USB_NAME=$(basename "$EXISTING_USB")
                        echo -e "  ${GREEN}✓ USB config already active: ${USB_NAME}${NC}"
                    else
                        # Build USB menu from available.d/ — USB templates only
                        declare -a USB_OPTIONS=()
                        declare -A USB_TAG_MAP=()
                        for tmpl in "${USB_TEMPLATES[@]}"; do
                            usb_base=$(basename "$tmpl" .yaml)
                            usb_desc=$(get_template_description "$tmpl")
                            tag="$usb_base"
                            [[ "$tag" == -* ]] && tag="f:$tag"
                            USB_TAG_MAP["$tag"]="$usb_base"
                            USB_OPTIONS+=("$tag" "$usb_desc")
                        done

                        if [[ ${#USB_OPTIONS[@]} -gt 0 ]]; then
                            USB_COUNT=$((${#USB_OPTIONS[@]} / 2))
                            SELECTED_USB=""

                            if command -v whiptail &>/dev/null; then
                                MENU_H=$((USB_COUNT + 7))
                                [[ $MENU_H -lt 12 ]] && MENU_H=12
                                [[ $MENU_H -gt 22 ]] && MENU_H=22
                                LIST_H=$((MENU_H - 8))
                                [[ $LIST_H -gt $USB_COUNT ]] && LIST_H=$USB_COUNT
                                [[ $LIST_H -lt 1 ]] && LIST_H=$USB_COUNT

                                flush_terminal_input
                                SELECTED_USB=$(whiptail --title "USB Radio Selection" \
                                    --menu \
                                    "Which USB radio is connected?\n\nConfigs from: ${AVAIL_DIR}/" \
                                    $MENU_H 70 $LIST_H \
                                    -- \
                                    "${USB_OPTIONS[@]}" \
                                    3>&1 1>&2 2>&3) || SELECTED_USB=""

                                # Resolve tag back to filename stem
                                if [[ -n "$SELECTED_USB" ]] && [[ -n "${USB_TAG_MAP[$SELECTED_USB]+x}" ]]; then
                                    SELECTED_USB="${USB_TAG_MAP[$SELECTED_USB]}"
                                fi
                            fi

                            # Fallback: text menu (if whiptail unavailable or failed)
                            if [[ -z "$SELECTED_USB" ]]; then
                                if command -v whiptail &>/dev/null; then
                                    echo -e "  ${YELLOW}Dialog failed or cancelled — trying text menu...${NC}" >&2
                                fi
                                echo "" >&2
                                echo -e "  ${BOLD}Select your USB radio:${NC}" >&2
                                i=1
                                for ((idx=0; idx<${#USB_OPTIONS[@]}; idx+=2)); do
                                    echo "    $i) ${USB_OPTIONS[$((idx+1))]}" >&2
                                    ((i++))
                                done
                                echo "" >&2
                                read -rp "  Select [1-${USB_COUNT}]: " usb_choice || true
                                if [[ "$usb_choice" =~ ^[0-9]+$ ]] && [[ "$usb_choice" -ge 1 ]] && [[ "$usb_choice" -le "$USB_COUNT" ]]; then
                                    idx=$(( (usb_choice - 1) * 2 ))
                                    sel_tag="${USB_OPTIONS[$idx]}"
                                    SELECTED_USB="${USB_TAG_MAP[$sel_tag]:-${sel_tag#f:}}"
                                fi
                            fi

                            if [[ -n "$SELECTED_USB" ]]; then
                                # Copy selected USB config to config.d/
                                mkdir -p "$MESHTASTICD_CONFIG_DIR/config.d"
                                cp "$AVAIL_DIR/${SELECTED_USB}.yaml" "$MESHTASTICD_CONFIG_DIR/config.d/"
                                echo -e "  ${GREEN}✓ USB config installed: ${SELECTED_USB}.yaml${NC}"
                            else
                                echo -e "  ${YELLOW}⚠ No USB radio selected — meshtasticd may not start correctly${NC}"
                                echo -e "  ${YELLOW}  Fix: cp /etc/meshtasticd/available.d/<your-radio>.yaml /etc/meshtasticd/config.d/${NC}"
                            fi
                        fi
                    fi
                fi

                # Deploy systemd service from template or create inline
                if [[ -f "$INSTALL_DIR/templates/systemd/meshtasticd-native.service" ]]; then
                    sed "s|@MESHTASTICD_BIN@|${MESHTASTICD_BIN}|g" \
                        "$INSTALL_DIR/templates/systemd/meshtasticd-native.service" \
                        > /etc/systemd/system/meshtasticd.service
                else
                    cat > /etc/systemd/system/meshtasticd.service << NATIVE_USB_SERVICE
[Unit]
Description=Meshtastic Daemon (USB Serial)
Documentation=https://meshtastic.org
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/etc/meshtasticd
ExecStart=${MESHTASTICD_BIN} -c /etc/meshtasticd/config.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
NATIVE_USB_SERVICE
                fi

                DAEMON_TYPE="native-usb"
            else
                # Native meshtasticd not available
                echo -e "  ${YELLOW}Note: Native meshtasticd not installed${NC}"
                echo -e "  ${YELLOW}  USB templates are available in ${MESHTASTICD_CONFIG_DIR}/available.d/${NC}"
                echo -e "  ${YELLOW}  Install meshtasticd later: sudo apt install meshtasticd${NC}"

                # Don't overwrite a working service with a placeholder
                if systemctl show meshtasticd --property=ExecStart 2>/dev/null | grep -q meshtasticd; then
                    echo -e "  ${GREEN}✓ Existing meshtasticd service is valid — keeping it${NC}"
                    DAEMON_TYPE="native-usb"
                else
                    cat > /etc/systemd/system/meshtasticd.service << 'USB_PLACEHOLDER'
[Unit]
Description=Meshtastic (pending native install)
Documentation=https://meshtastic.org

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/echo "Install native meshtasticd: sudo apt install meshtasticd — then select hardware in MeshForge TUI"

[Install]
WantedBy=multi-user.target
USB_PLACEHOLDER

                    DAEMON_TYPE="usb-pending"
                fi
            fi
            ;;

        none)
            echo -e "  ${YELLOW}⚠ No radio detected${NC}"
            echo -e "  ${YELLOW}  Installing Python meshtastic CLI tools${NC}"

            pip3 install $PIP_ARGS --ignore-installed -q meshtastic paho-mqtt 'cryptography>=45.0.7,<47' 'pyopenssl>=25.3.0'

            # Check if native meshtasticd is available
            if command -v meshtasticd &> /dev/null; then
                MESHTASTICD_BIN=$(command -v meshtasticd)
                echo -e "  ${GREEN}✓ Native meshtasticd available: ${MESHTASTICD_BIN}${NC}"
                echo -e "  ${YELLOW}  Configure hardware in /etc/meshtasticd/config.d/${NC}"

                # Create service using native meshtasticd
                cat > /etc/systemd/system/meshtasticd.service << NATIVE_GENERIC
[Unit]
Description=Meshtastic Daemon
Documentation=https://meshtastic.org
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/etc/meshtasticd
ExecStart=${MESHTASTICD_BIN} -c /etc/meshtasticd/config.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
NATIVE_GENERIC

                DAEMON_TYPE="native"
            else
                echo -e "  ${YELLOW}  Connect USB radio or configure SPI HAT${NC}"

                # Don't overwrite a working service with a placeholder
                if systemctl show meshtasticd --property=ExecStart 2>/dev/null | grep -q meshtasticd; then
                    echo -e "  ${GREEN}✓ Existing meshtasticd service is valid — keeping it${NC}"
                    DAEMON_TYPE="native"
                else
                    cat > /etc/systemd/system/meshtasticd.service << 'NO_RADIO_SERVICE'
[Unit]
Description=Meshtastic (No Radio Configured)
Documentation=https://meshtastic.org

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/echo "No radio detected. Connect USB radio or configure SPI HAT, then run: meshforge"

[Install]
WantedBy=multi-user.target
NO_RADIO_SERVICE

                    DAEMON_TYPE="placeholder"
                fi
            fi
            ;;
    esac

    systemctl daemon-reload

    echo -e "  ${GREEN}✓ meshtasticd installed (${DAEMON_TYPE})${NC}"
    echo -e "  ${GREEN}✓ Config directory: $MESHTASTICD_CONFIG_DIR${NC}"
else
    echo -e "${CYAN}[3/8] Skipping meshtasticd...${NC}"
    echo -e "  ${YELLOW}⊘ Skipped${NC}"
fi

# ─────────────────────────────────────────────────────────────────
# Install Reticulum (RNS)
# ─────────────────────────────────────────────────────────────────
if $INSTALL_RNS; then
    echo -e "${CYAN}[4/8] Installing Reticulum (RNS)...${NC}"

    # Install pipx (needed for NomadNet install via menu)
    if ! command -v pipx &>/dev/null; then
        echo "  Installing pipx..."
        apt-get install -y -qq pipx &>/dev/null
    fi

    # Install the MeshForge-owned RNS/LXMF forks (+ crypto pins) from the pinned
    # requirements file — the single source of truth for the fork git URLs and
    # versions (upstream Carrier Switch, Dec 2025; see requirements/rns.txt).
    pip3 install $PIP_ARGS --ignore-installed -q -r "$INSTALL_DIR/requirements/rns.txt"

    # Born-correct permission foundation (arch-meeting 2026-06-01): rnsd and its
    # RNS tree are owned by the non-root operator user (fleet parity), not root.
    # Ensure that user can reach serial/USB radio hardware (gateways) — systemd
    # reads group membership at service start, so no re-login is needed for rnsd.
    RNSD_USER="$(resolve_operator_user)"
    usermod -aG dialout,plugdev "$RNSD_USER" 2>/dev/null || true

    # Create /etc/reticulum directory structure
    # RNS stores data in a 'storage' subdirectory of its config directory.
    # When using system-wide config (/etc/reticulum/config), rnsd needs
    # the storage directory to exist with proper permissions.
    # Subdirectories required by RNS:
    #   ratchets/  - Identity.persist_job() key ratcheting
    #   resources/ - Reticulum.__init__() resource storage
    #   interfaces/ - Custom RNS interface plugins (operator-managed)
    echo "  Creating /etc/reticulum/ structure..."
    mkdir -p /etc/reticulum/storage/ratchets
    mkdir -p /etc/reticulum/storage/resources
    mkdir -p /etc/reticulum/interfaces
    chmod 755 /etc/reticulum
    chmod 755 /etc/reticulum/storage
    chmod 755 /etc/reticulum/storage/ratchets
    chmod 755 /etc/reticulum/storage/resources
    chmod 755 /etc/reticulum/interfaces

    # Deploy reticulum config — template if none exists, validate if it does
    if [[ ! -f /etc/reticulum/config ]]; then
        if [[ -f "$INSTALL_DIR/templates/reticulum.conf" ]]; then
            cp "$INSTALL_DIR/templates/reticulum.conf" /etc/reticulum/config
            chmod 644 /etc/reticulum/config
            echo -e "  ${GREEN}✓ RNS config deployed (share_instance=Yes)${NC}"
        else
            echo -e "  ${YELLOW}⚠ No config template found — rnsd will create default${NC}"
        fi
    else
        # Existing config — validate share_instance is enabled
        # Without this, rnsd runs but port 37428 never binds
        if grep -q '^\s*share_instance\s*=\s*[Yy]es' /etc/reticulum/config 2>/dev/null || \
           grep -q '^\s*share_instance\s*=\s*[Tt]rue' /etc/reticulum/config 2>/dev/null; then
            echo -e "  Existing config preserved (share_instance=Yes)"
        else
            echo -e "  ${YELLOW}⚠ Existing config has share_instance disabled${NC}"
            if grep -q '^\s*share_instance' /etc/reticulum/config 2>/dev/null; then
                sed -i 's/^\(\s*\)share_instance\s*=.*/\1share_instance = Yes/' /etc/reticulum/config
            elif grep -q '^\[reticulum\]' /etc/reticulum/config 2>/dev/null; then
                sed -i '/^\[reticulum\]/a\  share_instance = Yes' /etc/reticulum/config
            fi
            echo -e "  ${GREEN}✓ Fixed: share_instance = Yes${NC}"
        fi
    fi

    # rpc_key invariant (Issue #41 + rpc_key guard).
    # Every box MUST have its own unique 64-hex rpc_key. Substitute the
    # template's __RPC_KEY__ placeholder (fresh-install path) OR insert a
    # new line under [reticulum] if the config was hand-rolled without one.
    # Never overwrite an existing valid key.
    if [[ -f /etc/reticulum/config ]]; then
        if grep -q '__RPC_KEY__' /etc/reticulum/config 2>/dev/null; then
            RPC_KEY=$(openssl rand -hex 32)
            sed -i "s/__RPC_KEY__/$RPC_KEY/" /etc/reticulum/config
            echo -e "  ${GREEN}✓ Generated unique rpc_key for this box${NC}"
        elif ! grep -Eq '^\s*rpc_key\s*=\s*[0-9a-fA-F]{64}\s*$' /etc/reticulum/config 2>/dev/null; then
            RPC_KEY=$(openssl rand -hex 32)
            sed -i "/^\[reticulum\]/a\\  rpc_key = $RPC_KEY" /etc/reticulum/config
            echo -e "  ${GREEN}✓ Added missing rpc_key (unique per box)${NC}"
        else
            echo -e "  Existing rpc_key preserved"
        fi
    fi

    # Create systemd service (deploy from template or create inline)
    echo "  Setting up rnsd systemd service..."

    # Find rnsd binary — prefer venv (has all dependencies)
    if [[ -x "$VENV_DIR/bin/rnsd" ]]; then
        RNSD_BIN="$VENV_DIR/bin/rnsd"
    else
        RNSD_BIN=$(command -v rnsd 2>/dev/null || echo "/usr/local/bin/rnsd")
    fi

    # System-wide service running as the non-root operator user (fleet parity,
    # arch-meeting 2026-06-01), pinned to the canonical /etc/reticulum configdir
    # so it uses the born-correct, operator-owned RNS tree.
    cat > /etc/systemd/system/rnsd.service << RNSD_SERVICE
[Unit]
Description=Reticulum Network Stack Daemon
Documentation=https://reticulum.network
After=network-online.target meshtasticd.service
Wants=network-online.target

# Stop crash-looping after 5 failures in 60 seconds
# (e.g., NomadNet holding port 37428)
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=${RNSD_USER}
Group=${RNSD_USER}
ExecStart=${RNSD_BIN} --config /etc/reticulum --service
Restart=on-failure
RestartSec=5

StandardOutput=journal
StandardError=journal
SyslogIdentifier=rnsd

[Install]
WantedBy=multi-user.target
RNSD_SERVICE

    # Also deploy user-level service template for non-root setups
    REAL_USER="${SUDO_USER:-$USER}"
    REAL_HOME=$(eval echo "~${REAL_USER}")
    USER_SYSTEMD_DIR="${REAL_HOME}/.config/systemd/user"
    if [[ -d "$INSTALL_DIR/templates/systemd" ]]; then
        mkdir -p "$USER_SYSTEMD_DIR"
        cp "$INSTALL_DIR/templates/systemd/rnsd-user.service" "$USER_SYSTEMD_DIR/rnsd.service" 2>/dev/null || true
        if [[ -f "$INSTALL_DIR/templates/systemd/nomadnet-user.service" ]]; then
            cp "$INSTALL_DIR/templates/systemd/nomadnet-user.service" "$USER_SYSTEMD_DIR/nomadnet.service" 2>/dev/null || true
        fi
        chown -R "${REAL_USER}:" "$USER_SYSTEMD_DIR" 2>/dev/null || true
        echo -e "  ${GREEN}✓ User service templates deployed to ${USER_SYSTEMD_DIR}${NC}"
    fi

    # Born-correct permission foundation: the canonical RNS tree
    # (configdir root:${RNSD_USER} 1775, logfile + storage operator-owned) and the
    # app data-roots, applied via the SSOT — the SAME path the converge/repair flow
    # uses. The installer no longer leans on the TUI _fix_rnsd_user step for this.
    python3 "$INSTALL_DIR/scripts/fleet_foundation.py" apply --user "$RNSD_USER" 2>/dev/null \
        || echo -e "  ${YELLOW}⚠ foundation apply skipped — run: sudo python3 scripts/fleet_foundation.py apply${NC}"

    systemctl daemon-reload
    systemctl enable rnsd 2>/dev/null
    systemctl start rnsd 2>/dev/null
    echo -e "  ${GREEN}✓ rnsd (User=${RNSD_USER}) enabled and started${NC}"

    echo -e "  ${GREEN}✓ Reticulum installed${NC}"
else
    echo -e "${CYAN}[4/8] Skipping Reticulum...${NC}"
    echo -e "  ${YELLOW}⊘ Skipped${NC}"
fi

# ─────────────────────────────────────────────────────────────────
# Install/Update MeshForge
# ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}[5/8] Installing MeshForge...${NC}"

if [[ -d "$INSTALL_DIR" ]]; then
    echo "  Updating existing installation..."
    git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true
    cd "$INSTALL_DIR"
    git pull -q || echo -e "  ${YELLOW}Warning: Could not pull updates${NC}"
else
    echo "  Cloning repository..."
    git clone -q https://github.com/Nursedude/meshforge.git "$INSTALL_DIR"
    git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true
    cd "$INSTALL_DIR"
fi

echo -e "  ${GREEN}✓ MeshForge source ready${NC}"

# ─────────────────────────────────────────────────────────────────
# Python dependencies
# ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}[6/8] Installing Python dependencies...${NC}"

# Use virtual environment
if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv "$VENV_DIR" --system-site-packages
fi

"$VENV_DIR/bin/pip" install -q --timeout 60 --upgrade pip
"$VENV_DIR/bin/pip" install -q --timeout 60 -r requirements.txt

echo -e "  ${GREEN}✓ Python dependencies installed${NC}"

# ─────────────────────────────────────────────────────────────────
# Create NOC configuration
# ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}[7/8] Configuring NOC mode...${NC}"

# Determine mode based on what we installed
NOC_MODE="local"
if ! $INSTALL_MESHTASTICD; then
    NOC_MODE="client"
fi

# Set defaults if not set earlier
DAEMON_TYPE=${DAEMON_TYPE:-"python"}
RADIO_TYPE=${RADIO_TYPE:-"unknown"}
USB_DEV=${USB_DEV:-$(get_usb_device)}

# Create config directory
CONFIG_DIR="/etc/meshforge"
mkdir -p "$CONFIG_DIR"

# Create comprehensive NOC config
cat > "$CONFIG_DIR/noc.yaml" << NOC_CONFIG
# MeshForge NOC Configuration
# Generated by install_noc.sh on $(date)
# Architecture: $ARCH

noc:
  mode: "$NOC_MODE"  # local | client | remote-only
  version: "1.0.0"

  # Radio configuration
  radio:
    type: "$RADIO_TYPE"        # spi | usb | none
    daemon: "$DAEMON_TYPE"     # native | python
    device: "$USB_DEV"         # USB device path (if applicable)
    config_dir: "$MESHTASTICD_CONFIG_DIR"

  # Service management
  services:
    meshtasticd:
      managed: $INSTALL_MESHTASTICD
      auto_start: $INSTALL_MESHTASTICD
      daemon_type: "$DAEMON_TYPE"
      port: 4403

    rnsd:
      managed: $INSTALL_RNS
      auto_start: $INSTALL_RNS

  # Startup behavior
  startup:
    auto_start_services: true
    health_check_interval: 30
    restart_on_failure: true
    max_restart_attempts: 3

  # Paths
  paths:
    install_dir: "$INSTALL_DIR"
    venv_dir: "$VENV_DIR"
    meshtasticd_config: "$MESHTASTICD_CONFIG_DIR"
    meshforge_config: "$CONFIG_DIR"
NOC_CONFIG

echo -e "  ${GREEN}✓ NOC mode: $NOC_MODE${NC}"
echo -e "  ${GREEN}✓ Radio type: $RADIO_TYPE${NC}"
echo -e "  ${GREEN}✓ Daemon type: $DAEMON_TYPE${NC}"

# ─────────────────────────────────────────────────────────────────
# Create system commands and services
# ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}[8/8] Creating system integration...${NC}"

# Main command
cat > /usr/local/bin/meshforge << 'MESHFORGE_CMD'
#!/bin/bash
cd /opt/meshforge
exec sudo /opt/meshforge/venv/bin/python src/launcher.py "$@"
MESHFORGE_CMD
chmod +x /usr/local/bin/meshforge

# NOC orchestrator command
cat > /usr/local/bin/meshforge-noc << 'NOC_CMD'
#!/bin/bash
cd /opt/meshforge/src
exec sudo /opt/meshforge/venv/bin/python -m core.orchestrator "$@"
NOC_CMD
chmod +x /usr/local/bin/meshforge-noc

# LoRa configuration helper
cat > /usr/local/bin/meshforge-lora << 'LORA_CMD'
#!/bin/bash
exec sudo /opt/meshforge/scripts/configure_lora.sh "$@"
LORA_CMD
chmod +x /usr/local/bin/meshforge-lora

# Status command (terminal-native diagnostics)
cat > /usr/local/bin/meshforge-status << 'STATUS_CMD'
#!/bin/bash
cd /opt/meshforge
exec /opt/meshforge/venv/bin/python src/cli/status.py "$@"
STATUS_CMD
chmod +x /usr/local/bin/meshforge-status

# Web client launcher
cat > /usr/local/bin/meshforge-web << 'WEB_CMD'
#!/bin/bash
# Open or display the meshtasticd web client URL
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -z "$LOCAL_IP" ] && LOCAL_IP="localhost"
URL="https://${LOCAL_IP}:9443"

# Check if meshtasticd web server is responding
if timeout 2 bash -c "echo >/dev/tcp/${LOCAL_IP}/9443" 2>/dev/null; then
    echo "Meshtastic Web Client: ${URL}"
    echo ""
    echo "  Full radio configuration in your browser:"
    echo "    • Region, Preset, TX Power (Config → LoRa)"
    echo "    • Channels and PSK keys   (Config → Channels)"
    echo "    • Node name and position   (Config → Device)"
    echo "    • Messaging and map view"
    echo ""
    # Try to open browser (works on desktop, no-op on headless)
    if command -v xdg-open &>/dev/null && [ -n "$DISPLAY" ]; then
        xdg-open "$URL" 2>/dev/null &
        echo "  Opening browser..."
    else
        echo "  Open this URL in any browser on your network:"
        echo "  ${URL}"
    fi
else
    echo "ERROR: meshtasticd web server not responding on port 9443"
    echo ""
    echo "  Check: sudo systemctl status meshtasticd"
    echo "  Start: sudo systemctl start meshtasticd"
    echo ""
    echo "  The web client is served by meshtasticd when running."
    echo "  Config: /etc/meshtasticd/config.yaml (Webserver section)"
fi
WEB_CMD
chmod +x /usr/local/bin/meshforge-web

# MeshForge Map Server command (NOC web UI on port 5000)
cat > /usr/local/bin/meshforge-map << 'MAP_CMD'
#!/bin/bash
# MeshForge Map Server - NOC Web Interface
# Serves the live node map on port 5000

PYTHON_CMD="/opt/meshforge/venv/bin/python"
[ ! -x "$PYTHON_CMD" ] && PYTHON_CMD="python3"

case "${1:-}" in
    start)
        echo "Starting MeshForge Map Server..."
        sudo systemctl start meshforge-map
        ;;
    stop)
        echo "Stopping MeshForge Map Server..."
        sudo systemctl stop meshforge-map
        ;;
    restart)
        echo "Restarting MeshForge Map Server..."
        sudo systemctl restart meshforge-map
        ;;
    status)
        systemctl status meshforge-map --no-pager
        cd /opt/meshforge/src && $PYTHON_CMD -m utils.map_data_service --status
        ;;
    enable)
        echo "Enabling MeshForge Map Server on boot..."
        sudo systemctl enable meshforge-map
        ;;
    disable)
        echo "Disabling MeshForge Map Server on boot..."
        sudo systemctl disable meshforge-map
        ;;
    url)
        LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
        [ -z "$LOCAL_IP" ] && LOCAL_IP="localhost"
        echo "MeshForge Map: http://${LOCAL_IP}:5000/"
        ;;
    *)
        # Default: run interactively (for debugging)
        cd /opt/meshforge/src
        exec $PYTHON_CMD -m utils.map_data_service "$@"
        ;;
esac
MAP_CMD
chmod +x /usr/local/bin/meshforge-map

# Install MeshForge Map Server systemd service
# Map server runs as the operator (not root) so it reads/writes
# ~/.config/meshforge/map_settings.json + ~/.local/share/meshforge/node_history.db
# — same paths the TUI (sudo, $SUDO_USER) uses, eliminating drift.
MESHFORGE_MAP_USER="$(resolve_operator_user)"
if [[ -f "$INSTALL_DIR/scripts/meshforge-map.service" ]]; then
    sed "s/__MESHFORGE_USER__/${MESHFORGE_MAP_USER}/g" \
        "$INSTALL_DIR/scripts/meshforge-map.service" \
        > /etc/systemd/system/meshforge-map.service
    echo -e "  ${GREEN}✓ meshforge-map.service installed (User=${MESHFORGE_MAP_USER})${NC}"
else
    # Inline service definition (fallback) — heredoc unquoted so $MESHFORGE_MAP_USER expands
    cat > /etc/systemd/system/meshforge-map.service << MAP_SERVICE
[Unit]
Description=MeshForge Map Server - NOC Web Interface
Documentation=https://github.com/Nursedude/meshforge
After=network.target meshtasticd.service
Wants=meshtasticd.service

[Service]
Type=simple
User=${MESHFORGE_MAP_USER}
WorkingDirectory=/opt/meshforge/src
RuntimeDirectory=meshforge
ExecStart=/bin/bash -c 'if [ -x /opt/meshforge/venv/bin/python ]; then exec /opt/meshforge/venv/bin/python -m utils.map_data_service --daemon --host 0.0.0.0 --port 5000; else exec python3 -m utils.map_data_service --daemon --host 0.0.0.0 --port 5000; fi'
ExecStop=/bin/kill -TERM \$MAINPID
TimeoutStopSec=10
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal
SyslogIdentifier=meshforge-map

[Install]
WantedBy=multi-user.target
MAP_SERVICE
    echo -e "  ${GREEN}✓ meshforge-map.service created (User=${MESHFORGE_MAP_USER})${NC}"
fi

# Update systemd service to use orchestrator
# Start order: meshtasticd -> rnsd -> meshforge
cat > /etc/systemd/system/meshforge.service << 'MESHFORGE_SERVICE'
[Unit]
Description=MeshForge Mesh Network Operations Center
Documentation=https://github.com/Nursedude/meshforge
After=network.target meshtasticd.service rnsd.service
Wants=meshtasticd.service rnsd.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/meshforge/src
# Orchestrator manages meshtasticd and rnsd (graceful: stay up even if services missing)
ExecStart=/opt/meshforge/venv/bin/python -m core.orchestrator --start --monitor --graceful
ExecStop=/opt/meshforge/venv/bin/python -m core.orchestrator --stop
Restart=on-failure
RestartSec=10

Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
MESHFORGE_SERVICE

systemctl daemon-reload

echo -e "  ${GREEN}✓ System integration complete${NC}"

# Radio hardware already detected and configured above (ask_radio_type + SPI/USB setup)

# ─────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         MeshForge NOC Installation Complete!              ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${CYAN}Installed Components:${NC}"
if $INSTALL_MESHTASTICD; then
    case "$DAEMON_TYPE" in
        native)
            echo -e "  ${GREEN}✓${NC} meshtasticd (native SPI - running)"
            ;;
        native-usb)
            echo -e "  ${GREEN}✓${NC} meshtasticd (native USB serial - running)"
            ;;
        spi-reboot-needed)
            echo -e "  ${YELLOW}⚠${NC} meshtasticd installed (SPI enabled - REBOOT REQUIRED)"
            ;;
        spi-pending)
            echo -e "  ${YELLOW}⚠${NC} meshtasticd not available (native build required for SPI)"
            ;;
        usb-direct)
            echo -e "  ${GREEN}✓${NC} meshtastic CLI (USB radio - no daemon needed)"
            ;;
        placeholder)
            echo -e "  ${YELLOW}⚠${NC} meshtastic CLI (no radio configured yet)"
            ;;
        *)
            echo -e "  ${GREEN}✓${NC} meshtastic (Python CLI)"
            ;;
    esac
fi
if $INSTALL_RNS; then
    echo -e "  ${GREEN}✓${NC} Reticulum (RNS)"
fi
echo -e "  ${GREEN}✓${NC} MeshForge NOC"
echo -e "  ${GREEN}✓${NC} MeshForge Map Server (port 5000)"
echo ""
echo -e "${CYAN}Configuration:${NC}"
echo -e "  NOC Mode:    ${BOLD}$NOC_MODE${NC}"
echo -e "  Radio Type:  ${BOLD}$RADIO_TYPE${NC}"
echo -e "  Daemon:      ${BOLD}$DAEMON_TYPE${NC}"
if [[ -n "$USB_DEV" ]]; then
    echo -e "  USB Device:  ${BOLD}$USB_DEV${NC}"
fi
echo ""
echo -e "${CYAN}Config Files:${NC}"
echo "  /etc/meshforge/noc.yaml           - MeshForge NOC config"
echo "  /etc/meshtasticd/config.yaml      - Meshtasticd config"
echo "  /etc/meshtasticd/available.d/     - Radio templates"
echo "  /etc/meshtasticd/config.d/        - Active configs"
echo ""
echo -e "${CYAN}Commands:${NC}"
echo -e "  ${GREEN}meshforge-status${NC}             - Quick system status (no sudo needed)"
echo -e "  ${GREEN}meshforge-web${NC}                - Open radio web client (config)"
echo -e "  ${GREEN}meshforge-map url${NC}            - Show NOC map URL (port 5000)"
echo -e "  ${GREEN}meshforge-map status${NC}         - Check map server status"
echo -e "  ${GREEN}meshforge${NC}                    - Launch interface wizard"
echo -e "  ${GREEN}meshforge-noc --start${NC}        - Start NOC services"
echo -e "  ${GREEN}meshforge-noc --status${NC}       - Check service status"
echo -e "  ${GREEN}meshforge-noc --stop${NC}         - Stop NOC services"
echo ""
echo -e "${CYAN}Systemd Services:${NC}"
echo -e "  ${GREEN}sudo systemctl enable meshforge${NC}       - Enable NOC on boot"
echo -e "  ${GREEN}sudo systemctl enable meshforge-map${NC}   - Enable Map Server on boot"
echo -e "  ${GREEN}sudo systemctl start meshforge-map${NC}    - Start Map Server now"
echo -e "  ${GREEN}sudo systemctl status meshtasticd${NC}     - Check meshtasticd"
echo ""

# ─────────────────────────────────────────────────────────────────
# SPI Reboot Gate: If SPI was just enabled, stop here cleanly
# ─────────────────────────────────────────────────────────────────
if [[ "$DAEMON_TYPE" == "spi-reboot-needed" ]]; then
    echo ""
    echo -e "${YELLOW}╔═══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║  NEXT STEPS                                               ║${NC}"
    echo -e "${YELLOW}╠═══════════════════════════════════════════════════════════╣${NC}"
    echo -e "${YELLOW}║                                                           ║${NC}"
    echo -e "${YELLOW}║  1. Reboot now:                                           ║${NC}"
    echo -e "${YELLOW}║     ${NC}${GREEN}sudo reboot now${NC}${YELLOW}                                       ║${NC}"
    echo -e "${YELLOW}║                                                           ║${NC}"
    echo -e "${YELLOW}║  2. When back, complete SPI setup:                        ║${NC}"
    echo -e "${YELLOW}║     ${NC}${GREEN}cd /opt/meshforge${NC}${YELLOW}                                     ║${NC}"
    echo -e "${YELLOW}║     ${NC}${GREEN}sudo bash scripts/install_noc.sh${NC}${YELLOW}                      ║${NC}"
    echo -e "${YELLOW}║                                                           ║${NC}"
    echo -e "${YELLOW}║  The second run will:                                     ║${NC}"
    echo -e "${YELLOW}║    • Detect SPI bus is active                             ║${NC}"
    echo -e "${YELLOW}║    • Present HAT selection menu                           ║${NC}"
    echo -e "${YELLOW}║    • Start meshtasticd                                    ║${NC}"
    echo -e "${YELLOW}║    • Verify everything works                              ║${NC}"
    echo -e "${YELLOW}║                                                           ║${NC}"
    echo -e "${YELLOW}╚═══════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${CYAN}Software installed: meshtasticd, RNS, MeshForge${NC}"
    echo -e "${CYAN}SPI enabled in boot config - reboot activates the bus${NC}"
    echo ""
    exit 0
fi

# ─────────────────────────────────────────────────────────────────
# Post-Install Verification (CRITICAL - Issue #23)
# See: .claude/foundations/install_reliability_triage.md
# ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║           Verifying Installation...                       ║${NC}"
echo -e "${CYAN}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""

VERIFY_SCRIPT="$INSTALL_DIR/scripts/verify_post_install.sh"
if [[ -x "$VERIFY_SCRIPT" ]]; then
    if bash "$VERIFY_SCRIPT"; then
        echo ""
        echo -e "${GREEN}✓ Installation verified successfully${NC}"
    else
        VERIFY_EXIT=$?
        echo ""
        if [[ $VERIFY_EXIT -eq 1 ]]; then
            echo -e "${RED}╔═══════════════════════════════════════════════════════════╗${NC}"
            echo -e "${RED}║  WARNING: Installation has critical issues                ║${NC}"
            echo -e "${RED}╚═══════════════════════════════════════════════════════════╝${NC}"
            echo ""
            echo -e "${YELLOW}Review the failures above and fix before proceeding.${NC}"
            echo -e "${YELLOW}Re-run verification: sudo bash $VERIFY_SCRIPT${NC}"
        else
            echo -e "${YELLOW}⚠ Installation has warnings - review above${NC}"
        fi
    fi
else
    echo -e "${YELLOW}⚠ Verification script not found: $VERIFY_SCRIPT${NC}"
    echo -e "${YELLOW}  Skipping verification${NC}"
fi

# Enable and start MeshForge Map Server (NOC web UI)
echo ""
echo -e "${CYAN}Enabling MeshForge Map Server...${NC}"
systemctl daemon-reload
systemctl enable meshforge-map 2>/dev/null || true
systemctl start meshforge-map 2>/dev/null || true

# Check if map server started
sleep 2
if systemctl is-active --quiet meshforge-map; then
    LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
    echo -e "  ${GREEN}✓ Map Server running: http://${LOCAL_IP}:5000/${NC}"
else
    echo -e "  ${YELLOW}⚠ Map Server not running (check: journalctl -u meshforge-map)${NC}"
fi

# Offer to start services (only if services can actually run)
if [[ "$DAEMON_TYPE" != "spi-pending" && "$DAEMON_TYPE" != "placeholder" ]] && [[ -c /dev/tty ]]; then
    echo ""
    echo -e "${CYAN}Would you like to start MeshForge NOC now? [Y/n]${NC}"
    read -r response < /dev/tty
    if [[ ! "$response" =~ ^([nN][oO]|[nN])$ ]]; then
        echo ""
        echo -e "${GREEN}Starting MeshForge NOC...${NC}"
        /usr/local/bin/meshforge-noc --start
        echo ""
        echo -e "${GREEN}NOC is running. Launch interface with: meshforge${NC}"
    fi
fi

# Show web client URL if meshtasticd is running (the primary config interface)
if [[ "$DAEMON_TYPE" == "native" || "$DAEMON_TYPE" == "native-usb" ]]; then
    LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
    if timeout 2 bash -c "echo >/dev/tcp/${LOCAL_IP}/9443" 2>/dev/null; then
        echo ""
        echo -e "${GREEN}╔═══════════════════════════════════════════════════════════╗${NC}"
        echo -e "${GREEN}║  Meshtastic Web Client Ready                              ║${NC}"
        echo -e "${GREEN}╠═══════════════════════════════════════════════════════════╣${NC}"
        echo -e "${GREEN}║                                                           ║${NC}"
        echo -e "${GREEN}║  ${NC}${BOLD}https://${LOCAL_IP}:9443${NC}${GREEN}                               ║${NC}"
        echo -e "${GREEN}║                                                           ║${NC}"
        echo -e "${GREEN}║  Configure your radio:                                    ║${NC}"
        echo -e "${GREEN}║    Config → LoRa     (Region, Preset, TX Power)           ║${NC}"
        echo -e "${GREEN}║    Config → Channels (PSK, channel names)                 ║${NC}"
        echo -e "${GREEN}║    Config → Device   (Node name, position)                ║${NC}"
        echo -e "${GREEN}║                                                           ║${NC}"
        echo -e "${GREEN}║  Also: ${NC}${BOLD}meshforge-web${NC}${GREEN}  (shows this URL anytime)            ║${NC}"
        echo -e "${GREEN}║                                                           ║${NC}"
        echo -e "${GREEN}╚═══════════════════════════════════════════════════════════╝${NC}"
    fi
fi

echo ""
echo -e "${CYAN}Documentation:${NC} https://github.com/Nursedude/meshforge"
echo -e "${CYAN}Made with aloha for the mesh community${NC}"
echo ""
