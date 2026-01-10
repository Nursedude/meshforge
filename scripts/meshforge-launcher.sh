#!/bin/bash
# MeshForge Launcher Script
# This script handles launching MeshForge with proper privileges

MESHFORGE_DIR="/opt/meshforge"

# For GUI apps, we need to preserve DISPLAY/WAYLAND environment
# pkexec strips these, so we use a different approach

# Check if we're running in a terminal
is_interactive_terminal() {
    [ -t 0 ] && [ -t 1 ]
}

# Get graphical sudo - try various methods
get_graphical_sudo() {
    local script="$1"

    # Method 1: If pkexec is available and polkit is configured
    if command -v pkexec &>/dev/null && [ -f /usr/share/polkit-1/actions/org.meshforge.policy ]; then
        # Allow root to connect to X server first
        xhost +local:root 2>/dev/null || true
        # pkexec with env preservation via wrapper
        exec pkexec env DISPLAY="$DISPLAY" WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
            XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}" \
            XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
            python3 "$script"
    fi

    # Method 2: Try zenity/kdialog for password prompt
    if command -v zenity &>/dev/null; then
        local password
        password=$(zenity --password --title="MeshForge Authentication" 2>/dev/null)
        if [ -n "$password" ]; then
            xhost +local:root 2>/dev/null || true
            echo "$password" | sudo -S -E python3 "$script"
            return $?
        fi
    fi

    # Method 3: Try ssh-askpass style
    if [ -n "$SSH_ASKPASS" ] || command -v ssh-askpass &>/dev/null; then
        export SUDO_ASKPASS="${SSH_ASKPASS:-$(command -v ssh-askpass)}"
        xhost +local:root 2>/dev/null || true
        exec sudo -A -E python3 "$script"
    fi

    # Fallback: open terminal for password
    exec x-terminal-emulator -e "sudo python3 $script"
}

# Function to launch with sudo in a way that preserves display
launch_gui() {
    local script="$1"

    # If we're already root, just run
    if [ "$EUID" -eq 0 ]; then
        exec python3 "$script"
    fi

    # If we have a display but no terminal, use graphical sudo
    if [ -n "$DISPLAY" ] || [ -n "$WAYLAND_DISPLAY" ]; then
        if ! is_interactive_terminal; then
            get_graphical_sudo "$script"
            return $?
        fi

        # For Wayland with terminal
        if [ -n "$WAYLAND_DISPLAY" ]; then
            exec sudo -E python3 "$script"
        fi
        # For X11 with terminal
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

# Show usage help
show_help() {
    echo "MeshForge - Mesh Network Operations Center"
    echo ""
    echo "Usage: meshforge [command]"
    echo ""
    echo "Commands:"
    echo "  (none)    Show interactive menu (default)"
    echo "  gtk       Launch GTK graphical interface directly"
    echo "  tui       Launch terminal UI directly"
    echo "  web       Launch web interface directly"
    echo "  cli       Launch Rich CLI menu"
    echo "  help      Show this help message"
    echo ""
    echo "Examples:"
    echo "  meshforge          # Show menu with all options"
    echo "  meshforge gtk      # Launch GTK directly"
    echo "  meshforge tui      # Launch TUI directly"
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
    help|--help|-h)
        show_help
        ;;
    *)
        # Default: show interactive launcher menu
        launch_terminal "$MESHFORGE_DIR/src/launcher.py"
        ;;
esac
