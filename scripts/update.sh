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
#   4. Preserves: /etc/meshforge/, ~/.config/meshforge/, radio configs
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
echo -e "${CYAN}[1/4] Fetching updates...${NC}"

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
echo -e "${CYAN}[2/4] Checking Python dependencies...${NC}"

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
echo -e "${CYAN}[3/4] Updating desktop integration...${NC}"

if [[ "$SKIP_DESKTOP" == "true" ]]; then
    echo -e "${YELLOW}Skipped (--skip-desktop)${NC}"
elif [[ -f "$INSTALL_DIR/scripts/install-desktop.sh" ]]; then
    bash "$INSTALL_DIR/scripts/install-desktop.sh" 2>&1 | grep -E "^(Installing|Updated|✓)" || true
    echo -e "${GREEN}Desktop integration updated${NC}"
else
    echo -e "${YELLOW}Desktop script not found, skipping${NC}"
fi

# ─────────────────────────────────────────────────────────────────
# Step 4: Verify installation
# ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[4/4] Verifying installation...${NC}"

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
