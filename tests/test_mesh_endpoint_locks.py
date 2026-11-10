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
        ("localhost", 4404), ("127.0.0.1", 4405), ("192.0.2.20", 4403),
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
        assert connection_lock_for("LOCALHOST", 4404) is \
            connection_lock_for("localhost", 4404)

    def test_two_endpoints_never_share(self):
        from utils.meshtastic_connection import connection_lock_for
        assert connection_lock_for("192.0.2.20", 4403) is not \
            connection_lock_for("192.0.2.30", 4403)
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
        b = _bridge(("192.0.2.10", 4403), ("192.0.2.20", 4403))
        _connect(b, "primary")
        assert _connect(b, "secondary") < 5
        assert b._secondary_connected

    def test_two_legs_on_ONE_radio_are_still_refused_and_say_why(self, caplog):
        """One radio is still one slot: the second leg must NOT connect, and
        the error must name the endpoint (a config error, not a busy daemon)."""
        import logging
        caplog.set_level(logging.ERROR, logger="gateway.mesh_bridge")
        b = _bridge(("192.0.2.10", 4403), ("192.0.2.10", 4403))
        _connect(b, "primary")
        with patch("gateway.mesh_bridge._LOCK_WAIT_S", 0.2, create=True):
            _connect(b, "secondary")
        assert b._primary_connected and not b._secondary_connected
        assert any("192.0.2.10:4403" in r.getMessage() for r in caplog.records)

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

    @pytest.mark.parametrize("host,port", [("localhost", 4403), ("192.0.2.20", 4403)])
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
            def bind(self, addr):
                # the endpoint rule's "is this address mine?" probe: a TEST-NET
                # address is not, exactly as a real bind would answer
                raise OSError("not a local address (test)")
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

