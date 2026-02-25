"""
Firmware Flasher - Orchestrates the firmware update process.

Coordinates device detection, firmware download, backup, and flashing.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, List, Optional

from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# Import components via safe_import
FirmwareDownloader, FirmwareAsset, _HAS_DOWNLOADER = safe_import(
    'utils.firmware_downloader', 'FirmwareDownloader', 'FirmwareAsset'
)
EsptoolWrapper, FlashResult, FlashProgress, FlashStage, _HAS_ESPTOOL = safe_import(
    'utils.esptool_wrapper', 'EsptoolWrapper', 'FlashResult', 'FlashProgress', 'FlashStage'
)
from utils.device_scanner import DeviceScanner
from utils.device_backup import DeviceBackupManager
_check_esptool_available, _HAS_ESPTOOL_CHECK = safe_import(
    'utils.esptool_wrapper', 'check_esptool_available'
)

# Log any missing components
if not _HAS_DOWNLOADER or not _HAS_ESPTOOL:
    logger.warning("[Flasher] Some components not available: "
                   f"downloader={_HAS_DOWNLOADER}, esptool={_HAS_ESPTOOL}")


class FlasherState(Enum):
    """State of the flasher."""
    IDLE = "idle"
    DETECTING = "detecting"
    DOWNLOADING = "downloading"
    BACKING_UP = "backing_up"
    FLASHING = "flashing"
    RESTORING = "restoring"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass
class FlasherStatus:
    """Current status of the flasher."""
    state: FlasherState
    message: str = ""
    progress_percent: int = 0
    device_port: str = ""
    firmware_version: str = ""


class FirmwareFlasher:
    """Orchestrates Meshtastic firmware updates."""

    # Supported hardware types (ESP32-based)
    SUPPORTED_HARDWARE = [
        "tbeam",
        "tlora",
        "heltec",
        "esp32",
        "station-g1",
        "station-g2",
    ]

    def __init__(self):
        """Initialize firmware flasher."""
        self._downloader: Optional[FirmwareDownloader] = None
        self._esptool: Optional[EsptoolWrapper] = None
        self._scanner: Optional[DeviceScanner] = None
        self._backup_mgr: Optional[DeviceBackupManager] = None

        self._status = FlasherStatus(state=FlasherState.IDLE)
        self._status_callback: Optional[Callable[[FlasherStatus], None]] = None
        self._abort_requested = False

        self._init_components()

    def _init_components(self):
        """Initialize component instances."""
        if FirmwareDownloader:
            self._downloader = FirmwareDownloader()

        if EsptoolWrapper:
            self._esptool = EsptoolWrapper()
            self._esptool.set_progress_callback(self._on_flash_progress)

        if DeviceScanner:
            self._scanner = DeviceScanner()

        if DeviceBackupManager:
            self._backup_mgr = DeviceBackupManager()

    def set_status_callback(self, callback: Callable[[FlasherStatus], None]):
        """Set callback for status updates.

        Args:
            callback: Function to call with FlasherStatus updates.
        """
        self._status_callback = callback

    def _update_status(self, state: FlasherState, message: str = "",
                       progress: int = 0, **kwargs):
        """Update and report status."""
        self._status.state = state
        self._status.message = message
        self._status.progress_percent = progress

        for key, value in kwargs.items():
            if hasattr(self._status, key):
                setattr(self._status, key, value)

        logger.info(f"[Flasher] {state.value}: {message} ({progress}%)")

        if self._status_callback:
            self._status_callback(self._status)

    def _on_flash_progress(self, progress: FlashProgress):
        """Handle flash progress updates."""
        self._update_status(
            FlasherState.FLASHING,
            progress.message,
            progress.percent
        )

    @property
    def is_available(self) -> bool:
        """Check if flashing is available."""
        return self._esptool is not None and self._esptool.is_available

    @property
    def current_status(self) -> FlasherStatus:
        """Get current status."""
        return self._status

    def detect_devices(self) -> List[dict]:
        """Detect connected Meshtastic devices.

        Returns:
            List of device info dictionaries.
        """
        if not self._scanner:
            return []

        self._update_status(FlasherState.DETECTING, "Scanning for devices...")

        try:
            result = self._scanner.scan_all()
            devices = []

            for port in result.get("serial_ports", []):
                # Check if this is likely a Meshtastic device
                device = {
                    "port": port.device,
                    "by_id": port.by_id,
                    "driver": port.driver,
                    "is_meshtastic": False,
                    "chip_info": None,
                }

                # Try to get chip info if esptool available
                if self._esptool:
                    chip_info = self._esptool.get_chip_info(port.device)
                    if chip_info and chip_info.get("chip"):
                        device["chip_info"] = chip_info
                        device["is_meshtastic"] = True

                devices.append(device)

            self._update_status(FlasherState.IDLE, f"Found {len(devices)} devices")
            return devices

        except Exception as e:
            logger.error(f"[Flasher] Device detection failed: {e}")
            self._update_status(FlasherState.ERROR, str(e))
            return []

    def get_available_firmware(self, include_prereleases: bool = False) -> List[dict]:
        """Get list of available firmware versions.

        Args:
            include_prereleases: Include pre-release versions.

        Returns:
            List of version info dictionaries.
        """
        if not self._downloader:
            return []

        try:
            releases = self._downloader.get_releases(include_prereleases)
            return [
                {
                    "version": r.version,
                    "tag": r.tag_name,
                    "date": r.release_date,
                    "prerelease": r.prerelease,
                }
                for r in releases[:10]  # Limit to 10 most recent
            ]
        except Exception as e:
            logger.error(f"[Flasher] Failed to get firmware list: {e}")
            return []

    def get_firmware_for_device(self, version: str,
                                 hardware_type: str) -> List[FirmwareAsset]:
        """Get firmware assets for a specific device type.

        Args:
            version: Firmware version.
            hardware_type: Device hardware type.

        Returns:
            List of matching firmware assets.
        """
        if not self._downloader:
            return []

        return self._downloader.get_assets_for_hardware(version, hardware_type)

    def flash_device(self, port: str, firmware_version: str,
                     hardware_type: str, backup_first: bool = True,
                     restore_after: bool = True) -> FlashResult:
        """Flash firmware to a device.

        Args:
            port: Serial port of device.
            firmware_version: Version to flash.
            hardware_type: Device hardware type.
            backup_first: Backup device config before flashing.
            restore_after: Restore config after successful flash.

        Returns:
            FlashResult with success status.
        """
        if not self._downloader or not self._esptool:
            return FlashResult(
                success=False,
                message="Flashing components not available"
            )

        self._abort_requested = False
        backup_file = None
        start_time = time.time()

        try:
            # Step 1: Backup device config
            if backup_first and self._backup_mgr:
                self._update_status(FlasherState.BACKING_UP, "Backing up device config...", 5)
                try:
                    backup_result = self._backup_mgr.export_device_config()
                    backup_file = backup_result.get("backup_file")
                    logger.info(f"[Flasher] Config backed up to: {backup_file}")
                except Exception as e:
                    logger.warning(f"[Flasher] Backup failed (continuing): {e}")

                if self._abort_requested:
                    return FlashResult(success=False, message="Aborted by user")

            # Step 2: Download firmware
            self._update_status(FlasherState.DOWNLOADING,
                              f"Downloading firmware {firmware_version}...", 10)

            assets = self._downloader.get_assets_for_hardware(
                firmware_version, hardware_type
            )

            if not assets:
                return FlashResult(
                    success=False,
                    message=f"No firmware found for {hardware_type} v{firmware_version}"
                )

            # Find the .bin file
            firmware_asset = None
            for asset in assets:
                if asset.name.endswith(".bin") and "update" in asset.name.lower():
                    firmware_asset = asset
                    break

            # Fallback to first .bin
            if not firmware_asset:
                for asset in assets:
                    if asset.name.endswith(".bin"):
                        firmware_asset = asset
                        break

            if not firmware_asset:
                return FlashResult(
                    success=False,
                    message=f"No .bin file found for {hardware_type}"
                )

            def on_download_progress(downloaded, total):
                if total > 0:
                    pct = int(10 + (downloaded / total) * 20)
                    self._update_status(
                        FlasherState.DOWNLOADING,
                        f"Downloading: {downloaded}/{total} bytes",
                        pct
                    )

            firmware_path = self._downloader.download_firmware(
                firmware_asset,
                progress_callback=on_download_progress
            )

            if self._abort_requested:
                return FlashResult(success=False, message="Aborted by user")

            # Step 3: Flash firmware
            self._update_status(FlasherState.FLASHING,
                              "Flashing firmware...", 30,
                              device_port=port,
                              firmware_version=firmware_version)

            result = self._esptool.flash_firmware(port, firmware_path)

            if not result.success:
                self._update_status(FlasherState.ERROR, result.message)
                return result

            # Step 4: Wait for device to reboot
            self._update_status(FlasherState.COMPLETE,
                              "Waiting for device to reboot...", 95)
            time.sleep(5)  # Allow device to boot

            # Step 5: Restore config
            if restore_after and backup_file and self._backup_mgr:
                self._update_status(FlasherState.RESTORING,
                                  "Restoring device config...", 98)
                try:
                    self._backup_mgr.restore_device_config(
                        Path(backup_file),
                        dry_run=False
                    )
                    logger.info("[Flasher] Config restored successfully")
                except Exception as e:
                    logger.warning(f"[Flasher] Restore failed: {e}")
                    result.message += f"\n(Config restore failed: {e})"

            self._update_status(FlasherState.COMPLETE,
                              "Firmware update complete!", 100)
            result.duration_seconds = time.time() - start_time
            return result

        except Exception as e:
            logger.error(f"[Flasher] Flash failed: {e}")
            self._update_status(FlasherState.ERROR, str(e))
            return FlashResult(
                success=False,
                message=str(e),
                duration_seconds=time.time() - start_time
            )

    def abort(self):
        """Abort current operation."""
        self._abort_requested = True
        if self._esptool:
            self._esptool.abort()
        logger.warning("[Flasher] Abort requested")


# Convenience functions
def check_flash_capability() -> dict:
    """Check if system can perform firmware flashing.

    Returns:
        Dict with capability checks.
    """
    result = {
        "available": False,
        "esptool": False,
        "device_scanner": False,
        "backup_manager": False,
        "errors": [],
    }

    if _HAS_ESPTOOL_CHECK:
        result["esptool"] = _check_esptool_available()
        if not result["esptool"]:
            result["errors"].append("esptool not installed (pip install esptool)")
    else:
        result["errors"].append("esptool wrapper not available")

    DeviceScanner()
    result["device_scanner"] = True

    DeviceBackupManager()
    result["backup_manager"] = True

    result["available"] = result["esptool"] and result["device_scanner"]

    return result
