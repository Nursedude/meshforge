#!/bin/bash
#
# MeshForge Quick Update Script
#
# Updates an existing MeshForge installation in-place.
# Preserves all configuration and user data.
#
# Usage:
#   cd /opt/meshforge && sudo ./scripts/update.sh
#   sudo ./scripts/update.sh              # from any directory
#   sudo ./scripts/update.sh --branch alpha  # update to specific branch
#
# What it does:
#   1. Fetches latest code from GitHub
#   2. Updates Python dependencies if changed
#   3. Reinstalls desktop integration (icons/launchers)
#   4. Updates systemd service files (rnsd, meshforge, nomadnet)
#   5. Preserves: /etc/meshforge/, ~/.config/meshforge/, radio configs
#
# What it does NOT do:
#   - Reinstall meshtasticd or rnsd (use install_noc.sh for that)
#   - Modify radio configurations
#   - Touch message queue or user settings
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Defaults
INSTALL_DIR="/opt/meshforge"
BRANCH=""
SKIP_DESKTOP=false
SKIP_DEPS=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --branch|-b)
            BRANCH="$2"
            shift 2
            ;;
        --skip-desktop)
            SKIP_DESKTOP=true
            shift
            ;;
        --skip-deps)
            SKIP_DEPS=true
            shift
            ;;
        --help|-h)
            echo "MeshForge Update Script"
            echo ""
            echo "Usage: sudo $0 [options]"
            echo ""
            echo "Options:"
            echo "  --branch, -b BRANCH   Update to specific branch (default: current)"
            echo "  --skip-desktop        Skip desktop integration update"
            echo "  --skip-deps           Skip Python dependency update"
            echo "  --help, -h            Show this help"
            echo ""
            echo "Examples:"
            echo "  sudo $0                    # Update current branch"
            echo "  sudo $0 -b alpha           # Switch to alpha branch"
            echo "  sudo $0 -b main            # Switch to main branch"
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
echo "║              MeshForge Quick Update                       ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Check root
if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}Error: This script must be run as root${NC}"
    echo "Please run: sudo $0"
    exit 1
fi

# Find installation
if [[ ! -d "$INSTALL_DIR" ]] && [[ ! -L "$INSTALL_DIR" ]]; then
    # Try current directory
    if [[ -f "./src/launcher_tui/main.py" ]]; then
        INSTALL_DIR="$(pwd)"
    else
        echo -e "${RED}Error: MeshForge not found at $INSTALL_DIR${NC}"
        echo "Run this script from your MeshForge directory, or install first with:"
        echo "  sudo bash scripts/install_noc.sh"
        exit 1
    fi
fi

cd "$INSTALL_DIR"

# Resolve symlinks
INSTALL_DIR="$(pwd -P)"
echo -e "Installation: ${GREEN}$INSTALL_DIR${NC}"

# Get current state
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
CURRENT_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
echo -e "Current branch: ${CYAN}$CURRENT_BRANCH${NC} ($CURRENT_COMMIT)"

# ─────────────────────────────────────────────────────────────────
# Step 1: Fetch updates
# ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[1/5] Fetching updates...${NC}"

git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true

if ! git fetch origin 2>&1; then
    echo -e "${YELLOW}Warning: Could not fetch from origin${NC}"
    echo "Check your network connection"
fi

# Switch branch if requested
if [[ -n "$BRANCH" ]] && [[ "$BRANCH" != "$CURRENT_BRANCH" ]]; then
    echo -e "Switching to branch: ${GREEN}$BRANCH${NC}"

    # Check if branch exists
    if git show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then
        git checkout "$BRANCH" 2>/dev/null || git checkout -b "$BRANCH" "origin/$BRANCH"
        CURRENT_BRANCH="$BRANCH"
    else
        echo -e "${RED}Error: Branch '$BRANCH' not found${NC}"
        echo "Available branches:"
        git branch -r | grep -v HEAD | sed 's/origin\//  /'
        exit 1
    fi
fi

# Pull updates
echo "Pulling latest changes..."
if git pull origin "$CURRENT_BRANCH" 2>&1; then
    NEW_COMMIT=$(git rev-parse --short HEAD)
    if [[ "$CURRENT_COMMIT" != "$NEW_COMMIT" ]]; then
        echo -e "${GREEN}Updated: $CURRENT_COMMIT → $NEW_COMMIT${NC}"

        # Show what changed
        echo ""
        echo "Recent changes:"
        git log --oneline "$CURRENT_COMMIT..$NEW_COMMIT" 2>/dev/null | head -10
    else
        echo -e "${GREEN}Already up to date${NC}"
    fi
