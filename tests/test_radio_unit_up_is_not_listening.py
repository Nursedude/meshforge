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


# ── non-author read of d6ef3ecc (2026-10-15, Fable 5.1) ──────────────────

def _proc(tmp_path, *rows, v6=()):
    hdr = "  sl  local_address rem_address   st\n"
    t4, t6 = tmp_path / "tcp", tmp_path / "tcp6"
    t4.write_text(hdr + "".join(f"   0: {a}:1133 00000000:0000 0A\n" for a in rows))
    t6.write_text(hdr + "".join(f"   0: {a}:1133 00000000000000000000000000000000:0000 0A\n" for a in v6))
    return (str(t4), str(t6))


def test_a_lan_only_listener_is_not_reachable_on_loopback(tmp_path):
    from utils._port_detection import tcp_port_listening
    lan = _proc(tmp_path, "0A01A8C0")                       # 192.168.1.10 only
    assert tcp_port_listening(4403, proc_paths=lan) is True  # something listens…
    assert tcp_port_listening(4403, proc_paths=lan, loopback_reachable=True) is False


def test_loopback_reachable_binds_are_accepted(tmp_path):
    from utils._port_detection import tcp_port_listening
    for rows, v6 in ((("00000000",), ()), (("0100007F",), ()),
                     ((), ("00000000000000000000000000000000",)),
                     ((), ("00000000000000000000000001000000",)),
                     ((), ("0000000000000000FFFF00000100007F",))):
        assert tcp_port_listening(4403, proc_paths=_proc(tmp_path, *rows, v6=v6),
                                  loopback_reachable=True) is True, (rows, v6)


def test_the_gateway_branch_still_needs_the_meshtasticd_unit():
    # both radio consumers agree: unit up AND a loopback-reachable listener
    assert _avail(True, ServiceState.NOT_RUNNING, listening=True) is False
