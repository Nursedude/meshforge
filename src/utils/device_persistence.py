"""
Device Persistence for MeshForge

Remembers the last successfully connected device and allows auto-reconnection.

Features:
- Stores last successful connection (type, address, timestamp)
- Provides quick reconnect to last known device
- Tracks connection history for diagnostics
- Integrates with DeviceController

Usage:
    from utils.device_persistence import DevicePersistence

    # Get singleton instance
    persistence = DevicePersistence.get_instance()

    # Check for last device
    if persistence.has_last_device():
        last = persistence.get_last_device()
        print(f"Last device: {last['connection_type']} at {last['address']}")

    # Store successful connection
    persistence.record_connection(
        connection_type="tcp",
        address="localhost:4403",
        device_info={"firmware": "2.3.0"}
    )

    # Auto-connect helper
    config = persistence.get_reconnect_config()
    if config:
        controller = DeviceController(config)
        controller.connect()
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.common import SettingsManager

logger = logging.getLogger(__name__)


@dataclass
class ConnectionRecord:
    """Record of a device connection attempt."""
    connection_type: str  # "tcp", "serial", "ble"
    address: str          # "localhost:4403", "/dev/ttyUSB0", "AA:BB:CC:DD:EE:FF"
    timestamp: float      # Unix timestamp
    success: bool
    device_info: Dict[str, Any]
    error_message: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "connection_type": self.connection_type,
            "address": self.address,
            "timestamp": self.timestamp,
            "success": self.success,
            "device_info": self.device_info,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'ConnectionRecord':
        return cls(
            connection_type=data.get("connection_type", "unknown"),
            address=data.get("address", ""),
            timestamp=data.get("timestamp", 0),
            success=data.get("success", False),
            device_info=data.get("device_info", {}),
            error_message=data.get("error_message"),
        )


# Default settings for device persistence
DEVICE_PERSISTENCE_DEFAULTS = {
    "last_connection": None,          # Last successful connection info
    "connection_history": [],         # Recent connection attempts
    "auto_reconnect_enabled": True,   # Whether to auto-reconnect
    "max_history_entries": 20,        # Keep last N connection attempts
    "preferred_connection_type": None,  # User preference: "tcp", "serial", "ble", None=auto
}


class DevicePersistence:
    """
    Manages device connection persistence for MeshForge.

    Singleton pattern ensures consistent state across the application.
    Uses SettingsManager for thread-safe JSON persistence.
    """

    _instance: Optional['DevicePersistence'] = None

    @classmethod
    def get_instance(cls) -> 'DevicePersistence':
        """Get the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton for testing."""
        cls._instance = None

    def __init__(self):
        """Initialize device persistence (use get_instance() instead)."""
        self._settings = SettingsManager(
            "device_connection",
            defaults=DEVICE_PERSISTENCE_DEFAULTS
        )

    def _get(self, key: str, default: Any = None) -> Any:
        """Get a setting value."""
        return self._settings.get(key, default)

    def _set(self, key: str, value: Any) -> None:
        """Set a setting value."""
        self._settings.set(key, value)

    def _save(self) -> None:
        """Save settings to disk."""
        self._settings.save()

    # --- Last Device API ---

    def has_last_device(self) -> bool:
        """Check if there's a saved last device."""
        last = self._get("last_connection")
        return last is not None and isinstance(last, dict) and bool(last.get("address"))

    def get_last_device(self) -> Optional[Dict[str, Any]]:
        """
        Get the last successfully connected device info.

        Returns:
            Dict with connection_type, address, timestamp, device_info
            or None if no last device saved.
        """
        return self._get("last_connection")

    def clear_last_device(self) -> None:
        """Clear the saved last device."""
        self._set("last_connection", None)
        self._save()
        logger.info("Cleared last device")

    # --- Connection Recording ---

    def record_connection(self, connection_type: str, address: str,
                         device_info: Optional[Dict[str, Any]] = None,
                         success: bool = True,
                         error_message: Optional[str] = None) -> None:
        """
        Record a connection attempt.

        Args:
            connection_type: "tcp", "serial", or "ble"
            address: Connection address (host:port, device path, BLE address)
            device_info: Optional device information (firmware, node ID, etc.)
            success: Whether connection succeeded
            error_message: Error message if failed
        """
        record = ConnectionRecord(
            connection_type=connection_type,
            address=address,
            timestamp=time.time(),
            success=success,
            device_info=device_info or {},
            error_message=error_message,
        )

        # Update history
        history = self._get("connection_history", [])
        history.append(record.to_dict())

        # Trim to max size
        max_entries = self._get("max_history_entries", 20)
        if len(history) > max_entries:
            history = history[-max_entries:]

        self._set("connection_history", history)

        # Update last connection if successful
        if success:
            self._set("last_connection", {
                "connection_type": connection_type,
                "address": address,
                "timestamp": time.time(),
                "device_info": device_info or {},
                "connected_at": datetime.now().isoformat(),
            })
            logger.info(f"Saved last device: {connection_type}://{address}")

        self._save()

    def get_connection_history(self, count: int = 10) -> List[ConnectionRecord]:
        """
        Get recent connection attempts.

        Args:
            count: Maximum number of records to return

        Returns:
            List of ConnectionRecord objects, most recent first
        """
        history = self._get("connection_history", [])
        records = [ConnectionRecord.from_dict(r) for r in history[-count:]]
        return list(reversed(records))

    # --- Auto-Reconnect Configuration ---

    @property
    def auto_reconnect_enabled(self) -> bool:
        """Check if auto-reconnect is enabled."""
        return self._get("auto_reconnect_enabled", True)

    @auto_reconnect_enabled.setter
    def auto_reconnect_enabled(self, value: bool) -> None:
        """Enable or disable auto-reconnect."""
        self._set("auto_reconnect_enabled", value)
        self._save()

    @property
    def preferred_connection_type(self) -> Optional[str]:
        """Get user's preferred connection type."""
        return self._get("preferred_connection_type")

    @preferred_connection_type.setter
    def preferred_connection_type(self, value: Optional[str]) -> None:
        """Set preferred connection type (tcp, serial, ble, or None for auto)."""
        if value not in (None, "tcp", "serial", "ble"):
            raise ValueError(f"Invalid connection type: {value}")
        self._set("preferred_connection_type", value)
        self._save()

    def get_reconnect_config(self) -> Optional[Dict[str, Any]]:
        """
        Get configuration for reconnecting to last device.

        Returns a dict suitable for DeviceController initialization,
        or None if no last device or auto-reconnect disabled.

        Returns:
            Dict with connection_type, host/port or serial_port, etc.
            or None if not available
        """
        if not self.auto_reconnect_enabled:
            return None

        last = self.get_last_device()
        if not last:
            return None

        conn_type = last.get("connection_type")
        address = last.get("address")

        if not conn_type or not address:
            return None

        config = {}

        if conn_type == "tcp":
            # Parse host:port
            parts = address.split(":")
            config["connection_type"] = "TCP"
            config["host"] = parts[0] if parts else "localhost"
            config["port"] = int(parts[1]) if len(parts) > 1 else 4403

        elif conn_type == "serial":
            config["connection_type"] = "SERIAL"
            config["serial_port"] = address

        elif conn_type == "ble":
            config["connection_type"] = "BLE"
            config["ble_address"] = address

        else:
            logger.warning(f"Unknown connection type: {conn_type}")
            return None

        return config

    # --- Diagnostics ---

    def get_stats(self) -> Dict[str, Any]:
        """Get persistence statistics."""
        history = self._get("connection_history", [])
        success_count = sum(1 for r in history if r.get("success"))

        return {
            "has_last_device": self.has_last_device(),
            "auto_reconnect_enabled": self.auto_reconnect_enabled,
            "preferred_type": self.preferred_connection_type,
            "history_entries": len(history),
            "successful_connections": success_count,
            "failed_connections": len(history) - success_count,
        }


# Convenience function for quick access
def get_device_persistence() -> DevicePersistence:
    """Get the device persistence singleton."""
    return DevicePersistence.get_instance()
