"""RESTful JSON API for live configuration management.

Based on NGINX Unit Control API pattern - enables dynamic configuration
changes without service restarts.

This module provides:
- RESTful GET/PUT/DELETE operations on config paths
- Validation before applying changes
- Thread-safe multi-process access
- Audit logging for configuration changes

Example Usage:
    from utils.config_api import ConfigurationAPI, ConfigValidator
    from utils.common import SettingsManager

    # Create API with existing settings manager
    settings = SettingsManager("gateway", defaults={"rns": {"port": 37428}})
    api = ConfigurationAPI(settings)

    # Register validators
    api.register_validator("rns.port", ConfigValidator.port_validator())

    # Get configuration
    port = api.get("rns.port")  # Returns 37428

    # Update configuration (validates first)
    result = api.put("rns.port", 37429)
    if result.success:
        print("Config updated")
    else:
        print(f"Validation failed: {result.error}")

    # Delete (reset to default)
    result = api.delete("rns.port")

HTTP Server (localhost:8081, auto-started by TUI):
    from utils.config_api import ConfigAPIServer

    server = ConfigAPIServer(api, host="127.0.0.1", port=8081)
    server.start()
    # curl http://localhost:8081/config/rns/port
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import socketserver
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes
# =============================================================================


class ConfigChangeType(Enum):
    """Type of configuration change."""
    SET = "set"
    DELETE = "delete"
    RESET = "reset"


@dataclass
class ConfigResult:
    """Result of a configuration operation."""
    success: bool
    path: str = ""
    value: Any = None
    error: Optional[str] = None
    change_type: Optional[ConfigChangeType] = None
    previous_value: Any = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "success": self.success,
            "path": self.path,
        }
        if self.value is not None:
            result["value"] = self.value
        if self.error:
            result["error"] = self.error
        if self.change_type:
            result["change_type"] = self.change_type.value
        if self.previous_value is not None:
            result["previous_value"] = self.previous_value
        return result


@dataclass
class ValidationResult:
    """Result of configuration validation."""
    valid: bool
    error: Optional[str] = None
    suggestion: Optional[str] = None

    @staticmethod
    def ok() -> ValidationResult:
        """Create a successful validation result."""
        return ValidationResult(valid=True)

    @staticmethod
    def fail(error: str, suggestion: str = None) -> ValidationResult:
        """Create a failed validation result."""
        return ValidationResult(valid=False, error=error, suggestion=suggestion)


@dataclass
class ConfigChange:
    """Record of a configuration change for audit logging."""
    timestamp: float
    path: str
    change_type: ConfigChangeType
    old_value: Any
    new_value: Any
    source: str = "api"  # api, file_reload, reset

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "timestamp": self.timestamp,
            "path": self.path,
            "change_type": self.change_type.value,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "source": self.source,
        }


# =============================================================================
# Configuration Validators
# =============================================================================


class ConfigValidator:
    """Factory for common configuration validators."""

    @staticmethod
    def port_validator(min_port: int = 1, max_port: int = 65535) -> Callable[[Any], ValidationResult]:
        """Validate a port number."""
        def validate(value: Any) -> ValidationResult:
            if not isinstance(value, int):
                return ValidationResult.fail(
                    f"Port must be an integer, got {type(value).__name__}",
                    "Use an integer value like 37428"
                )
            if value < min_port or value > max_port:
                return ValidationResult.fail(
                    f"Port must be between {min_port} and {max_port}, got {value}",
                    f"Choose a port in valid range ({min_port}-{max_port})"
                )
            # Check if port is in common reserved range
            if value < 1024:
                return ValidationResult.fail(
                    f"Port {value} is in reserved range (requires root)",
                    "Choose a port >= 1024 for non-root operation"
                )
            return ValidationResult.ok()
        return validate

    @staticmethod
    def integer_validator(
        min_val: Optional[int] = None,
        max_val: Optional[int] = None
    ) -> Callable[[Any], ValidationResult]:
        """Validate an integer within optional bounds."""
        def validate(value: Any) -> ValidationResult:
            if not isinstance(value, int):
                return ValidationResult.fail(
                    f"Value must be an integer, got {type(value).__name__}"
                )
            if min_val is not None and value < min_val:
                return ValidationResult.fail(
                    f"Value must be >= {min_val}, got {value}"
                )
            if max_val is not None and value > max_val:
                return ValidationResult.fail(
                    f"Value must be <= {max_val}, got {value}"
                )
            return ValidationResult.ok()
        return validate

    @staticmethod
    def float_validator(
        min_val: Optional[float] = None,
        max_val: Optional[float] = None
    ) -> Callable[[Any], ValidationResult]:
        """Validate a float within optional bounds."""
        def validate(value: Any) -> ValidationResult:
            if not isinstance(value, (int, float)):
                return ValidationResult.fail(
                    f"Value must be a number, got {type(value).__name__}"
                )
            if min_val is not None and value < min_val:
                return ValidationResult.fail(
                    f"Value must be >= {min_val}, got {value}"
                )
            if max_val is not None and value > max_val:
                return ValidationResult.fail(
                    f"Value must be <= {max_val}, got {value}"
                )
            return ValidationResult.ok()
        return validate

    @staticmethod
    def string_validator(
        min_length: int = 0,
        max_length: Optional[int] = None,
        pattern: Optional[str] = None
    ) -> Callable[[Any], ValidationResult]:
        """Validate a string with optional length and pattern constraints."""
        compiled_pattern = re.compile(pattern) if pattern else None

        def validate(value: Any) -> ValidationResult:
            if not isinstance(value, str):
                return ValidationResult.fail(
                    f"Value must be a string, got {type(value).__name__}"
                )
            if len(value) < min_length:
                return ValidationResult.fail(
                    f"String must be at least {min_length} characters"
                )
            if max_length is not None and len(value) > max_length:
                return ValidationResult.fail(
                    f"String must be at most {max_length} characters"
                )
            if compiled_pattern and not compiled_pattern.match(value):
                return ValidationResult.fail(
                    f"String does not match required pattern: {pattern}"
                )
            return ValidationResult.ok()
        return validate

    @staticmethod
    def boolean_validator() -> Callable[[Any], ValidationResult]:
        """Validate a boolean value."""
        def validate(value: Any) -> ValidationResult:
            if not isinstance(value, bool):
                return ValidationResult.fail(
                    f"Value must be a boolean, got {type(value).__name__}",
                    "Use true or false"
                )
            return ValidationResult.ok()
        return validate

    @staticmethod
    def enum_validator(allowed_values: List[Any]) -> Callable[[Any], ValidationResult]:
        """Validate value is in allowed set."""
        def validate(value: Any) -> ValidationResult:
            if value not in allowed_values:
                return ValidationResult.fail(
                    f"Value must be one of {allowed_values}, got {value!r}"
                )
            return ValidationResult.ok()
        return validate

    @staticmethod
    def hostname_validator() -> Callable[[Any], ValidationResult]:
        """Validate a hostname."""
        # RFC 1123 hostname pattern
        hostname_pattern = re.compile(
            r'^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?'
            r'(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$'
        )

        def validate(value: Any) -> ValidationResult:
            if not isinstance(value, str):
                return ValidationResult.fail(
                    f"Hostname must be a string, got {type(value).__name__}"
                )
            if len(value) > 253:
                return ValidationResult.fail(
                    "Hostname exceeds maximum length of 253 characters"
                )
            if not hostname_pattern.match(value):
                return ValidationResult.fail(
                    f"Invalid hostname: {value}",
                    "Use valid hostname (e.g., localhost, node1.mesh.local)"
                )
            return ValidationResult.ok()
        return validate

    @staticmethod
    def composite(*validators: Callable[[Any], ValidationResult]) -> Callable[[Any], ValidationResult]:
        """Combine multiple validators (all must pass)."""
        def validate(value: Any) -> ValidationResult:
            for validator in validators:
                result = validator(value)
                if not result.valid:
                    return result
            return ValidationResult.ok()
        return validate


# =============================================================================
# Configuration API
# =============================================================================


class ConfigurationAPI:
    """RESTful JSON API for live configuration management.

    Based on NGINX Unit Control API pattern - enables dynamic configuration
    changes without service restarts.

    Thread-safe for concurrent access from multiple threads/processes.
    """

    def __init__(
        self,
        settings_manager,
        audit_log_max_entries: int = 1000
    ):
        """Initialize the Configuration API.

        Args:
            settings_manager: SettingsManager instance for persistence
            audit_log_max_entries: Maximum audit log entries to retain
        """
        self._settings = settings_manager
        self._validators: Dict[str, Callable[[Any], ValidationResult]] = {}
        self._change_callbacks: Dict[str, List[Callable[[Any, Any], None]]] = {}
        self._global_callbacks: List[Callable[[ConfigChange], None]] = []
        self._audit_log: List[ConfigChange] = []
        self._audit_log_max = audit_log_max_entries
        self._lock = threading.RLock()

    # -------------------------------------------------------------------------
    # RESTful Operations
    # -------------------------------------------------------------------------

    def get(self, path: str = "") -> Any:
        """Get configuration value by dot-notation path.

        Args:
            path: Configuration path (e.g., "rns.port", "gateway.timeout")
                  Empty string returns entire config

        Returns:
            Configuration value at path, or None if not found

        Example:
            api.get("")  # Returns entire config dict
            api.get("rns")  # Returns {"port": 37428, ...}
            api.get("rns.port")  # Returns 37428
        """
        with self._lock:
            if not path:
                return copy.deepcopy(self._settings.all())

            parts = path.split(".")
            value = self._settings.all()

            for part in parts:
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    return None

            # Return deep copy to prevent external mutation
            return copy.deepcopy(value) if isinstance(value, (dict, list)) else value

    def put(
        self,
        path: str,
        value: Any,
        source: str = "api"
    ) -> ConfigResult:
        """Set configuration value at path.

        Validates before applying, triggers callbacks on success.

        Args:
            path: Configuration path (e.g., "rns.port")
            value: New value to set
            source: Change source for audit log (default: "api")

        Returns:
            ConfigResult with success/failure status

        Example:
            result = api.put("rns.port", 37429)
            if result.success:
                print("Updated!")
            else:
                print(f"Failed: {result.error}")
        """
        if not path:
            return ConfigResult(
                success=False,
                path=path,
                error="Cannot PUT to root path. Use specific config keys."
            )

        with self._lock:
            # Get current value for comparison and callbacks
            old_value = self.get(path)

            # Validate if validator registered
            if path in self._validators:
                validation = self._validators[path](value)
                if not validation.valid:
                    return ConfigResult(
                        success=False,
                        path=path,
                        value=value,
                        error=validation.error
                    )

            # Apply atomically
            parts = path.split(".")
            settings_dict = self._settings.all()
            current = settings_dict

            # Navigate/create nested structure
            for part in parts[:-1]:
                if part not in current or not isinstance(current[part], dict):
                    current[part] = {}
                current = current[part]

            # Set the value
            current[parts[-1]] = value

            # Save to settings manager
            self._settings._settings = settings_dict
            if not self._settings.save():
                return ConfigResult(
                    success=False,
                    path=path,
                    value=value,
                    error="Failed to persist configuration"
                )

            # Record audit log
            change = ConfigChange(
                timestamp=time.time(),
                path=path,
                change_type=ConfigChangeType.SET,
                old_value=old_value,
                new_value=value,
                source=source
            )
            self._record_change(change)

            # Fire callbacks
            self._fire_callbacks(path, old_value, value, change)

            return ConfigResult(
                success=True,
                path=path,
                value=value,
                change_type=ConfigChangeType.SET,
                previous_value=old_value
            )

    def delete(self, path: str, source: str = "api") -> ConfigResult:
        """Delete configuration value at path (reset to default).

        Args:
            path: Configuration path to delete
            source: Change source for audit log

        Returns:
            ConfigResult with success/failure status
        """
        if not path:
            return ConfigResult(
                success=False,
                path=path,
                error="Cannot DELETE root path. Use reset() to restore defaults."
            )

        with self._lock:
            # Get current value
            old_value = self.get(path)
            if old_value is None:
                return ConfigResult(
                    success=False,
                    path=path,
                    error=f"Path not found: {path}"
                )

            # Navigate to parent and delete key
            parts = path.split(".")
            settings_dict = self._settings.all()
            current = settings_dict

            # Navigate to parent
            for part in parts[:-1]:
                if part not in current:
                    return ConfigResult(
                        success=False,
                        path=path,
                        error=f"Parent path not found"
                    )
                current = current[part]

            # Delete the key
            key = parts[-1]
            if key not in current:
                return ConfigResult(
                    success=False,
                    path=path,
                    error=f"Key not found: {key}"
                )

            del current[key]

            # Save
            self._settings._settings = settings_dict
            if not self._settings.save():
                return ConfigResult(
                    success=False,
                    path=path,
                    error="Failed to persist configuration"
                )

            # Record audit log
            change = ConfigChange(
                timestamp=time.time(),
                path=path,
                change_type=ConfigChangeType.DELETE,
                old_value=old_value,
                new_value=None,
                source=source
            )
            self._record_change(change)

            # Fire callbacks with None as new value
            self._fire_callbacks(path, old_value, None, change)

            return ConfigResult(
                success=True,
                path=path,
                change_type=ConfigChangeType.DELETE,
                previous_value=old_value
            )

    def patch(
        self,
        path: str,
        updates: Dict[str, Any],
        source: str = "api"
    ) -> ConfigResult:
        """Partially update configuration at path.

        Unlike PUT which replaces, PATCH merges updates into existing config.

        Args:
            path: Configuration path (can be empty for root)
            updates: Dictionary of updates to merge
            source: Change source for audit log

        Returns:
            ConfigResult with success/failure status
        """
        with self._lock:
            # Get current value
            current = self.get(path) if path else self._settings.all()
            if current is None:
                current = {}
            if not isinstance(current, dict):
                return ConfigResult(
                    success=False,
                    path=path,
                    error=f"Cannot PATCH non-dict value at {path}"
                )

            # Merge updates
            merged = {**current, **updates}

            # Validate all updated paths
            for key, value in updates.items():
                full_path = f"{path}.{key}" if path else key
                if full_path in self._validators:
                    validation = self._validators[full_path](value)
                    if not validation.valid:
                        return ConfigResult(
                            success=False,
                            path=full_path,
                            error=validation.error
                        )

            # Apply merged config
            if path:
                return self.put(path, merged, source)
            else:
                # Root patch - update each key individually
                results = []
                for key, value in updates.items():
                    result = self.put(key, value, source)
                    results.append(result)
                    if not result.success:
                        return result

                return ConfigResult(
                    success=True,
                    path="",
                    change_type=ConfigChangeType.SET
                )

    def reset(self, path: str = "", source: str = "api") -> ConfigResult:
        """Reset configuration to defaults.

        Args:
            path: Path to reset (empty resets entire config)
            source: Change source for audit log

        Returns:
            ConfigResult with success/failure status
        """
        with self._lock:
            old_config = self._settings.all()

            if not path:
                # Full reset
                self._settings.reset()

                change = ConfigChange(
                    timestamp=time.time(),
                    path="",
                    change_type=ConfigChangeType.RESET,
                    old_value=old_config,
                    new_value=self._settings.all(),
                    source=source
                )
                self._record_change(change)

                # Fire global callbacks
                for callback in self._global_callbacks:
                    try:
                        callback(change)
                    except Exception as e:
                        logger.error(f"Global callback error: {e}")

                return ConfigResult(
                    success=True,
                    path="",
                    change_type=ConfigChangeType.RESET,
                    previous_value=old_config
                )
            else:
                # Path-specific reset - get default value
                defaults = self._settings._defaults
                parts = path.split(".")
                default_value = defaults

                for part in parts:
                    if isinstance(default_value, dict) and part in default_value:
                        default_value = default_value[part]
                    else:
                        default_value = None
                        break

                if default_value is not None:
                    return self.put(path, default_value, source)
                else:
                    return self.delete(path, source)

    # -------------------------------------------------------------------------
    # Validators
    # -------------------------------------------------------------------------

    def register_validator(
        self,
        path: str,
        validator: Callable[[Any], ValidationResult]
    ) -> None:
        """Register a validator for a configuration path.

        Args:
            path: Configuration path to validate
            validator: Function that returns ValidationResult
        """
        with self._lock:
            self._validators[path] = validator

    def unregister_validator(self, path: str) -> None:
        """Remove validator for a configuration path."""
        with self._lock:
            self._validators.pop(path, None)

    def validate(self, path: str, value: Any) -> ValidationResult:
        """Validate a value without applying it.

        Args:
            path: Configuration path
            value: Value to validate

        Returns:
            ValidationResult
        """
        with self._lock:
            if path in self._validators:
                return self._validators[path](value)
            return ValidationResult.ok()

    def validate_all(self, config: Dict[str, Any], prefix: str = "") -> List[ValidationResult]:
        """Validate an entire configuration dictionary.

        Args:
            config: Configuration dictionary to validate
            prefix: Path prefix for nested validation

        Returns:
            List of ValidationResults (only failures)
        """
        failures = []

        def validate_recursive(obj: Any, path: str):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    full_path = f"{path}.{key}" if path else key
                    if full_path in self._validators:
                        result = self._validators[full_path](value)
                        if not result.valid:
                            failures.append(result)
                    if isinstance(value, dict):
                        validate_recursive(value, full_path)

        with self._lock:
            validate_recursive(config, prefix)

        return failures

    def _fire_callbacks(
        self,
        path: str,
        old_value: Any,
        new_value: Any,
        change: ConfigChange
    ) -> None:
        """Fire registered callbacks for a change."""
        # Path-specific callbacks
        if path in self._change_callbacks:
            for callback in self._change_callbacks[path]:
                try:
                    callback(old_value, new_value)
                except Exception as e:
                    logger.error(f"Callback error for {path}: {e}")

        # Global callbacks
        for callback in self._global_callbacks:
            try:
                callback(change)
            except Exception as e:
                logger.error(f"Global callback error: {e}")

    # -------------------------------------------------------------------------
    # Audit Log
    # -------------------------------------------------------------------------

    def _record_change(self, change: ConfigChange) -> None:
        """Record a change to the audit log."""
        self._audit_log.append(change)
        # Trim log if needed
        if len(self._audit_log) > self._audit_log_max:
            self._audit_log = self._audit_log[-self._audit_log_max:]

    def get_audit_log(
        self,
        limit: int = 50,
        path_filter: Optional[str] = None
    ) -> List[ConfigChange]:
        """Get recent configuration changes.

        Args:
            limit: Maximum entries to return
            path_filter: Optional path prefix filter

        Returns:
            List of ConfigChange objects (newest first)
        """
        with self._lock:
            log = list(reversed(self._audit_log))
            if path_filter:
                log = [c for c in log if c.path.startswith(path_filter)]
            return log[:limit]

    # -------------------------------------------------------------------------
    # Utility Methods
    # -------------------------------------------------------------------------

    def list_paths(self, prefix: str = "") -> List[str]:
        """List all configuration paths.

        Args:
            prefix: Optional prefix to filter paths

        Returns:
            List of all configuration paths
        """
        paths = []

        def collect_paths(obj: Any, current_path: str):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    full_path = f"{current_path}.{key}" if current_path else key
                    paths.append(full_path)
                    collect_paths(value, full_path)

        with self._lock:
            collect_paths(self._settings.all(), "")

        if prefix:
            paths = [p for p in paths if p.startswith(prefix)]

        return sorted(paths)

    def export_json(self, pretty: bool = True) -> str:
        """Export entire configuration as JSON.

        Args:
            pretty: Use indentation for readability

        Returns:
            JSON string
        """
        with self._lock:
            config = self._settings.all()
            if pretty:
                return json.dumps(config, indent=2, sort_keys=True)
            return json.dumps(config)

    def import_json(
        self,
        json_str: str,
        validate: bool = True,
        source: str = "import"
    ) -> ConfigResult:
        """Import configuration from JSON.

        Args:
            json_str: JSON configuration string
            validate: Whether to validate before applying
            source: Change source for audit log

        Returns:
            ConfigResult with success/failure status
        """
        try:
            config = json.loads(json_str)
        except json.JSONDecodeError as e:
            return ConfigResult(
                success=False,
                error=f"Invalid JSON: {e}"
            )

        if not isinstance(config, dict):
            return ConfigResult(
                success=False,
                error="Configuration must be a JSON object"
            )

        # Validate if requested
        if validate:
            failures = self.validate_all(config)
            if failures:
                errors = "; ".join(f.error for f in failures if f.error)
                return ConfigResult(
                    success=False,
                    error=f"Validation failed: {errors}"
                )

        # Apply all changes
        with self._lock:
            old_config = self._settings.all()
            self._settings._settings = config
            if not self._settings.save():
                self._settings._settings = old_config
                return ConfigResult(
                    success=False,
                    error="Failed to persist configuration"
                )

            change = ConfigChange(
                timestamp=time.time(),
                path="",
                change_type=ConfigChangeType.SET,
                old_value=old_config,
                new_value=config,
                source=source
            )
            self._record_change(change)

            # Fire global callbacks
            for callback in self._global_callbacks:
                try:
                    callback(change)
                except Exception as e:
                    logger.error(f"Global callback error: {e}")

            return ConfigResult(
                success=True,
                path="",
                change_type=ConfigChangeType.SET,
                previous_value=old_config
            )


# =============================================================================
# HTTP Server for Configuration API
# =============================================================================


class ConfigAPIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Configuration API.

    Implements RESTful endpoints:
    - GET /config[/path] - Get configuration
    - PUT /config/path - Set configuration
    - DELETE /config/path - Delete configuration
    - PATCH /config[/path] - Partial update
    - POST /config/_reset[/path] - Reset to defaults
    - GET /config/_audit - Get audit log
    - GET /config/_paths - List all paths
    """

    api: ConfigurationAPI = None  # Set by server

    def log_message(self, format, *args):
        """Override to use our logger."""
        logger.debug(f"ConfigAPI: {args[0]}")

    def _set_json_headers(self, status: int = 200):
        """Set JSON response headers."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

    def _send_json(self, data: Any, status: int = 200):
        """Send JSON response."""
        self._set_json_headers(status)
        response = json.dumps(data, indent=2)
        self.wfile.write(response.encode())

    def _send_error_json(self, status: int, error: str):
        """Send JSON error response."""
        self._set_json_headers(status)
        response = json.dumps({"error": error})
        self.wfile.write(response.encode())

    def _parse_path(self) -> Tuple[str, str]:
        """Parse request path into (action, config_path).

        Returns:
            (action, config_path) tuple
            action: "config", "_reset", "_audit", "_paths"
        """
        path = self.path.strip("/")
        parts = path.split("/")

        if not parts or parts[0] != "config":
            return ("", "")

        if len(parts) == 1:
            return ("config", "")

        if parts[1].startswith("_"):
            # Special action
            action = parts[1]
            config_path = ".".join(parts[2:]) if len(parts) > 2 else ""
            return (action, config_path)

        # Regular config path
        return ("config", ".".join(parts[1:]))

    # Maximum request body size (1 MB)
    _MAX_BODY_SIZE = 1_048_576

    def _check_localhost(self) -> bool:
        """Verify request originates from localhost. Returns True if allowed."""
        try:
            client_ip = __import__('ipaddress').ip_address(self.client_address[0])
            return client_ip.is_loopback
        except (ValueError, AttributeError):
            return False

    def _read_body(self) -> Optional[Any]:
        """Read and parse JSON request body."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            return None
        if content_length > self._MAX_BODY_SIZE:
            return None

        body = self.rfile.read(content_length)
        try:
            return json.loads(body.decode())
        except json.JSONDecodeError:
            return None

    def do_GET(self):
        """Handle GET requests."""
        if self.api is None:
            self._send_error_json(503, "Configuration API not initialized")
            return

        action, config_path = self._parse_path()

        if action == "":
            self._send_error_json(404, "Not found")
            return

        if action == "config":
            value = self.api.get(config_path)
            if value is None and config_path:
                self._send_error_json(404, f"Path not found: {config_path}")
            else:
                self._send_json({"path": config_path, "value": value})

        elif action == "_audit":
            limit = 50
            # Parse query params (basic)
            if "?" in self.path:
                query = self.path.split("?")[1]
                for param in query.split("&"):
                    if param.startswith("limit="):
                        try:
                            limit = int(param.split("=")[1])
                        except ValueError:
                            pass

            log = self.api.get_audit_log(limit=limit, path_filter=config_path or None)
            self._send_json({
                "count": len(log),
                "changes": [c.to_dict() for c in log]
            })

        elif action == "_paths":
            paths = self.api.list_paths(config_path)
            self._send_json({"paths": paths})

        else:
            self._send_error_json(404, f"Unknown action: {action}")

    def do_PUT(self):
        """Handle PUT requests (set configuration)."""
        if not self._check_localhost():
            self._send_error_json(403, "Forbidden — localhost only")
            return
        if self.api is None:
            self._send_error_json(503, "Configuration API not initialized")
            return

        action, config_path = self._parse_path()

        if action != "config" or not config_path:
            self._send_error_json(400, "PUT requires a config path")
            return

        body = self._read_body()
        if body is None:
            self._send_error_json(400, "Request body required")
            return

        # Body can be {"value": X} or just X
        value = body.get("value", body) if isinstance(body, dict) and "value" in body else body

        result = self.api.put(config_path, value, source="http")
        if result.success:
            self._send_json(result.to_dict())
        else:
            self._send_error_json(400, result.error or "Unknown error")

    def do_DELETE(self):
        """Handle DELETE requests."""
        if not self._check_localhost():
            self._send_error_json(403, "Forbidden — localhost only")
            return
        if self.api is None:
            self._send_error_json(503, "Configuration API not initialized")
            return

        action, config_path = self._parse_path()

        if action != "config" or not config_path:
            self._send_error_json(400, "DELETE requires a config path")
            return

        result = self.api.delete(config_path, source="http")
        if result.success:
            self._send_json(result.to_dict())
        else:
            self._send_error_json(404, result.error or "Not found")

    def do_PATCH(self):
        """Handle PATCH requests (partial update)."""
        if not self._check_localhost():
            self._send_error_json(403, "Forbidden — localhost only")
            return
        if self.api is None:
            self._send_error_json(503, "Configuration API not initialized")
            return

        action, config_path = self._parse_path()

        if action != "config":
            self._send_error_json(400, "Invalid path for PATCH")
            return

        body = self._read_body()
        if body is None or not isinstance(body, dict):
            self._send_error_json(400, "PATCH requires JSON object body")
            return

        result = self.api.patch(config_path, body, source="http")
        if result.success:
            self._send_json(result.to_dict())
        else:
            self._send_error_json(400, result.error or "Unknown error")

    def do_POST(self):
        """Handle POST requests (reset)."""
        if not self._check_localhost():
            self._send_error_json(403, "Forbidden — localhost only")
            return
        if self.api is None:
            self._send_error_json(503, "Configuration API not initialized")
            return

        action, config_path = self._parse_path()

        if action == "_reset":
            result = self.api.reset(config_path, source="http")
            if result.success:
                self._send_json(result.to_dict())
            else:
                self._send_error_json(400, result.error or "Reset failed")
        else:
            self._send_error_json(400, f"Unknown POST action: {action}")


