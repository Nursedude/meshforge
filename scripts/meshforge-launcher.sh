#!/bin/bash
# MeshForge Launcher Script
# This script is called by pkexec to launch MeshForge with proper privileges

MESHFORGE_DIR="/opt/meshforge"

# Determine which interface to launch
case "$1" in
    gtk)
        exec python3 "$MESHFORGE_DIR/src/main_gtk.py"
        ;;
    web)
        exec python3 "$MESHFORGE_DIR/src/main_web.py"
        ;;
    tui)
        exec python3 "$MESHFORGE_DIR/src/main_tui.py"
        ;;
    cli)
        exec python3 "$MESHFORGE_DIR/src/main.py"
        ;;
    *)
        # Default: use launcher
        exec python3 "$MESHFORGE_DIR/src/launcher.py"
        ;;
esac
