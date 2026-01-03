#!/bin/bash
# MeshForge Launcher Script
# This script handles launching MeshForge with proper privileges

MESHFORGE_DIR="/opt/meshforge"

# For GUI apps, we need to preserve DISPLAY/WAYLAND environment
# pkexec strips these, so we use a different approach

# Function to launch with sudo in a way that preserves display
launch_gui() {
    local script="$1"

    # If we're already root, just run
    if [ "$EUID" -eq 0 ]; then
        exec python3 "$script"
    fi

    # Try to run with preserved environment using sudo
    # This works if user has NOPASSWD or enters password in terminal
    if [ -n "$DISPLAY" ] || [ -n "$WAYLAND_DISPLAY" ]; then
        # For Wayland
        if [ -n "$WAYLAND_DISPLAY" ]; then
            exec sudo -E python3 "$script"
        fi
        # For X11
        if [ -n "$DISPLAY" ]; then
            # Allow root to connect to X server
            xhost +local:root 2>/dev/null || true
            exec sudo -E python3 "$script"
        fi
    fi

    # Fallback: run in terminal
    exec x-terminal-emulator -e "sudo python3 $script"
}

# Function to launch terminal apps
launch_terminal() {
    local script="$1"
    exec sudo python3 "$script"
}

# Determine which interface to launch
case "$1" in
    gtk)
        launch_gui "$MESHFORGE_DIR/src/main_gtk.py"
        ;;
    web)
        # Web UI can run without GUI, just needs network
        if [ "$EUID" -eq 0 ]; then
            exec python3 "$MESHFORGE_DIR/src/main_web.py"
        else
            exec sudo python3 "$MESHFORGE_DIR/src/main_web.py"
        fi
        ;;
    tui)
        launch_terminal "$MESHFORGE_DIR/src/main_tui.py"
        ;;
    cli)
        launch_terminal "$MESHFORGE_DIR/src/main.py"
        ;;
    *)
        # Default: use launcher (GTK-based)
        launch_gui "$MESHFORGE_DIR/src/launcher.py"
        ;;
esac
