#!/bin/bash
#
# MeshForge Fleet Restore
#
# Restores a MeshForge node from a fleet backup archive on a fresh
# Raspberry Pi OS installation. Works with zero dependencies beyond
# standard Debian tools (tar, bash, ssh).
#
# The restore process:
#   1. Validates the backup archive
#   2. Installs MeshForge from GitHub (git clone + install_noc.sh)
#   3. Overlays backed-up configs, identities, and AI memory
#   4. Restarts services with restored identity
#   5. Runs post-install verification
#
# Usage:
#   # From local archive (SCP'd from another Pi):
#   sudo bash fleet_restore.sh /path/to/fleet-host-20260416-120000.tar.gz
#
#   # Pull latest backup from a peer Pi:
#   sudo bash fleet_restore.sh --pull-from 192.168.86.38 --hostname fleet-host
#
#   # Dry run (show what would be restored):
#   sudo bash fleet_restore.sh backup.tar.gz --dry-run
#

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Defaults
INSTALL_DIR="/opt/meshforge"
REPO_URL="https://github.com/Nursedude/meshforge.git"
BRANCH="main"
DRY_RUN=false
ARCHIVE_PATH=""
PULL_FROM=""
RESTORE_HOSTNAME=""
SET_HOSTNAME=""
NO_CONFIRM=false
TARGET_USER="wh6gxz"

# ─────────────────────────────────────────────────────────────────
# Parse arguments
# ─────────────────────────────────────────────────────────────────
show_help() {
    echo "MeshForge Fleet Restore"
    echo ""
    echo "Restores a MeshForge node from a fleet backup archive."
    echo "Run on a fresh Raspberry Pi OS installation."
    echo ""
    echo "Usage: sudo bash $0 <archive.tar.gz> [options]"
    echo "       sudo bash $0 --pull-from <ip> --hostname <name> [options]"
    echo ""
    echo "Options:"
    echo "  --pull-from IP     Pull latest backup from a peer Pi"
    echo "  --hostname NAME    Hostname to restore (required with --pull-from)"
    echo "  --set-hostname     Set this Pi's hostname from the backup"
    echo "  --branch BRANCH    MeshForge branch to install (default: main)"
    echo "  --user USER        Target user account (default: wh6gxz)"
    echo "  --dry-run          Show what would be restored without doing it"
    echo "  --no-confirm, -y   Skip confirmation prompt"
    echo "  --help, -h         Show this help"
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --pull-from)
            PULL_FROM="$2"
            shift 2
            ;;
        --hostname)
            RESTORE_HOSTNAME="$2"
            shift 2
            ;;
        --set-hostname)
            SET_HOSTNAME="yes"
            shift
            ;;
        --branch|-b)
            BRANCH="$2"
            shift 2
            ;;
        --user|-u)
            TARGET_USER="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --no-confirm|-y)
            NO_CONFIRM=true
            shift
            ;;
        --help|-h)
            show_help
            exit 0
            ;;
        -*)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage"
            exit 1
            ;;
        *)
            ARCHIVE_PATH="$1"
            shift
            ;;
    esac
done

# ─────────────────────────────────────────────────────────────────
# Pre-flight checks
# ─────────────────────────────────────────────────────────────────

# Must be root
if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}Error: This script must be run as root${NC}"
    echo "Please run: sudo bash $0 ..."
    exit 1
fi

# Determine target user home
TARGET_HOME=$(getent passwd "$TARGET_USER" 2>/dev/null | cut -d: -f6 || true)
if [[ -z "$TARGET_HOME" ]]; then
    # User doesn't exist yet — will create later
    TARGET_HOME="/home/${TARGET_USER}"
fi

