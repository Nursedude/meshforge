#!/bin/bash
#
# MeshForge Clean Reinstall
#
# Removes MeshForge completely and does a fresh install from GitHub.
# Preserves all user/radio configuration. No need to re-image your Pi.
#
# What is REMOVED:
#   - /opt/meshforge (source code + Python venv)
#   - /usr/local/bin/meshforge* commands
#   - meshforge*.service systemd units
#
# What is PRESERVED (backed up and restored):
#   - /etc/meshforge/           (MeshForge NOC config)
#   - /etc/meshtasticd/config.d/ (active radio hardware configs)
#   - ~/.config/meshforge/      (user settings)
#   - /etc/meshtasticd/         (meshtasticd package + config.yaml)
#   - ~/.reticulum/             (Reticulum identity + config)
#   - meshtasticd apt package   (untouched)
#   - rnsd/RNS pip package      (untouched)
#   - mosquitto                 (untouched)
#
# Usage:
#   sudo bash /opt/meshforge/scripts/reinstall.sh
#   sudo bash /opt/meshforge/scripts/reinstall.sh --no-confirm
#   sudo bash /opt/meshforge/scripts/reinstall.sh --branch alpha
#
# After completion, MeshForge is fully operational with your existing configs.
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
INSTALL_DIR="/opt/meshforge"
BACKUP_TIMESTAMP=$(date +%Y%m%d-%H%M%S)
# Use persistent location — /tmp gets wiped on reboot and could lose RNS identity
if [[ -n "$SUDO_USER" ]]; then
    BACKUP_BASE=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
    BACKUP_BASE="$HOME"
fi
BACKUP_DIR="${BACKUP_BASE}/meshforge-backup-${BACKUP_TIMESTAMP}"
REPO_URL="https://github.com/Nursedude/meshforge.git"
BRANCH="main"
NO_CONFIRM=false
SKIP_INSTALL=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --no-confirm|-y)
            NO_CONFIRM=true
            shift
            ;;
        --branch|-b)
            BRANCH="$2"
            shift 2
            ;;
        --remove-only)
            SKIP_INSTALL=true
            shift
            ;;
        --help|-h)
            echo "MeshForge Clean Reinstall"
            echo ""
            echo "Usage: sudo $0 [options]"
            echo ""
            echo "Options:"
            echo "  --no-confirm, -y     Skip confirmation prompt"
            echo "  --branch, -b BRANCH  Install specific branch (default: main)"
            echo "  --remove-only        Remove MeshForge without reinstalling"
            echo "  --help, -h           Show this help"
            echo ""
            echo "Preserves: radio configs, RNS identity, MQTT broker, meshtasticd"
            echo "Removes:   MeshForge source, venv, system commands, systemd units"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage"
            exit 1
            ;;
    esac
done

echo -e "${CYAN}"
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║          MeshForge Clean Reinstall                        ║"
echo "║          Fresh install without re-imaging your Pi         ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Check root
if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}Error: This script must be run as root${NC}"
    echo "Please run: sudo bash $0"
    exit 1
fi

# Detect real user home (for ~/.config/meshforge backup)
if [[ -n "$SUDO_USER" ]]; then
    REAL_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
    REAL_HOME="$HOME"
fi

if [[ -z "$REAL_HOME" ]]; then
    echo -e "${RED}Error: Cannot determine user home directory${NC}"
    exit 1
fi

# ─────────────────────────────────────────────────────────────────
# Pre-flight: show what will happen
# ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}Current state:${NC}"

