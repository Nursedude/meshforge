"""Common utilities for MeshForge - centralized to reduce code duplication.

This module provides:
- Settings management (save/load JSON with defaults)
- Async CLI command execution
- Common path utilities
- Thread-safe operations

Use these utilities instead of duplicating code across modules.
"""

import json
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar, Union

from utils.cli import find_meshtastic_cli

logger = logging.getLogger(__name__)

# Type variable for generic settings
T = TypeVar('T', bound=Dict[str, Any])


# Import centralized path utility for sudo compatibility
from utils.paths import get_real_user_home


# Default config directory - use real user's home when running with sudo
CONFIG_DIR = get_real_user_home() / ".config" / "meshforge"


class SettingsManager:
    """Centralized settings management with JSON persistence.

    Provides save/load functionality with defaults, reducing code duplication
    across panels and plugins.

    Usage:
        class MyPanel:
            def __init__(self):
                self.settings = SettingsManager(
                    "mypanel",
                    defaults={"theme": "dark", "interval": 5}
                )

            def get_theme(self):
                return self.settings.get("theme")

            def set_theme(self, value):
                self.settings.set("theme", value)
                self.settings.save()
    """

    def __init__(
        self,
        name: str,
        defaults: Optional[Dict[str, Any]] = None,
        config_dir: Optional[Path] = None
    ):
        """Initialize settings manager.

        Args:
            name: Settings file name (without .json extension)
            defaults: Default values dictionary
            config_dir: Optional custom config directory
        """
        self._name = name
        self._defaults = defaults or {}
        self._config_dir = config_dir or CONFIG_DIR
        self._settings_file = self._config_dir / f"{name}.json"
        self._settings: Dict[str, Any] = {}
        self._lock = threading.RLock()  # RLock allows reset() to call save() under lock
        self.load()

    @property
    def file_path(self) -> Path:
        """Get the settings file path."""
        return self._settings_file

    def load(self) -> Dict[str, Any]:
        """Load settings from file, merging with defaults.

        Returns:
            Current settings dictionary
        """
        with self._lock:
            self._settings = self._defaults.copy()
            try:
                if self._settings_file.exists():
                    content = self._settings_file.read_text().strip()
                    if not content:
                        # Empty file - treat as fresh start
                        logger.info(f"Empty settings file {self._settings_file}, using defaults")
                    else:
                        saved = json.loads(content)
                        self._settings.update(saved)
            except json.JSONDecodeError as e:
                logger.warning(f"Corrupted settings in {self._settings_file}: {e}")
                logger.info("Using default settings. File will be fixed on next save.")
                # Backup corrupted file for debugging
                try:
                    backup = self._settings_file.with_suffix('.json.bak')
                    self._settings_file.rename(backup)
                    logger.info(f"Corrupted file backed up to {backup}")
                except Exception:  # Non-critical: backup may fail
                    pass
            except IOError as e:
                logger.error(f"Error reading {self._settings_file}: {e}")
            return self._settings.copy()

    def save(self) -> bool:
        """Save current settings to file atomically.

        Uses temp-file-then-rename to prevent partial writes on crash.

        Returns:
            True if save was successful
        """
        with self._lock:
            try:
                self._config_dir.mkdir(parents=True, exist_ok=True)
                content = json.dumps(self._settings, indent=2)
                from utils.paths import atomic_write_text
                atomic_write_text(self._settings_file, content)
                return True
            except (IOError, OSError) as e:
                logger.error(f"Error saving {self._settings_file}: {e}")
                return False

    def get(self, key: str, default: Any = None) -> Any:
        """Get a setting value.

        Args:
            key: Setting key
            default: Default value if key not found

        Returns:
            Setting value or default
        """
        with self._lock:
            return self._settings.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a setting value (does not auto-save).

        Args:
            key: Setting key
            value: Setting value
        """
        with self._lock:
            self._settings[key] = value

    def update(self, values: Dict[str, Any]) -> None:
        """Update multiple settings at once (does not auto-save).

        Args:
            values: Dictionary of key-value pairs to update
        """
        with self._lock:
            self._settings.update(values)

    def reset(self) -> None:
        """Reset settings to defaults and save."""
        with self._lock:
            self._settings = self._defaults.copy()
            self.save()

    def all(self) -> Dict[str, Any]:
        """Get a copy of all current settings.

        Returns:
            Copy of settings dictionary
        """
        with self._lock:
            return self._settings.copy()

    def __getitem__(self, key: str) -> Any:
        """Dictionary-style access."""
        return self.get(key)

    def __setitem__(self, key: str, value: Any) -> None:
        """Dictionary-style assignment."""
        self.set(key, value)


def run_cli_async(
    args: List[str],
    callback: Callable[[bool, str, str], None],
    cli_path: Optional[str] = None,
    host: str = 'localhost',
    timeout: int = 30
) -> threading.Thread:
    """Run CLI commands in background threads with callback.

    Args:
        args: Command arguments (without meshtastic prefix)
        callback: Function called with (success, stdout, stderr)
        cli_path: Optional explicit CLI path (auto-detected if None)
        host: Meshtastic host (default: localhost)
        timeout: Command timeout in seconds

    Returns:
        The started thread (for joining if needed)

    Usage:
        def on_result(success, stdout, stderr):
            if success:
                print(f"Output: {stdout}")
            else:
                print(f"Error: {stderr}")

        run_cli_async(['--info'], on_result)
    """
    def do_run():
        # Find CLI if not provided
        nonlocal cli_path
        if cli_path is None:
            cli_path = find_meshtastic_cli()

        if not cli_path:
            callback(False, "", "Meshtastic CLI not found. Install with: pipx install meshtastic[cli]")
            return

        cmd = [cli_path, '--host', host] + args
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            callback(
                result.returncode == 0,
                result.stdout,
                result.stderr if result.returncode != 0 else ""
            )
        except subprocess.TimeoutExpired:
            callback(False, "", f"Command timed out after {timeout}s")
        except FileNotFoundError:
            callback(False, "", f"CLI not found at {cli_path}")
        except Exception as e:
            callback(False, "", str(e))

    thread = threading.Thread(target=do_run, daemon=True)
    thread.start()
    return thread


def ensure_config_dir(subdir: Optional[str] = None) -> Path:
    """Ensure config directory exists and return path.

    Args:
        subdir: Optional subdirectory within config dir

    Returns:
        Path to the config directory
    """
    path = CONFIG_DIR
    if subdir:
        path = path / subdir
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_data_dir(app_name: str = "meshforge") -> Path:
    """Get the application data directory.

    Uses real user's home when running with sudo.

    Args:
        app_name: Application name for directory

    Returns:
        Path to data directory
    """
    real_home = get_real_user_home()
    xdg_data = os.environ.get('XDG_DATA_HOME', str(real_home / '.local' / 'share'))
    data_dir = Path(xdg_data) / app_name
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_cache_dir(app_name: str = "meshforge") -> Path:
    """Get the application cache directory.

    Uses real user's home when running with sudo.

    Args:
        app_name: Application name for directory

    Returns:
        Path to cache directory
    """
    real_home = get_real_user_home()
    xdg_cache = os.environ.get('XDG_CACHE_HOME', str(real_home / '.cache'))
    cache_dir = Path(xdg_cache) / app_name
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


class Singleton(type):
    """Metaclass for creating singleton classes.

    Usage:
        class MyManager(metaclass=Singleton):
            def __init__(self):
                self.data = []

        # All instances are the same
        a = MyManager()
        b = MyManager()
        assert a is b
    """
    _instances: Dict[type, Any] = {}
    _lock = threading.Lock()

    def __call__(cls, *args, **kwargs):
        with cls._lock:
            if cls not in cls._instances:
                cls._instances[cls] = super().__call__(*args, **kwargs)
            return cls._instances[cls]


def debounce(wait_ms: int):
    """Decorator to debounce function calls.

    Prevents rapid repeated calls - only executes after wait_ms
    milliseconds have passed since the last call.

    Args:
        wait_ms: Milliseconds to wait before executing

    Usage:
        @debounce(500)
        def on_search_changed(text):
            # Only called 500ms after user stops typing
            search(text)
    """
    def decorator(fn):
        timer = None
        lock = threading.Lock()

        def debounced(*args, **kwargs):
            nonlocal timer
            with lock:
                if timer is not None:
                    timer.cancel()
                timer = threading.Timer(wait_ms / 1000.0, lambda: fn(*args, **kwargs))
                timer.daemon = True
                timer.start()

        return debounced
    return decorator


def throttle(interval_ms: int):
    """Decorator to throttle function calls.

    Ensures function is called at most once per interval_ms milliseconds.

    Args:
        interval_ms: Minimum milliseconds between calls

    Usage:
        @throttle(1000)
        def update_status():
            # Called at most once per second
            refresh_ui()
    """
    import time

    def decorator(fn):
        last_called = [0.0]
        lock = threading.Lock()

        def throttled(*args, **kwargs):
            with lock:
                now = time.time()
                if now - last_called[0] >= interval_ms / 1000.0:
                    last_called[0] = now
                    return fn(*args, **kwargs)
            return None

        return throttled
    return decorator
