#!/bin/bash
# MeshForge Terminal Launcher
# Launches TUI in a terminal with proper icon/class

MESHFORGE_DIR="/opt/meshforge"
ICON_NAME="org.meshforge.app"
TITLE="MeshForge"
CMD="sudo python3 $MESHFORGE_DIR/src/launcher_tui.py"

# Function to launch with gnome-terminal
launch_gnome() {
    gnome-terminal --class="$ICON_NAME" --title="$TITLE" -- $CMD
}

# Function to launch with xfce4-terminal
launch_xfce() {
    xfce4-terminal --icon="$ICON_NAME" --title="$TITLE" -e "$CMD"
}

# Function to launch with konsole
launch_konsole() {
    konsole --title "$TITLE" -e $CMD
}

# Function to launch with xterm (fallback)
launch_xterm() {
    xterm -class "$ICON_NAME" -title "$TITLE" -e "$CMD"
}

# Function to launch with generic terminal
launch_generic() {
    x-terminal-emulator -e "$CMD"
}

# Detect and use best available terminal
if command -v gnome-terminal &>/dev/null; then
    launch_gnome
elif command -v xfce4-terminal &>/dev/null; then
    launch_xfce
elif command -v konsole &>/dev/null; then
    launch_konsole
elif command -v xterm &>/dev/null; then
    launch_xterm
else
    launch_generic
fi
