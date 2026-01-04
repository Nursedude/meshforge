"""
MeshForge - LoRa Mesh Network Development & Operations Suite
Version information and changelog
"""

__version__ = "4.2.1"
__version_info__ = (4, 2, 1)
__release_date__ = "2026-01-04"
__app_name__ = "MeshForge"
__app_description__ = "LoRa Mesh Network Development & Operations Suite"
__app_tagline__ = "Build. Test. Deploy. Monitor."

# Version history
VERSION_HISTORY = [
    {
        "version": "4.2.1",
        "date": "2026-01-04",
        "changes": [
            "SECURITY: Added shlex.quote() to prevent shell injection in terminal commands",
            "SECURITY: Fixed command injection vulnerabilities in system.py using list args",
            "FIX: Gateway now properly sends Mesh→RNS traffic (was only receiving)",
            "FIX: NodeTracker no longer auto-starts, preventing RNS port conflicts",
            "FIX: Improved NomadNet Text UI launch with proper daemon detection",
            "NEW: Diagnostic tool (cli/diagnose.py) for troubleshooting",
            "NEW: Thread management utilities (utils/threads.py) for clean shutdown",
            "REFACTOR: Consolidated 20+ CLI path lookups to use utils/cli.py",
            "IMPROVED: Thread cleanup in NodeTracker and NodeMonitor on shutdown",
        ]
    },
    {
        "version": "4.2.0",
        "date": "2026-01-03",
        "changes": [
            "NEW: Unified Node Map - Shows nodes from both Meshtastic AND RNS networks",
            "NEW: Interactive Leaflet.js map with WebKit or browser display",
            "NEW: Filter nodes by network type (Meshtastic, RNS) and online status",
            "NEW: RNS Configuration Editor - Full editor for ~/.reticulum/config",
            "NEW: Interface templates - One-click TCP, UDP, RNode, Auto-discovery insertion",
            "NEW: Gateway Configuration Dialog - Complete settings editor for RNS-Meshtastic bridge",
            "NEW: Gateway settings: Meshtastic connection, RNS config, telemetry, routing rules",
            "NEW: Export/Import gateway configuration as JSON",
            "IMPROVED: RNS panel now has 'Config Editor' button with template support",
            "IMPROVED: Node Map panel added to main navigation",
        ]
    },
    {
        "version": "4.1.0",
        "date": "2026-01-03",
        "changes": [
            "NEW: Mesh Network Map - Interactive Leaflet.js map showing node positions",
            "NEW: Node markers with color-coded status (online/stale/offline)",
            "NEW: Click nodes on map to see details (battery, SNR, hardware, etc.)",
            "NEW: /api/nodes/full endpoint with detailed position and metrics data",
            "NEW: Version Checker - Check for updates to meshtasticd, CLI, firmware",
            "NEW: Updates tab in Web UI showing component versions",
            "NEW: Desktop launcher for Raspberry Pi menu integration",
            "NEW: Install script for desktop integration (scripts/install-desktop.sh)",
            "NEW: MeshForge SVG icon",
            "NEW: Site Planner integration - Opens site.meshtastic.org for RF coverage planning",
            "NEW: Frequency Slot Calculator redesign with all 22 Meshtastic regions",
            "NEW: Channel Preset dropdown for quick frequency slot selection",
            "FIX: Device role parsing now correctly detects CLIENT_MUTE",
            "FIX: Desktop launcher uses terminal for sudo authentication",
            "FIX: Dropdown matching uses exact match first (prevents CLIENT matching CLIENT_MUTE)",
            "IMPROVED: Web UI now titled 'MeshForge'",
            "IMPROVED: Uses NodeMonitor for rich node data including positions"
        ]
    },
    {
        "version": "4.0.1",
        "date": "2026-01-03",
        "changes": [
            "SECURITY: Replaced os.system() with subprocess.run() in launcher.py",
            "SECURITY: Removed shell=True from subprocess calls in hardware.py",
            "SECURITY: Fixed bare except clauses across 12 files",
            "CLEAN: Removed debug print statements from radio_config.py",
            "NEW: Frequency Slot Calculator with djb2 hash algorithm",
            "NEW: Region and modem preset selection for frequency calculation",
            "IMPROVED: Better exception handling with specific exception types"
        ]
    },
    {
        "version": "4.0.0",
        "date": "2026-01-03",
        "changes": [
            "REBRAND: Project renamed to MeshForge",
            "NEW: Professional suite branding - 'LoRa Mesh Network Development & Operations Suite'",
            "NEW: Frequency Slot Calculator in Radio Config panel",
            "NEW: Enhanced Radio Config parsing with robust data extraction",
            "NEW: Hardware detection for USB LoRa devices (CH340, CP2102, ESP32, nRF52840)",
            "NEW: Serial port detection for GPS modules",
            "NEW: Desktop launcher support for Raspberry Pi",
            "IMPROVED: Session notes for development continuity",
            "IMPROVED: Debug output for radio parsing troubleshooting",
            "FOUNDATION: Preparing for future node flashing capability"
        ]
    },
    {
        "version": "3.2.7",
        "date": "2026-01-02",
        "changes": [
            "NEW: Web UI - Browser-based interface accessible via http://ip:8080/",
            "NEW: Dashboard with live CPU, Memory, Disk, Temperature stats",
            "NEW: Service control (Start/Stop/Restart) from browser",
            "NEW: Config management - Activate/deactivate YAML files",
            "NEW: Hardware detection panel in Web UI",
            "NEW: Radio info display in Web UI",
            "NEW: Optional password authentication with --password flag",
            "NEW: Configurable port with --port flag (default 8080)",
            "NEW: Auto-refresh dashboard every 5 seconds",
            "IMPROVED: Dark theme UI with mobile-responsive design"
        ]
    },
    {
        "version": "3.2.6",
        "date": "2026-01-02",
        "changes": [
            "NEW: System Monitor in Tools - CPU, Memory, Disk, Temperature with progress bars",
            "NEW: Open htop button and Show Processes in System Tools",
            "NEW: Daemon control - --status and --stop commands for GTK app",
            "FIX: Daemon mode no longer causes fork() deprecation warning",
            "FIX: Service detection uses multiple methods (systemctl, pgrep, TCP port)",
            "FIX: Hardware detection improved with better meshtasticd status check",
            "FIX: Dashboard shows 'Running (systemd/process/TCP)' based on detection method",
            "FIX: PID file cleanup handles permission errors gracefully",
            "IMPROVED: Daemon mode uses subprocess instead of fork() for safety",
            "IMPROVED: --status shows helpful info for finding the GTK window"
        ]
    },
    {
        "version": "3.2.5",
        "date": "2026-01-02",
        "changes": [
            "NEW: Escape key to exit fullscreen mode in GTK app",
            "NEW: F11 to toggle fullscreen in GTK app",
            "NEW: Ctrl+Q to quit GTK app",
            "FIX: Connection error spam removed - no more 'Connection reset by peer' messages",
            "FIX: Radio Config panel now checks meshtasticd connectivity before loading",
            "FIX: SUDO_USER path detection for meshtastic CLI",
            "FIX: Better error messages when meshtasticd not reachable",
            "IMPROVED: Socket pre-check before CLI calls (fail fast)"
        ]
    },
    {
        "version": "3.2.4",
        "date": "2026-01-02",
        "changes": [
            "NEW: Connected Radio info section in Radio Config panel",
            "NEW: Daemon mode for GTK app - `--daemon` or `-d` flag returns terminal control",
            "NEW: MQTT settings parsing - server, username, encryption, JSON, TLS status",
            "FIX: Radio info JSON parsing - Extract firmware/hardware from JSON correctly",
            "FIX: Firmware field no longer shows entire JSON object",
            "FIX: Skip JSON lines in fallback radio info parsing",
            "IMPROVED: Radio config auto-loads radio info and current settings",
            "IMPROVED: Rebroadcast mode parsing in config loader"
        ]
    },
    {
        "version": "3.2.3",
        "date": "2026-01-02",
        "changes": [
            "NEW: Node Monitoring module - Sudo-free monitoring via TCP interface",
            "NEW: Monitor entry point - python3 -m src.monitor (no root required)",
            "NEW: Interactive setup - Configure host/port with --setup flag",
            "NEW: Watch mode - Continuous monitoring with --watch",
            "NEW: JSON output - Machine-readable output with --json",
            "NEW: Config persistence - Saves host/port to ~/.config/meshtastic-monitor/",
            "NEW: DEVELOPMENT.md - Critical patterns and methods for contributors",
            "FIX: TUI @work decorator errors - Removed await from decorated methods",
            "FIX: Monitor connection timeout - Fast failure with socket pre-check"
        ]
    },
    {
        "version": "3.2.2",
        "date": "2026-01-02",
        "changes": [
            "NEW: Radio Configuration Panel in GTK UI (Mesh, LoRa, Position, Power, MQTT, Telemetry)",
            "NEW: Real-time node count in GTK status bar from meshtastic CLI",
            "FIX: GTK status bar now shows nodes and uptime correctly",
            "FIX: GTK dashboard config count now checks both .yaml and .yml files",
            "FIX: Hardware detection Enable buttons now work with sudo",
            "FIX: Hardware detection shows active meshtasticd hardware and configs",
            "IMPROVED: Hardware panel queries running meshtasticd for device info",
            "DOCS: Added Meshtastic Web Client documentation to RESEARCH.md"
        ]
    },
    {
        "version": "3.2.1",
        "date": "2026-01-01",
        "changes": [
            "NEW: Hardware Configuration (Main Menu → w) - SPI, I2C, Serial, GPIO setup",
            "NEW: Safe Reboot - Checks for running editors, SSH sessions, apt locks before reboot",
            "NEW: Device Selection - Configure known Meshtastic hardware with auto-setup",
            "NEW: SPI Overlay Management - Add dtoverlay=spi0-0cs for LoRa HATs",
            "NEW: Config File Copy - Copy configs from available.d to config.d",
            "NEW: YAML Config Editor - Edit config files with syntax validation",
            "IMPROVED: Channel configuration with PSK key management, MQTT, position precision",
            "IMPROVED: Raspi-config integration for interface enable/disable"
        ]
    },
    {
        "version": "3.2.0",
        "date": "2026-01-01",
        "changes": [
            "NEW: Network Tools - TCP/IP diagnostics, ping, port scanning, device discovery",
            "NEW: RF Tools - Link budget calculator, FSPL, Fresnel zones, LoRa analysis",
            "NEW: MUDP Tools - Meshtastic UDP monitoring, multicast, virtual node",
            "NEW: Tool Manager - Install, update, and version check for all tools",
            "NEW: RESEARCH.md - Bibliography and technical documentation",
            "FIX: Site Planner URL display for headless/SSH sessions",
            "IMPROVED: Tools available across all UIs (CLI, GTK4, TUI)"
        ]
    },
    {
        "version": "3.1.1",
        "date": "2026-01-01",
        "changes": [
            "FIX: Textual TUI - Widget IDs now handle dots in filenames (e.g., display-waveshare-2.8.yaml)",
            "FIX: Textual TUI - Config activate/deactivate/edit now work correctly",
            "FIX: GTK4 - Fixed content_stack initialization race condition",
            "FIX: pip install uses --ignore-installed to avoid Debian package conflicts",
            "IMPROVED: README updated with correct pip install flags for Raspberry Pi"
        ]
    },
    {
        "version": "3.1.0",
        "date": "2026-01-01",
        "changes": [
            "NEW: System Diagnostics - Network, hardware, and health checks",
            "NEW: Site Planner integration - Coverage analysis and link budget calculator",
            "NEW: RF coverage tools - Links to Radio Mobile, HeyWhatsThat, Splat!",
            "NEW: Preset range estimates for all modem presets",
            "NEW: Antenna guidelines and frequency/power reference",
            "NEW: Mesh network diagnostics - Node count, activity, API status",
            "NEW: Full system diagnostic report with health score",
            "IMPROVED: Main menu reorganized with new Tools section"
        ]
    },
    {
        "version": "3.0.6",
        "date": "2025-12-31",
        "changes": [
            "FIX: Meshtastic CLI detection now works with pipx installations",
            "FIX: CLI found in /root/.local/bin, /home/pi/.local/bin, ~/.local/bin",
            "FIX: SUDO_USER environment variable checked for user's home directory",
            "NEW: Centralized find_meshtastic_cli() utility in utils/cli.py",
            "IMPROVED: All modules now use consistent CLI detection"
        ]
    },
    {
        "version": "3.0.5",
        "date": "2025-12-31",
        "changes": [
            "IMPROVED: Emoji detection now checks for installed fonts (fonts-noto-color-emoji)",
            "NEW: Emoji status diagnostic in Debug menu (option 9)",
            "NEW: Detailed instructions for enabling emojis on Raspberry Pi",
            "FIX: Emojis only enabled when proper fonts are installed",
            "FIX: SSH sessions properly detect font availability"
        ]
    },
    {
        "version": "3.0.4",
        "date": "2025-12-31",
        "changes": [
            "NEW: Uninstaller - Remove meshtasticd and components interactively",
            "NEW: Progress indicator utilities for installations",
            "NEW: Launcher saves UI preference with auto-launch option",
            "NEW: Use --wizard flag to force wizard and reset preference",
            "IMPROVED: Edit existing channels with pre-filled values",
            "IMPROVED: Consistent 'm' = Main Menu across all prompts",
            "IMPROVED: Better emoji detection for SSH and RPi terminals"
        ]
    },
    {
        "version": "3.0.3",
        "date": "2025-12-31",
        "changes": [
            "NEW: Edit existing channels with pre-filled values",
            "IMPROVED: Consistent 'm' = Main Menu across all prompts",
            "IMPROVED: Better emoji detection for SSH and RPi terminals",
            "IMPROVED: Region selection now has back/menu options",
            "IMPROVED: Channel edit shows current values as defaults",
            "FIX: Back navigation works in all channel config steps"
        ]
    },
    {
        "version": "3.0.2",
        "date": "2025-12-31",
        "changes": [
            "NEW: Enhanced channel configuration with full settings",
            "NEW: PSK options - Generate 256-bit, 128-bit, default, none, or custom",
            "NEW: MQTT uplink/downlink settings per channel",
            "NEW: Auto-install meshtastic CLI via pipx when needed",
            "NEW: Modem presets now apply directly to device",
            "FIX: Channel configuration saves to device via meshtastic CLI",
            "FIX: Meshtastic CLI PATH auto-added after pipx install",
            "FIX: Existing channels detected from device before config",
            "IMPROVED: Role selection (PRIMARY/SECONDARY/DISABLED) per channel",
            "IMPROVED: Channel summary table shows MQTT status"
        ]
    },
    {
        "version": "3.0.1",
        "date": "2025-12-30",
        "changes": [
            "NEW: Launcher wizard for selecting interface (GTK4/TUI/CLI)",
            "FIX: Log following now updates properly in GTK4 and TUI",
            "FIX: Journalctl --since parameter format corrected",
            "FIX: Channel configuration now has proper back navigation",
            "FIX: pip install uses --break-system-packages for RPi",
            "FIX: Meshtastic CLI detection checks multiple paths",
            "IMPROVED: All Rich CLI menus have Back (0) and Main Menu (m) options",
            "IMPROVED: Auto-scroll in log views",
            "IMPROVED: TUI follow logs with proper start/stop toggle"
        ]
    },
    {
        "version": "3.0.0",
        "date": "2025-12-30",
        "changes": [
            "NEW: GTK4 graphical interface for systems with display",
            "NEW: Textual TUI for SSH/headless access (Raspberry Pi Connect friendly)",
            "GTK4 UI: Modern libadwaita design with dashboard, service, config panels",
            "Textual TUI: Full-featured terminal UI with mouse support",
            "Config File Manager: Select YAML from available.d, edit with nano",
            "Service Management: Start/stop/restart with live logs",
            "Meshtastic CLI: Integrated CLI commands panel",
            "Hardware Detection: Detect SPI/I2C devices",
            "Reboot Persistence: Installer auto-restarts after reboot",
            "Three UI options: GTK4 (display), Textual TUI (SSH), Rich CLI (fallback)",
            "Auto-detect display availability and suggest appropriate UI"
        ]
    },
    {
        "version": "2.3.0",
        "date": "2025-12-30",
        "changes": [
            "Added Config File Manager - select YAML from /etc/meshtasticd/available.d",
            "Uses official meshtasticd package yaml files (lora-*, display-*, etc.)",
            "Integrated nano editor for direct config file editing",
            "Copy selected config to config.d for activation",
            "Auto daemon-reload and service restart after config changes",
            "View/deactivate active configurations",
            "Create basic config.yaml if not exists",
            "Full integration with meshtasticd package structure"
        ]
    },
    {
        "version": "2.2.0",
        "date": "2025-12-30",
        "changes": [
            "Added Service Management menu (start/stop/restart/status/logs)",
            "Added Meshtastic CLI commands menu with full CLI integration",
            "Enhanced config.yaml editor with all hardware sections",
            "Added LoRa module presets (MeshAdv-Mini, Waveshare, Ebyte E22, etc.)",
            "Added Touchscreen, Input, HostMetrics configuration",
            "All submenus now have Back (0) and Main Menu (m) navigation",
            "Added connection configuration for CLI (localhost, serial, BLE)",
            "Service management: view logs by time, follow logs, boot control"
        ]
    },
    {
        "version": "2.1.2",
        "date": "2025-12-30",
        "changes": [
            "Added interactive config.yaml editor (option 7 in main menu)",
            "Simplified emoji detection - uses stdout UTF-8 encoding",
            "All menus now have Back option to return to previous menu",
            "Main menu reorganized: d for Debug menu",
            "Config editor supports: LoRa, GPS, I2C, Display, Logging, Webserver, General"
        ]
    },
    {
        "version": "2.1.1",
        "date": "2025-12-30",
        "changes": [
            "Fixed version comparison for non-standard versions (e.g., 2.7.15.567b8ea)",
            "Improved emoji detection for Raspberry Pi local console with UTF-8",
            "Updated config.yaml template with complete meshtasticd configuration",
            "Added reboot prompt after SPI/I2C changes with auto-return to installer",
            "Added all config sections: GPS, I2C, Display, Touchscreen, Logging, etc.",
            "Better locale detection for emoji support on Pi"
        ]
    },
    {
        "version": "2.1.0",
        "date": "2025-12-30",
        "changes": [
            "Fixed packaging 25.0 conflict - use pipx for meshtastic CLI isolation",
            "Added pipx support for isolated meshtastic CLI installation",
            "Added .env configuration file support",
            "Improved debugging with DEBUG_MODE environment variable",
            "Added --show-config CLI option to display configuration",
            "Enhanced error handling in installation scripts",
            "Removed meshtastic from pip requirements (now installed via pipx)",
            "Added configuration validation and summary display",
            "Debug menu now shows environment configuration"
        ]
    },
    {
        "version": "2.0.2",
        "date": "2025-12-30",
        "changes": [
            "Improved emoji detection - defaults to ASCII on Raspberry Pi OS",
            "Enhanced SSH session detection for emoji fallback",
            "Better OS and board model detection",
            "Added Meshtastic Linux native compatibility check",
            "Improved hardware detection for all supported LoRa devices",
            "Complete emoji mapping with comprehensive ASCII fallbacks",
            "Auto-detects Raspberry Pi models from device tree"
        ]
    },
    {
        "version": "2.0.1",
        "date": "2025-12-30",
        "changes": [
            "Fixed Python externally-managed-environment error",
            "Added virtual environment support for dependencies",
            "Enhanced Raspberry Pi OS compatibility",
            "Added emoji fallback system for terminals without UTF-8 support",
            "Improved terminal detection and ASCII alternatives",
            "Better support for SSH and basic terminals"
        ]
    },
    {
        "version": "2.0.0",
        "date": "2025-12-29",
        "changes": [
            "Added Quick Status Dashboard",
            "Added Interactive Channel Configuration with presets",
            "Added Automatic Update Notifications",
            "Added Configuration Templates for common setups",
            "Improved UI with better navigation and help",
            "Added version control system",
            "New templates: Emergency/SAR, Urban, MtnMesh, Repeater"
        ]
    },
    {
        "version": "1.2.0",
        "date": "2025-12-15",
        "changes": [
            "Added device configuration support",
            "Added SPI HAT configuration (MeshAdv-Mini)",
            "Improved hardware detection"
        ]
    },
    {
        "version": "1.1.0",
        "date": "2025-12-01",
        "changes": [
            "Added modem preset configuration",
            "Added channel slot configuration",
            "Added module configuration"
        ]
    },
    {
        "version": "1.0.0",
        "date": "2025-11-15",
        "changes": [
            "Initial release",
            "Basic installation support",
            "LoRa configuration",
            "Hardware detection"
        ]
    }
]


def get_version():
    """Get current version string"""
    return __version__


def get_version_info():
    """Get version as tuple"""
    return __version_info__


def get_full_version():
    """Get version with release date"""
    return f"{__version__} ({__release_date__})"


def show_version_history():
    """Display version history"""
    from rich.console import Console
    from rich.table import Table

    console = Console()

    table = Table(title="Version History", show_header=True, header_style="bold magenta")
    table.add_column("Version", style="cyan", width=10)
    table.add_column("Date", style="green", width=12)
    table.add_column("Changes", style="white")

    for entry in VERSION_HISTORY:
        changes = "\n".join(f"- {c}" for c in entry['changes'][:3])
        if len(entry['changes']) > 3:
            changes += f"\n  ... and {len(entry['changes']) - 3} more"
        table.add_row(entry['version'], entry['date'], changes)

    console.print(table)
