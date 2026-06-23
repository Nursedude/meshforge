#!/bin/bash
#
# MeshForge - One-Liner Quick Install
#
# LoRa Mesh Network Development & Operations Suite
# Downloads, installs, and launches MeshForge automatically.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/Nursedude/meshforge/main/install.sh | sudo bash
#
# Options:
#   UPGRADE_SYSTEM=yes      - Automatically upgrade all system packages
#   SKIP_UPGRADE=yes        - Skip the system upgrade prompt entirely
#   BREAK_SYSTEM_PACKAGES=yes - Use pip --break-system-packages instead of venv
#   USE_VENV=no             - Same as BREAK_SYSTEM_PACKAGES=yes
#
# Examples:
#   # Install with system upgrade
#   curl -sSL https://raw.githubusercontent.com/Nursedude/meshforge/main/install.sh | sudo UPGRADE_SYSTEM=yes bash
#
#   # Install without upgrade prompt
#   curl -sSL https://raw.githubusercontent.com/Nursedude/meshforge/main/install.sh | sudo SKIP_UPGRADE=yes bash
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}"
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║   MeshForge - LoRa Mesh Network Operations Suite          ║"
echo "║   For Raspberry Pi OS & Linux                             ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}Error: This script must be run as root${NC}"
   echo "Please run: sudo bash install.sh"
   exit 1
fi

# Detect OS and architecture
echo -e "${CYAN}[1/7] Detecting system...${NC}"
OS=$(uname -s)
ARCH=$(uname -m)
echo "  OS: $OS"
echo "  Architecture: $ARCH"

# Check for Raspberry Pi OS
if [[ ! -f /etc/os-release ]]; then
    echo -e "${YELLOW}Warning: Cannot detect OS version${NC}"
fi

# System update
echo -e "${CYAN}[2/7] Updating package lists...${NC}"
apt-get update -qq
echo -e "${GREEN}  ✓ Package lists updated${NC}"

# Optional: Upgrade system packages
# Check if running interactively or if UPGRADE_SYSTEM is set
echo -e "${CYAN}[3/7] System upgrade (optional)...${NC}"
if [[ "${UPGRADE_SYSTEM}" == "yes" ]]; then
    echo -e "${YELLOW}  Upgrading all system packages (this may take several minutes)...${NC}"
    apt-get upgrade -y
    echo -e "${GREEN}  ✓ System upgraded${NC}"
elif [[ "${SKIP_UPGRADE}" == "yes" ]]; then
    echo -e "${YELLOW}  ⊘ Skipped (SKIP_UPGRADE=yes)${NC}"
else
    # Try to read from TTY if available for interactive prompt
    if [[ -c /dev/tty ]]; then
        echo -e "${YELLOW}  This will upgrade all system packages and may take several minutes.${NC}"
        read -p "  Would you like to upgrade system packages now? [y/N] " -n 1 -r < /dev/tty
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            apt-get upgrade -y
            echo -e "${GREEN}  ✓ System upgraded${NC}"
        else
            echo -e "${YELLOW}  ⊘ System upgrade skipped${NC}"
        fi
    else
        echo -e "${YELLOW}  ⊘ Skipped (non-interactive mode - use UPGRADE_SYSTEM=yes to enable)${NC}"
    fi
fi

# Install system dependencies
echo -e "${CYAN}[4/7] Installing required dependencies...${NC}"
# Pre-clone: the shared install lib isn't available yet (curl|bash runs before
# the repo exists), so this one apt is inline-but-CHECKED — no &>/dev/null
# swallow, and we only print ✓ after a real success (the `if` disables set -e
# so the failure branch can give actionable guidance).
if apt-get install -y -q python3 python3-pip python3-venv git wget curl; then
    echo -e "${GREEN}  ✓ Core dependencies installed${NC}"
else
    echo -e "${RED}  ✗ Failed to install core dependencies (python3/python3-pip/${NC}" >&2
    echo -e "${RED}    python3-venv/git/wget/curl). Check network + apt, then re-run.${NC}" >&2
    exit 1
fi

# Clone or update repository
INSTALL_DIR="/opt/meshforge"
echo -e "${CYAN}[5/7] Setting up MeshForge...${NC}"

# Optional source pin: MESHFORGE_REF=<tag|branch|sha> installs that exact ref.
# A pinned install that silently lands on main is worse than stopping, so an
# unresolvable ref HARD-fails. Default (unset) tracks main as before. This is
# the pre-clone bootstrap, so it is inline (the shared lib's mf_git_sync isn't
# available until after the repo exists).
MESHFORGE_REF="${MESHFORGE_REF:-}"

