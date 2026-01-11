#!/bin/bash
# MeshForge SSH Login Message
# Installed to /etc/profile.d/meshforge.sh

# Only show for interactive shells
[ -z "$PS1" ] && return

# Check if MeshForge is installed
if [ -x /usr/local/bin/meshforge ] || [ -f /opt/meshforge/src/launcher_tui.py ]; then
    # Show message on SSH login
    if [ -n "$SSH_TTY" ] || [ -n "$SSH_CLIENT" ]; then
        echo ""
        echo "  ┌─────────────────────────────────────┐"
        echo "  │  MeshForge NOC is installed         │"
        echo "  │  Type 'meshforge' to launch         │"
        echo "  └─────────────────────────────────────┘"
        echo ""
    fi
fi
