#!/bin/bash
# MeshForge Launcher Script
# Launches MeshForge tools and services

MESHFORGE_DIR="/opt/meshforge"

# Root's bytecode must not land in the repo — see the file for the fleet-wide
# census that forced this. Sourced, never copied (scripts/lib convention).
# shellcheck source=lib/pycache_prefix.sh
. "$MESHFORGE_DIR/scripts/lib/pycache_prefix.sh"

# THE interpreter this install actually uses. `.no-venv` is the installer's
# own marker; without it the venv is authoritative, and using system python3
# there would silently miss every dependency installed into the venv.
mf_python() {
    if [ -f "$MESHFORGE_DIR/.no-venv" ] || [ ! -x "$MESHFORGE_DIR/venv/bin/python" ]; then
        echo "python3"
    else
        echo "$MESHFORGE_DIR/venv/bin/python"
    fi
}

# Function to launch the NOC with sudo.
#
# Routes through src/launcher.py, NOT straight at launcher_tui/main.py:
# launcher.py does profile detection, the startup health check and the setup
# wizard, then `os.execv`s into launcher_tui/main.py (src/launcher.py:288)
# with sys.executable — so the TUI still appears and the environment,
# PYTHONPYCACHEPREFIX included, is inherited across the exec.
#
# Unifying on this shape 2026-09-20: /usr/local/bin/meshforge had drifted into
# TWO different programs across the fleet (6 boxes on the installer's
# launcher.py form, 2 on a stale copy of this script that went straight to the
# TUI). Going through launcher.py is what the installer generates, what most
# boxes already ran, and what CLAUDE.md documents as the entry point; the
# maps/prometheus subcommands below are kept so the other boxes lose nothing.
launch_tui() {
    cd "$MESHFORGE_DIR" || exit 1
    local py; py="$(mf_python)"

    if [ "$EUID" -eq 0 ]; then
        exec env PYTHONPYCACHEPREFIX="$MF_ROOT_PYCACHE" "$py" src/launcher.py "$@"
    else
        exec sudo PYTHONPYCACHEPREFIX="$MF_ROOT_PYCACHE" "$py" src/launcher.py "$@"
    fi
}

# Function to launch coverage map generator
launch_maps() {
    cd "$MESHFORGE_DIR"
    exec python3 -c "
from src.utils.coverage_map import CoverageMapGenerator
import sys

gen = CoverageMapGenerator()
output = sys.argv[1] if len(sys.argv) > 1 else 'coverage_map.html'
gen.generate(output)
print(f'Map generated: {output}')
" "$@"
}

# Function to launch prometheus metrics server
launch_prometheus() {
    local port="${1:-9090}"
    cd "$MESHFORGE_DIR"

    if [ "$EUID" -eq 0 ]; then
        exec env PYTHONPYCACHEPREFIX="$MF_ROOT_PYCACHE" python3 -c "
from src.utils.metrics_export import start_metrics_server
import signal
import sys

port = int(sys.argv[1]) if len(sys.argv) > 1 else 9090
print(f'Starting Prometheus metrics server on port {port}...')
print(f'Scrape endpoint: http://localhost:{port}/metrics')
print('Press Ctrl+C to stop')

server = start_metrics_server(port=port)
signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
signal.pause()
" "$port"
    else
        exec sudo PYTHONPYCACHEPREFIX="$MF_ROOT_PYCACHE" python3 -c "
from src.utils.metrics_export import start_metrics_server
import signal
import sys

port = int(sys.argv[1]) if len(sys.argv) > 1 else 9090
print(f'Starting Prometheus metrics server on port {port}...')
print(f'Scrape endpoint: http://localhost:{port}/metrics')
print('Press Ctrl+C to stop')

server = start_metrics_server(port=port)
signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
signal.pause()
" "$port"
    fi
}

# Show usage help
show_help() {
    echo "MeshForge - Mesh Network Operations Center"
    echo ""
    echo "Usage: meshforge [command] [options]"
    echo ""
    echo "Commands:"
    echo "  (none)         Launch TUI menu (default)"
    echo "  tui            Same as default"
    echo "  maps [file]    Generate coverage map (default: coverage_map.html)"
    echo "  prometheus [p] Start Prometheus metrics server (default port: 9090)"
    echo "  help           Show this help message"
    echo ""
    echo "The TUI uses whiptail/dialog for a raspi-config style"
    echo "interface that works over SSH."
    echo ""
    echo "Examples:"
    echo "  meshforge                  # Launch TUI menu"
    echo "  meshforge maps output.html # Generate coverage map"
    echo "  meshforge prometheus 8080  # Start metrics on port 8080"
}

# Determine which interface to launch
case "$1" in
    tui)
        shift
        launch_tui "$@"
        ;;
    maps|map)
        shift
        launch_maps "$@"
        ;;
    prometheus|metrics)
        shift
        launch_prometheus "$@"
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        launch_tui "$@"
        ;;
esac