# ─────────────────────────────────────────────────────────────────
# Phase 0: Acquire backup archive
# ─────────────────────────────────────────────────────────────────
if [[ -n "$PULL_FROM" ]]; then
    if [[ -z "$RESTORE_HOSTNAME" ]]; then
        echo -e "${RED}Error: --hostname is required with --pull-from${NC}"
        exit 1
    fi

    echo -e "${CYAN}Pulling backup for ${BOLD}${RESTORE_HOSTNAME}${NC}${CYAN} from ${PULL_FROM}...${NC}"

    # Try to find the SSH key
    SSH_KEY=""
    for key_path in \
        "${TARGET_HOME}/.claude/ssh/id_ed25519" \
        "${TARGET_HOME}/.ssh/id_ed25519" \
        "${TARGET_HOME}/.ssh/id_rsa" \
        "/root/.ssh/id_ed25519" \
        "/root/.ssh/id_rsa"; do
        if [[ -f "$key_path" ]]; then
            SSH_KEY="$key_path"
            break
        fi
    done

    SSH_OPTS=(-o ConnectTimeout=15 -o BatchMode=yes -o StrictHostKeyChecking=no)
    if [[ -n "$SSH_KEY" ]]; then
        SSH_OPTS+=(-i "$SSH_KEY")
    fi

    REMOTE_PATH=".meshforge-fleet-backups/${RESTORE_HOSTNAME}/latest.tar.gz"
    ARCHIVE_PATH="/tmp/${RESTORE_HOSTNAME}-restore.tar.gz"

    if ! scp "${SSH_OPTS[@]}" "${TARGET_USER}@${PULL_FROM}:~/${REMOTE_PATH}" "$ARCHIVE_PATH" 2>/dev/null; then
        echo -e "${RED}Error: Could not pull backup from ${PULL_FROM}${NC}"
        echo "  Tried: ~/${REMOTE_PATH}"
        echo "  Make sure the peer Pi has a backup for ${RESTORE_HOSTNAME}"
        exit 1
    fi

    echo -e "  ${GREEN}+${NC} Downloaded backup to ${ARCHIVE_PATH}"
fi

if [[ -z "$ARCHIVE_PATH" ]]; then
    echo -e "${RED}Error: No backup archive specified${NC}"
    echo "Use: sudo bash $0 <archive.tar.gz>"
    echo "Or:  sudo bash $0 --pull-from <ip> --hostname <name>"
    exit 1
fi

if [[ ! -f "$ARCHIVE_PATH" ]]; then
    echo -e "${RED}Error: Archive not found: ${ARCHIVE_PATH}${NC}"
    exit 1
fi

# ─────────────────────────────────────────────────────────────────
# Phase 1: Validate archive
# ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}"
echo "╔════════════════════════════════════════════════════════╗"
echo "║          MeshForge Fleet Restore                       ║"
echo "╚════════════════════════════════════════════════════════╝"
echo -e "${NC}"

echo -e "${CYAN}[1/6] Validating backup archive...${NC}"

# Test archive integrity
if ! tar -tzf "$ARCHIVE_PATH" &>/dev/null; then
    echo -e "${RED}Error: Archive is corrupted or not a valid gzip tarball${NC}"
    exit 1
fi

echo -e "  ${GREEN}+${NC} Archive integrity OK"

# Extract to temp dir for inspection
TMPDIR=$(mktemp -d "/tmp/meshforge-restore-XXXXXX")
trap "rm -rf '$TMPDIR'" EXIT

tar -xzf "$ARCHIVE_PATH" -C "$TMPDIR"

# Find the extracted directory (should be {hostname}-{timestamp})
EXTRACT_DIR=$(find "$TMPDIR" -mindepth 1 -maxdepth 1 -type d | head -1)

if [[ -z "$EXTRACT_DIR" || ! -d "$EXTRACT_DIR" ]]; then
    echo -e "${RED}Error: Archive does not contain expected directory structure${NC}"
    exit 1
fi

BACKUP_NAME=$(basename "$EXTRACT_DIR")

