"""Version information for Meshtasticd Interactive Installer"""

__version__ = "3.1.1"
__version_info__ = (3, 1, 1)
__release_date__ = "2026-01-01"

# Version history
VERSION_HISTORY = [
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
