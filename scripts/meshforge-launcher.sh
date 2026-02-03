#!/bin/bash
# MeshForge Launcher Script
# Launches MeshForge tools and services

MESHFORGE_DIR="/opt/meshforge"

# Function to launch TUI with sudo
launch_tui() {
    local script="$MESHFORGE_DIR/src/launcher_tui/main.py"

    if [ "$EUID" -eq 0 ]; then
        exec python3 "$script" "$@"
    else
        exec sudo python3 "$script" "$@"
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
        exec python3 -c "
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
        exec sudo python3 -c "
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
