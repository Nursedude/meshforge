"""Issue #75 root-cause fix (2026-06-19): the /api/status radio summary must
NOT open a TCP connection to meshtasticd's PhoneAPI (:4403).

A bare ``connect_ex(('localhost', 4403))`` is itself a PhoneAPI *client*
connect — :4403 is single-consumer, so the probe seizes the stream and makes
meshtasticd ``Force close previous TCP connection``. /api/status is polled on a
hot path (federation cross-poll + node_map UI auto-refresh), so that churn
intermittently wedged moc's gateway mesh-TX (the 2026-06-13→15 bot-dark
incident). Root-caused on 2026-06-19 to ``_get_radio_status_summary`` via gdb
(map pid = the :4403 connector) + py-spy (sample landed on the connect_ex line)
+ request-rate correlation (~18 /api/status ≈ ~20 force-closes per 10 min).

The cure: report ``tcp_available`` from a /proc LISTEN check that never opens a
connection. These tests pin both the helper and the no-connect invariant so the
contending probe can't be reintroduced.
"""

from unittest.mock import patch

from utils._port_detection import tcp_port_listening
from utils.map_http_handler import MapRequestHandler
import utils._map_status_endpoints as mse


def _handler():
    """A bare handler instance — enough state to call _get_radio_status_summary."""
    return MapRequestHandler.__new__(MapRequestHandler)


class TestTcpPortListening:
    """The non-contending /proc-table LISTEN check (4403 == 0x1133)."""

    def test_detects_listen_on_port(self, tmp_path):
        p = tmp_path / "tcp"
        p.write_text(
            "  sl  local_address rem_address   st tx_queue rx_queue\n"
            "   0: 00000000:1133 00000000:0000 0A 00000000:00000000 00:0 0 0 1 0\n"
        )
        assert tcp_port_listening(4403, proc_paths=(str(p),)) is True

    def test_ignores_established_same_port(self, tmp_path):
        # Port 4403 present but ESTABLISHED (st 01), not LISTEN — must be False:
        # an established peer is a consumer, not proof the port is being served.
        p = tmp_path / "tcp"
        p.write_text(
            "  sl  local_address rem_address   st\n"
            "   0: 0100007F:1133 0100007F:ABCD 01 x\n"
        )
        assert tcp_port_listening(4403, proc_paths=(str(p),)) is False

    def test_ignores_other_listen_ports(self, tmp_path):
        p = tmp_path / "tcp"
        p.write_text(
            "  sl  local_address rem_address   st\n"
            "   0: 00000000:0050 00000000:0000 0A x\n"  # :80 LISTEN, not 4403
        )
        assert tcp_port_listening(4403, proc_paths=(str(p),)) is False

    def test_missing_file_returns_false(self, tmp_path):
        assert tcp_port_listening(4403, proc_paths=(str(tmp_path / "nope"),)) is False

    def test_checks_both_v4_and_v6(self, tmp_path):
        v4 = tmp_path / "tcp"
        v4.write_text("  sl  local_address rem_address   st\n")  # nothing on v4
        v6 = tmp_path / "tcp6"
        v6.write_text(
            "  sl  local_address rem_address   st\n"
            "   0: 00000000000000000000000000000000:1133 "
            "00000000000000000000000000000000:0000 0A x\n"
        )
        assert tcp_port_listening(4403, proc_paths=(str(v4), str(v6))) is True


class TestRadioStatusSummaryNoContentionIssue75:
    """_get_radio_status_summary must observe :4403 without connecting to it."""

    def test_uses_listen_check_and_never_opens_a_socket(self):
        h = _handler()
        with patch.object(mse, "_HAS_MESHTASTIC_CONN", True), \
                patch.object(mse, "tcp_port_listening", return_value=True) as m_listen, \
                patch("socket.socket") as m_sock:
            summary = h._get_radio_status_summary()

        assert summary["tcp_available"] is True
        assert summary["mode"] == "tcp"
        assert summary["connected"] is True
        m_listen.assert_called_once_with(4403)
        # THE #17/#75 INVARIANT: a status read opens no socket — a connect to
        # :4403 IS a PhoneAPI client connect that force-closes the real consumer.
        m_sock.assert_not_called()

    def test_none_when_not_listening_and_no_usb(self):
        h = _handler()
        with patch.object(mse, "_HAS_MESHTASTIC_CONN", True), \
                patch.object(mse, "tcp_port_listening", return_value=False), \
                patch("glob.glob", return_value=[]), \
                patch("socket.socket") as m_sock:
            summary = h._get_radio_status_summary()

        assert summary["tcp_available"] is False
        assert summary["mode"] == "none"
        assert summary["connected"] is False
        m_sock.assert_not_called()

    def test_serial_when_usb_present_but_no_tcp(self):
        h = _handler()
        with patch.object(mse, "_HAS_MESHTASTIC_CONN", True), \
                patch.object(mse, "tcp_port_listening", return_value=False), \
                patch("glob.glob", return_value=["/dev/ttyACM0"]), \
                patch("socket.socket") as m_sock:
            summary = h._get_radio_status_summary()

        assert summary["mode"] == "serial"
        assert summary["connected"] is True
        m_sock.assert_not_called()
