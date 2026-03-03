"""
TUI Security Tests

Tests for security fixes identified in the TUI code and security review.
Covers input validation, shell safety, and MF001 compliance.

Usage:
    python3 -m pytest tests/test_tui_security.py -v
"""

import os
import re
import sys

import pytest

# Ensure src/ is on the path for imports
SRC_DIR = os.path.join(os.path.dirname(__file__), '..', 'src')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


class TestValidateUnixUsername:
    """Tests for validate_unix_username() in utils/validation.py."""

    def test_valid_usernames(self):
        from utils.validation import validate_unix_username
        assert validate_unix_username("alice")
        assert validate_unix_username("_svc")
        assert validate_unix_username("mesh-user")
        assert validate_unix_username("user123")
        assert validate_unix_username("a")

    def test_rejects_empty(self):
        from utils.validation import validate_unix_username
        assert not validate_unix_username("")
        assert not validate_unix_username(None)

    def test_rejects_flag_injection(self):
        from utils.validation import validate_unix_username
        assert not validate_unix_username("-eflag")
        assert not validate_unix_username("--help")

    def test_rejects_special_characters(self):
        from utils.validation import validate_unix_username
        assert not validate_unix_username("user;rm")
        assert not validate_unix_username("user name")
        assert not validate_unix_username("root\x00evil")
        assert not validate_unix_username("../etc")
        assert not validate_unix_username("user|pipe")

    def test_rejects_uppercase(self):
        from utils.validation import validate_unix_username
        assert not validate_unix_username("Root")
        assert not validate_unix_username("USER")

    def test_rejects_too_long(self):
        from utils.validation import validate_unix_username
        assert not validate_unix_username("a" * 33)

    def test_accepts_max_length(self):
        from utils.validation import validate_unix_username
        assert validate_unix_username("a" * 32)


class TestValidateSerialPort:
    """Tests for validate_serial_port() in utils/validation.py."""

    def test_valid_ports(self):
        from utils.validation import validate_serial_port
        assert validate_serial_port("/dev/ttyUSB0")
        assert validate_serial_port("/dev/ttyACM1")
        assert validate_serial_port("/dev/ttyS0")
        assert validate_serial_port("/dev/ttyAMA0")

    def test_rejects_empty(self):
        from utils.validation import validate_serial_port
        assert not validate_serial_port("")
        assert not validate_serial_port(None)

    def test_rejects_path_traversal(self):
        from utils.validation import validate_serial_port
        assert not validate_serial_port("/dev/../etc/passwd")
        assert not validate_serial_port("/dev/tty/../shadow")

    def test_rejects_non_tty_paths(self):
        from utils.validation import validate_serial_port
        assert not validate_serial_port("/tmp/fake")
        assert not validate_serial_port("/dev/sda1")
        assert not validate_serial_port("/etc/passwd")

    def test_rejects_bare_dev(self):
        from utils.validation import validate_serial_port
        assert not validate_serial_port("/dev/tty")


class TestUpdateCommandSafety:
    """Tests for the updates handler command dispatch table."""

    def test_no_bash_c_in_updates_handler(self):
        """Regression: updates.py must not contain bash -c patterns."""
        updates_path = os.path.join(
            SRC_DIR, 'launcher_tui', 'handlers', 'updates.py'
        )
        with open(updates_path, 'r') as f:
            content = f.read()

        # Should not contain bash -c pattern
        assert "'bash', '-c'" not in content, (
            "updates.py contains 'bash', '-c' — use _UPDATE_COMMANDS dispatch table"
        )
        assert '"bash", "-c"' not in content, (
            "updates.py contains \"bash\", \"-c\" — use _UPDATE_COMMANDS dispatch table"
        )

    def test_update_commands_use_list_args(self):
        """All entries in _UPDATE_COMMANDS must be list-of-lists (no strings)."""
        # Import the handler class
        sys.path.insert(0, os.path.join(SRC_DIR, 'launcher_tui'))
        try:
            from handlers.updates import UpdatesHandler
            for component, steps in UpdatesHandler._UPDATE_COMMANDS.items():
                assert isinstance(steps, list), (
                    f"_UPDATE_COMMANDS[{component!r}] must be a list of steps"
                )
                for i, step in enumerate(steps):
                    assert isinstance(step, tuple) and len(step) == 2, (
                        f"_UPDATE_COMMANDS[{component!r}][{i}] must be (argv_list, timeout)"
                    )
                    argv, timeout = step
                    assert isinstance(argv, list), (
                        f"_UPDATE_COMMANDS[{component!r}][{i}] argv must be a list, got {type(argv)}"
                    )
                    assert isinstance(timeout, int), (
                        f"_UPDATE_COMMANDS[{component!r}][{i}] timeout must be int"
                    )
                    for j, arg in enumerate(argv):
                        assert isinstance(arg, str), (
                            f"_UPDATE_COMMANDS[{component!r}][{i}] argv[{j}] must be str"
                        )
        finally:
            sys.path.remove(os.path.join(SRC_DIR, 'launcher_tui'))


class TestShellValidation:
    """Tests for $SHELL validation in system_tools.py."""

    def test_known_shells_are_valid_paths(self):
        """Known shells list should contain absolute paths only."""
        # Read the handler file and extract the _KNOWN_SHELLS set
        handler_path = os.path.join(
            SRC_DIR, 'launcher_tui', 'handlers', 'system_tools.py'
        )
        with open(handler_path, 'r') as f:
            content = f.read()

        # Verify _KNOWN_SHELLS exists and contains only absolute paths
        assert '_KNOWN_SHELLS' in content, (
            "system_tools.py must define _KNOWN_SHELLS for shell validation"
        )
        # Extract paths from the set definition
        paths = re.findall(r"'(/[^']+)'", content[content.index('_KNOWN_SHELLS'):])
        assert len(paths) > 0, "No shell paths found in _KNOWN_SHELLS"
        for path in paths:
            assert path.startswith('/'), f"Shell path must be absolute: {path}"
            assert '..' not in path, f"Shell path must not contain ..: {path}"


class TestSweepStoreNoPathHome:
    """Tests for MF001 compliance in nanovna sweep_store.py."""

    def test_no_path_home_in_sweep_store(self):
        """sweep_store.py must not use Path.home() directly (MF001)."""
        sweep_path = os.path.join(
            SRC_DIR, 'plugins', 'nanovna', 'sweep_store.py'
        )
        if not os.path.exists(sweep_path):
            pytest.skip("NanoVNA plugin not present")

        with open(sweep_path, 'r') as f:
            for lineno, line in enumerate(f, 1):
                stripped = line.strip()
                if stripped.startswith('#'):
                    continue
                assert 'Path.home()' not in line, (
                    f"sweep_store.py:{lineno} uses Path.home() — "
                    "use get_real_user_home() from utils.paths (MF001)"
                )
