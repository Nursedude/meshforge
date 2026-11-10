"""BR-T #13 — one connection lock PER ENDPOINT, not one per process.

Found 2026-11-10 (probe on the BR-T #8/#10 fix): ``MESHTASTIC_CONNECTION_LOCK``
is a plain process-global Lock that a TCP leg holds for its connection's LIFE
(Issue #17: meshtasticd serves ONE TCP client). With both bridge legs on TCP —
the DEFAULT config — the second leg waited 10 s on the first leg's hold and
failed, every 10 s, forever: the default bridge never bridged.

The rule (operator 11-10: "go with your advice"): the lock guards a TCP SLOT,
and a slot belongs to an endpoint. ``connection_lock_for(host, port)`` is the
ONE place that decides: the local meshtasticd (a ``_LOCAL_HOSTS`` name on 4403)
keeps ``MESHTASTIC_CONNECTION_LOCK`` — every existing holder already uses it, so
local behaviour is unchanged — and every other endpoint gets its own lock. Two
radios never block each other; one radio is still guarded.
"""
import ast
import pathlib
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


@pytest.fixture(autouse=True)
def _locks_left_as_found(tmp_path, monkeypatch):
    import gateway.mesh_bridge as mb
    import utils.meshtastic_connection as mc
    monkeypatch.setattr(mb, "get_real_user_home", lambda: tmp_path)
    assert not mc.MESHTASTIC_CONNECTION_LOCK.locked()
    yield
    locks = [mc.MESHTASTIC_CONNECTION_LOCK] + list(
        getattr(mc, "_endpoint_locks", {}).values())
    for lk in locks:
        if lk.locked():
            lk.release()
    from pubsub import pub
    for topic in ("meshtastic.receive", "meshtastic.connection.lost"):
        try:
            pub.unsubAll(topic)
        except Exception:  # topic never created
            pass


# ------------------------------------------------------------- the rule ---

class TestConnectionLockFor:

    @pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1", "LOCALHOST"])
    def test_the_local_meshtasticd_keeps_the_global_lock(self, host):
        from utils.meshtastic_connection import (
            MESHTASTIC_CONNECTION_LOCK, connection_lock_for)
        assert connection_lock_for(host, 4403) is MESHTASTIC_CONNECTION_LOCK

    @pytest.mark.parametrize("host,port", [
        ("localhost", 4404), ("127.0.0.1", 4405), ("radio2.invalid", 4403),
        ("10.0.0.9", 4403)])
    def test_any_other_endpoint_gets_its_own_lock(self, host, port):
        from utils.meshtastic_connection import (
            MESHTASTIC_CONNECTION_LOCK, connection_lock_for)
        lk = connection_lock_for(host, port)
        assert lk is not MESHTASTIC_CONNECTION_LOCK
        assert connection_lock_for(host, port) is lk          # stable per endpoint

    def test_aliases_of_one_endpoint_share_one_lock(self):
        from utils.meshtastic_connection import connection_lock_for
        assert connection_lock_for("localhost", 4404) is \
            connection_lock_for("127.0.0.1", 4404)
        assert connection_lock_for("Radio2.Invalid", 4403) is \
            connection_lock_for("radio2.invalid", 4403)

    def test_two_endpoints_never_share(self):
        from utils.meshtastic_connection import connection_lock_for
        assert connection_lock_for("radio2.invalid", 4403) is not \
            connection_lock_for("radio3.invalid", 4403)
        assert connection_lock_for("localhost", 4404) is not \
            connection_lock_for("localhost", 4405)


# ----------------------------------------------------------- the bridge ---

def _bridge(primary, secondary):
    from gateway.config import GatewayConfig, MeshtasticBridgeConfig, MeshtasticConfig
    from gateway.mesh_bridge import MeshtasticPresetBridge
    cfg = GatewayConfig()
    cfg.mesh_bridge = MeshtasticBridgeConfig(
        enabled=True,
        primary=MeshtasticConfig(host=primary[0], port=primary[1], preset="LONG_FAST"),
        secondary=MeshtasticConfig(host=secondary[0], port=secondary[1],
                                   preset="SHORT_TURBO"),
    )
    return MeshtasticPresetBridge(config=cfg)


def _connect(bridge, leg):
    from pubsub import pub as real_pub
    fake_tcp = MagicMock()
    fake_tcp.TCPInterface = MagicMock(side_effect=lambda **kw: MagicMock())
    with patch("gateway.mesh_bridge._HAS_MESHTASTIC", True), \
         patch("gateway.mesh_bridge._HAS_MESHTASTIC_TCP", True), \
         patch("gateway.mesh_bridge._HAS_PUBSUB", True), \
         patch("gateway.mesh_bridge._meshtastic_tcp", fake_tcp), \
         patch("gateway.mesh_bridge._pub", real_pub), \
         patch("utils.meshtastic_connection.wait_for_cooldown"), \
         patch("utils.service_check.check_service"):
        t0 = time.monotonic()
        getattr(bridge, f"_connect_{leg}")()
        return time.monotonic() - t0


