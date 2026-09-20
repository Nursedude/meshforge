#!/bin/bash
# MeshForge Terminal Launcher
# Launches TUI in a terminal emulator with proper taskbar icon support
#
# Priority:
# 1. xterm with -class (proven to work with WM_CLASS)
# 2. Desktop-specific terminals as fallback
#
# Note: gnome-terminal --class is broken (Debian bug #238145)

MESHFORGE_DIR="/opt/meshforge"
ICON_NAME="org.meshforge.app"
TITLE="MeshForge"

# Root's bytecode must not land in the repo. Sourced, never copied — see
# scripts/lib/pycache_prefix.sh for the fleet-wide census that forced this.
# ⚠️ sudo resets the environment, so this MUST ride as `sudo VAR=... python3`;
# exporting it out here would not reach the child.
# shellcheck source=lib/pycache_prefix.sh
. "$MESHFORGE_DIR/scripts/lib/pycache_prefix.sh"

TUI_CMD="sudo PYTHONPYCACHEPREFIX=$MF_ROOT_PYCACHE python3 $MESHFORGE_DIR/src/launcher_tui/main.py"

# Log file for debugging launch issues
LOG_FILE="/tmp/meshforge-launch.log"

log_msg() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG_FILE"
}

# Show error notification to user
show_error() {
    local msg="$1"
    log_msg "ERROR: $msg"

    # Try notify-send first (most desktops)
    if command -v notify-send &>/dev/null; then
        notify-send -u critical "MeshForge Launch Error" "$msg"
    fi

    # Try zenity dialog
    if command -v zenity &>/dev/null; then
        zenity --error --title="MeshForge" --text="$msg" 2>/dev/null &
        return
    fi

    # Fallback: write to stderr
    echo "MeshForge Error: $msg" >&2
}

# Check if display is available
has_display() {
    [ -n "$DISPLAY" ] || [ -n "$WAYLAND_DISPLAY" ]
}

# xterm with proper class (WORKS - xterm respects -class and -name flags)
# -name sets WM_CLASS instance, -class sets WM_CLASS class
# Together they allow desktop to find the icon
# Uses nice font and colors for better TUI experience
launch_xterm() {
    log_msg "Launching xterm"
    xterm -name "$ICON_NAME" \
          -class "$ICON_NAME" \
          -title "$TITLE" \
          -fa "Monospace" \
          -fs 11 \
          -bg "#1e1e2e" \
          -fg "#cdd6f4" \
          -geometry 100x35 \
          -e "$TUI_CMD"
}

# lxterminal (common on Raspberry Pi / LXDE)
launch_lxterminal() {
    log_msg "Launching lxterminal"
    lxterminal --title="$TITLE" \
               --geometry=100x35 \
               -e "$TUI_CMD"
}

# xfce4-terminal (works on XFCE desktops)
launch_xfce() {
    log_msg "Launching xfce4-terminal"
    xfce4-terminal --icon="org.meshforge.app" \
                   --title="$TITLE" \
                   --geometry=100x35 \
                   -e "$TUI_CMD"
}

# konsole (KDE)
launch_konsole() {
    log_msg "Launching konsole"
    konsole --title "$TITLE" -e $TUI_CMD
}

# gnome-terminal (--class is broken, but still usable)
launch_gnome() {
    log_msg "Launching gnome-terminal"
    gnome-terminal --title="$TITLE" -- $TUI_CMD
}

# Generic fallback
launch_generic() {
    log_msg "Launching x-terminal-emulator"
    x-terminal-emulator -e "$TUI_CMD"
}

# Check if /opt/meshforge exists
check_installation() {
    if [ ! -d "$MESHFORGE_DIR" ] && [ ! -L "$MESHFORGE_DIR" ]; then
        show_error "MeshForge not installed at $MESHFORGE_DIR\n\nRun: sudo ./scripts/install-desktop.sh"
        exit 1
    fi

    if [ ! -f "$MESHFORGE_DIR/src/launcher_tui/main.py" ]; then
        show_error "launcher_tui not found at $MESHFORGE_DIR/src/\n\nInstallation may be corrupted."
        exit 1
    fi
}

# Main launch logic
log_msg "=== MeshForge Terminal Launcher Started ==="
log_msg "DISPLAY=$DISPLAY WAYLAND_DISPLAY=$WAYLAND_DISPLAY"

# Verify installation
check_installation

if has_display; then
    log_msg "Display available, checking terminal options..."

    # Option 1: xterm (proven WM_CLASS support)
    if command -v xterm &>/dev/null; then
        launch_xterm
        exit $?
    fi
    log_msg "xterm not available"

    # Option 2: lxterminal (Raspberry Pi default)
    if command -v lxterminal &>/dev/null; then
        launch_lxterminal
        exit $?
    fi
    log_msg "lxterminal not available"

    # Option 3: Desktop-specific terminals
    if command -v xfce4-terminal &>/dev/null; then
        launch_xfce
        exit $?
    elif command -v konsole &>/dev/null; then
        launch_konsole
        exit $?
    elif command -v gnome-terminal &>/dev/null; then
        launch_gnome
        exit $?
    elif command -v x-terminal-emulator &>/dev/null; then
        launch_generic
        exit $?
    fi

    # Nothing worked - show error
    log_msg "No terminal emulator found!"
    show_error "No terminal emulator found!\n\nInstall one with:\n  sudo apt install xterm\n\nOr run directly:\n  $MESHFORGE_DIR/scripts/meshforge-launcher.sh"
    exit 1
else
    # No display (SSH session) - run TUI directly
    log_msg "No display - running TUI directly"
    exec $TUI_CMD
fi
