"""Non-author adversarial review (2026-10-15) of 0e459bec..ffb33040 — the
":4403 contention" arc. Each test PASSES when the attack SUCCEEDS, i.e. when a
consumer reports a radio it never measured.

Only systemd is stubbed (the layer the arc chose to TRUST); the functions under
review run for real against a real, closed TCP port on 127.0.0.1.
"""

import socket
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from utils.service_check import ServiceState
import utils.meshtastic_connection as mc
from utils.meshtastic_connection import MeshtasticConnectionManager, ConnectionMode


def _closed_port() -> int:
    """A port nothing listens on: bind, read the number, close."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _svc(state: ServiceState, available: bool = None):
    if available is None:
        available = state == ServiceState.AVAILABLE
    return MagicMock(state=state, available=available)


def _check_service_stub(meshtasticd_state, available=None):
    def _check(name, *a, **k):
        if name == "meshtasticd":
            return _svc(meshtasticd_state, available)
        return _svc(ServiceState.NOT_RUNNING)
    return _check


def test_attack_active_unit_closed_port_reads_available():
    """ATTACK A1: unit `active`, :4403 not listening (Type=simple start window,
    crashloop between restarts, API port moved) -> is_available() == True."""
    port = _closed_port()
    with socket.socket() as s:
        s.settimeout(1.0)
        with pytest.raises(OSError):           # the port really refuses
            s.connect(("127.0.0.1", port))

    with patch.object(mc, "_MESHTASTICD_TCP_PORT", port), \
         patch.object(mc, "check_service", _check_service_stub(ServiceState.AVAILABLE)):
        mgr = MeshtasticConnectionManager(host="127.0.0.1", port=port, mode=ConnectionMode.TCP)
        assert mgr.is_available(timeout=1.0) is True   # the lie


def test_attack_degraded_placeholder_reads_available():
    """ATTACK A2: _service_is_up folds DEGRADED into up. check_service returns
    DEGRADED with available=False for the stale-placeholder / SPI-mismatch
    meshtasticd (active+exited, nothing on :4403) -> is_available() == True."""
    port = _closed_port()
    with patch.object(mc, "_MESHTASTICD_TCP_PORT", port), \
         patch.object(mc, "check_service",
                      _check_service_stub(ServiceState.DEGRADED, available=False)):
        mgr = MeshtasticConnectionManager(host="localhost", port=port, mode=ConnectionMode.TCP)
        assert mgr.is_available(timeout=1.0) is True   # available=False became True


def test_attack_api_radio_status_reports_connected_on_closed_port():
    """ATTACK A3: GET /api/radio/status -> {"connected": true} while the port
    refuses — the handler's real _serve_radio_status over the real manager."""
    from utils.map_http_handler import MapRequestHandler
    import utils._map_radio_endpoints as ep

    port = _closed_port()
    served = {}
    h = MapRequestHandler.__new__(MapRequestHandler)
    h._serve_json = lambda obj, status=200, **k: served.update(obj=obj, status=status)

    with patch.object(mc, "_MESHTASTICD_TCP_PORT", port), \
         patch.object(mc, "check_service", _check_service_stub(ServiceState.AVAILABLE)):
        mgr = MeshtasticConnectionManager(host="127.0.0.1", port=port, mode=ConnectionMode.TCP)
        with patch.object(ep, "_HAS_MESHTASTIC_CONN", True), \
             patch.object(ep, "_get_connection_manager", return_value=mgr):
            h._serve_radio_status()

    assert served["status"] == 200
    assert served["obj"]["connected"] is True
    assert served["obj"]["port"] == port


def test_attack_fleet_slo_radio_connected_without_measurement():
    """ATTACK A4: /fleet/slo.radio.connected is True from `systemctl is-active`
    alone; any socket measurement would have raised. fleet_truth then renders
    the radio cell HEALTHY."""
    import utils.fleet_snapshot as fs
    from utils.fleet_truth import _radio_cell, cell, HEALTHY

    def fake_run(argv, **k):
        unit = argv[-1]
        out = "active\n" if unit == "meshtasticd" else "inactive\n"
        return subprocess.CompletedProcess(argv, 0 if unit == "meshtasticd" else 3, out, "")

    with fs._systemctl_state_cache_lock:
        fs._systemctl_state_cache.clear()
    with patch.object(fs.subprocess, "run", side_effect=fake_run), \
         patch.object(fs.socket, "socket", side_effect=OSError("no measurement allowed")):
        radio = fs._probe_radio()
    with fs._systemctl_state_cache_lock:
        fs._systemctl_state_cache.clear()

    assert radio == {"connected": True, "name": "meshtasticd", "preset": None, "battery_pct": None}
    assert _radio_cell({"radio": radio}) == cell(HEALTHY, source="/fleet/slo.radio")


@pytest.fixture
def collector(tmp_path):
    from utils.map_data_collector import MapDataCollector
    return MapDataCollector(cache_dir=tmp_path, config_dir=tmp_path)


def test_attack_collector_live_unit_closed_port_runs_cli_fallback(collector):
    """ATTACK A5: the collector's `unreachable` fast-exit is gone for a live unit
    with a closed port. The pre-check passes, TCP yields nothing, and the
    `meshtastic --host ... --info` CLI fallback runs (15 s bound) while the
    caller holds _collect_lock. No `unreachable` diagnostic is recorded."""
    import utils._map_collector_meshtastic as cm

    port = _closed_port()
    calls = []

    def fake_run(argv, **k):
        calls.append((argv, k))
        return subprocess.CompletedProcess(argv, 0, "", "")

    with patch.object(mc, "_MESHTASTICD_TCP_PORT", port), \
         patch.object(mc, "check_service", _check_service_stub(ServiceState.AVAILABLE)), \
         patch.object(collector, "get_meshtasticd_host", return_value="127.0.0.1"), \
         patch.object(collector, "get_meshtasticd_port", return_value=port), \
         patch.object(collector, "_collect_via_http", return_value=[]), \
         patch.object(collector, "_gateway_owns_phoneapi", return_value=False), \
         patch.object(collector, "_collect_interface_bounded", return_value=([], 0, False)), \
         patch("utils.cli.find_meshtastic_cli", return_value="/usr/bin/meshtastic"), \
         patch.object(cm.subprocess, "run", side_effect=fake_run):
        features = collector._collect_meshtasticd()

    assert features == []
    assert len(calls) == 1, "CLI fallback ran against a port that refuses"
    argv, kw = calls[0]
    assert "--info" in argv and kw.get("timeout") == 15
    diags = getattr(collector, "_source_diagnostics", {}) or {}
    reasons = [d.get("reason_if_zero") for d in diags.values()] if isinstance(diags, dict) else []
    assert "unreachable" not in reasons