if [[ -d "$INSTALL_DIR/.git" ]]; then
    echo "  Updating existing installation..."
    git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true
    cd "$INSTALL_DIR"
    git pull -q || echo -e "${YELLOW}  Warning: git pull failed; continuing with existing checkout${NC}"
else
    echo "  Cloning repository..."
    if [[ -n "$MESHFORGE_REF" ]]; then
        git clone -q --branch "$MESHFORGE_REF" https://github.com/Nursedude/meshforge.git "$INSTALL_DIR" 2>/dev/null \
            || git clone -q https://github.com/Nursedude/meshforge.git "$INSTALL_DIR"
    else
        git clone -q https://github.com/Nursedude/meshforge.git "$INSTALL_DIR"
    fi
    git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true
    cd "$INSTALL_DIR"
fi

if [[ -n "$MESHFORGE_REF" ]]; then
    git fetch -q --tags origin 2>/dev/null || true
    git checkout -q "$MESHFORGE_REF" \
        || { echo -e "${RED}  ✗ MESHFORGE_REF '$MESHFORGE_REF' could not be resolved${NC}" >&2; exit 1; }
fi

INSTALLED_SHA="$(git -C "$INSTALL_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
echo -e "${GREEN}  ✓ Repository ready (HEAD: ${INSTALLED_SHA})${NC}"

# Hardened install primitives now that the repo exists (curl|bash could not
# source these before the clone). Adds ensure_pip + checked pip + the install
# transcript. From here on, every install run leaves /var/log/meshforge/.
# shellcheck source=scripts/lib/install_common.sh
source "$INSTALL_DIR/scripts/lib/install_common.sh"
mf_log_init

# Install Python dependencies
echo -e "${CYAN}[6/7] Installing Python dependencies...${NC}"

# Check for PEP 668 (externally-managed-environment) via the shared SSOT
# detector — Debian Bookworm, RPi OS.
PEP668_DETECTED=false
if mf_pep668_active python3; then
    PEP668_DETECTED=true
fi

# Determine install method
INSTALL_METHOD="venv"
if [[ "${BREAK_SYSTEM_PACKAGES}" == "yes" ]] || [[ "${USE_VENV}" == "no" ]]; then
    INSTALL_METHOD="system"
    echo -e "${YELLOW}  Using --break-system-packages (per environment variable)${NC}"
elif [[ "$PEP668_DETECTED" == "true" ]] && [[ -c /dev/tty ]]; then
    echo -e "${YELLOW}  Detected: Externally managed Python (PEP 668 - Debian/RPi OS)${NC}"
    echo ""
    echo "  Choose Python package installation method:"
    echo "    1) Virtual environment at /opt/meshforge/venv (recommended)"
    echo "    2) System-wide with --break-system-packages (simpler)"
    echo ""
    read -p "  Choose [1/2] (default: 1): " -n 1 -r choice < /dev/tty
    echo ""
    if [[ "$choice" == "2" ]]; then
        INSTALL_METHOD="system"
    fi
fi

if [[ "$INSTALL_METHOD" == "venv" ]]; then
    # Create virtual environment if it doesn't exist
    if [[ ! -d "$INSTALL_DIR/venv" ]]; then
        echo "  Creating virtual environment..."
        python3 -m venv "$INSTALL_DIR/venv" --system-site-packages
    fi
    # Install dependencies in virtual environment (checked; ✓ only after a
    # verified import — never an unconditional success echo).
    echo "  Installing packages in virtual environment..."
    VENV_PY="$INSTALL_DIR/venv/bin/python"
    mf_ensure_pip "$VENV_PY" || { echo -e "${RED}  ✗ pip unavailable in venv${NC}" >&2; exit 1; }
    mf_pip_install "$VENV_PY" -q --timeout 60 --upgrade pip \
        || { echo -e "${RED}  ✗ pip self-upgrade failed (see ${MF_INSTALL_LOG:-console})${NC}" >&2; exit 1; }
    mf_pip_install "$VENV_PY" -q --timeout 60 -r requirements.txt \
        || { echo -e "${RED}  ✗ dependency install failed (see ${MF_INSTALL_LOG:-console})${NC}" >&2; exit 1; }
    if mf_verify_import "$VENV_PY" rich; then
        echo -e "${GREEN}  ✓ Python dependencies installed (venv)${NC}"
    else
        echo -e "${RED}  ✗ deps installed but 'rich' not importable in venv${NC}" >&2
        exit 1
    fi
