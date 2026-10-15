"""Non-author pass on the :4403 arc (2026-10-15, drill
scripts/radio_4403_assumption_drill.py): "meshtasticd unit active" became the
answer to "is the radio reachable?" in is_available() and _probe_radio().
meshtasticd is Type=simple — `active` precedes the listener (251 ms measured;
forever when the API port moved; every restart of a crashlooping SPI unit), and
a DEGRADED placeholder (active+exited, nothing serving) was folded into up.
The non-contending answer is the kernel's LISTEN table, which /api/status
already reads (#17/#75): measure, never assume, and still never connect."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import utils.fleet_snapshot as fs  # noqa: E402
import utils.meshtastic_connection as mc  # noqa: E402
from utils.meshtastic_connection import ConnectionMode, MeshtasticConnectionManager  # noqa: E402
from utils.service_check import ServiceState  # noqa: E402


def _svc(gateway_up, mtd_state):
    def check(name, *a, **k):
        if name == "meshforge-gateway":
            return MagicMock(state=ServiceState.AVAILABLE if gateway_up else ServiceState.NOT_RUNNING)
        return MagicMock(state=mtd_state)
    return check


def _no_connect(*a, **k):
    raise AssertionError("a connect() to :4403 is a PhoneAPI contender (#17)")


def _avail(gateway_up, mtd_state, listening):
    with patch.object(mc, "check_service", _svc(gateway_up, mtd_state)), \
         patch.object(mc, "tcp_port_listening", lambda port, **k: listening), \
         patch.object(mc.socket, "socket", _no_connect):
        return MeshtasticConnectionManager(host="localhost", port=4403,
                                           mode=ConnectionMode.TCP).is_available(timeout=0.5)


def test_an_active_unit_that_is_not_listening_is_not_available():
    assert _avail(False, ServiceState.AVAILABLE, listening=False) is False
    assert _avail(True, ServiceState.AVAILABLE, listening=False) is False


def test_a_degraded_placeholder_is_not_available():
    assert _avail(False, ServiceState.DEGRADED, listening=False) is False
    assert _avail(True, ServiceState.DEGRADED, listening=False) is False


def test_an_active_listening_unit_is_available_without_a_connect():
    assert _avail(False, ServiceState.AVAILABLE, listening=True) is True
    assert _avail(True, ServiceState.AVAILABLE, listening=True) is True


def _probe(gateway, mtd, listening):
    states = {"meshforge-gateway": gateway, "meshtasticd": mtd}
    with patch.object(fs, "_systemctl_state", lambda u: states.get(u, "not_running")), \
         patch.object(fs, "tcp_port_listening", lambda port, **k: listening), \
         patch.object(fs.socket, "socket", _no_connect):
        return fs._probe_radio()["connected"]


def test_fleet_slo_radio_reads_the_listener_not_the_unit():
    assert _probe("not_running", "available", listening=False) is False
    assert _probe("available", "available", listening=False) is False
    assert _probe("not_running", "available", listening=True) is True
    assert _probe("available", "available", listening=True) is True
