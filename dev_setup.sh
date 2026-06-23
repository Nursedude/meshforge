#!/bin/bash
#
# MeshForge - Local Development Setup
#
# For users who clone to their home directory instead of using the full installer.
# Handles Python's externally-managed-environment (PEP 668) on Debian/Bookworm/RPi.
#
# Usage:
#   cd ~/meshforge
#   ./dev_setup.sh
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}MeshForge - Local Development Setup${NC}"
echo ""

# Check we're in the meshforge directory
if [[ ! -f "src/__version__.py" ]]; then
    echo -e "${RED}Error: Run this script from the meshforge directory${NC}"
    echo "  cd ~/meshforge && ./dev_setup.sh"
    exit 1
fi

MESHFORGE_DIR=$(pwd)

# Hardened install primitives (ensure_pip + checked pip + import verify + the
# install transcript). This also adds the pip bootstrap dev_setup never had —
# it assumed pip already worked, the documented fresh-user failure.
# shellcheck source=scripts/lib/install_common.sh
source "$MESHFORGE_DIR/scripts/lib/install_common.sh"
mf_log_init

# Option 1: Create local venv (cleanest)
setup_venv() {
    echo -e "${CYAN}Creating virtual environment...${NC}"
    python3 -m venv .venv --system-site-packages

    echo -e "${CYAN}Installing dependencies in venv...${NC}"
    local vpy=".venv/bin/python"
    mf_ensure_pip "$vpy" || { echo -e "${RED}pip unavailable in venv${NC}" >&2; exit 1; }
    mf_pip_install "$vpy" --timeout 60 --upgrade pip \
        || { echo -e "${RED}pip self-upgrade failed${NC}" >&2; exit 1; }
    mf_pip_install "$vpy" --timeout 60 -r requirements.txt \
        || { echo -e "${RED}dependency install failed (see ${MF_INSTALL_LOG:-console})${NC}" >&2; exit 1; }
    mf_verify_import "$vpy" rich \
        || { echo -e "${RED}deps installed but 'rich' not importable in venv${NC}" >&2; exit 1; }

    # Create activation helper
    cat > activate.sh << 'EOF'
#!/bin/bash
# Activate MeshForge virtual environment
source "$(dirname "$0")/.venv/bin/activate"
echo "MeshForge venv activated. Run: python3 src/launcher.py"
EOF
    chmod +x activate.sh

    # Create run helper
    cat > run.sh << 'EOF'
#!/bin/bash
# Run MeshForge from local venv
cd "$(dirname "$0")"
exec .venv/bin/python src/launcher.py "$@"
EOF
    chmod +x run.sh

    echo ""
    echo -e "${GREEN}Setup complete!${NC}"
    echo ""
    echo -e "${CYAN}To run MeshForge:${NC}"
    echo "  ./run.sh                    # Quick launch"
    echo "  source ./activate.sh        # Activate venv for development"
    echo "  python3 src/launcher.py     # Then run manually"
}

# Option 2: Install with --break-system-packages (simpler for dedicated Pi)
setup_system() {
    echo -e "${CYAN}Installing dependencies system-wide...${NC}"
    mf_ensure_pip python3 || { echo -e "${RED}pip unavailable${NC}" >&2; exit 1; }
    mf_pip_install python3 $(mf_pip_args python3) -r requirements.txt \
        || { echo -e "${RED}dependency install failed (see ${MF_INSTALL_LOG:-console})${NC}" >&2; exit 1; }
    mf_verify_import python3 rich \
        || { echo -e "${RED}deps installed but 'rich' not importable${NC}" >&2; exit 1; }

    echo ""
    echo -e "${GREEN}Setup complete!${NC}"
    echo ""
    echo -e "${CYAN}To run MeshForge:${NC}"
    echo "  python3 src/launcher.py"
    echo "  python3 src/standalone.py"
}

# Check for externally managed environment
if mf_pep668_active; then
    echo -e "${YELLOW}Detected: Externally managed Python environment (PEP 668)${NC}"
    echo -e "${YELLOW}This is common on Debian Bookworm, Ubuntu 23.04+, and Raspberry Pi OS.${NC}"
    echo ""
    echo "Choose installation method:"
    echo ""
    echo "  1) Virtual environment (recommended for development)"
    echo "     - Isolated Python environment"
    echo "     - Creates .venv/ directory"
    echo "     - Use ./run.sh to launch"
    echo ""
    echo "  2) System packages with --break-system-packages"
    echo "     - Simpler for dedicated MeshForge Pi"
    echo "     - Installs to system Python packages"
    echo "     - Run directly with python3"
    echo ""
    read -p "Choose [1/2] (default: 1): " choice

    case "$choice" in
        2)
            setup_system
            ;;
        *)
            setup_venv
            ;;
    esac
else
    # Standard pip install works
    echo -e "${CYAN}Installing dependencies...${NC}"
    mf_ensure_pip python3 || { echo -e "${RED}pip unavailable${NC}" >&2; exit 1; }
    mf_pip_install python3 --timeout 60 -r requirements.txt \
        || { echo -e "${RED}dependency install failed (see ${MF_INSTALL_LOG:-console})${NC}" >&2; exit 1; }
    mf_verify_import python3 rich \
        || { echo -e "${RED}deps installed but 'rich' not importable${NC}" >&2; exit 1; }

    echo ""
    echo -e "${GREEN}Setup complete!${NC}"
    echo ""
    echo -e "${CYAN}To run MeshForge:${NC}"
    echo "  python3 src/launcher.py"
fi
