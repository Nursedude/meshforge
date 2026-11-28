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

    def test_stale_reading_is_known_but_not_fresh(self, peer):
        _write(peer, num=ID_A, source="gw", ts=NOW - orad.OWN_FRESH_S - 1)
        assert orad.own_node_status(now=NOW)["num"] == ID_A   # witness: last known
        assert orad.fresh_own_node(NOW) is None                # a send: not stamped


class TestNewestWins:
    def test_in_process_newer_than_file(self, peer):
        _write(peer, num=ID_A, source="gw", ts=NOW - 100)
        orad.note_own_node(ID_B, source="map collector connection", now=NOW - 10)
        st = orad.own_node_status(now=NOW)
        assert st["num"] == ID_B and "map collector" in st["source"]

    def test_file_newer_than_in_process(self, peer):
        orad.note_own_node(ID_B, source="map collector connection", now=NOW - 100)
        _write(peer, num=ID_A, source="gw", ts=NOW - 10)
        assert orad.own_node_status(now=NOW)["num"] == ID_A
        assert orad.fresh_own_node(NOW) == ID_A

    def test_superseded_disagreement_is_said(self, peer):
        orad.note_own_node(ID_B, source="map collector connection", now=NOW - 100)
        _write(peer, num=ID_A, source="gw", ts=NOW - 10)
        assert "superseded" in orad.own_node_status(now=NOW)["note"]


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
    def __init__(self, my=None, local=None):
        self.myInfo = type("M", (), {"my_node_num": my})() if my is not None else None
        self.localNode = type("L", (), {"nodeNum": local})() if local is not None else None


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
    def _handler(self, host, iface):
        from gateway.meshtastic_handler import MeshtasticHandler
        h = MeshtasticHandler.__new__(MeshtasticHandler)
        h.config = type("C", (), {"meshtastic": type("M", (), {"host": host})()})()
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


def test_conftest_pins_the_peer_file():
    """No test reads THIS box's real gateway file (ambient state)."""
    p = orad.peer_path()
    assert "pytest" in str(p) or not str(p).startswith(os.path.expanduser("~/.local"))