else
    echo -e "${YELLOW}Warning: Pull failed, continuing with current code${NC}"
fi

# ─────────────────────────────────────────────────────────────────
# Step 2: Update Python dependencies
# ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[2/5] Checking Python dependencies...${NC}"

if [[ "$SKIP_DEPS" == "true" ]]; then
    echo -e "${YELLOW}Skipped (--skip-deps)${NC}"
else
    VENV_DIR="$INSTALL_DIR/venv"

    if [[ -d "$VENV_DIR" ]]; then
        # Check if requirements.txt changed
        REQ_HASH=$(md5sum requirements.txt 2>/dev/null | cut -d' ' -f1)
        CACHED_HASH=""
        if [[ -f "$VENV_DIR/.requirements_hash" ]]; then
            CACHED_HASH=$(cat "$VENV_DIR/.requirements_hash")
        fi

        if [[ "$REQ_HASH" != "$CACHED_HASH" ]]; then
            echo "Requirements changed, updating..."
            "$VENV_DIR/bin/pip" install -q --upgrade pip
            "$VENV_DIR/bin/pip" install -q -r requirements.txt
            echo "$REQ_HASH" > "$VENV_DIR/.requirements_hash"
            echo -e "${GREEN}Dependencies updated${NC}"
        else
            echo -e "${GREEN}Dependencies up to date${NC}"
        fi
    else
        echo -e "${YELLOW}No venv found, skipping dependency update${NC}"
        echo "Run install_noc.sh to set up Python environment"
    fi
fi

# ─────────────────────────────────────────────────────────────────
# Step 3: Update desktop integration
# ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[3/5] Updating desktop integration...${NC}"

if [[ "$SKIP_DESKTOP" == "true" ]]; then
    echo -e "${YELLOW}Skipped (--skip-desktop)${NC}"
elif [[ -f "$INSTALL_DIR/scripts/install-desktop.sh" ]]; then
    bash "$INSTALL_DIR/scripts/install-desktop.sh" 2>&1 | grep -E "^(Installing|Updated|✓)" || true
    echo -e "${GREEN}Desktop integration updated${NC}"
else
    echo -e "${YELLOW}Desktop script not found, skipping${NC}"
fi

# ─────────────────────────────────────────────────────────────────
# Step 4: Update service files
# ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[4/5] Updating systemd service files...${NC}"

SVC_UPDATED=false

# Update meshforge.service from repo template (if it exists in /etc/systemd/system/)
if [[ -f /etc/systemd/system/meshforge.service ]] && [[ -f "$INSTALL_DIR/scripts/meshforge.service" ]]; then
    cp "$INSTALL_DIR/scripts/meshforge.service" /etc/systemd/system/meshforge.service
    echo -e "  ${GREEN}✓ meshforge.service updated${NC}"
    SVC_UPDATED=true
fi

# Update rnsd.service (system-level) if it exists
if [[ -f /etc/systemd/system/rnsd.service ]]; then
    RNSD_BIN=$(command -v rnsd 2>/dev/null || echo "/usr/local/bin/rnsd")
    # Only update if the current service lacks crash-loop protection
    if ! grep -q "StartLimitBurst" /etc/systemd/system/rnsd.service 2>/dev/null; then
        cat > /etc/systemd/system/rnsd.service << RNSD_SVC
[Unit]
Description=Reticulum Network Stack Daemon
Documentation=https://reticulum.network
After=network-online.target
Wants=network-online.target

# Stop crash-looping after 5 failures in 60 seconds
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=root
ExecStart=${RNSD_BIN} --service
Restart=on-failure
RestartSec=5

StandardOutput=journal
StandardError=journal
SyslogIdentifier=rnsd

[Install]
WantedBy=multi-user.target
RNSD_SVC
        echo -e "  ${GREEN}✓ rnsd.service updated (added crash-loop protection)${NC}"
        SVC_UPDATED=true
    else
        echo -e "  ${GREEN}✓ rnsd.service already current${NC}"
    fi
fi

