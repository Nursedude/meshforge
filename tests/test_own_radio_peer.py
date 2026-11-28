"""BIC-2 slice 2, "measure the id instead" (operator 2026-11-27).

Measured 2026-11-28: on every map box but one the map's TCP drain reads the
radio's own id every collect cycle (meshtasticd serves no /json/*, Issue #76,
so HTTP always yields nothing). The exception is a box whose gateway owns
:4403 (moc): the map defers (#17) and never reads it. There the GATEWAY's
connection is the measurement -- it publishes the id to a file, the map's
registry reads it as a second connection reading. Newest valid reading wins.
"""
import json
import os
import threading

import pytest

from utils import own_radio as orad


@pytest.fixture
def peer(tmp_path, monkeypatch):
    p = tmp_path / "own_radio_gateway.json"
    monkeypatch.setattr(orad, "peer_path", lambda: p)
    orad._reset()
    yield p
    orad._reset()


def _write(p, **doc):
    p.write_text(json.dumps(doc))


ID_A, ID_B = 0x1234ABCD, 0x0BADF00D
NOW = 1_800_000_000.0


class TestPublish:
    def test_round_trip(self, peer):
        assert orad.publish_own_node(ID_A, source="gateway connection (my_node_num)", now=NOW)
        st = orad.own_node_status(now=NOW + 5)
        assert st["num"] == ID_A and st["ts"] == NOW
        assert "gateway" in st["source"]
        assert orad.fresh_own_node(NOW + 5) == ID_A

    @pytest.mark.parametrize("bad", [0, 0xFFFFFFFF, -1, True, "123", None, 1.5])
    def test_never_a_node_is_not_published(self, peer, bad):
        assert orad.publish_own_node(bad, source="x", now=NOW) is False
        assert not peer.exists()

    def test_write_error_never_raises(self, peer, monkeypatch):
        def boom(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr("utils.paths.atomic_write_text", boom)
        assert orad.publish_own_node(ID_A, source="x", now=NOW) is False


class TestReadPeer:
    def test_absent_file_is_no_reading(self, peer):
        assert orad.own_node_status(now=NOW)["num"] is None
        assert orad.own_node_status(now=NOW).get("peer_error", "") == ""

    @pytest.mark.parametrize("doc", [
        "not json", "[]", '{"num": "x", "ts": 1}', '{"num": 5}',
        '{"num": 0, "ts": 1800000000}', '{"num": 5, "ts": "soon"}',
        '{"num": 5, "ts": true}',
    ])
    def test_garbage_is_no_reading_and_is_said(self, peer, doc):
        peer.write_text(doc)
        st = orad.own_node_status(now=NOW)
        assert st["num"] is None
        assert st["peer_error"]            # hfm #9: the swallow has a witness

    def test_future_stamp_is_refused(self, peer):
        _write(peer, num=ID_A, source="gw", ts=NOW + 600)
        st = orad.own_node_status(now=NOW)
        assert st["num"] is None and "future" in st["peer_error"]

    def test_stale_file_is_no_reading_and_says_its_age(self, peer):
        """r4 F5: a dead gateway's file never vouches -- not even as the
        witness's last-known id or as a config veto."""
        _write(peer, num=ID_A, source="gw", ts=NOW - orad.OWN_FRESH_S - 1)
        st = orad.own_node_status(now=NOW)
        assert st["num"] is None and st["readings"] == []
        assert "h old" in st["peer_error"]
        assert orad.fresh_own_node(NOW) is None


class TestNewestWins:
    def test_in_process_newer_than_file_same_id(self, peer):
        _write(peer, num=ID_A, source="gw", ts=NOW - 100)
        orad.note_own_node(ID_A, source="map collector connection", now=NOW - 10)
        st = orad.own_node_status(now=NOW)
        assert st["num"] == ID_A and "map collector" in st["source"] and st["note"] == ""

    def test_file_newer_than_in_process_same_id(self, peer):
        orad.note_own_node(ID_A, source="map collector connection", now=NOW - 100)
        _write(peer, num=ID_A, source="gw", ts=NOW - 10)
        st = orad.own_node_status(now=NOW)
        assert st["num"] == ID_A and st["ts"] == NOW - 10 and st["note"] == ""
        assert orad.fresh_own_node(NOW) == ID_A

    def test_superseded_disagreement_is_said(self, peer):
        """An older rival PAST the window is superseded (a swap), said."""
        orad.note_own_node(ID_B, source="map collector connection",
                           now=NOW - orad.OWN_FRESH_S - 50)
        _write(peer, num=ID_A, source="gw", ts=NOW - 10)
        st = orad.own_node_status(now=NOW)
        assert st["num"] == ID_A and "superseded" in st["note"]


class TestResolveOwnUsesPeer:
    def test_gateway_reading_is_connection_authority(self, peer):
        _write(peer, num=ID_A, source="gateway connection (my_node_num)", ts=NOW - 30)
        own = orad.resolve_own(NOW)
        assert own["num"] == ID_A and "gateway" in own["source"]

    def test_gateway_reading_vetoes_mismatched_config(self, peer, monkeypatch):
        monkeypatch.setattr(orad, "_read_config",
                            lambda: {"state": "ok", "num": ID_B, "why": "", "raw": "!0badf00d"})
        _write(peer, num=ID_A, source="gw", ts=NOW - 30)
        own = orad.resolve_own(NOW)
        assert own["num"] is None and own["config"] == "mismatch"

    def test_peer_error_surfaces_in_note(self, peer):
        peer.write_text("garbage")
        assert "unreadable" in orad.resolve_own(NOW)["note"]


class _Iface:
    def __init__(self, my=None, local=None, live=True):
        self.myInfo = type("M", (), {"my_node_num": my})() if my is not None else None
        self.localNode = type("L", (), {"nodeNum": local})() if local is not None else None
        self.isConnected = threading.Event()
        if live:
            self.isConnected.set()


class TestNumFromInterface:
    """ONE reader of an interface's own id (hfm #5): map drain + gateway."""

    def test_my_info_first(self):
        assert orad.num_from_interface(_Iface(my=ID_A, local=ID_B)) == (ID_A, "my_node_num")

    def test_local_node_fallback(self):
        assert orad.num_from_interface(_Iface(local=ID_B)) == (ID_B, "nodeNum")

    @pytest.mark.parametrize("v", [0, True, "5", -3])
    def test_never_a_node(self, v):
        assert orad.num_from_interface(_Iface(my=v)) == (None, "")

    def test_none_interface(self):
        assert orad.num_from_interface(None) == (None, "")


class TestGatewayPublishes:
    def _handler(self, host, iface, port=4403):
        from gateway.meshtastic_handler import MeshtasticHandler
        h = MeshtasticHandler.__new__(MeshtasticHandler)
        h.config = type("C", (), {"meshtastic": type("M", (), {"host": host, "port": port})()})()
        h._interface = iface
        h._own_pub_mono = None
        return h

    def test_localhost_publishes(self, peer):
        h = self._handler("localhost", _Iface(my=ID_A))
        h._publish_own_id(mono=100.0, now=NOW)
        assert json.loads(peer.read_text())["num"] == ID_A

    def test_remote_host_never_publishes(self, peer):
        h = self._handler("10.0.0.9", _Iface(my=ID_A))
        h._publish_own_id(mono=100.0, now=NOW)
        assert not peer.exists()

    def test_rate_limited(self, peer):
        h = self._handler("127.0.0.1", _Iface(my=ID_A))
        h._publish_own_id(mono=100.0, now=NOW)
        peer.unlink()
        h._publish_own_id(mono=100.0 + orad.PEER_PUBLISH_S - 1, now=NOW + 1)
        assert not peer.exists()
        h._publish_own_id(mono=100.0 + orad.PEER_PUBLISH_S, now=NOW + 2)
        assert peer.exists()

    def test_never_raises(self, peer):
        h = self._handler("localhost", object())
        h._publish_own_id(mono=1.0, now=NOW)   # no myInfo at all
        assert not peer.exists()


class TestReviewR4:
    """Fable r4 (11-28), each drill made a test (red on da1e1109)."""

    def _cfg(self, monkeypatch, num):
        monkeypatch.setattr(orad, "_read_config", lambda: {
            "state": "ok", "num": num, "why": "", "raw": f"!{num:08x}"})

    def test_f1_dead_link_never_publishes(self, peer):
        h = TestGatewayPublishes()._handler("localhost", _Iface(my=ID_A, live=False))
        h._publish_own_id(mono=1.0, now=NOW)
        assert not peer.exists()

    def test_f1_non_event_is_not_live(self, peer):
        iface = _Iface(my=ID_A)
        iface.isConnected = True           # a truthy non-Event proves nothing
        h = TestGatewayPublishes()._handler("localhost", iface)
        h._publish_own_id(mono=1.0, now=NOW)
        assert not peer.exists()

    def test_f2_live_disagreement_refuses(self, peer):
        orad.note_own_node(ID_B, source="map collector connection", now=NOW - 60)
        _write(peer, num=ID_A, source="gw", ts=NOW - 5)
        own = orad.resolve_own(NOW)
        assert own["num"] is None and "disagree" in own["note"]
        assert orad.fresh_own_node(NOW) is None

    def test_f2_config_checked_against_every_reading(self, peer, monkeypatch):
        self._cfg(monkeypatch, ID_A)
        orad.note_own_node(ID_B, source="map collector connection", now=NOW - 60)
        _write(peer, num=ID_A, source="gw", ts=NOW - 5)
        own = orad.resolve_own(NOW)
        assert own["num"] is None and own["config"] == "mismatch"

    def test_f2_send_stamp_end_to_end(self, peer):
        """The drill's end: a DM to B while the readings disagree is never
        stamped with A (which would rule the self-DM out)."""
        from utils.inbox_api import _send_stamp
        orad.note_own_node(ID_B, source="map collector connection", now=NOW - 60)
        _write(peer, num=ID_A, source="gw", ts=NOW - 5)
        assert _send_stamp(orad.resolve_own(NOW), ID_B) is None

    def test_f3_alt_port_never_publishes(self, peer):
        h = TestGatewayPublishes()._handler("localhost", _Iface(my=ID_A), port=4404)
        h._publish_own_id(mono=1.0, now=NOW)
        assert not peer.exists()

    @pytest.mark.parametrize("host,port,ok", [
        ("localhost", 4403, True), ("127.0.0.1", "4403", True), ("::1", 4403, True),
        ("localhost", 4404, False), ("10.0.0.2", 4403, False), ("localhost", None, False),
        ("localhost", "x", False)])
    def test_f3_one_rule(self, host, port, ok):
        assert orad.is_this_box_radio(host, port) is ok

    def test_f3_map_drain_alt_port_never_notes(self):
        from utils._map_collector_meshtastic import MeshtasticDataCollectorMixin as M
        obj = type("X", (M,), {"get_meshtasticd_host": lambda s: "localhost",
                               "get_meshtasticd_port": lambda s: 4404})()
        orad._reset()
        obj._own_node_id(_Iface(my=ID_A))
        assert orad.own_node_status()["readings"] == []

    @pytest.mark.parametrize("body", [
        "[" * 200_000, '{"num": 5, "ts": -' + "9" * 400 + "}",
        '{"num": 5, "ts": NaN}', '{"num": 5, "ts": -Infinity}', '{"num": 5, "ts": Infinity}'])
    def test_f4_never_raises_and_is_said(self, peer, body):
        peer.write_text(body)
        st = orad.own_node_status(now=NOW)
        assert st["num"] is None and st["peer_error"]
        assert "unreadable" in orad.resolve_own(NOW)["note"]

    def test_f5_stale_file_never_vetoes_good_config(self, peer, monkeypatch):
        self._cfg(monkeypatch, ID_B)
        _write(peer, num=ID_A, source="gw", ts=NOW - 90 * 86400)
        own = orad.resolve_own(NOW)
        assert own["config"] == "ok" and own["self_only"] == ID_B

    def test_f6_failed_publish_logged_and_spaced(self, peer, monkeypatch, caplog):
        calls = []
        monkeypatch.setattr(orad, "publish_own_node",
                            lambda *a, **k: calls.append(1) or False)
        h = TestGatewayPublishes()._handler("localhost", _Iface(my=ID_A))
        with caplog.at_level("WARNING"):
            h._publish_own_id(mono=1.0, now=NOW)
            h._publish_own_id(mono=2.0, now=NOW + 1)       # within the interval
            h._publish_own_id(mono=1.0 + orad.PEER_PUBLISH_S, now=NOW + 2)
        assert len(calls) == 2
        assert sum("publish failed" in r.getMessage() for r in caplog.records) == 1

    def test_f7_future_in_process_reading_refused_and_said(self, peer):
        orad.note_own_node(ID_A, source="map collector connection", now=NOW + 7200)
        _write(peer, num=ID_A, source="gw", ts=NOW - 5)
        st = orad.own_node_status(now=NOW)
        assert st["num"] == ID_A and st["ts"] == NOW - 5
        assert "future" in st["note"]


def test_conftest_pins_the_peer_file():
    """No test reads THIS box's real gateway file (ambient state)."""
    p = orad.peer_path()
    assert "pytest" in str(p) or not str(p).startswith(os.path.expanduser("~/.local"))