else
    # Install system-wide with --break-system-packages (--ignore-installed to
    # avoid conflicts with apt-owned packages).
    echo "  Installing packages system-wide..."
    mf_ensure_pip python3 || { echo -e "${RED}  ✗ pip unavailable${NC}" >&2; exit 1; }
    mf_pip_install python3 --break-system-packages --ignore-installed --timeout 60 --upgrade pip \
        || { echo -e "${RED}  ✗ pip self-upgrade failed (see ${MF_INSTALL_LOG:-console})${NC}" >&2; exit 1; }
    mf_pip_install python3 --break-system-packages --ignore-installed --timeout 60 -r requirements.txt \
        || { echo -e "${RED}  ✗ dependency install failed (see ${MF_INSTALL_LOG:-console})${NC}" >&2; exit 1; }
    # Mark that we're not using venv for the launcher scripts
    touch "$INSTALL_DIR/.no-venv"
    if mf_verify_import python3 rich; then
        echo -e "${GREEN}  ✓ Python dependencies installed (system)${NC}"
    else
        echo -e "${RED}  ✗ deps installed but 'rich' not importable${NC}" >&2
        exit 1
    fi
fi

# Create symlink for easy access
echo -e "${CYAN}[7/7] Creating system commands...${NC}"

# Main launcher wizard (default)
cat > /usr/local/bin/meshforge << 'EOF'
#!/bin/bash
cd /opt/meshforge
if [[ -f .no-venv ]]; then
    exec sudo python3 src/launcher.py "$@"
else
    exec sudo /opt/meshforge/venv/bin/python src/launcher.py "$@"
fi
EOF
chmod +x /usr/local/bin/meshforge

# TUI access (raspi-config style)
cat > /usr/local/bin/meshforge-tui << 'EOF'
#!/bin/bash
cd /opt/meshforge
if [[ -f .no-venv ]]; then
    exec sudo python3 src/launcher_tui/main.py "$@"
else
    exec sudo /opt/meshforge/venv/bin/python src/launcher_tui/main.py "$@"
fi
EOF
chmod +x /usr/local/bin/meshforge-tui

echo -e "${GREEN}  ✓ Commands created: meshforge, meshforge-tui${NC}"

# Reclaim user-writable dirs that sudo invocations may have left root-owned.
# Gateway writes to ~/.cache/meshforge/logs and ~/.config/meshforge; if those
# dirs are root-owned, runtime fails with Errno 13 (see persistent_issues #33).
if [[ -n "$SUDO_USER" ]]; then
    REAL_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
    if [[ -n "$REAL_HOME" ]]; then
        for dir in "$REAL_HOME/.cache/meshforge" "$REAL_HOME/.config/meshforge"; do
            if [[ -d "$dir" ]]; then
                chown -R "$SUDO_USER:$SUDO_USER" "$dir" 2>/dev/null || true
            fi
        done
        echo -e "${GREEN}  ✓ User dirs reclaimed for $SUDO_USER${NC}"
    fi
fi

# Success message
echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         MeshForge Installation Complete!                  ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${CYAN}Quick Start:${NC}"
echo "  ${GREEN}meshforge${NC}              - Launch TUI (raspi-config style)"
echo "  ${GREEN}meshforge-tui${NC}          - Same as above (explicit)"
echo ""
echo -e "${CYAN}Features:${NC}"
echo "  • Meshtastic + Reticulum (RNS) gateway bridge"
echo "  • RF tools (LOS, Fresnel, path loss)"
echo "  • Hardware simulation mode"
echo "  • HamClock space weather integration"
echo "  • Interactive node map"
echo ""
echo -e "${CYAN}Would you like to start MeshForge now? [Y/n]${NC}"

# Try to read from TTY if available for interactive prompt
if [[ -c /dev/tty ]]; then
    read -r response < /dev/tty
    if [[ "$response" =~ ^([nN][oO]|[nN])$ ]]; then
        echo ""
        echo -e "${YELLOW}Installation complete. Run 'meshforge' when ready.${NC}"
        exit 0
    fi
fi

# Launch installer (default action)
echo ""
echo -e "${GREEN}Starting MeshForge...${NC}"
echo ""
exec /usr/local/bin/meshforge
