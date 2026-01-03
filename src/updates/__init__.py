"""
MeshForge Updates Module

Provides version checking and update functionality for:
- meshtasticd (Linux native daemon)
- meshtastic CLI (Python package)
- Node firmware

Usage:
    from src.updates import get_version_summary

    summary = get_version_summary()
    print(f"Updates available: {summary['updates_available']}")
"""

from .version_checker import (
    check_all_versions,
    get_version_summary,
    get_meshtasticd_version,
    get_meshtastic_cli_version,
    get_node_firmware_version,
    VersionInfo,
)

__all__ = [
    'check_all_versions',
    'get_version_summary',
    'get_meshtasticd_version',
    'get_meshtastic_cli_version',
    'get_node_firmware_version',
    'VersionInfo',
]
