"""
Tests for common utilities (SettingsManager, path utils, decorators).

Run: python3 -m pytest tests/test_common.py -v
"""

import json
import pytest
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.utils.common import (
    SettingsManager,
    ensure_config_dir,
    Singleton,
    debounce,
    throttle,
)


class TestSettingsManager:
    """Tests for SettingsManager JSON persistence."""

    def test_create_with_defaults(self, tmp_path):
        """Test initialization with default values."""
        defaults = {"theme": "dark", "interval": 5}
        manager = SettingsManager("test", defaults=defaults, config_dir=tmp_path)

        assert manager.get("theme") == "dark"
        assert manager.get("interval") == 5

    def test_get_nonexistent_key(self, tmp_path):
        """Test getting a key that doesn't exist."""
        manager = SettingsManager("test", config_dir=tmp_path)

        assert manager.get("nonexistent") is None
        assert manager.get("nonexistent", "default") == "default"

    def test_set_and_get(self, tmp_path):
        """Test setting and retrieving values."""
        manager = SettingsManager("test", config_dir=tmp_path)

        manager.set("key1", "value1")
        manager.set("key2", 42)
        manager.set("key3", {"nested": True})

        assert manager.get("key1") == "value1"
        assert manager.get("key2") == 42
        assert manager.get("key3") == {"nested": True}

    def test_save_and_load(self, tmp_path):
        """Test persistence across instances."""
        # Save settings
        manager1 = SettingsManager("test", config_dir=tmp_path)
        manager1.set("saved_key", "saved_value")
        manager1.save()

        # Load in new instance
        manager2 = SettingsManager("test", config_dir=tmp_path)
        assert manager2.get("saved_key") == "saved_value"

    def test_update_multiple(self, tmp_path):
        """Test updating multiple settings at once."""
        manager = SettingsManager("test", config_dir=tmp_path)

        manager.update({"a": 1, "b": 2, "c": 3})

        assert manager.get("a") == 1
        assert manager.get("b") == 2
        assert manager.get("c") == 3

    def test_reset_to_defaults(self, tmp_path):
        """Test resetting settings to defaults."""
        defaults = {"default_key": "default_value"}
        manager = SettingsManager("test", defaults=defaults, config_dir=tmp_path)

        manager.set("custom_key", "custom_value")
        manager.set("default_key", "modified")
        manager.reset()

        assert manager.get("default_key") == "default_value"
        assert manager.get("custom_key") is None

    def test_all_returns_copy(self, tmp_path):
        """Test that all() returns a copy, not the original."""
        manager = SettingsManager("test", config_dir=tmp_path)
        manager.set("key", "value")

        settings = manager.all()
        settings["key"] = "modified"

        assert manager.get("key") == "value"  # Original unchanged

    def test_dict_style_access(self, tmp_path):
        """Test dictionary-style access."""
        manager = SettingsManager("test", config_dir=tmp_path)

        manager["key"] = "value"
        assert manager["key"] == "value"

    def test_file_path_property(self, tmp_path):
        """Test file_path property returns correct path."""
        manager = SettingsManager("myname", config_dir=tmp_path)

        assert manager.file_path == tmp_path / "myname.json"

    def test_corrupted_file_handling(self, tmp_path):
        """Test handling of corrupted JSON file."""
        settings_file = tmp_path / "test.json"
        settings_file.write_text("not valid json {{{")

        defaults = {"fallback": "value"}
        manager = SettingsManager("test", defaults=defaults, config_dir=tmp_path)

        # Should use defaults when file is corrupted
        assert manager.get("fallback") == "value"

        # Corrupted file should be backed up
        backup = tmp_path / "test.json.bak"
        assert backup.exists()

    def test_empty_file_handling(self, tmp_path):
        """Test handling of empty settings file."""
        settings_file = tmp_path / "test.json"
        settings_file.write_text("")

        defaults = {"key": "default"}
        manager = SettingsManager("test", defaults=defaults, config_dir=tmp_path)

        assert manager.get("key") == "default"

    def test_creates_directory_on_save(self, tmp_path):
        """Test that save creates directory if it doesn't exist."""
        config_dir = tmp_path / "new" / "nested" / "dir"
        manager = SettingsManager("test", config_dir=config_dir)
        manager.set("key", "value")

        assert manager.save() is True
        assert config_dir.exists()
        assert (config_dir / "test.json").exists()

    def test_thread_safety(self, tmp_path):
        """Test thread-safe access to settings."""
        manager = SettingsManager("test", config_dir=tmp_path)
        errors = []

        def writer(n):
            try:
                for i in range(100):
                    manager.set(f"key_{n}_{i}", i)
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(100):
                    manager.all()
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=(0,)),
            threading.Thread(target=writer, args=(1,)),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

    def test_merge_with_saved_settings(self, tmp_path):
        """Test that saved settings are merged with defaults."""
        # First instance saves some settings
        manager1 = SettingsManager(
            "test",
            defaults={"a": 1, "b": 2},
            config_dir=tmp_path
        )
        manager1.set("c", 3)  # Additional setting not in defaults
        manager1.set("a", 10)  # Override default
        manager1.save()

        # Second instance loads with potentially different defaults
        manager2 = SettingsManager(
            "test",
            defaults={"a": 1, "b": 2, "d": 4},  # New default
            config_dir=tmp_path
        )

        assert manager2.get("a") == 10  # Saved value
        assert manager2.get("b") == 2   # Default
        assert manager2.get("c") == 3   # Saved additional
        assert manager2.get("d") == 4   # New default


