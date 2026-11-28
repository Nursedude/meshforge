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

    def test_r4_f3_map_drain_alt_port_never_notes(self):
        self._collector(4404)._own_node_id(
            SimpleNamespace(myInfo=SimpleNamespace(my_node_num=ID_A)))
        assert orad.own_node_status()["readings"] == []

    def test_map_drain_primary_port_notes(self):
        self._collector(4403)._own_node_id(
            SimpleNamespace(myInfo=SimpleNamespace(my_node_num=ID_A)))
        assert orad.own_node() == ID_A

    def test_r5_f1_map_drain_reconnect_shape_never_notes(self):
        self._collector(4403)._own_node_id(
            SimpleNamespace(myInfo=None, localNode=SimpleNamespace(nodeNum=ID_A)))
        assert orad.own_node_status()["readings"] == []


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
