#!/bin/bash
# MeshForge Terminal Launcher
# Launches TUI with proper taskbar icon support
#
# Priority:
# 1. VTE GTK4 wrapper (best icon support, native GTK window)
# 2. xterm with -class (proven to work with WM_CLASS)
# 3. Other terminals as fallback
#
# Note: gnome-terminal --class is broken (Debian bug #238145)

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
    python3 -c "import gi; gi.require_version('Vte', '2.91'); from gi.repository import Vte" 2>/dev/null
}

# VTE GTK4 wrapper (best option - native GTK window with proper app_id)
launch_vte() {
    exec $VTE_CMD
}

# xterm with proper class (WORKS - xterm respects -class flag)
# Uses nice font and colors for better TUI experience
launch_xterm() {
    xterm -class "$ICON_NAME" \
          -title "$TITLE" \
          -fa "Monospace" \
          -fs 11 \
          -bg "#1e1e2e" \
          -fg "#cdd6f4" \
          -geometry 100x35 \
          -e "$TUI_CMD"
}

# xfce4-terminal (works on XFCE desktops)
launch_xfce() {
    xfce4-terminal --icon="meshforge-icon" \
                   --title="$TITLE" \
                   --geometry=100x35 \
                   -e "$TUI_CMD"
}

# konsole (KDE)
launch_konsole() {
    konsole --title "$TITLE" -e $TUI_CMD
}

# gnome-terminal (--class is broken, but still usable)
launch_gnome() {
    gnome-terminal --title="$TITLE" -- $TUI_CMD
}

# Generic fallback
launch_generic() {
    x-terminal-emulator -e "$TUI_CMD"
}

# Main launch logic
if has_display; then
    # Display available - try best options first

    # Option 1: VTE wrapper (native GTK window with proper app_id)
    if has_vte; then
        launch_vte
        exit $?
    fi

    # Option 2: xterm (proven WM_CLASS support)
    if command -v xterm &>/dev/null; then
        launch_xterm
        exit $?
    fi

    # Option 3: Desktop-specific terminals
    if command -v xfce4-terminal &>/dev/null; then
        launch_xfce
    elif command -v konsole &>/dev/null; then
        launch_konsole
    elif command -v gnome-terminal &>/dev/null; then
        launch_gnome
    else
        launch_generic
    fi
else
    # No display (SSH session) - run TUI directly
    exec $TUI_CMD
fi
