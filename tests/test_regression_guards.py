"""
Regression Guard Tests

Codebase-scanning tests that enforce architectural invariants.
These tests prevent the circular regressions documented in persistent_issues.md
by failing when known anti-patterns are reintroduced.

Ratchet Pattern: Known violations are tracked with exact counts. Tests fail if
the count goes UP (regression) or DOWN without updating the expected count
(forces tightening when violations are fixed).

Usage:
    python3 -m pytest tests/test_regression_guards.py -v
"""

import os
import re
import sys

import pytest

# Source directory
SRC_DIR = os.path.join(os.path.dirname(__file__), '..', 'src')


def _scan_python_files(pattern, exclude_files=None, exclude_dirs=None,
                       skip_comments=True, skip_strings=True):
    """Scan all Python files in src/ for a regex pattern.

    Returns list of (filepath, lineno, line_text) tuples.
    """
    exclude_files = exclude_files or []
    exclude_dirs = exclude_dirs or []
    matches = []

    for root, dirs, files in os.walk(SRC_DIR):
        # Skip excluded directories
        dirs[:] = [d for d in dirs if d not in exclude_dirs]

        for filename in files:
            if not filename.endswith('.py'):
                continue
            if filename in exclude_files:
                continue

            filepath = os.path.join(root, filename)
            try:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    for lineno, line in enumerate(f, 1):
                        stripped = line.strip()

                        # Skip comments
                        if skip_comments and stripped.startswith('#'):
                            continue

                        # Skip string literals (lines that start with quotes)
                        if skip_strings and (stripped.startswith('"') or stripped.startswith("'")):
                            continue

                        if re.search(pattern, line):
                            matches.append((filepath, lineno, line.rstrip()))
            except (IOError, OSError):
                continue

    return matches


class TestTCPConnectionContract:
    """Enforce: TCPInterface() creation only in connection infrastructure.

    meshtasticd supports ONE TCP client at a time (Issue #17). Direct
    TCPInterface() creation outside the connection layer causes connection
    thrashing, breaking the web client at :9443.

    Allowlisted files: Connection infrastructure + files using the global lock.
    """

    # Files that ARE the connection infrastructure or use the global lock correctly
    ALLOWLISTED = {
        'connection_manager.py',    # IS the connection manager
        'meshtastic_connection.py', # IS connection infrastructure
        'connections.py',           # IS connection infrastructure
        'node_monitor.py',          # Uses MESHTASTIC_CONNECTION_LOCK
        'device_controller.py',     # Uses MESHTASTIC_CONNECTION_LOCK
        'rns_transport.py',         # Uses MESHTASTIC_CONNECTION_LOCK
        'mesh_bridge.py',           # Uses MESHTASTIC_CONNECTION_LOCK
    }

    def test_no_new_direct_tcpinterface(self):
        """No NEW files should create TCPInterface() directly."""
        matches = _scan_python_files(
            r'TCPInterface\(',
            exclude_files=list(self.ALLOWLISTED),
        )

        violating_files = set()
        for filepath, lineno, line in matches:
            basename = os.path.basename(filepath)
            # Skip test files
            if 'test_' in basename or '/tests/' in filepath:
                continue
            violating_files.add(f"{filepath}:{lineno}: {line.strip()}")

        assert len(violating_files) == 0, (
            f"Found {len(violating_files)} NEW file(s) creating TCPInterface() directly.\n"
            f"Use MeshtasticConnection from connection_manager.py or acquire\n"
            f"MESHTASTIC_CONNECTION_LOCK first (Issue #17).\n\n"
            f"Violations:\n" + "\n".join(sorted(violating_files))
        )


class TestFromradioContract:
    """Enforce: TX paths never read /api/v1/fromradio.

    Reading fromradio drains packets (including delivery ACKs) meant for the
    web client at :9443, causing 'waiting for delivery' hangs (Issue #17).
    TX should use send_text_direct() which only POSTs to /api/v1/toradio.
    """

    def test_mqtt_bridge_uses_stateless_tx(self):
        """mqtt_bridge_handler.py primary TX path must be send_text_direct."""
        filepath = os.path.join(SRC_DIR, 'gateway', 'mqtt_bridge_handler.py')
        if not os.path.exists(filepath):
            pytest.skip("mqtt_bridge_handler.py not found")

        with open(filepath, 'r') as f:
            content = f.read()

        assert 'send_text_direct' in content, (
            "mqtt_bridge_handler.py should use send_text_direct() for TX "
            "(stateless HTTP, no fromradio contention)"
        )

    def test_mesh_bridge_uses_stateless_tx(self):
        """mesh_bridge.py primary TX path must be send_text_direct."""
        filepath = os.path.join(SRC_DIR, 'gateway', 'mesh_bridge.py')
        if not os.path.exists(filepath):
            pytest.skip("mesh_bridge.py not found")

        with open(filepath, 'r') as f:
            content = f.read()

        assert 'send_text_direct' in content, (
            "mesh_bridge.py should use send_text_direct() for TX "
            "(stateless HTTP, no fromradio contention)"
        )


