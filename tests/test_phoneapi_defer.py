"""Tests for the #17 single-consumer defer in MeshtasticConnectionManager.is_available()
(src/utils/meshtastic_connection.py, added 2026-06-27 after the moc phoneapi-wedge dig).

The defect being closed: `is_available()` did a raw `socket.connect((host, 4403))`
probe. On a GATEWAY box, meshforge-gateway owns meshtasticd's :4403 PhoneAPI, which
is a SINGLE-consumer stream (#17). Any connect — even a connect-then-close
reachability probe — is accepted as a PhoneAPI client and force-closes the prior
("Force close previous TCP connection"). The map's radio-status path calls
is_available() (and get_mode() AUTO, which calls is_available()) ~every 15s, so on
moc this leaked PAST the collector's #17 defer (which only guards _collect_meshtasticd)
and produced sustained force-close churn that tripped probe_meshtasticd_phoneapi_wedge
(strace 2026-06-27: connect(htons(4403)) + immediate close, no PhoneAPI data, from
the map process; the gateway itself never touched :4403).

Fix: when meshforge-gateway owns the LOCAL :4403, is_available() must NOT open a
contending socket — answer from meshtasticd's service state (non-contending, and
honest: a down daemon still reads False, never masked — honest_failure_modes #2).
A non-gateway box (or a remote/non-4403 target) probes exactly as before.
"""

import socket
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from utils.meshtastic_connection import MeshtasticConnectionManager, ConnectionMode
from utils.service_check import ServiceState


def _svc(**states):
    """Build a check_service stub: name -> ServiceState (default NOT_INSTALLED)."""
    def _check(name, *a, **k):
        return MagicMock(state=states.get(name, ServiceState.NOT_INSTALLED))
    return _check


def _mgr(host="localhost", port=4403):
    return MeshtasticConnectionManager(host=host, port=port, mode=ConnectionMode.TCP)


class TestPhoneApiDeferIssue17:
    """is_available() honors the #17 gateway-defer instead of contending on :4403."""

    def test_gateway_owns_phoneapi_meshtasticd_up_returns_true_without_probing(self):
        mgr = _mgr()
        with patch("utils.meshtastic_connection.check_service",
                   _svc(**{"meshforge-gateway": ServiceState.AVAILABLE,
                           "meshtasticd": ServiceState.AVAILABLE})), \
             patch("utils.meshtastic_connection.socket.socket") as mock_sock:
            assert mgr.is_available() is True
            mock_sock.assert_not_called()      # NO contending :4403 connect

    def test_gateway_owns_phoneapi_meshtasticd_down_returns_false_without_probing(self):
        mgr = _mgr()
        with patch("utils.meshtastic_connection.check_service",
                   _svc(**{"meshforge-gateway": ServiceState.AVAILABLE,
                           "meshtasticd": ServiceState.NOT_INSTALLED})), \
             patch("utils.meshtastic_connection.socket.socket") as mock_sock:
            assert mgr.is_available() is False  # honest: daemon down ≠ available
            mock_sock.assert_not_called()

    def test_non_gateway_box_still_probes_socket(self):
        mgr = _mgr()
        with patch("utils.meshtastic_connection.check_service",
                   _svc(**{"meshforge-gateway": ServiceState.NOT_INSTALLED})), \
             patch("utils.meshtastic_connection.socket.socket") as mock_sock:
            # connect succeeds (no raise) -> available True via the real probe
            mock_sock.return_value.__enter__.return_value.connect.return_value = None
            assert mgr.is_available() is True
            mock_sock.assert_called_once()      # the probe DID run (unchanged path)

    def test_non_gateway_box_probe_failure_returns_false(self):
        mgr = _mgr()
        with patch("utils.meshtastic_connection.check_service",
                   _svc(**{"meshforge-gateway": ServiceState.NOT_INSTALLED})), \
             patch("utils.meshtastic_connection.socket.socket") as mock_sock:
            mock_sock.return_value.__enter__.return_value.connect.side_effect = \
                ConnectionRefusedError()
            assert mgr.is_available() is False
            mock_sock.assert_called_once()

    def test_remote_host_probes_even_if_local_gateway_active(self):
        # Local gateway ownership is irrelevant to a REMOTE meshtasticd — must still probe.
        mgr = _mgr(host="10.0.0.9", port=4403)
        with patch("utils.meshtastic_connection.check_service",
                   _svc(**{"meshforge-gateway": ServiceState.AVAILABLE,
                           "meshtasticd": ServiceState.AVAILABLE})), \
             patch("utils.meshtastic_connection.socket.socket") as mock_sock:
            mock_sock.return_value.__enter__.return_value.connect.return_value = None
            assert mgr.is_available() is True
            mock_sock.assert_called_once()

    def test_check_service_error_falls_through_to_probe(self):
        # Can't determine gateway ownership -> don't claim/skip; probe as before.
        mgr = _mgr()
        def _boom(*a, **k):
            raise RuntimeError("systemctl unavailable")
        with patch("utils.meshtastic_connection.check_service", _boom), \
             patch("utils.meshtastic_connection.socket.socket") as mock_sock:
            mock_sock.return_value.__enter__.return_value.connect.return_value = None
            assert mgr.is_available() is True
            mock_sock.assert_called_once()

    def test_resolve_mode_auto_on_gateway_box_resolves_tcp_without_probing(self):
        # AUTO resolution (_resolve_mode) calls is_available(); on a gateway box it
        # must resolve TCP from the service state, never the contending :4403 probe.
        mgr = MeshtasticConnectionManager(host="localhost", port=4403,
                                          mode=ConnectionMode.AUTO)
        with patch("utils.meshtastic_connection.check_service",
                   _svc(**{"meshforge-gateway": ServiceState.AVAILABLE,
                           "meshtasticd": ServiceState.AVAILABLE})), \
             patch("utils.meshtastic_connection.socket.socket") as mock_sock:
            assert mgr._resolve_mode() == ConnectionMode.TCP
            mock_sock.assert_not_called()