if [[ -d "$INSTALL_DIR" ]]; then
    CURRENT_VERSION=$(python3 -c "import sys; sys.path.insert(0, '$INSTALL_DIR/src'); from __version__ import __version__; print(__version__)" 2>/dev/null || echo "unknown")
    CURRENT_BRANCH=$(cd "$INSTALL_DIR" && git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    CURRENT_COMMIT=$(cd "$INSTALL_DIR" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    echo -e "  Version:  ${BOLD}${CURRENT_VERSION}${NC}"
    echo -e "  Branch:   ${BOLD}${CURRENT_BRANCH}${NC} (${CURRENT_COMMIT})"
else
    echo -e "  ${YELLOW}MeshForge not found at $INSTALL_DIR${NC}"
fi

echo ""
echo -e "${CYAN}Will be ${RED}REMOVED${NC}:"
echo "  /opt/meshforge/          (source code + venv)"
echo "  /usr/local/bin/meshforge* (system commands)"
echo "  meshforge*.service       (systemd units)"
echo ""
echo -e "${CYAN}Will be ${GREEN}PRESERVED${NC}:"
echo "  /etc/meshforge/          (NOC config)"
echo "  /etc/meshtasticd/        (radio configs)"
echo "  ${REAL_HOME}/.config/meshforge/  (user settings)"
echo "  ${REAL_HOME}/.reticulum/         (RNS identity)"
echo "  meshtasticd, rnsd, mosquitto (packages untouched)"
echo ""

if $SKIP_INSTALL; then
    echo -e "${YELLOW}Mode: Remove only (no reinstall)${NC}"
else
    echo -e "${CYAN}Will install: branch ${BOLD}${BRANCH}${NC}"
fi

echo ""

# Confirmation
if ! $NO_CONFIRM && [[ -c /dev/tty ]]; then
    read -rp "  Continue? [y/N] " confirm < /dev/tty
    if [[ ! "$confirm" =~ ^[yY]$ ]]; then
        echo -e "${YELLOW}Cancelled${NC}"
        exit 0
    fi
fi

echo ""

# ─────────────────────────────────────────────────────────────────
# Step 1: Backup configs
# ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}[1/5] Backing up configuration...${NC}"

mkdir -p "$BACKUP_DIR"

BACKED_UP=0

# MeshForge NOC config
if [[ -d /etc/meshforge ]]; then
    cp -a /etc/meshforge "$BACKUP_DIR/etc-meshforge"
    echo -e "  ${GREEN}✓${NC} /etc/meshforge/"
    BACKED_UP=$((BACKED_UP + 1))
fi

# meshtasticd active configs (config.d/ only — not the package's config.yaml)
if [[ -d /etc/meshtasticd/config.d ]] && ls /etc/meshtasticd/config.d/*.yaml &>/dev/null 2>&1; then
    mkdir -p "$BACKUP_DIR/meshtasticd-config-d"
    cp -a /etc/meshtasticd/config.d/*.yaml "$BACKUP_DIR/meshtasticd-config-d/"
    echo -e "  ${GREEN}✓${NC} /etc/meshtasticd/config.d/ ($(ls /etc/meshtasticd/config.d/*.yaml 2>/dev/null | wc -l) files)"
    BACKED_UP=$((BACKED_UP + 1))
fi

# User MeshForge settings
if [[ -d "$REAL_HOME/.config/meshforge" ]]; then
    cp -a "$REAL_HOME/.config/meshforge" "$BACKUP_DIR/user-config-meshforge"
    echo -e "  ${GREEN}✓${NC} ${REAL_HOME}/.config/meshforge/"
    BACKED_UP=$((BACKED_UP + 1))
fi

# Reticulum identity and config
if [[ -d "$REAL_HOME/.reticulum" ]]; then
    cp -a "$REAL_HOME/.reticulum" "$BACKUP_DIR/user-reticulum"
    echo -e "  ${GREEN}✓${NC} ${REAL_HOME}/.reticulum/ (RNS identity + config)"
    BACKED_UP=$((BACKED_UP + 1))
fi

# Note: SQLite message queue is included in ~/.config/meshforge/ backup above
if [[ -f "$REAL_HOME/.config/meshforge/message_queue.db" ]]; then
    echo -e "  ${GREEN}✓${NC} Message queue database (included in config backup)"
fi

if [[ $BACKED_UP -eq 0 ]]; then
    echo -e "  ${YELLOW}No configs to backup (fresh system)${NC}"
fi

echo -e "  Backup location: ${BOLD}$BACKUP_DIR${NC}"

# ─────────────────────────────────────────────────────────────────
# Step 2: Stop services
# ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}[2/5] Stopping MeshForge services...${NC}"

STOPPED=0

for svc in meshforge meshforge-map; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        systemctl stop "$svc"
        echo -e "  ${GREEN}✓${NC} Stopped $svc"
        STOPPED=$((STOPPED + 1))
    fi
done

if [[ $STOPPED -eq 0 ]]; then
    echo -e "  ${YELLOW}No MeshForge services were running${NC}"
fi

# ─────────────────────────────────────────────────────────────────
# Step 3: Remove MeshForge
# ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}[3/5] Removing MeshForge...${NC}"

# Remove system commands
for cmd in meshforge meshforge-noc meshforge-lora meshforge-status meshforge-web meshforge-map; do
    if [[ -f "/usr/local/bin/$cmd" ]]; then
        rm -f "/usr/local/bin/$cmd"
        echo -e "  ${GREEN}✓${NC} Removed /usr/local/bin/$cmd"
    fi
done

# Remove systemd units (MeshForge only — NOT meshtasticd or rnsd)
for unit in meshforge.service meshforge-map.service; do
    if [[ -f "/etc/systemd/system/$unit" ]]; then
        systemctl disable "$unit" 2>/dev/null || true
        rm -f "/etc/systemd/system/$unit"
        echo -e "  ${GREEN}✓${NC} Removed $unit"
    fi
done
systemctl daemon-reload

# Remove installation directory (defensive check against empty/root path)
if [[ -z "$INSTALL_DIR" ]] || [[ "$INSTALL_DIR" == "/" ]]; then
    echo -e "${RED}FATAL: Invalid INSTALL_DIR — aborting${NC}"
    exit 1
fi
if [[ -d "$INSTALL_DIR" ]]; then
    cd /  # Escape CWD before removing it (script runs from inside INSTALL_DIR)
    rm -rf "$INSTALL_DIR"
    echo -e "  ${GREEN}✓${NC} Removed $INSTALL_DIR"
else
    echo -e "  ${YELLOW}$INSTALL_DIR not found (already removed?)${NC}"
fi

echo -e "  ${GREEN}✓ MeshForge removed${NC}"

# If remove-only mode, stop here
if $SKIP_INSTALL; then
    echo ""
    echo -e "${GREEN}╔═══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║          MeshForge Removed                                ║${NC}"
    echo -e "${GREEN}╚═══════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "Config backups at: $BACKUP_DIR"
    echo "To reinstall: git clone $REPO_URL /opt/meshforge && sudo bash /opt/meshforge/scripts/install_noc.sh"
    exit 0
fi

# ─────────────────────────────────────────────────────────────────
# Step 4: Fresh install
# ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}[4/5] Installing fresh MeshForge...${NC}"

echo "  Cloning from GitHub (branch: $BRANCH)..."

# Clone with retry (network can be flaky on Pi)
CLONE_OK=false
for attempt in 1 2 3 4; do
    if git clone -q -b "$BRANCH" "$REPO_URL" "$INSTALL_DIR" 2>&1; then
        CLONE_OK=true
        break
    fi
    WAIT=$((attempt * 2))
    echo -e "  ${YELLOW}Clone failed (attempt $attempt/4), retrying in ${WAIT}s...${NC}"
    sleep "$WAIT"
done

if ! $CLONE_OK; then
    echo -e "${RED}Error: Could not clone repository after 4 attempts${NC}"
    echo "Check your network connection"
    echo ""
    echo "Config backups saved at: $BACKUP_DIR"
    echo "To restore manually:"
    echo "  git clone $REPO_URL $INSTALL_DIR"
    echo "  sudo bash $INSTALL_DIR/scripts/install_noc.sh"
    exit 1
fi

git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true

NEW_VERSION=$(python3 -c "import sys; sys.path.insert(0, '$INSTALL_DIR/src'); from __version__ import __version__; print(__version__)" 2>/dev/null || echo "unknown")
NEW_COMMIT=$(cd "$INSTALL_DIR" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
echo -e "  ${GREEN}✓${NC} Cloned: v${NEW_VERSION} (${NEW_COMMIT})"

# Run the installer (handles venv, deps, system integration)
if [[ ! -f "$INSTALL_DIR/scripts/install_noc.sh" ]]; then
    echo -e "${RED}Error: install_noc.sh not found in cloned repository${NC}"
    echo "The clone may be incomplete. Backups preserved at: $BACKUP_DIR"
    exit 1
fi

echo ""
echo -e "  ${CYAN}Running install_noc.sh...${NC}"
echo ""
bash "$INSTALL_DIR/scripts/install_noc.sh" --skip-meshtasticd --skip-rns

# ─────────────────────────────────────────────────────────────────
# Step 5: Restore configs
# ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[5/5] Restoring configuration...${NC}"

RESTORED=0

# Restore NOC config
if [[ -d "$BACKUP_DIR/etc-meshforge" ]]; then
    cp -a "$BACKUP_DIR/etc-meshforge"/* /etc/meshforge/ 2>/dev/null || true
    echo -e "  ${GREEN}✓${NC} Restored /etc/meshforge/"
    RESTORED=$((RESTORED + 1))
fi

# Restore radio hardware configs
if [[ -d "$BACKUP_DIR/meshtasticd-config-d" ]]; then
    mkdir -p /etc/meshtasticd/config.d
    cp -a "$BACKUP_DIR/meshtasticd-config-d"/* /etc/meshtasticd/config.d/ 2>/dev/null || true
    echo -e "  ${GREEN}✓${NC} Restored /etc/meshtasticd/config.d/"
    RESTORED=$((RESTORED + 1))
fi

# Restore user settings
if [[ -d "$BACKUP_DIR/user-config-meshforge" ]]; then
    mkdir -p "$REAL_HOME/.config/meshforge"
    cp -a "$BACKUP_DIR/user-config-meshforge"/* "$REAL_HOME/.config/meshforge/" 2>/dev/null || true
    # Fix ownership back to real user
    if [[ -n "$SUDO_USER" ]]; then
        chown -R "$SUDO_USER:$(id -gn "$SUDO_USER")" "$REAL_HOME/.config/meshforge"
    fi
    echo -e "  ${GREEN}✓${NC} Restored ${REAL_HOME}/.config/meshforge/"
    RESTORED=$((RESTORED + 1))
fi

# Restore Reticulum identity and config
if [[ -d "$BACKUP_DIR/user-reticulum" ]]; then
    mkdir -p "$REAL_HOME/.reticulum"
    cp -a "$BACKUP_DIR/user-reticulum"/* "$REAL_HOME/.reticulum/" 2>/dev/null || true
    if [[ -n "$SUDO_USER" ]]; then
        chown -R "$SUDO_USER:$(id -gn "$SUDO_USER")" "$REAL_HOME/.reticulum"
    fi
    echo -e "  ${GREEN}✓${NC} Restored ${REAL_HOME}/.reticulum/"
    RESTORED=$((RESTORED + 1))
fi

if [[ $RESTORED -eq 0 ]]; then
    echo -e "  ${YELLOW}No configs to restore (fresh install)${NC}"
fi

# ─────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║          MeshForge Reinstall Complete!                    ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Version:  ${BOLD}${NEW_VERSION}${NC}"
echo -e "  Branch:   ${BOLD}${BRANCH}${NC} (${NEW_COMMIT})"
echo -e "  Configs:  ${GREEN}${RESTORED} restored${NC}"
echo ""
echo -e "${CYAN}Commands:${NC}"
echo "  meshforge               Launch TUI"
echo "  meshforge-status        Quick health check"
echo "  meshforge-web           Open radio web client"
echo ""

# Keep backup for safety — user can delete manually once verified
echo -e "${CYAN}Backup preserved at: ${BOLD}${BACKUP_DIR}${NC}"
echo -e "${CYAN}  Delete after verifying: rm -rf ${BACKUP_DIR}${NC}"
echo ""
echo -e "${CYAN}Made with aloha for the mesh community${NC}"
echo ""
