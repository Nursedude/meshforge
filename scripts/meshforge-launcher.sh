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
# PYTHONPYCACHEPREFIX included, is inherited across the exec (VERIFIED live
# 2026-09-20: the running main.py's /proc environ carried the prefix).
# ⚠️ launcher.py's DEFAULT is its interface menu plus NOC service startup;
# it goes straight to the TUI only with `--tui` or a saved auto_launch
# preference. `meshforge tui` / `meshforge-tui` pass `--tui` (see below).
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

# ---------------------------------------------------------------------------
# The five OTHER installed commands (2026-09-20). Until now install_noc.sh
# wrote /usr/local/bin/meshforge-noc/-lora/-status/-web/-map as generated copies:
# frozen at install time, and the -noc one ran a privileged venv python with
# NO pycache prefix on every box it was typed on (root bytecode in the repo).
# They are now symlinks to this script, dispatched on basename below, so a
# pull moves them and scripts/guard_drill.py Layer D covers each name. The
# bodies are the installer's heredocs, moved here verbatim except for the
# interpreter (mf_python, honouring .no-venv) and the prefix on privileged runs.
# ---------------------------------------------------------------------------

# meshforge-noc: the NOC orchestrator (privileged).
launch_noc() {
    cd "$MESHFORGE_DIR/src" || exit 1
    local py; py="$(mf_python)"
    if [ "$EUID" -eq 0 ]; then
        exec env PYTHONPYCACHEPREFIX="$MF_ROOT_PYCACHE" "$py" -m core.orchestrator "$@"
    else
        exec sudo PYTHONPYCACHEPREFIX="$MF_ROOT_PYCACHE" "$py" -m core.orchestrator "$@"
    fi
}

# meshforge-lora: LoRa configuration helper (a privileged shell script, no python).
launch_lora() {
    if [ "$EUID" -eq 0 ]; then
        exec "$MESHFORGE_DIR/scripts/configure_lora.sh" "$@"
    else
        exec sudo "$MESHFORGE_DIR/scripts/configure_lora.sh" "$@"
    fi
}

# meshforge-status: terminal-native one-shot status. UNPRIVILEGED by design, so
# the interpreter is `$upy`, not `$py` — TestPrivilegedPycachePrefix reads
# `$py` as "a privileged launch" and would demand the prefix here. The root
# branch (a typed `sudo meshforge-status`) still carries it.
launch_status() {
    cd "$MESHFORGE_DIR" || exit 1
    local upy; upy="$(mf_python)"
    if [ "$EUID" -eq 0 ]; then
        exec env PYTHONPYCACHEPREFIX="$MF_ROOT_PYCACHE" "$upy" src/cli/status.py "$@"
    else
        exec "$upy" src/cli/status.py "$@"
    fi
}

# meshforge-web: open or display the meshtasticd web client URL (pure bash).
launch_web() {
    local LOCAL_IP URL
    LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    [ -z "$LOCAL_IP" ] && LOCAL_IP="localhost"
    URL="https://${LOCAL_IP}:9443"

    # Check if meshtasticd web server is responding
    if timeout 2 bash -c "echo >/dev/tcp/${LOCAL_IP}/9443" 2>/dev/null; then
        echo "Meshtastic Web Client: ${URL}"
        echo ""
        echo "  Full radio configuration in your browser:"
        echo "    • Region, Preset, TX Power (Config → LoRa)"
        echo "    • Channels and PSK keys   (Config → Channels)"
        echo "    • Node name and position   (Config → Device)"
        echo "    • Messaging and map view"
        echo ""
        # Try to open browser (works on desktop, no-op on headless)
        if command -v xdg-open &>/dev/null && [ -n "$DISPLAY" ]; then
            xdg-open "$URL" 2>/dev/null &
            echo "  Opening browser..."
        else
            echo "  Open this URL in any browser on your network:"
            echo "  ${URL}"
        fi
    else
        echo "ERROR: meshtasticd web server not responding on port 9443"
        echo ""
        echo "  Check: sudo systemctl status meshtasticd"
        echo "  Start: sudo systemctl start meshtasticd"
        echo ""
        echo "  The web client is served by meshtasticd when running."
        echo "  Config: /etc/meshtasticd/config.yaml (Webserver section)"
    fi
}