class ConfigAPIServer:
    """HTTP server for Configuration API (TCP, localhost only)."""

    def __init__(
        self,
        api: ConfigurationAPI,
        host: str = "127.0.0.1",
        port: int = 8081,
        **kwargs
    ):
        self.api = api
        self.host = host
        self.port = port
        self._server = None
        self._thread = None
        self._running = False

    def start(self) -> bool:
        """Start the HTTP server in a background thread."""
        if self._running:
            logger.warning("ConfigAPIServer already running")
            return True

        handler = type(
            "ConfigAPIHandlerWithAPI",
            (ConfigAPIHandler,),
            {"api": self.api}
        )

        try:
            class TCPServer(socketserver.TCPServer):
                allow_reuse_address = True

            self._server = TCPServer((self.host, self.port), handler)
            logger.info(f"ConfigAPI server starting on {self.host}:{self.port}")

            self._running = True
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                daemon=True
            )
            self._thread.start()
            return True

        except Exception as e:
            logger.error(f"Failed to start ConfigAPI server: {e}")
            return False

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the HTTP server."""
        if not self._running:
            return

        self._running = False

        if self._server:
            self._server.shutdown()
            self._server.server_close()

        if self._thread:
            self._thread.join(timeout=timeout)

        logger.info("ConfigAPI server stopped")

    @property
    def is_running(self) -> bool:
        """Check if server is running."""
        return self._running


# =============================================================================
# Factory Functions
# =============================================================================


def create_gateway_config_api(settings_manager=None) -> ConfigurationAPI:
    """Create a ConfigurationAPI pre-configured for gateway settings.

    Registers common validators for gateway configuration.

    Args:
        settings_manager: Optional SettingsManager (created if not provided)

    Returns:
        Configured ConfigurationAPI instance
    """
    if settings_manager is None:
        from utils.common import SettingsManager
        settings_manager = SettingsManager(
            "gateway",
            defaults={
                "rns": {
                    "port": 37428,
                    "host": "localhost",
                    "timeout": 30.0,
                },
                "meshtastic": {
                    "host": "localhost",
                    "port": 4403,
                    "timeout": 30.0,
                },
                "health": {
                    "probe_interval": 30,
                    "fails_threshold": 3,
                    "passes_threshold": 2,
                },
                "retry": {
                    "max_attempts": 3,
                    "base_delay": 1.0,
                    "max_delay": 60.0,
                },
            }
        )

    api = ConfigurationAPI(settings_manager)

    # Register validators
    api.register_validator("rns.port", ConfigValidator.port_validator())
    api.register_validator("rns.host", ConfigValidator.hostname_validator())
    api.register_validator("rns.timeout", ConfigValidator.float_validator(1.0, 300.0))

    api.register_validator("meshtastic.port", ConfigValidator.port_validator())
    api.register_validator("meshtastic.host", ConfigValidator.hostname_validator())
    api.register_validator("meshtastic.timeout", ConfigValidator.float_validator(1.0, 300.0))

    api.register_validator(
        "health.probe_interval",
        ConfigValidator.integer_validator(5, 600)
    )
    api.register_validator(
        "health.fails_threshold",
        ConfigValidator.integer_validator(1, 10)
    )
    api.register_validator(
        "health.passes_threshold",
        ConfigValidator.integer_validator(1, 10)
    )

    api.register_validator(
        "retry.max_attempts",
        ConfigValidator.integer_validator(1, 10)
    )
    api.register_validator(
        "retry.base_delay",
        ConfigValidator.float_validator(0.1, 60.0)
    )
    api.register_validator(
        "retry.max_delay",
        ConfigValidator.float_validator(1.0, 3600.0)
    )

    return api


def start_config_server(
    api: ConfigurationAPI,
    port: int = 8081,
) -> ConfigAPIServer:
    """Start a Configuration API HTTP server.

    Args:
        api: ConfigurationAPI instance
        port: TCP port (default 8081)

    Returns:
        Running ConfigAPIServer instance
    """
    server = ConfigAPIServer(api, port=port)
    server.start()
    return server