class TestTwoTcpLegs:

    def test_the_default_two_tcp_leg_bridge_connects_both_legs(self):
        """The defect: secondary waited 10 s on primary's hold and failed."""
        b = _bridge(("localhost", 4403), ("localhost", 4404))
        _connect(b, "primary")
        waited = _connect(b, "secondary")
        assert b._primary_connected and b._secondary_connected
        assert waited < 5

    def test_two_legs_on_two_remote_radios_connect(self):
        b = _bridge(("radio1.invalid", 4403), ("radio2.invalid", 4403))
        _connect(b, "primary")
        assert _connect(b, "secondary") < 5
        assert b._secondary_connected

    def test_two_legs_on_ONE_radio_are_still_refused_and_say_why(self, caplog):
        """One radio is still one slot: the second leg must NOT connect, and
        the error must name the endpoint (a config error, not a busy daemon)."""
        import logging
        caplog.set_level(logging.ERROR, logger="gateway.mesh_bridge")
        b = _bridge(("radio1.invalid", 4403), ("radio1.invalid", 4403))
        _connect(b, "primary")
        with patch("gateway.mesh_bridge._LOCK_WAIT_S", 0.2, create=True):
            _connect(b, "secondary")
        assert b._primary_connected and not b._secondary_connected
        assert any("radio1.invalid:4403" in r.getMessage() for r in caplog.records)

    def test_each_leg_releases_the_lock_it_took(self):
        from utils.meshtastic_connection import (
            MESHTASTIC_CONNECTION_LOCK, connection_lock_for)
        b = _bridge(("localhost", 4403), ("localhost", 4404))
        _connect(b, "primary")
        _connect(b, "secondary")
        mine = connection_lock_for("localhost", 4404)
        assert MESHTASTIC_CONNECTION_LOCK.locked() and mine.locked()
        b._disconnect_secondary()
        assert not mine.locked()
        assert MESHTASTIC_CONNECTION_LOCK.locked()       # primary's hold untouched
        b._disconnect_primary()
        assert not MESHTASTIC_CONNECTION_LOCK.locked()


# --------------------------------------------------------- node_monitor ---

class TestNodeMonitorUsesTheSameRule:

    @pytest.mark.parametrize("host,port", [("localhost", 4403), ("radio2.invalid", 4403)])
    def test_node_monitor_takes_and_returns_its_endpoints_lock(self, host, port):
        """It takes ITS endpoint's lock (observed while held, from inside the
        library constructor) and returns that same lock on the failure path."""
        from monitoring.node_monitor import NodeMonitor
        from utils.meshtastic_connection import connection_lock_for
        nm = NodeMonitor(host=host, port=port)
        lk = connection_lock_for(host, port)
        seen = []

        class NoNetwork:
            """connect() opens a reachability socket to host:port BEFORE the
            library — on localhost:4403 that is the box's LIVE meshtasticd and
            its one TCP slot. Never let a test reach it."""
            def __init__(self, *a, **k):
                pass
            def settimeout(self, t):
                pass
            def connect(self, addr):
                seen.append((addr, lk.locked()))   # held while connecting?
                raise OSError("no radio (test)")
            def close(self):
                pass

        with patch("socket.socket", NoNetwork), \
             patch("monitoring.node_monitor._TCPInterface") as ctor, \
             patch("monitoring.node_monitor._HAS_TCP_INTERFACE", True), \
             patch("monitoring.node_monitor._HAS_PUBSUB", True), \
             patch("monitoring.node_monitor._pub", MagicMock()), \
             patch("monitoring.node_monitor.wait_for_cooldown"), \
             patch("utils.service_check.check_service"):
            assert nm.connect(timeout=1) is False
        assert seen == [((host, port), True)]
        assert ctor.call_count == 0
        assert not lk.locked()


# ----------------------------------------------------------------- gate ---

def test_no_one_acquires_the_global_lock_directly():
    """Every holder must ask connection_lock_for(host, port): a direct
    MESHTASTIC_CONNECTION_LOCK.acquire() outside its module would guard the
    local daemon's slot for a REMOTE endpoint, or miss the per-endpoint lock
    another holder takes for the same radio."""
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name == "meshtastic_connection.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and node.attr == "acquire"
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "MESHTASTIC_CONNECTION_LOCK"):
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert offenders == [], offenders
