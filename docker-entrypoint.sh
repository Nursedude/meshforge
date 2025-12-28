#!/bin/bash
set -e

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║   Meshtasticd Interactive Installer (Docker)             ║"
echo "║   For Raspberry Pi OS                                     ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""

# If no arguments or --help, show usage
if [ $# -eq 0 ] || [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    echo "Docker Usage Examples:"
    echo ""
    echo "  # Interactive mode:"
    echo "  docker run -it --privileged -v /dev:/dev meshtasticd-installer"
    echo ""
    echo "  # Install stable version:"
    echo "  docker run -it --privileged -v /dev:/dev meshtasticd-installer --install stable"
    echo ""
    echo "  # Configure device:"
    echo "  docker run -it --privileged -v /dev:/dev meshtasticd-installer --configure"
    echo ""
    echo "  # Web installer:"
    echo "  docker run -d -p 8080:8080 --privileged -v /dev:/dev meshtasticd-installer web"
    echo "  Then visit: http://localhost:8080"
    echo ""
    exit 0
fi

# Handle special commands
if [ "$1" = "web" ]; then
    echo "Starting web installer..."
    exec python3 /app/web_installer.py
elif [ "$1" = "bash" ]; then
    exec /bin/bash
else
    # Run main installer
    exec python3 /app/src/main.py "$@"
fi
