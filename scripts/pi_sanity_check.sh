#!/bin/bash
# MeshForge Pi Sanity Check
# Run after updates to verify the installation is healthy
#
# Usage: sudo ./scripts/pi_sanity_check.sh
#        or: cd /path/to/meshforge && sudo bash scripts/pi_sanity_check.sh

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

# Find script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

echo "========================================"
echo "  MeshForge Pi Sanity Check"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"
echo ""

FAILURES=0
WARNINGS=0

# Helper functions
pass() {
    echo -e "${GREEN}[PASS]${NC} $1"
}

fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    FAILURES=$((FAILURES + 1))
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
    WARNINGS=$((WARNINGS + 1))
}

info() {
    echo -e "[INFO] $1"
}

# 1. Python version check
echo "--- Python Environment ---"
if python3 --version 2>/dev/null | grep -q "Python 3"; then
    pass "Python 3 available: $(python3 --version 2>&1)"
else
    fail "Python 3 not found"
fi

# 2. Import check - TUI
echo ""
echo "--- Import Tests ---"
if python3 -c "from src.launcher_tui.main import MeshForgeLauncher" 2>/dev/null; then
    pass "TUI imports successfully"
else
    fail "TUI import failed"
fi

# 3. Import check - Version
if python3 -c "from src.__version__ import __version__; print(f'Version: {__version__}')" 2>/dev/null; then
    pass "Version module OK"
else
    warn "Version module import issue"
fi

# 4. Import check - RF tools
if python3 -c "from src.utils.rf import haversine_distance, fspl_db" 2>/dev/null; then
    pass "RF tools import OK"
else
    fail "RF tools import failed"
fi

# 5. Import check - Service check
if python3 -c "from src.utils.service_check import check_service, ServiceState" 2>/dev/null; then
    pass "Service check module OK"
else
    warn "Service check module issue (may use fallback)"
fi

# 6. Pytest availability
echo ""
echo "--- Test Infrastructure ---"
if python3 -m pytest --version 2>/dev/null; then
    pass "pytest available"

    # 7. Run smoke tests if they exist
    if [ -f "tests/test_tui_smoke.py" ]; then
        echo ""
        echo "--- Running TUI Smoke Tests ---"
        if python3 -m pytest tests/test_tui_smoke.py -v --tb=short -x 2>&1; then
            pass "TUI smoke tests passed"
        else
            fail "TUI smoke tests failed"
        fi
    else
        info "No TUI smoke tests found (tests/test_tui_smoke.py)"
    fi

    # 8. Run RF tests (critical for HAM operations)
    echo ""
    echo "--- Running RF Tests ---"
    if python3 -m pytest tests/test_rf.py -v --tb=short 2>&1; then
        pass "RF tests passed"
    else
        fail "RF tests failed"
    fi

    # 9. Run service check tests
    echo ""
    echo "--- Running Service Check Tests ---"
    if [ -f "tests/test_service_check.py" ]; then
        if python3 -m pytest tests/test_service_check.py -v --tb=short 2>&1; then
            pass "Service check tests passed"
        else
            warn "Service check tests had issues"
        fi
    else
        info "No service check tests found"
    fi
else
    warn "pytest not installed - skipping test execution"
    info "Install with: pip install pytest"
fi

# 10. Service status checks (if running as root)
echo ""
echo "--- Service Status ---"
if [ "$(id -u)" -eq 0 ]; then
    # Check meshtasticd
    if systemctl is-active --quiet meshtasticd 2>/dev/null; then
        pass "meshtasticd is running"
    else
        info "meshtasticd is not running (may be expected)"
    fi

    # Check rnsd
    if systemctl is-active --quiet rnsd 2>/dev/null || pgrep -x rnsd >/dev/null 2>&1; then
        pass "rnsd is running"
    else
        info "rnsd is not running (may be expected)"
    fi

    # Check mosquitto
    if systemctl is-active --quiet mosquitto 2>/dev/null; then
        pass "mosquitto MQTT broker is running"
    else
        info "mosquitto is not running (optional)"
    fi
else
    info "Run as root (sudo) to check service status"
fi

# 11. File permissions check
echo ""
echo "--- File Permissions ---"
if [ -r "src/launcher_tui/main.py" ]; then
    pass "TUI main.py is readable"
else
    fail "Cannot read src/launcher_tui/main.py"
fi

if [ -d "src/utils" ]; then
    pass "Utils directory exists"
else
    fail "Utils directory missing"
fi

# Summary
echo ""
echo "========================================"
echo "  Summary"
echo "========================================"
if [ $FAILURES -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo -e "${GREEN}All checks passed!${NC}"
    exit 0
elif [ $FAILURES -eq 0 ]; then
    echo -e "${YELLOW}Passed with $WARNINGS warning(s)${NC}"
    exit 0
else
    echo -e "${RED}$FAILURES failure(s), $WARNINGS warning(s)${NC}"
    exit 1
fi
