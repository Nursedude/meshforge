#!/bin/bash
# MeshForge Terminal Launcher
# Launches TUI with proper taskbar icon support
#
# Priority:
# 1. VTE GTK4 wrapper (if display available) - best icon support
# 2. External terminal with window class flags
# 3. Fallback to basic terminal

MESHFORGE_DIR="/opt/meshforge"
ICON_NAME="org.meshforge.app"
TITLE="MeshForge"
TUI_CMD="sudo python3 $MESHFORGE_DIR/src/launcher_tui.py"
VTE_CMD="python3 $MESHFORGE_DIR/src/launcher_vte.py"

# Check if display is available
has_display() {
    [ -n "$DISPLAY" ] || [ -n "$WAYLAND_DISPLAY" ]
}

# Check if VTE launcher is available and working
has_vte() {
    [ -f "$MESHFORGE_DIR/src/launcher_vte.py" ] && \
    python3 -c "import gi; gi.require_version('Vte', '3.91')" 2>/dev/null
}

# Try VTE GTK4 wrapper first (best taskbar icon support)
launch_vte() {
    $VTE_CMD
}

# Function to launch with gnome-terminal
launch_gnome() {
    gnome-terminal --class="$ICON_NAME" --title="$TITLE" -- $TUI_CMD
}

# Function to launch with xfce4-terminal
launch_xfce() {
    xfce4-terminal --icon="$ICON_NAME" --title="$TITLE" -e "$TUI_CMD"
}

# Function to launch with konsole
launch_konsole() {
    konsole --title "$TITLE" -e $TUI_CMD
}

# Function to launch with xterm (fallback)
launch_xterm() {
    xterm -class "$ICON_NAME" -title "$TITLE" -e "$TUI_CMD"
}

# Function to launch with generic terminal
launch_generic() {
    x-terminal-emulator -e "$TUI_CMD"
}

# Main launch logic
if has_display; then
    # Display available - try VTE wrapper first for best icon support
    if has_vte; then
        launch_vte
        exit $?
    fi

    # VTE not available, use external terminal with class flags
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
else
    # No display (SSH session) - run TUI directly
    exec $TUI_CMD
fi