def _global_lock_refs(source, filename="<src>"):
    """Every CODE reference to MESHTASTIC_CONNECTION_LOCK (a Name, an attribute
    ``x.MESHTASTIC_CONNECTION_LOCK``, or an import of it — any alias)."""
    hits = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and node.id == "MESHTASTIC_CONNECTION_LOCK":
            hits.append(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == "MESHTASTIC_CONNECTION_LOCK":
            hits.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and any(
                a.name == "MESHTASTIC_CONNECTION_LOCK" for a in node.names):
            hits.append(node.lineno)
    return hits


def test_no_one_outside_the_rule_touches_the_global_lock():
    """Every holder must ask connection_lock_for(host, port). Since r4 the gate
    refuses ANY code reference outside utils/meshtastic_connection.py — not
    just ``.acquire(`` (r4 M33–M35: ``mc.X.acquire()``, an ``as`` alias and
    ``with X:`` all slipped the old gate)."""
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name == "meshtastic_connection.py":
            continue
        for line in _global_lock_refs(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(SRC)}:{line}")
    assert offenders == [], offenders


@pytest.mark.parametrize("planted", [
    "MESHTASTIC_CONNECTION_LOCK.acquire()\n",
    "import utils.meshtastic_connection as mc\nmc.MESHTASTIC_CONNECTION_LOCK.acquire()\n",
    "from utils.meshtastic_connection import MESHTASTIC_CONNECTION_LOCK as _L\n",
    "with MESHTASTIC_CONNECTION_LOCK:\n    pass\n",
])
def test_the_gate_catches_every_reference_shape(planted):
    assert _global_lock_refs(planted)


# ------------------------------------------------ r4 review: aliases ---

def _own_lan_ip():
    """This box's outbound address (a UDP connect sends no packet), or None."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        ip = s.getsockname()[0]
        return None if ip.startswith("127.") or ip == "0.0.0.0" else ip
    except OSError:
        return None
    finally:
        s.close()


class TestOneDaemonOneLockUnderAnySpelling:
    """r4 F1 (a REGRESSION of the first #13 cut): the local daemon reached as
    ``127.1`` / ``0.0.0.0`` / the box's own name got a DIFFERENT lock from
    ``localhost`` — two clients on one meshtasticd slot (the #17 thrash). The
    endpoint is now decided on addresses."""

    @pytest.mark.parametrize("host", [
        "localhost", "127.0.0.1", "::1", "127.1", "127.0.0.2", "0.0.0.0", "::",
        "localhost.", "[::1]", "::ffff:127.0.0.1", " localhost ", "LOCALHOST", None, ""])
    def test_every_spelling_of_this_box_takes_the_global_lock(self, host):
        from utils.meshtastic_connection import (
            MESHTASTIC_CONNECTION_LOCK, connection_lock_for)
        assert connection_lock_for(host, 4403) is MESHTASTIC_CONNECTION_LOCK

    def test_the_boxs_own_hostname_takes_the_global_lock(self):
        import socket
        from utils.meshtastic_connection import (
            MESHTASTIC_CONNECTION_LOCK, connection_lock_for)
        assert connection_lock_for(socket.gethostname(), 4403) is MESHTASTIC_CONNECTION_LOCK

    def test_the_boxs_own_lan_address_takes_the_global_lock(self):
        from utils.meshtastic_connection import (
            MESHTASTIC_CONNECTION_LOCK, connection_lock_for)
        ip = _own_lan_ip()
        if ip is None:
            pytest.skip("no outbound route on this box — own LAN address unknown")
        assert connection_lock_for(ip, 4403) is MESHTASTIC_CONNECTION_LOCK

    def test_local_on_another_port_shares_across_spellings(self):
        from utils.meshtastic_connection import (
            MESHTASTIC_CONNECTION_LOCK, connection_lock_for)
        lk = connection_lock_for("localhost", 4404)
        assert lk is not MESHTASTIC_CONNECTION_LOCK
        # on a NON-default port every spelling must still name ONE endpoint:
        # on 4403 an unresolvable spelling falls back to the global lock — the
        # same answer — which masked the normalizations (r4fix mutants)
        for h in ("127.1", "0.0.0.0", "::1", "127.0.0.2", " localhost ", "LOCALHOST",
                  None, "", "[::1]", "localhost.", "::ffff:127.0.0.1"):
            assert connection_lock_for(h, 4404) is lk, repr(h)

    def test_a_mapped_v6_literal_is_its_v4_endpoint(self):
        from utils.meshtastic_connection import connection_lock_for
        assert connection_lock_for("::ffff:192.0.2.20", 4404) is \
            connection_lock_for("192.0.2.20", 4404)

    @pytest.mark.parametrize("port", [None, 0, "", "4403"])
    def test_port_spellings_of_the_default(self, port):
        """r4 M04/M05: None/0/'' mean the default; '4403' is 4403."""
        from utils.meshtastic_connection import (
            MESHTASTIC_CONNECTION_LOCK, connection_lock_for)
        assert connection_lock_for("localhost", port) is MESHTASTIC_CONNECTION_LOCK

    def test_a_string_port_is_the_same_endpoint(self):
        from utils.meshtastic_connection import connection_lock_for
        assert connection_lock_for("192.0.2.20", "4404") is \
            connection_lock_for("192.0.2.20", 4404)

    def test_an_unresolvable_endpoint_excludes_more_never_less(self):
        """It cannot be told whether it is this box's daemon → the global lock."""
        from utils.meshtastic_connection import (
            MESHTASTIC_CONNECTION_LOCK, connection_lock_for)
        with patch("socket.getaddrinfo", side_effect=OSError("no such host")):
            assert connection_lock_for("radio.example", 4404) is MESHTASTIC_CONNECTION_LOCK

    def test_two_spellings_of_one_daemon_cannot_both_hold_it(self):
        """The reviewer's drill, as a test: legs on localhost:P and 127.1:P —
        the second must be refused (one slot), and the error must say why."""
        import logging
        b = _bridge(("localhost", 4404), ("127.1", 4404))
        _connect(b, "primary")
        with patch("gateway.mesh_bridge._LOCK_WAIT_S", 0.2):
            _connect(b, "secondary")
        assert b._primary_connected and not b._secondary_connected


# ---------------------------------------------- r4 review: node_monitor ---

class TestNodeMonitorReleasesExactlyWhatItTook:

    def test_it_releases_the_lock_it_took_even_if_host_changed(self):
        """r4 M14: release must not recompute the lock from (host, port)."""
        import threading as th
        from monitoring.node_monitor import NodeMonitor
        nm = NodeMonitor(host="127.0.0.1", port=4404)
        taken = th.Lock()
        taken.acquire()
        nm._held_lock, nm._holds_global_lock = taken, True
        nm.host = "192.0.2.99"
        nm._release_global_lock()
        assert not taken.locked()
        assert nm._held_lock is None                        # r4 M16

    def test_no_record_releases_nothing(self):
        """r4 F3: a flag with no recorded lock must NOT free the global lock
        someone else holds."""
        from monitoring.node_monitor import NodeMonitor
        from utils.meshtastic_connection import MESHTASTIC_CONNECTION_LOCK
        nm = NodeMonitor(host="192.0.2.20", port=4403)
        assert MESHTASTIC_CONNECTION_LOCK.acquire(timeout=1)    # someone else's
        try:
            nm._held_lock, nm._holds_global_lock = None, True
            nm._release_global_lock()
            assert MESHTASTIC_CONNECTION_LOCK.locked()
        finally:
            MESHTASTIC_CONNECTION_LOCK.release()

    def test_it_takes_its_own_port_s_lock(self):
        """r4 M12: NodeMonitor('127.0.0.1', 4404) is not the 4403 daemon."""
        from monitoring.node_monitor import NodeMonitor
        from utils.meshtastic_connection import (
            MESHTASTIC_CONNECTION_LOCK, connection_lock_for)
        nm = NodeMonitor(host="127.0.0.1", port=4404)
        assert nm._connection_lock() is connection_lock_for("localhost", 4404)
        assert nm._connection_lock() is not MESHTASTIC_CONNECTION_LOCK


# ------------------------------------------------- r4 review: bridge ---

class TestBridgeR4Pins:

    def test_busy_messages_name_who_holds_it(self, caplog):
        """r4 M30–M32: the other-leg case says so and points at the config;
        a foreign holder is 'another component', with no config hint."""
        import logging
        from utils.meshtastic_connection import connection_lock_for
        caplog.set_level(logging.ERROR, logger="gateway.mesh_bridge")
        b = _bridge(("192.0.2.10", 4403), ("192.0.2.10", 4403))
        _connect(b, "primary")
        with patch("gateway.mesh_bridge._LOCK_WAIT_S", 0.2):
            _connect(b, "secondary")
        msg = [r.getMessage() for r in caplog.records if "busy" in r.getMessage()][-1]
        assert "the OTHER bridge leg" in msg and "primary/secondary host+port" in msg
        caplog.clear()
        foreign = connection_lock_for("192.0.2.50", 4403)
        assert foreign.acquire(timeout=1)
        try:
            c = _bridge(("192.0.2.50", 4403), ("192.0.2.60", 4403))
            with patch("gateway.mesh_bridge._LOCK_WAIT_S", 0.2):
                _connect(c, "primary")
            msg = [r.getMessage() for r in caplog.records if "busy" in r.getMessage()][-1]
            assert "another component" in msg and "host+port" not in msg
        finally:
            foreign.release()

    def test_release_never_frees_another_handle_s_lock(self):
        """r4 M26."""
        import threading as th
        b = _bridge(("192.0.2.10", 4403), ("192.0.2.20", 4403))
        theirs = th.Lock()
        theirs.acquire()
        other, mine = MagicMock(), MagicMock()
        b._tcp_lock_holds[other] = theirs
        b._release_tcp_lock_if_owner(mine)
        assert theirs.locked() and b._tcp_lock_holds == {other: theirs}
        theirs.release()

    def test_a_none_install_after_stop_still_marks_down(self):
        """r4 M20: refusing applies to a HANDLE; recording 'down' is always fine."""
        b = _bridge(("192.0.2.10", 4403), ("192.0.2.20", 4403))
        b._primary_connected = True
        b._stop_event.set()
        assert b._install_leg("primary", None, False) is True
        assert b._primary_connected is False

    def test_the_stop_check_happens_under_the_leg_lock(self):
        """r4 M21: the race fix rests on checking _stop_event INSIDE _leg_lock."""
        b = _bridge(("192.0.2.10", 4403), ("192.0.2.20", 4403))
        held = []

        class Recording:
            def __enter__(self):
                held.append("in")
            def __exit__(self, *a):
                held.append("out")
                return False

        class Probe:
            def is_set(self):
                held.append("check")
                return False

        b._leg_lock, b._stop_event = Recording(), Probe()
        b._install_leg("primary", MagicMock(), True)
        assert held == ["in", "check", "out"]

    def test_start_refuses_while_a_previous_leg_thread_is_alive(self, caplog):
        """r4 F2: stop() joins 5 s; a leg stuck in the library connect outlives
        it, and start() would let it resume BESIDE the new loop."""
        import logging
        caplog.set_level(logging.ERROR, logger="gateway.mesh_bridge")
        b = _bridge(("192.0.2.10", 4403), ("192.0.2.20", 4403))
        b.bridge_config.enabled = True
        gate = threading.Event()
        stuck = threading.Thread(target=gate.wait, name="MeshBridge-Primary", daemon=True)
        stuck.start()
        b._primary_thread = stuck
        try:
            assert b.start() is False
            assert b._running is False
            assert any("have not exited" in r.getMessage() for r in caplog.records)
        finally:
            gate.set()
            stuck.join(2)