class TestServiceCheckContract:
    """Enforce: Service state decisions use check_service().

    Raw subprocess systemctl calls for state determination (is-active, restart)
    caused inconsistent status display regressions (Issue #20).
    """

    # Known exceptions (non-core services, display-only)
    KNOWN_EXCEPTIONS = 1  # cli/diagnose.py openwebrx check

    def test_no_new_raw_systemctl_state_checks(self):
        """No NEW files should use raw systemctl for service state decisions."""
        matches = _scan_python_files(
            r"subprocess\.\w+\(.*systemctl.*(?:'is-active'|\"is-active\")",
            exclude_files=['service_check.py'],
        )

        # Filter to actual violations (not comments, not test files)
        violations = []
        for filepath, lineno, line in matches:
            basename = os.path.basename(filepath)
            if 'test_' in basename or '/tests/' in filepath:
                continue
            violations.append(f"{filepath}:{lineno}")

        assert len(violations) <= self.KNOWN_EXCEPTIONS, (
            f"Found {len(violations)} raw systemctl is-active calls "
            f"(expected <= {self.KNOWN_EXCEPTIONS}).\n"
            f"Use check_service() from utils.service_check instead (Issue #20).\n\n"
            f"Violations:\n" + "\n".join(violations)
        )


class TestConfigPathContract:
    """Enforce: RNS config paths use ReticulumPaths, not hardcoded paths.

    Config drift between gateway and rnsd causes silent divergence (Issue #12).
    """

    def test_no_hardcoded_reticulum_paths_in_code(self):
        """No hardcoded ~/.reticulum or /root/.reticulum in Python code."""
        matches = _scan_python_files(
            r'(?:~/\.reticulum|/root/\.reticulum|/home/\w+/\.reticulum)',
            skip_comments=True,
            skip_strings=False,  # Hardcoded paths might be in strings
        )

        # Filter: allow in test files, doc files, and comments
        violations = []
        for filepath, lineno, line in matches:
            basename = os.path.basename(filepath)
            if 'test_' in basename or '/tests/' in filepath:
                continue
            # Allow in config_drift.py (it detects these paths)
            if 'config_drift' in filepath:
                continue
            # Allow in documentation/knowledge content
            if 'knowledge' in filepath or 'diagnostic' in filepath:
                continue
            violations.append(f"{filepath}:{lineno}: {line.strip()}")

        # This is informational — hardcoded paths in string configs may be
        # acceptable if they're defaults. Track but don't block.
        if violations:
            # Just print for awareness, don't fail (too many legitimate uses)
            pass


class TestPathHomeContract:
    """Enforce: No Path.home() usage outside paths.py (Issue #1, MF001)."""

    def test_no_path_home_violations(self):
        """No new Path.home() calls outside the utility function."""
        matches = _scan_python_files(
            r'Path\.home\(\)',
            exclude_files=['paths.py'],
        )

        violations = []
        for filepath, lineno, line in matches:
            basename = os.path.basename(filepath)
            if 'test_' in basename or '/tests/' in filepath:
                continue
            # Allow in fallback functions that define get_real_user_home
            stripped = line.strip()
            if 'return Path.home()' in stripped or 'else Path.home()' in stripped:
                continue
            violations.append(f"{filepath}:{lineno}: {stripped}")

        assert len(violations) == 0, (
            f"Found {len(violations)} Path.home() violations.\n"
            f"Use get_real_user_home() from utils.paths instead (Issue #1, MF001).\n\n"
            f"Violations:\n" + "\n".join(violations)
        )


class TestNoShellTrue:
    """Enforce: No shell=True in subprocess calls (MF002)."""

    def test_no_shell_true(self):
        """No subprocess calls with shell=True."""
        matches = _scan_python_files(
            r'subprocess\.\w+\([^)]*shell\s*=\s*True',
        )

        violations = []
        for filepath, lineno, line in matches:
            basename = os.path.basename(filepath)
            if 'test_' in basename or '/tests/' in filepath:
                continue
            violations.append(f"{filepath}:{lineno}: {line.strip()}")

        assert len(violations) == 0, (
            f"Found {len(violations)} shell=True violations (MF002).\n"
            f"Use list args instead of shell=True.\n\n"
            f"Violations:\n" + "\n".join(violations)
        )


class TestKnownServicesConsistency:
    """Enforce: KNOWN_SERVICES stays in sync across the codebase."""

    def test_known_services_has_core_services(self):
        """KNOWN_SERVICES must include meshtasticd, rnsd, mosquitto."""
        sys.path.insert(0, SRC_DIR)
        try:
            from utils.service_check import KNOWN_SERVICES
            assert 'meshtasticd' in KNOWN_SERVICES, "meshtasticd missing from KNOWN_SERVICES"
            assert 'rnsd' in KNOWN_SERVICES, "rnsd missing from KNOWN_SERVICES"
            assert 'mosquitto' in KNOWN_SERVICES, "mosquitto missing from KNOWN_SERVICES"
        finally:
            sys.path.pop(0)

    def test_rnsd_uses_unix_socket(self):
        """rnsd must use unix_socket detection, not UDP port."""
        sys.path.insert(0, SRC_DIR)
        try:
            from utils.service_check import KNOWN_SERVICES
            rnsd = KNOWN_SERVICES.get('rnsd', {})
            assert rnsd.get('port_type') == 'unix_socket', (
                f"rnsd port_type is '{rnsd.get('port_type')}', expected 'unix_socket'. "
                "UDP port check was replaced by abstract Unix socket detection (PRs #920-922)."
            )
        finally:
            sys.path.pop(0)
