"""BIC-2 slice 2, "measure the id instead" (operator 2026-11-27), and
"drop the gateway file" (operator 2026-11-28, after Fable r4 + r5).

Measured 2026-11-28: on every map box but one the map's TCP drain reads the
radio's own id every collect cycle (meshtasticd serves no /json/*, Issue #76).
Where the gateway owns :4403 the map defers (#17) and there is NO own-id
reading -- a cross-process file failed review twice (a dead or reconnected
link kept vouching; wall-clock age survived reboots). Those boxes keep the
header-only rule. Each test below was a reviewer drill.
"""
import inspect
import threading
from types import SimpleNamespace

import pytest

from utils import own_radio as orad

ID_A, ID_B = 0x1234ABCD, 0x0BADF00D
NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _clean():
    orad._reset()
    yield
    orad._reset()


def _cfg(monkeypatch, num):
    monkeypatch.setattr(orad, "_read_config", lambda: {
        "state": "ok", "num": num, "why": "", "raw": f"!{num:08x}"})


class TestNumFromInterface:
    def test_my_info_is_the_reading(self):
        iface = SimpleNamespace(myInfo=SimpleNamespace(my_node_num=ID_A),
                                localNode=SimpleNamespace(nodeNum=ID_B))
        assert orad.num_from_interface(iface) == (ID_A, "my_node_num")

    def test_r5_f1_reconnect_shape_is_no_reading(self):
        """meshtastic 2.7.11 ``_reconnect()``: isConnected stays SET, myInfo
        = None, localNode.nodeNum = the OLD session's id. No reading."""
        ev = threading.Event()
        ev.set()
        iface = SimpleNamespace(isConnected=ev, myInfo=None,
                                localNode=SimpleNamespace(nodeNum=ID_A))
        assert orad.num_from_interface(iface) == (None, "")

    @pytest.mark.parametrize("v", [0, True, "5", -3, 0xFFFFFFFF, None, 1.5])
    def test_never_a_node(self, v):
        assert orad.num_from_interface(
            SimpleNamespace(myInfo=SimpleNamespace(my_node_num=v))) == (None, "")

    def test_none_and_hostile_interfaces(self):
        class Boom:
            @property
            def myInfo(self):
                raise RuntimeError("torn down")
        assert orad.num_from_interface(None) == (None, "")
        assert orad.num_from_interface(Boom()) == (None, "")


class TestThisBoxRadio:
    @pytest.mark.parametrize("host,port,ok", [
        ("localhost", 4403, True), ("127.0.0.1", "4403", True), ("::1", 4403, True),
        ("localhost", 4404, False), ("10.0.0.2", 4403, False), ("localhost", None, False),
        ("localhost", "x", False)])
    def test_one_rule(self, host, port, ok):
        assert orad.is_this_box_radio(host, port) is ok

    def _collector(self, port):
        from utils._map_collector_meshtastic import MeshtasticDataCollectorMixin as M
        return type("X", (M,), {"get_meshtasticd_host": lambda s: "localhost",
                                "get_meshtasticd_port": lambda s: port})()

    @staticmethod
    def _tcp(host="localhost", port=4403, num=ID_A, **kw):
        return SimpleNamespace(myInfo=SimpleNamespace(my_node_num=num),
                               hostname=host, portNumber=port, **kw)

    def test_r4_f3_map_drain_alt_port_never_notes(self):
        self._collector(4403)._own_node_id(self._tcp(port=4404))
        assert orad.own_node_status()["readings"] == []

    def test_map_drain_primary_port_notes(self):
        self._collector(4403)._own_node_id(self._tcp())
        assert orad.own_node() == ID_A

    def test_r5_f1_map_drain_reconnect_shape_never_notes(self):
        self._collector(4403)._own_node_id(SimpleNamespace(
            myInfo=None, localNode=SimpleNamespace(nodeNum=ID_A),
            hostname="localhost", portNumber=4403))
        assert orad.own_node_status()["readings"] == []

    # slice-2 r6 F1: the settings said localhost:4403, the interface was not
    def test_r6_f1_serial_drain_never_notes(self):
        serial = SimpleNamespace(myInfo=SimpleNamespace(my_node_num=ID_A),
                                 devPath="/dev/ttyACM0")
        assert self._collector(4403)._own_node_id(serial) == f"!{ID_A:08x}"   # tagging ok
        assert orad.own_node_status()["readings"] == []

    def test_r6_f1_serial_with_tcp_lookalike_attrs_never_notes(self):
        self._collector(4403)._own_node_id(self._tcp(devPath="/dev/ttyUSB0"))
        assert orad.own_node_status()["readings"] == []

    def test_r6_f1_foreign_host_behind_local_settings_never_notes(self):
        self._collector(4403)._own_node_id(self._tcp(host="remote-box.example"))
        assert orad.own_node_status()["readings"] == []

    def test_r6_f1_interface_without_wire_facts_never_notes(self):
        self._collector(4403)._own_node_id(
            SimpleNamespace(myInfo=SimpleNamespace(my_node_num=ID_A)))
        assert orad.own_node_status()["readings"] == []

    def test_r6_f1_real_library_classes_if_installed(self):
        tcp = pytest.importorskip("meshtastic.tcp_interface")
        ser = pytest.importorskip("meshtastic.serial_interface")
        t = tcp.TCPInterface.__new__(tcp.TCPInterface)
        t.hostname, t.portNumber = "localhost", 4403
        assert orad.interface_is_this_box_radio(t)
        s_ = ser.SerialInterface.__new__(ser.SerialInterface)
        s_.devPath = "/dev/ttyACM0"
        assert not orad.interface_is_this_box_radio(s_)