class TestEnsureConfigDir:
    """Tests for ensure_config_dir function."""

    def test_creates_directory(self, tmp_path):
        """Test that directory is created."""
        with patch('src.utils.common.CONFIG_DIR', tmp_path):
            from src.utils.common import ensure_config_dir
            path = ensure_config_dir()
            assert path.exists()

    def test_creates_subdirectory(self, tmp_path):
        """Test creating a subdirectory."""
        with patch('src.utils.common.CONFIG_DIR', tmp_path):
            from src.utils.common import ensure_config_dir
            path = ensure_config_dir("subdir")
            assert path.exists()
            assert path == tmp_path / "subdir"


class TestSingleton:
    """Tests for Singleton metaclass."""

    def test_same_instance(self):
        """Test that all instances are the same object."""
        class MySingleton(metaclass=Singleton):
            def __init__(self):
                self.value = 0

        a = MySingleton()
        b = MySingleton()

        assert a is b

    def test_state_shared(self):
        """Test that state is shared between 'instances'."""
        class Counter(metaclass=Singleton):
            def __init__(self):
                self.count = 0

        c1 = Counter()
        c1.count = 42

        c2 = Counter()
        assert c2.count == 42

    def test_thread_safe(self):
        """Test thread-safe singleton creation."""
        class ThreadSafeSingleton(metaclass=Singleton):
            def __init__(self):
                self.created_at = time.time()

        instances = []
        errors = []

        def create():
            try:
                instances.append(ThreadSafeSingleton())
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # All instances should be the same object
        assert all(inst is instances[0] for inst in instances)


class TestDebounce:
    """Tests for debounce decorator."""

    def test_single_call_executes(self):
        """Test that a single call eventually executes."""
        results = []

        @debounce(50)
        def collect(value):
            results.append(value)

        collect("test")
        time.sleep(0.1)  # Wait for debounce

        assert results == ["test"]

    def test_rapid_calls_debounced(self):
        """Test that rapid calls are debounced to one execution."""
        results = []

        @debounce(100)
        def collect(value):
            results.append(value)

        # Rapid calls
        collect(1)
        collect(2)
        collect(3)

        time.sleep(0.05)  # Before debounce period
        assert len(results) == 0  # Not executed yet

        time.sleep(0.15)  # After debounce period
        assert results == [3]  # Only last call executed

    def test_spaced_calls_all_execute(self):
        """Test that properly spaced calls all execute."""
        results = []

        @debounce(50)
        def collect(value):
            results.append(value)

        collect(1)
        time.sleep(0.1)
        collect(2)
        time.sleep(0.1)

        assert 1 in results
        assert 2 in results


class TestThrottle:
    """Tests for throttle decorator."""

    def test_first_call_executes_immediately(self):
        """Test that first call executes immediately."""
        results = []

        @throttle(1000)
        def collect(value):
            results.append(value)
            return value

        result = collect("first")

        assert result == "first"
        assert results == ["first"]

    def test_rapid_calls_throttled(self):
        """Test that rapid calls are throttled."""
        results = []

        @throttle(100)
        def collect(value):
            results.append(value)
            return value

        collect(1)  # Executes
        result2 = collect(2)  # Throttled
        result3 = collect(3)  # Throttled

        assert results == [1]
        assert result2 is None
        assert result3 is None

    def test_calls_after_interval_execute(self):
        """Test that calls after interval execute."""
        results = []

        @throttle(50)
        def collect(value):
            results.append(value)
            return value

        collect(1)
        time.sleep(0.1)
        result2 = collect(2)

        assert 1 in results
        assert 2 in results
        assert result2 == 2


class TestRunCliAsync:
    """Tests for run_cli_async function."""

    def test_callback_on_cli_not_found(self, tmp_path):
        """Test callback receives error when CLI not found."""
        from src.utils.common import run_cli_async

        results = {}

        def callback(success, stdout, stderr):
            results['success'] = success
            results['stderr'] = stderr

        with patch('shutil.which', return_value=None):
            thread = run_cli_async(['--info'], callback, cli_path=None)
            thread.join(timeout=5)

        assert results['success'] is False
        assert 'not found' in results['stderr'].lower()

    def test_timeout_handling(self):
        """Test handling of command timeout."""
        from src.utils.common import run_cli_async

        results = {}

        def callback(success, stdout, stderr):
            results['success'] = success
            results['stderr'] = stderr

        # Use a command that will timeout
        with patch('subprocess.run') as mock_run:
            import subprocess
            mock_run.side_effect = subprocess.TimeoutExpired(['test'], 1)

            thread = run_cli_async(['--info'], callback, cli_path='/bin/test', timeout=1)
            thread.join(timeout=5)

        assert results['success'] is False
        assert 'timed out' in results['stderr'].lower()

    def test_successful_execution(self):
        """Test successful command execution."""
        from src.utils.common import run_cli_async

        results = {}

        def callback(success, stdout, stderr):
            results['success'] = success
            results['stdout'] = stdout

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="test output",
                stderr=""
            )

            thread = run_cli_async(['--info'], callback, cli_path='/bin/test')
            thread.join(timeout=5)

        assert results['success'] is True
        assert results['stdout'] == "test output"
