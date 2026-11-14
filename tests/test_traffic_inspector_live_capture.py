"""The traffic inspector's LIVE Meshtastic capture path, end to end.

Research artifact 2026-11-14 (2 m / fw 2.8 / sensors) found the dissector's
decoded-field branches keyed to the wrong port numbers (4 = POSITION,
5 = NODEINFO; firmware: 3 POSITION, 4 NODEINFO, 5 ROUTING). Following the
consumer path showed a deeper defect: the pubsub hook hands the dissector
meshtastic-python's portnum STRING ("POSITION_APP") and never the decoded
payload, so ``int(portnum)`` raised and every live packet degraded to a bare
UNKNOWN packet. These tests drive the real hook with the packet shape
meshtastic-python publishes on ``meshtastic.receive`` and assert the END: the
inspector's stored packet carries the decoded fields.
"""
import pytest

from monitoring import traffic_inspector as ti
from monitoring.traffic_models import MESHTASTIC_PORTS


class _FakePub:
    def __init__(self):
        self.cbs = {}

    def subscribe(self, cb, topic):
        self.cbs[topic] = cb


@pytest.fixture
def live(monkeypatch, tmp_path):
    """The real start_packet_capture() hook, wired to a fresh inspector."""
    pub = _FakePub()
    inspector = ti.TrafficInspector(db_path=str(tmp_path / "t.db"), enable_logging=False)
    monkeypatch.setattr(ti, "_pub", pub)
    monkeypatch.setattr(ti, "_HAS_PUBSUB", True)
    monkeypatch.setattr(ti, "_capture_subscribed", False)
    monkeypatch.setattr(ti, "get_traffic_inspector", lambda: inspector)
    assert ti.start_packet_capture() is True
    cb = pub.cbs["meshtastic.receive"]

    def feed(decoded):
        pkt = {"from": 0x1234ABCD, "to": 0xFFFFFFFF, "fromId": "!1234abcd",
               "toId": "^all", "id": 42, "channel": 0, "hopLimit": 2,
               "hopStart": 3, "rxSnr": 5.25, "rxRssi": -97, "decoded": decoded}
        seen.clear()
        cb(pkt, interface=None)
        assert len(seen) == 1, "the hook captured no packet"
        return seen[0], inspector._capture.get_stats().get("dissect_errors", 0), inspector
    seen = []
    inspector._capture.register_callback(seen.append)
    return feed


def _fields(packet):
    assert packet.tree is not None, "packet was never dissected (no tree)"
    return packet.tree.get_all_fields()


def test_live_position_packet_is_dissected(live):
    p, errors, _ = live({"portnum": "POSITION_APP",
                    "position": {"latitude": 19.43, "longitude": -155.26, "altitude": 1210}})
    assert p.portnum == 3 and p.port_name == "POSITION"
    f = _fields(p)
    assert f["mesh.pos.lat"].value == 19.43
    assert f["mesh.pos.lon"].value == -155.26
    assert errors == 0, "the live hook degraded the packet (dissect_errors)"


def test_live_nodeinfo_packet_is_dissected(live):
    p, _, _ = live({"portnum": "NODEINFO_APP",
                    "user": {"longName": "moc lab", "shortName": "mocl", "hwModel": "RAK4631"}})
    assert p.portnum == 4 and p.port_name == "NODEINFO"
    assert _fields(p)["mesh.user.long"].value == "moc lab"


def test_live_routing_packet_gets_no_user_fields(live):
    """5 = ROUTING: the old branch read it as NODEINFO."""
    p, _, _ = live({"portnum": "ROUTING_APP", "routing": {"errorReason": "NONE"}})
    assert p.portnum == 5 and p.port_name == "ROUTING"
    assert "mesh.user.long" not in _fields(p)


def test_live_source_and_hops_survive(live):
    p, _, _ = live({"portnum": "TEXT_MESSAGE_APP", "text": "aloha"})
    assert p.port_name == "TEXT_MESSAGE"
    assert _fields(p)["mesh.text"].value == "aloha"
    assert "1234abcd" in p.source.lower()
    assert p.hops_taken == 1


def test_an_unknown_portnum_string_is_witnessed_not_crashed(live):
    p, errors, _ = live({"portnum": "SOME_FUTURE_APP"})
    assert p.port_name == "SOME_FUTURE_APP"
    assert errors == 0


def test_dissector_port_branches_use_the_shared_port_table():
    """Honest-failure rule 5: the dissector branches on port NAMES from the
    one table (traffic_models.MESHTASTIC_PORTS), never on its own numbers —
    its literals said 4 = POSITION / 5 = NODEINFO while firmware says 3/4."""
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "src/monitoring/packet_dissectors.py").read_text()
    assert not re.search(r"packet\.portnum\s*==\s*\d", src)
    for name in ("TEXT_MESSAGE", "POSITION", "NODEINFO", "TELEMETRY", "TRACEROUTE"):
        assert f'port == "{name}"' in src, name
        assert name in MESHTASTIC_PORTS.values(), name
    assert MESHTASTIC_PORTS[3] == "POSITION" and MESHTASTIC_PORTS[4] == "NODEINFO"