# Deploy the meshtasticd MUDP-leg self-heal guard (oneshot + timer). The guard
# script self-gates: it no-ops unless meshtasticd is active AND the box has been
# seen actually using UDP multicast (224.0.0.69:4403), so it is safe to install
# fleet-wide. Boot-race origin 2026-06-24 — meshtasticd came up "active" without
# binding the multicast socket, so the meshforge channel went dark ~13h.
# See scripts/meshtasticd_mudp_guard.sh + docs/fleet_roles.yaml.
if [[ -f "$INSTALL_DIR/templates/systemd/meshtasticd-mudp-guard.timer" ]]; then
    cp "$INSTALL_DIR/templates/systemd/meshtasticd-mudp-guard.service" /etc/systemd/system/ 2>/dev/null || true
    cp "$INSTALL_DIR/templates/systemd/meshtasticd-mudp-guard.timer"   /etc/systemd/system/ 2>/dev/null || true
    systemctl daemon-reload 2>/dev/null || true
    SVC_UPDATED=true
    # Enable only where meshtasticd exists (role model lists it on meshtasticd
    # roles); the guard is a cheap no-op everywhere else, but no point arming a
    # timer on a box that has no radio daemon to guard.
    if systemctl list-unit-files meshtasticd.service &>/dev/null; then
        systemctl enable --now meshtasticd-mudp-guard.timer 2>/dev/null || true
        echo -e "  ${GREEN}✓ meshtasticd-mudp-guard.timer deployed + enabled${NC}"
    else
        echo -e "  ${GREEN}✓ meshtasticd-mudp-guard units deployed (no meshtasticd; timer not armed)${NC}"
    fi
fi

# Deploy user-level service templates
REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME=$(eval echo "~${REAL_USER}")
REAL_UID="$(id -u "${REAL_USER}" 2>/dev/null || echo "")"
USER_SYSTEMD_DIR="${REAL_HOME}/.config/systemd/user"

# User-scope systemctl that bridges a sudo/root context to the operator's
# user bus (Issue #45 documented incantation, shared with install_nomadnet.sh).
# Used to daemon-reload + restart changed USER units so a `git pull` actually
# lands the new code on the running daemon (the defect: nothing restarted the
# mini user units before, leaving the daemon on OLD code until a hand-restart).
run_user_systemctl() {
    if [[ "$(id -un)" == "${REAL_USER}" ]]; then
        systemctl --user "$@"
    elif [[ -n "${REAL_UID}" ]]; then
        sudo -u "${REAL_USER}" -H \
            env "XDG_RUNTIME_DIR=/run/user/${REAL_UID}" \
                "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/${REAL_UID}/bus" \
            systemctl --user "$@"
    else
        return 1
    fi
}

USER_SVC_UPDATED=false
if [[ -d "$INSTALL_DIR/templates/systemd" ]]; then
    mkdir -p "$USER_SYSTEMD_DIR"
    # *-user.service templates map to <name>.service (existing behavior).
    for tmpl in "$INSTALL_DIR/templates/systemd/"*-user.service; do
        if [[ -f "$tmpl" ]]; then
            svc_name=$(basename "$tmpl" | sed 's/-user\.service/.service/')
            cp "$tmpl" "$USER_SYSTEMD_DIR/$svc_name" 2>/dev/null || true
        fi
    done
    # mini-dudeai user units have NO -user suffix, so the loop above misses
    # them; enumerate them explicitly (service + dream service/timer). Copy
    # 1:1 — the unit names match the template names exactly.
    for mini_tmpl in \
        "$INSTALL_DIR/templates/systemd/meshforge-mini-dudeai.service" \
        "$INSTALL_DIR/templates/systemd/meshforge-mini-dudeai-dream.service" \
        "$INSTALL_DIR/templates/systemd/meshforge-mini-dudeai-dream.timer" \
        "$INSTALL_DIR/templates/systemd/meshforge-mini-dudeai-claw.service" \
        "$INSTALL_DIR/templates/systemd/meshforge-mini-dudeai-claw@.service"; do
        if [[ -f "$mini_tmpl" ]]; then
            cp "$mini_tmpl" "$USER_SYSTEMD_DIR/$(basename "$mini_tmpl")" 2>/dev/null || true
        fi
    done
    chown -R "${REAL_USER}:" "$USER_SYSTEMD_DIR" 2>/dev/null || true
    USER_SVC_UPDATED=true
    echo -e "  ${GREEN}✓ User service templates deployed${NC}"
