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

echo -e "\n${YELLOW}Step 1: Removing Debian python3-packaging package${NC}"
apt-get remove --purge python3-packaging -y || true

echo -e "\n${YELLOW}Step 2: Cleaning up package manager${NC}"
apt-get autoremove -y
apt-get clean

echo -e "\n${YELLOW}Step 3: Reinstalling packaging via pip${NC}"
python3 -m pip install --upgrade --force-reinstall packaging --break-system-packages

echo -e "\n${YELLOW}Step 4: Installing setuptools and wheel${NC}"
python3 -m pip install --upgrade setuptools wheel --break-system-packages

echo -e "\n${GREEN}=========================================${NC}"
echo -e "${GREEN}Packaging conflict resolved!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo -e "${CYAN}You can now retry the meshtasticd installation${NC}"
