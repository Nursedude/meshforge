#!/bin/bash
# Fix Python packaging conflict for Meshtasticd installation
# This resolves the "Cannot uninstall packaging 25.0" error

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}=========================================${NC}"
echo -e "${CYAN}Fixing Python Packaging Conflict${NC}"
echo -e "${CYAN}=========================================${NC}"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Error: This script must be run as root (use sudo)${NC}"
    exit 1
fi

# Hardened install primitives (ensure_pip + checked pip + transcript).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/install_common.sh
source "$SCRIPT_DIR/lib/install_common.sh"
mf_log_init

echo -e "\n${YELLOW}Step 1: Removing Debian python3-packaging package${NC}"
apt-get remove --purge python3-packaging -y || true

echo -e "\n${YELLOW}Step 2: Cleaning up package manager${NC}"
apt-get autoremove -y
apt-get clean

echo -e "\n${YELLOW}Step 3: Reinstalling packaging via pip${NC}"
if ! mf_pip_install python3 --upgrade --force-reinstall --break-system-packages packaging; then
    echo -e "${RED}ERROR: packaging reinstall failed (see ${MF_INSTALL_LOG:-console})${NC}" >&2
    exit 1
fi

echo -e "\n${YELLOW}Step 4: Installing setuptools and wheel${NC}"
if ! mf_pip_install python3 --upgrade --break-system-packages setuptools wheel; then
    echo -e "${RED}ERROR: setuptools/wheel install failed (see ${MF_INSTALL_LOG:-console})${NC}" >&2
    exit 1
fi

# Verify the conflict is actually resolved — the whole point of this script.
if ! mf_verify_import python3 packaging; then
    echo -e "${RED}ERROR: 'packaging' still not importable after reinstall${NC}" >&2
    exit 1
fi

echo -e "\n${GREEN}=========================================${NC}"
echo -e "${GREEN}Packaging conflict resolved!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo -e "${CYAN}You can now retry the meshtasticd installation${NC}"