class TestFutureReading:
    def test_r4_f7_future_reading_is_not_the_id_and_is_said(self):
        orad.note_own_node(ID_A, source="map collector connection", now=NOW + 7200)
        st = orad.own_node_status(now=NOW)
        assert st["num"] is None and "future" in st["note"]
        assert orad.fresh_own_node(NOW) is None
        assert "future" in orad.resolve_own(NOW)["note"]

    def test_future_disagreeing_reading_still_vetoes_config(self, monkeypatch):
        _cfg(monkeypatch, ID_B)
        orad.note_own_node(ID_A, source="map collector connection", now=NOW + 7200)
        own = orad.resolve_own(NOW)
        assert own["num"] is None and own["self_only"] is None and own["config"] == "mismatch"

    def test_slack_inside_60s_is_a_reading(self):
        orad.note_own_node(ID_A, source="c", now=NOW + 30)
        assert orad.own_node_status(now=NOW)["num"] == ID_A


class TestReviewR6:
    def test_f2_backward_wall_step_cannot_freshen_a_real_read(self, monkeypatch):
        """A real read (now=None) carries a monotonic anchor; after a 4.5 h
        backward wall step its true 5 h age still refuses."""
        import time as _t
        mono0, wall0 = 1000.0, NOW
        monkeypatch.setattr(_t, "monotonic", lambda: mono0)
        monkeypatch.setattr(_t, "time", lambda: wall0)
        orad.note_own_node(ID_A, source="c")
        monkeypatch.setattr(_t, "monotonic", lambda: mono0 + 5 * 3600)
        monkeypatch.setattr(_t, "time", lambda: wall0 + 30 * 60)   # 5 h - 4.5 h step
        assert orad.fresh_own_node() is None
        monkeypatch.setattr(_t, "monotonic", lambda: mono0 + 60)
        monkeypatch.setattr(_t, "time", lambda: wall0 + 60)
        assert orad.fresh_own_node() == ID_A                          # control

    @pytest.mark.parametrize("pid", [{}, [], set(), True, None, 1.5])
    def test_f5_hostile_packet_ids_never_raise(self, monkeypatch, pid):
        _cfg(monkeypatch, ID_A)
        orad.observe_own_headers([(pid, ID_B)], now=NOW)
        assert orad.resolve_own(NOW)["trust"] != orad.TRUST_CONTRADICTED

    def test_f3_ensure_section_keeps_a_shared_files_mode(self, tmp_path):
        from utils.global_config import ensure_own_node_id_section
        p = tmp_path / "global.ini"
        p.write_text("[mqtt]\nbroker = x\n")
        p.chmod(0o644)
        ensure_own_node_id_section(p)
        assert (p.stat().st_mode & 0o777) == 0o644
        assert "[meshtastic]" in p.read_text()


class TestNoCrossProcessReading:
    def test_the_gateway_publishes_nothing(self):
        from gateway import meshtastic_handler
        src = inspect.getsource(meshtastic_handler)
        assert "own_radio" not in src and "_publish_own_id" not in src

    def test_the_registry_reads_no_file(self):
        src = inspect.getsource(orad.own_node_status)
        for word in ("open(", "read_text", "json", "peer"):
            assert word not in src



class TestMapTaggingKeepsItsFallback:
    """Full suite 11-28: myInfo-only must not leak into the map's own-row
    tagging (MAP-H H14 r3 F6) -- that consumer keeps localNode.nodeNum."""

    def _collector(self):
        from utils._map_collector_meshtastic import MeshtasticDataCollectorMixin as M
        return type("X", (M,), {"get_meshtasticd_host": lambda s: "localhost",
                                "get_meshtasticd_port": lambda s: 4403})()

    def test_local_node_tags_but_never_notes(self):
        got = self._collector()._own_node_id(
            SimpleNamespace(myInfo=None, localNode=SimpleNamespace(nodeNum=ID_A)))
        assert got == f"!{ID_A:08x}"
        assert orad.own_node_status()["readings"] == []

    def test_nothing_at_all_is_none(self):
        assert self._collector()._own_node_id(SimpleNamespace(myInfo=None, localNode=None)) is None