fi

# Reload systemd if anything changed
if $SVC_UPDATED; then
    systemctl daemon-reload 2>/dev/null || true
    echo -e "  ${GREEN}✓ systemctl daemon-reload${NC}"
fi

# Restart changed USER units on the operator's bus so the running daemon picks
# up the new code. try-restart only touches an already-active unit, so an
# operator-disabled/stopped mini daemon stays stopped (intent honored). Without
# this step the git pull above leaves the daemon on OLD code until a manual
# restart — the deploy gap this fix closes.
if $USER_SVC_UPDATED; then
    if run_user_systemctl daemon-reload 2>/dev/null; then
        # mini-dudeai is the daemon to refresh on code change; the dream
        # .timer pulls a fresh oneshot at its next fire, so it needs no
        # restart here.
        run_user_systemctl try-restart meshforge-mini-dudeai.service 2>/dev/null || true
        # standalone dude-claw sibling — try-restart is a no-op on boxes
        # that don't run it (only the claw-brain box does)
        run_user_systemctl try-restart meshforge-mini-dudeai-claw.service 2>/dev/null || true
        # Templated dude-claw brains — enumerate ACTIVE @-instances instead
        # of hardcoding claw02 (a future @claw03 would silently re-open #79).
        for _cin in $(run_user_systemctl list-units --plain --no-legend 'meshforge-mini-dudeai-claw@*.service' 2>/dev/null | cut -d' ' -f1); do
            run_user_systemctl try-restart "$_cin" 2>/dev/null || true
        done
        # Other long-lived USER daemons that run MeshForge code from this repo
        # and would otherwise sit on OLD code after the pull (the #79 deploy
        # gap, surfaced 2026-06-15 by the §3b-ii honesty guard): the lab echo
        # responder (lab.lxmf_echo) and the nomadnet silence watcher
        # (scripts/nomadnet_silence_watch.py). try-restart is a no-op on boxes
        # that don't run them, and honors an operator-disabled unit. Oneshot
        # user units (synth-soak, lab-rollup, drain-snapshot, tracer, dream)
        # are deliberately NOT here — they pull fresh code at their next timer
        # fire, so a restart would be pointless churn.
        run_user_systemctl try-restart meshforge-echo.service 2>/dev/null || true
        run_user_systemctl try-restart nomadnet-silence-watch.service 2>/dev/null || true
        echo -e "  ${GREEN}✓ mini-dudeai + lab user daemons refreshed (try-restart)${NC}"
    else
        echo -e "  ${YELLOW}⚠ Could not reach the operator user bus to restart mini-dudeai.${NC}"
        echo -e "  ${YELLOW}  As ${REAL_USER}: systemctl --user restart meshforge-mini-dudeai.service${NC}"
    fi
fi

# ─────────────────────────────────────────────────────────────────
# Step 5: Verify installation
# ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[5/5] Verifying installation...${NC}"

# Check version
VERSION=$(python3 -c "import sys; sys.path.insert(0, '$INSTALL_DIR/src'); from __version__ import __version__; print(__version__)" 2>/dev/null || echo "unknown")
echo -e "Version: ${GREEN}$VERSION${NC}"

# Quick import test
if python3 -c "import sys; sys.path.insert(0, '$INSTALL_DIR/src'); from gateway.circuit_breaker import CircuitBreaker" 2>/dev/null; then
    echo -e "Circuit breaker: ${GREEN}OK${NC}"
else
    echo -e "Circuit breaker: ${YELLOW}Not available${NC}"
fi

if python3 -c "import sys; sys.path.insert(0, '$INSTALL_DIR/src'); from gateway.bridge_health import BridgeStatus" 2>/dev/null; then
    echo -e "Bridge health: ${GREEN}OK${NC}"
else
    echo -e "Bridge health: ${YELLOW}Not available${NC}"
fi

# ─────────────────────────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║              Update complete!                             ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "Branch: $CURRENT_BRANCH"
echo "Version: $VERSION"
echo ""
echo "To run MeshForge:"
echo "  meshforge              # If desktop installed"
echo "  sudo python3 $INSTALL_DIR/src/launcher_tui/main.py"
echo ""
