"""
Pytest configuration for MeshForge test suite.

Handles CI-specific settings:
- Auto-skip hardware-dependent tests in CI
- Timeout defaults
- Fixtures for common mocks

TUI handler test infrastructure (FakeDialog, make_handler_context) lives
in ``tests/handler_test_utils.py`` — single source of truth. All handler
tests import from there. A duplicate copy used to live in this file; it
was unused (no test imported it) and was removed in the 2026-05-19
pattern-audit cleanup (Finding #9).
"""

import os
import sys
import pytest
from unittest.mock import MagicMock, patch

# Ensure src and launcher_tui are importable for handler tests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Detect CI environment
CI = os.environ.get('CI', 'false').lower() == 'true'
MESHFORGE_CI = os.environ.get('MESHFORGE_CI', 'false').lower() == 'true'


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "hardware: mark test as requiring hardware (skipped in CI)"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow (may be skipped with --fast)"
    )
    config.addinivalue_line(
        "markers", "network: mark test as requiring network access"
    )

    # Disable cascade-detector probes during pytest. The
    # rns_rpc_wedge fingerprint calls `subprocess.run(["ss", ...],
    # timeout=2)`; when a daemon detector thread (started by any test
    # that exercises the full service init path) is still running
    # during another test's `with patch('subprocess.run')` window, the
    # probe's call leaks into the mock's call_args_list and trips
    # assertions like `assert call[1]['timeout'] == 60` —
    # observed on Python 3.9/3.11 in CI starting at commit 79f5d7b.
    # See project_ci_red_track0_followup.md for full forensics.
    # Tests that explicitly exercise probe behavior unset this via
    # `monkeypatch.delenv("MESHFORGE_CASCADE_PROBE_DISABLED", raising=False)`.
    os.environ["MESHFORGE_CASCADE_PROBE_DISABLED"] = "1"

    # Redirect the gateway's content_id_view state file (dedup arc STEP 4c)
    # to a throwaway tmp path for the whole suite. The producer hook fires
    # inside the real RNSMeshtasticBridge._rns_loop, which several bridge
    # tests drive (bridge._running = True), and without this it would write
    # the operator's actual ~/.local/state/meshforge/content_id_view.json.
    # setdefault so a test can still point it elsewhere.
    import tempfile as _tempfile
    os.environ.setdefault(
        "MESHFORGE_CONTENT_ID_VIEW_STATE",
        os.path.join(_tempfile.gettempdir(),
                     "meshforge-pytest-content_id_view.json"),
    )


def pytest_collection_modifyitems(config, items):
    """Auto-skip certain tests in CI environment."""
    if not (CI or MESHFORGE_CI):
        return

    skip_hardware = pytest.mark.skip(reason="Hardware not available in CI")
    skip_network = pytest.mark.skip(reason="Network tests skipped in CI")

    for item in items:
        # Skip hardware-marked tests
        if "hardware" in item.keywords:
            item.add_marker(skip_hardware)

        # Skip network-marked tests in CI
        if "network" in item.keywords:
            item.add_marker(skip_network)

        # Auto-detect likely hardware tests by name
        test_name = item.name.lower()
        if any(kw in test_name for kw in ['real_device', 'physical', 'actual_hardware']):
            item.add_marker(skip_hardware)


@pytest.fixture
def mock_meshtastic():
    """Mock meshtastic module for tests that don't need real hardware."""
    mock_module = MagicMock()
    mock_interface = MagicMock()
    mock_interface.nodes = {}
    mock_interface.myInfo = MagicMock()
    mock_interface.myInfo.my_node_num = 12345678

    mock_module.serial_interface.SerialInterface.return_value = mock_interface
    mock_module.tcp_interface.TCPInterface.return_value = mock_interface

    with patch.dict('sys.modules', {
        'meshtastic': mock_module,
        'meshtastic.serial_interface': mock_module.serial_interface,
        'meshtastic.tcp_interface': mock_module.tcp_interface,
    }):
        yield mock_module


@pytest.fixture
def mock_rns():
    """Mock RNS module for tests that don't need real Reticulum."""
    mock_module = MagicMock()

    with patch.dict('sys.modules', {
        'RNS': mock_module,
    }):
        yield mock_module


@pytest.fixture
def no_network():
    """Block network access for isolated tests."""
    import socket
    original_socket = socket.socket

    def guarded_socket(*args, **kwargs):
        raise OSError("Network access blocked in test")

    with patch.object(socket, 'socket', guarded_socket):
        yield


# TUI Handler Test Infrastructure (FakeDialog, make_handler_context) lives
# in tests/handler_test_utils.py — the single source of truth. Don't
# re-add definitions here; they were duplicated previously and the copy
# in this file was unused (no test imported it from conftest). Re-adding
# definitions risks silent divergence the next time someone edits one
# and not the other.