# Read manifest
if [[ -f "$EXTRACT_DIR/manifest.json" ]]; then
    BACKUP_HOSTNAME=$(python3 -c "
import json
with open('${EXTRACT_DIR}/manifest.json') as f:
    print(json.load(f).get('hostname', 'unknown'))
" 2>/dev/null || echo "unknown")

    BACKUP_TIMESTAMP=$(python3 -c "
import json
with open('${EXTRACT_DIR}/manifest.json') as f:
    print(json.load(f).get('timestamp', 'unknown'))
" 2>/dev/null || echo "unknown")

    BACKUP_VERSION=$(python3 -c "
import json
with open('${EXTRACT_DIR}/manifest.json') as f:
    print(json.load(f).get('meshforge_version', 'unknown'))
" 2>/dev/null || echo "unknown")

    BACKUP_ITEMS=$(python3 -c "
import json
with open('${EXTRACT_DIR}/manifest.json') as f:
    m = json.load(f)
    files = m.get('files', [])
    print(f\"{len(files)} files, {m.get('total_size_bytes', 0)} bytes\")
" 2>/dev/null || echo "unknown")

    echo -e "  ${GREEN}+${NC} Manifest found"
    echo ""
    echo -e "  Backup of:  ${BOLD}${BACKUP_HOSTNAME}${NC}"
    echo -e "  Created:    ${BACKUP_TIMESTAMP}"
    echo -e "  Version:    ${BACKUP_VERSION}"
    echo -e "  Contents:   ${BACKUP_ITEMS}"
else
    echo -e "${YELLOW}  Warning: No manifest.json found — proceeding with best-effort restore${NC}"
    BACKUP_HOSTNAME="unknown"
fi

# Hostname mismatch warning
CURRENT_HOSTNAME=$(hostname -s)
if [[ "$BACKUP_HOSTNAME" != "unknown" && "$BACKUP_HOSTNAME" != "$CURRENT_HOSTNAME" ]]; then
    echo ""
    echo -e "  ${YELLOW}Note: This Pi is '${CURRENT_HOSTNAME}' but backup is from '${BACKUP_HOSTNAME}'${NC}"
    if [[ "$SET_HOSTNAME" == "yes" ]]; then
        echo -e "  ${CYAN}Will set hostname to '${BACKUP_HOSTNAME}' (--set-hostname)${NC}"
    fi
fi

echo ""

# Show what will be restored
echo -e "${CYAN}Will restore:${NC}"

[[ -d "$EXTRACT_DIR/etc/reticulum" ]] && echo -e "  ${GREEN}+${NC} /etc/reticulum/ (RNS config + identity)"
[[ -d "$EXTRACT_DIR/etc/meshtasticd" ]] && echo -e "  ${GREEN}+${NC} /etc/meshtasticd/ (radio config)"
[[ -d "$EXTRACT_DIR/home/config/meshforge" ]] && echo -e "  ${GREEN}+${NC} ~/.config/meshforge/ (MeshForge settings)"
[[ -d "$EXTRACT_DIR/home/claude" ]] && echo -e "  ${GREEN}+${NC} ~/.claude/ (AI memory + settings)"
[[ -f "$EXTRACT_DIR/opt/meshforge/.claude.json" ]] && echo -e "  ${GREEN}+${NC} /opt/meshforge/.claude.json (project config)"

echo ""

# Dry run exits here
if $DRY_RUN; then
    echo -e "${YELLOW}DRY RUN — no changes made${NC}"
    exit 0
fi

# Confirmation
if ! $NO_CONFIRM && [[ -c /dev/tty ]]; then
    read -rp "  Continue with restore? [y/N] " confirm < /dev/tty
    if [[ ! "$confirm" =~ ^[yY]$ ]]; then
        echo -e "${YELLOW}Cancelled${NC}"
        exit 0
    fi
fi

echo ""

# ─────────────────────────────────────────────────────────────────
# Phase 2: System preparation
# ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}[2/6] Preparing system...${NC}"

# Create target user if needed
if ! id "$TARGET_USER" &>/dev/null; then
    echo -e "  ${GREEN}+${NC} Creating user ${TARGET_USER}"
    useradd -m -s /bin/bash "$TARGET_USER"
    # Add to standard groups
    for grp in sudo dialout gpio spi i2c; do
        usermod -aG "$grp" "$TARGET_USER" 2>/dev/null || true
    done
    TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)
fi

# Set hostname if requested
if [[ "$SET_HOSTNAME" == "yes" && "$BACKUP_HOSTNAME" != "unknown" ]]; then
    echo -e "  ${GREEN}+${NC} Setting hostname to ${BACKUP_HOSTNAME}"
    hostnamectl set-hostname "$BACKUP_HOSTNAME" 2>/dev/null || \
        echo "$BACKUP_HOSTNAME" > /etc/hostname
fi

# Ensure git is available
if ! command -v git &>/dev/null; then
    echo -e "  ${GREEN}+${NC} Installing git"
    apt-get update -qq && apt-get install -y -qq git
fi

echo -e "  ${GREEN}+${NC} System ready"

# ─────────────────────────────────────────────────────────────────
# Phase 3: Install MeshForge (if not already present)
# ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}[3/6] Installing MeshForge...${NC}"

if [[ -d "$INSTALL_DIR/.git" ]]; then
    echo -e "  ${GREEN}+${NC} MeshForge already installed at ${INSTALL_DIR}"
    echo -e "  ${GREEN}+${NC} Updating to latest..."
    git -C "$INSTALL_DIR" pull --ff-only 2>/dev/null || true
else
    echo -e "  ${GREEN}+${NC} Cloning MeshForge (branch: ${BRANCH})"
    git clone -b "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

# Run install script if available
if [[ -f "$INSTALL_DIR/scripts/install_noc.sh" ]]; then
    echo -e "  ${GREEN}+${NC} Running install_noc.sh..."
    echo -e "  ${YELLOW}!${NC} This may take several minutes"
    bash "$INSTALL_DIR/scripts/install_noc.sh" --no-confirm 2>&1 | \
        while IFS= read -r line; do
            # Show progress dots, suppress verbose output
            echo -n "." >&2
        done
    echo "" >&2
    echo -e "  ${GREEN}+${NC} install_noc.sh complete"
else
    echo -e "  ${YELLOW}!${NC} install_noc.sh not found — manual setup may be needed"
fi

# ─────────────────────────────────────────────────────────────────
# Phase 4: Overlay backed-up state
# ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}[4/6] Restoring backed-up state...${NC}"

RESTORED=0

# --- Stop services before restoring identity files ---
for svc in rnsd meshtasticd meshforge meshforge-map; do
    systemctl stop "$svc" 2>/dev/null || true
done

# --- Reticulum ---
if [[ -d "$EXTRACT_DIR/etc/reticulum" ]]; then
    mkdir -p /etc/reticulum/storage

    # Config
    if [[ -f "$EXTRACT_DIR/etc/reticulum/config" ]]; then
        cp -a "$EXTRACT_DIR/etc/reticulum/config" /etc/reticulum/config
        echo -e "  ${GREEN}+${NC} /etc/reticulum/config"
        RESTORED=$((RESTORED + 1))
    fi

    # Transport identity (CRITICAL)
    if [[ -f "$EXTRACT_DIR/etc/reticulum/storage/transport_identity" ]]; then
        cp -a "$EXTRACT_DIR/etc/reticulum/storage/transport_identity" /etc/reticulum/storage/transport_identity
        chmod 600 /etc/reticulum/storage/transport_identity
        echo -e "  ${GREEN}+${NC} /etc/reticulum/storage/transport_identity ${BOLD}(CRITICAL)${NC}"
        RESTORED=$((RESTORED + 1))
    fi

    # Identities
    if [[ -d "$EXTRACT_DIR/etc/reticulum/storage/identities" ]]; then
        cp -a "$EXTRACT_DIR/etc/reticulum/storage/identities" /etc/reticulum/storage/
        echo -e "  ${GREEN}+${NC} /etc/reticulum/storage/identities/"
        RESTORED=$((RESTORED + 1))
    fi

    # Ratchets
    if [[ -d "$EXTRACT_DIR/etc/reticulum/storage/ratchets" ]]; then
        cp -a "$EXTRACT_DIR/etc/reticulum/storage/ratchets" /etc/reticulum/storage/
        echo -e "  ${GREEN}+${NC} /etc/reticulum/storage/ratchets/"
        RESTORED=$((RESTORED + 1))
    fi

    # Known destinations
    if [[ -f "$EXTRACT_DIR/etc/reticulum/storage/known_destinations" ]]; then
        cp -a "$EXTRACT_DIR/etc/reticulum/storage/known_destinations" /etc/reticulum/storage/
        echo -e "  ${GREEN}+${NC} /etc/reticulum/storage/known_destinations"
        RESTORED=$((RESTORED + 1))
    fi

    # Interfaces
    if [[ -d "$EXTRACT_DIR/etc/reticulum/interfaces" ]]; then
        cp -a "$EXTRACT_DIR/etc/reticulum/interfaces" /etc/reticulum/
        echo -e "  ${GREEN}+${NC} /etc/reticulum/interfaces/"
        RESTORED=$((RESTORED + 1))
    fi

    # Fix permissions (rnsd needs access)
    chmod -R a+rX /etc/reticulum/storage/ 2>/dev/null || true
    chmod 600 /etc/reticulum/storage/transport_identity 2>/dev/null || true
fi

# --- meshtasticd ---
if [[ -d "$EXTRACT_DIR/etc/meshtasticd" ]]; then
    mkdir -p /etc/meshtasticd/config.d

    if [[ -f "$EXTRACT_DIR/etc/meshtasticd/config.yaml" ]]; then
        cp -a "$EXTRACT_DIR/etc/meshtasticd/config.yaml" /etc/meshtasticd/config.yaml
        echo -e "  ${GREEN}+${NC} /etc/meshtasticd/config.yaml"
        RESTORED=$((RESTORED + 1))
    fi

    if ls "$EXTRACT_DIR/etc/meshtasticd/config.d/"*.yaml &>/dev/null 2>&1; then
        cp -a "$EXTRACT_DIR/etc/meshtasticd/config.d/"*.yaml /etc/meshtasticd/config.d/
        local_count=$(ls "$EXTRACT_DIR/etc/meshtasticd/config.d/"*.yaml 2>/dev/null | wc -l)
        echo -e "  ${GREEN}+${NC} /etc/meshtasticd/config.d/ (${local_count} HAT configs)"
        RESTORED=$((RESTORED + 1))
    fi
fi

# --- MeshForge user config ---
if [[ -d "$EXTRACT_DIR/home/config/meshforge" ]]; then
    local_cfg="${TARGET_HOME}/.config/meshforge"
    mkdir -p "$local_cfg"

    # Copy all files from backup
    cp -a "$EXTRACT_DIR/home/config/meshforge/"* "$local_cfg/" 2>/dev/null || true

    # Fix ownership
    chown -R "${TARGET_USER}:${TARGET_USER}" "$local_cfg"

    # Identity files get restricted permissions
    for identity_file in "$local_cfg/gateway_identity"; do
        if [[ -f "$identity_file" ]]; then
            chmod 600 "$identity_file"
        fi
    done

    echo -e "  ${GREEN}+${NC} ~/.config/meshforge/ (configs + databases)"
    RESTORED=$((RESTORED + 1))
fi

# --- Claude Code AI memory ---
if [[ -d "$EXTRACT_DIR/home/claude" ]]; then
    local_claude="${TARGET_HOME}/.claude"
    mkdir -p "$local_claude"

    # Global memory
    if [[ -d "$EXTRACT_DIR/home/claude/memory" ]]; then
        mkdir -p "$local_claude/memory"
        cp -a "$EXTRACT_DIR/home/claude/memory/"* "$local_claude/memory/" 2>/dev/null || true
        echo -e "  ${GREEN}+${NC} ~/.claude/memory/ (global AI memory)"
        RESTORED=$((RESTORED + 1))
    fi

    # Project-specific memories
    if [[ -d "$EXTRACT_DIR/home/claude/projects" ]]; then
        for proj_dir in "$EXTRACT_DIR/home/claude/projects"/*/; do
            if [[ -d "$proj_dir" ]]; then
                proj_name=$(basename "$proj_dir")
                mkdir -p "$local_claude/projects/${proj_name}/memory"
                if [[ -d "${proj_dir}memory" ]]; then
                    cp -a "${proj_dir}memory/"* "$local_claude/projects/${proj_name}/memory/" 2>/dev/null || true
                fi
            fi
        done
        echo -e "  ${GREEN}+${NC} ~/.claude/projects/*/memory/ (project AI memories)"
        RESTORED=$((RESTORED + 1))
    fi

    # Claude settings
    if [[ -f "$EXTRACT_DIR/home/claude/settings.json" ]]; then
        cp -a "$EXTRACT_DIR/home/claude/settings.json" "$local_claude/settings.json"
        echo -e "  ${GREEN}+${NC} ~/.claude/settings.json"
        RESTORED=$((RESTORED + 1))
    fi

    # Fix ownership
    chown -R "${TARGET_USER}:${TARGET_USER}" "$local_claude"
fi

# --- Project-level Claude config ---
if [[ -f "$EXTRACT_DIR/opt/meshforge/.claude.json" ]]; then
    cp -a "$EXTRACT_DIR/opt/meshforge/.claude.json" "$INSTALL_DIR/.claude.json"
    echo -e "  ${GREEN}+${NC} /opt/meshforge/.claude.json"
    RESTORED=$((RESTORED + 1))
fi

echo ""
echo -e "  Restored ${BOLD}${RESTORED}${NC} items"

# ─────────────────────────────────────────────────────────────────
# Phase 5: Restart services
# ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}[5/6] Restarting services...${NC}"

STARTED=0

for svc in meshtasticd rnsd; do
    if systemctl is-enabled "$svc" &>/dev/null 2>&1; then
        if systemctl start "$svc" 2>/dev/null; then
            echo -e "  ${GREEN}+${NC} Started ${svc}"
            STARTED=$((STARTED + 1))
        else
            echo -e "  ${YELLOW}!${NC} ${svc} failed to start (check: journalctl -u ${svc})"
        fi
    else
        echo -e "  ${YELLOW}!${NC} ${svc} not enabled"
    fi
done

for svc in meshforge meshforge-map; do
    if systemctl is-enabled "$svc" &>/dev/null 2>&1; then
        systemctl start "$svc" 2>/dev/null || true
        echo -e "  ${GREEN}+${NC} Started ${svc}"
        STARTED=$((STARTED + 1))
    fi
done

# ─────────────────────────────────────────────────────────────────
# Phase 6: Verify
# ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}[6/6] Verifying restore...${NC}"

# Check RNS identity
if [[ -f /etc/reticulum/storage/transport_identity ]]; then
    IDENTITY_SIZE=$(stat -c %s /etc/reticulum/storage/transport_identity 2>/dev/null || echo 0)
    if [[ "$IDENTITY_SIZE" -gt 0 ]]; then
        IDENTITY_HASH=$(sha256sum /etc/reticulum/storage/transport_identity | cut -c1-16)
        echo -e "  ${GREEN}+${NC} RNS transport identity: ${BOLD}${IDENTITY_HASH}...${NC}"
    else
        echo -e "  ${RED}x${NC} RNS transport identity is empty!"
    fi
else
    echo -e "  ${YELLOW}!${NC} No RNS transport identity (new identity will be generated)"
fi

# Check gateway identity
if [[ -f "${TARGET_HOME}/.config/meshforge/gateway_identity" ]]; then
    echo -e "  ${GREEN}+${NC} Gateway identity present"
else
    echo -e "  ${YELLOW}!${NC} No gateway identity (will be generated on first gateway start)"
fi

# Check meshtasticd config
if [[ -f /etc/meshtasticd/config.yaml ]]; then
    echo -e "  ${GREEN}+${NC} meshtasticd config present"
else
    echo -e "  ${YELLOW}!${NC} No meshtasticd config"
fi

# Check Claude memory
local_claude_mem="${TARGET_HOME}/.claude/memory"
if [[ -d "$local_claude_mem" ]]; then
    mem_count=$(ls "$local_claude_mem/"*.md 2>/dev/null | wc -l)
    echo -e "  ${GREEN}+${NC} Claude memory: ${mem_count} files restored"
else
    echo -e "  ${YELLOW}!${NC} No Claude memory files"
fi

# Run post-install verification if available
if [[ -f "$INSTALL_DIR/scripts/verify_post_install.sh" ]]; then
    echo ""
    echo -e "  Running post-install verification..."
    bash "$INSTALL_DIR/scripts/verify_post_install.sh" --quiet 2>/dev/null && \
        echo -e "  ${GREEN}+${NC} Post-install verification passed" || \
        echo -e "  ${YELLOW}!${NC} Some verification checks failed (run verify_post_install.sh for details)"
fi

# ─────────────────────────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}╔════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  ${GREEN}Restore complete!${CYAN}                                     ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Node:       ${BOLD}$(hostname -s)${NC}"
echo -e "  From:       ${BACKUP_NAME}"
echo -e "  Restored:   ${RESTORED} items"
echo -e "  Services:   ${STARTED} started"
echo ""
echo -e "  ${CYAN}Next steps:${NC}"
echo -e "    1. Verify radio at http://localhost:9443"
echo -e "    2. Check RNS: rnstatus"
echo -e "    3. Launch TUI: sudo meshforge"
echo ""
