"""Version information for Meshtasticd Interactive Installer"""

__version__ = "2.0.2"
__version_info__ = (2, 0, 2)
__release_date__ = "2025-12-30"

# Version history
VERSION_HISTORY = [
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