# meshforge-map (the INSTALLED name) = the MAP SERVER control on port 5000. NOT
# the `map`/`maps` subcommand above, which generates a coverage map: the
# installed name predates the subcommand, so basename dispatch routes it to
# `mapserver` and the two never meet.
launch_mapserver() {
    local upy; upy="$(mf_python)"
    local LOCAL_IP
    case "${1:-}" in
        start)
            echo "Starting MeshForge Map Server..."
            sudo systemctl start meshforge-map
            ;;
        stop)
            echo "Stopping MeshForge Map Server..."
            sudo systemctl stop meshforge-map
            ;;
        restart)
            echo "Restarting MeshForge Map Server..."
            sudo systemctl restart meshforge-map
            ;;
        status)
            systemctl status meshforge-map --no-pager
            cd "$MESHFORGE_DIR/src" && "$upy" -m utils.map_data_service --status
            ;;
        enable)
            echo "Enabling MeshForge Map Server on boot..."
            sudo systemctl enable meshforge-map
            ;;
        disable)
            echo "Disabling MeshForge Map Server on boot..."
            sudo systemctl disable meshforge-map
            ;;
        url)
            LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
            [ -z "$LOCAL_IP" ] && LOCAL_IP="localhost"
            echo "MeshForge Map: http://${LOCAL_IP}:5000/"
            ;;
        *)
            # Default: run interactively (for debugging)
            cd "$MESHFORGE_DIR/src" || exit 1
            if [ "$EUID" -eq 0 ]; then
                exec env PYTHONPYCACHEPREFIX="$MF_ROOT_PYCACHE" "$upy" -m utils.map_data_service "$@"
            else
                exec "$upy" -m utils.map_data_service "$@"
            fi
            ;;
    esac
}

# Show usage help
show_help() {
    echo "MeshForge - Mesh Network Operations Center"
    echo ""
    echo "Usage: meshforge [command] [options]"
    echo ""
    echo "Commands:"
    echo "  (none)         Launch the NOC launcher (menu, or auto-launch the TUI"
    echo "                 if you saved that preference); starts NOC services"
    echo "  tui            Launch the TUI directly — skips the launcher menu AND"
    echo "                 NOC service startup (same as the meshforge-tui command)"
    echo "  maps [file]    Generate coverage map (default: coverage_map.html)"
    echo "  prometheus [p] Start Prometheus metrics server (default port: 9090)"
    echo "  noc [args]     NOC orchestrator (same as meshforge-noc, e.g. --status)"
    echo "  lora [args]    LoRa configuration helper (same as meshforge-lora)"
    echo "  status [args]  One-shot terminal status (same as meshforge-status)"
    echo "  web            Show/open the meshtasticd web client URL (meshforge-web)"
    echo "  mapserver [op] Map server control: start|stop|restart|status|url|"
    echo "                 enable|disable (same as the meshforge-map command)"
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

# `meshforge-tui` is a symlink to this script (install.sh). Until 2026-09-20
# it was its own wrapper that went STRAIGHT to launcher_tui/main.py; the
# symlink made it run launcher.py's interface MENU and NOC service startup
# instead — the two things the alias existed to skip (caught by the
# adversarial review of cfad33b2..e25ee21d). `--tui` is launcher.py's own
# flag for "the TUI, now, no services", so the alias means `tui`.
case "$(basename "$0")" in
    meshforge-tui) set -- tui "$@" ;;
    meshforge-noc) set -- noc "$@" ;;
    meshforge-lora) set -- lora "$@" ;;
    meshforge-status) set -- status "$@" ;;
    meshforge-web) set -- web "$@" ;;
    meshforge-map) set -- mapserver "$@" ;;   # the map SERVER, not the coverage map
esac

# Determine which interface to launch
case "$1" in
    tui)
        shift
        launch_tui --tui "$@"
        ;;
    maps|map)
        shift
        launch_maps "$@"
        ;;
    prometheus|metrics)
        shift
        launch_prometheus "$@"
        ;;
    noc)
        shift
        launch_noc "$@"
        ;;
    lora)
        shift
        launch_lora "$@"
        ;;
    status)
        shift
        launch_status "$@"
        ;;
    web)
        shift
        launch_web "$@"
        ;;
    mapserver)
        shift
        launch_mapserver "$@"
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        launch_tui "$@"
        ;;
esac
