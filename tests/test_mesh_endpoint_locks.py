"""BR-T #13 — one connection lock per daemon PORT, not one per process.

Found 2026-11-10 (probe on the BR-T #8/#10 fix): ``MESHTASTIC_CONNECTION_LOCK``
is a plain process-global Lock that a TCP leg holds for its connection's LIFE
(Issue #17: meshtasticd serves ONE TCP client). With both bridge legs on TCP —
the DEFAULT config, localhost:4403 + localhost:4404 — the second leg waited 10 s
on the first leg's hold and failed, every 10 s, forever.

Operator 11-10: "go with your advice" (per-endpoint lock). Two review rounds by
running (r4, r5) then showed that "endpoint" cannot be decided from a HOST:
spellings, a transient resolver failure, zone ids, multi-address names and NAT
fronts each split one daemon across two locks. A daemon listens on ONE port, so
the lock is keyed by PORT. The whole input space, as a table:

  same daemon, any spelling       -> same port -> SAME lock        (always)
  default bridge 4403 + 4404      -> two locks -> both legs connect (#13)
  two radios, different ports     -> two locks                      (better)
  two radios, SAME port           -> one lock  -> second waits, says why
                                     (over-excludes: the safe direction, and
                                      never more than before #13)
  unresolvable / zone id / NAT    -> nothing is resolved; cannot split
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
    locks = [mc.MESHTASTIC_CONNECTION_LOCK] + list(mc._port_locks.values())
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

SPELLINGS_OF_THIS_BOX = [
    "localhost", "127.0.0.1", "::1", "127.1", "127.0.0.2", "0.0.0.0", "::",
    "localhost.", "[::1]", "::ffff:127.0.0.1", " localhost ", "LOCALHOST", None, "",
    "fe80::1%eth0", "ip6-localhost", "2130706433", "this-box.invalid"]


class TestTheLockIsKeyedByPort:

    @pytest.mark.parametrize("host", SPELLINGS_OF_THIS_BOX + ["192.0.2.20", "radio.example"])
    def test_the_default_port_is_the_global_lock_under_any_host(self, host):
        from utils.meshtastic_connection import (
            MESHTASTIC_CONNECTION_LOCK, connection_lock_for)
        assert connection_lock_for(host, 4403) is MESHTASTIC_CONNECTION_LOCK

    def test_one_daemon_on_another_port_is_one_lock_under_any_spelling(self):
        """r4 F1 / r5 F1+F3+F4: every spelling of one daemon, one lock."""
        from utils.meshtastic_connection import (
            MESHTASTIC_CONNECTION_LOCK, connection_lock_for)
        lk = connection_lock_for("localhost", 4404)
        assert lk is not MESHTASTIC_CONNECTION_LOCK
        for h in SPELLINGS_OF_THIS_BOX + ["192.0.2.20"]:
            assert connection_lock_for(h, 4404) is lk, repr(h)

    def test_two_ports_are_two_locks(self):
        from utils.meshtastic_connection import connection_lock_for
        assert connection_lock_for("localhost", 4404) is not \
            connection_lock_for("localhost", 4405)

    def test_no_resolver_and_no_socket_is_consulted(self):
        """r5 F1/F2/F5: nothing to fail, block or raise — not even a hung DNS."""
        from utils.meshtastic_connection import connection_lock_for
        with patch("socket.getaddrinfo", side_effect=AssertionError("resolved")), \
             patch("socket.socket", side_effect=AssertionError("opened a socket")):
            connection_lock_for("radio.example", 4404)
            connection_lock_for("fe80::1%eth0", 4403)

    @pytest.mark.parametrize("port", [None, 0, "", "4403", "abc", object()])
    def test_port_spellings_and_garbage_key_as_the_default(self, port):
        """None/0/'' mean the default; '4403' is 4403; a non-number keys as the
        MOST exclusive lock and never raises (r5 F2: int('abc'))."""
        from utils.meshtastic_connection import (
            MESHTASTIC_CONNECTION_LOCK, connection_lock_for)
        assert connection_lock_for("localhost", port) is MESHTASTIC_CONNECTION_LOCK

    def test_a_string_port_is_the_same_lock(self):
        from utils.meshtastic_connection import connection_lock_for
        assert connection_lock_for("x", "4404") is connection_lock_for("y", 4404)

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

    def test_two_remote_radios_on_different_ports_connect(self):
        b = _bridge(("192.0.2.10", 4403), ("192.0.2.20", 4404))
        _connect(b, "primary")
        assert _connect(b, "secondary") < 5
        assert b._secondary_connected

    def test_two_radios_on_the_SAME_port_take_turns_and_say_why(self, caplog):
        """The one over-exclusion the port key accepts (safe direction; the
        same as before #13): the second waits, and the error names the port
        and the way out."""
        import logging
        caplog.set_level(logging.ERROR, logger="gateway.mesh_bridge")
        b = _bridge(("192.0.2.10", 4403), ("192.0.2.20", 4403))
        _connect(b, "primary")
        with patch("gateway.mesh_bridge._LOCK_WAIT_S", 0.2):
            _connect(b, "secondary")
        assert b._primary_connected and not b._secondary_connected
        msg = [r.getMessage() for r in caplog.records if "busy" in r.getMessage()][-1]
        assert "port 4403" in msg and "MQTT" in msg

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
        b._disconnect_primary()          # free 4403 before the second scenario
        caplog.clear()
        foreign = connection_lock_for("192.0.2.50", 4410)
        assert foreign.acquire(timeout=1)
        try:
            c = _bridge(("192.0.2.50", 4410), ("192.0.2.60", 4411))
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


# ------------------------------------------------- r5 review pins ---

class TestR5Pins:

    def test_start_after_a_clean_stop_is_not_refused(self):
        """r5 M33: only a LIVE old thread refuses a start."""
        b = _bridge(("192.0.2.10", 4403), ("192.0.2.20", 4404))
        b.bridge_config.enabled = True
        dead = threading.Thread(target=lambda: None, name="MeshBridge-Primary")
        dead.start()
        dead.join()
        b._primary_thread = b._secondary_thread = dead
        with patch.object(b, "_primary_loop"), patch.object(b, "_secondary_loop"), \
             patch.object(b, "_bridge_loop"), patch.object(b, "_cleanup_loop"):
            assert b.start() is True
        b._running = False
        b._stop_event.set()

    def test_a_live_SECONDARY_thread_refuses_too(self, caplog):
        """r5 M31: the check covers both legs."""
        import logging
        caplog.set_level(logging.ERROR, logger="gateway.mesh_bridge")
        b = _bridge(("192.0.2.10", 4403), ("192.0.2.20", 4404))
        b.bridge_config.enabled = True
        gate = threading.Event()
        stuck = threading.Thread(target=gate.wait, name="MeshBridge-Secondary", daemon=True)
        stuck.start()
        b._secondary_thread = stuck
        try:
            assert b.start() is False
        finally:
            gate.set()
            stuck.join(2)

    def test_node_monitor_clears_its_hold_flag(self):
        """r5 M44."""
        from monitoring.node_monitor import NodeMonitor
        nm = NodeMonitor(host="127.0.0.1", port=4404)
        taken = threading.Lock()
        taken.acquire()
        nm._held_lock, nm._holds_global_lock = taken, True
        nm._release_global_lock()
        assert nm._holds_global_lock is False


def test_a_leg_waits_out_a_brief_hold_on_its_port():
    """r6 M37: _LOCK_WAIT_S is the leg's patience. A holder that lets go 0.3 s
    later (a node_monitor reconnect, a short-lived send) must not fail the
    connect — a near-zero wait turned every brief overlap into a 10 s retry."""
    from utils.meshtastic_connection import connection_lock_for
    lk = connection_lock_for("192.0.2.10", 4420)
    assert lk.acquire(timeout=1)
    t = threading.Timer(0.3, lambda: lk.locked() and lk.release())
    t.start()
    try:
        b = _bridge(("192.0.2.10", 4420), ("192.0.2.20", 4421))
        _connect(b, "primary")
        assert b._primary_connected
    finally:
        t.cancel()
        t.join(1)


# ------------------------------------- I4: all four threads gate start() ---

@pytest.mark.parametrize("attr,name", [
    ("_bridge_thread", "MeshBridge-Forward"),
    ("_cleanup_thread", "MeshBridge-Cleanup"),
])
def test_start_refuses_while_a_previous_NON_LEG_thread_is_alive(attr, name, caplog):
    """#25 r4 I4 (proven by running: 'start with OLD forward thread alive ->
    True'): the guard covered only the two LEG threads. A forward thread that
    outlives stop()'s 5 s join resumes beside the new one once start() clears
    _stop_event — two drains injecting from one queue. Refuse, and name it."""
    import logging
    caplog.set_level(logging.ERROR, logger="gateway.mesh_bridge")
    b = _bridge(("192.0.2.10", 4403), ("192.0.2.20", 4404))
    b.bridge_config.enabled = True
    gate = threading.Event()
    stuck = threading.Thread(target=gate.wait, name=name, daemon=True)
    stuck.start()
    setattr(b, attr, stuck)
    try:
        with patch.object(b, "_primary_loop"), patch.object(b, "_secondary_loop"), \
             patch.object(b, "_bridge_loop"), patch.object(b, "_cleanup_loop"):
            assert b.start() is False
        assert b._running is False
        assert any(name in r.getMessage() and "have not exited" in r.getMessage()
                   for r in caplog.records)
    finally:
        b._running = False
        b._stop_event.set()
        gate.set()
        stuck.join(2)


@pytest.mark.parametrize("attr,name", [
    ("_bridge_thread", "MeshBridge-Forward"),
    ("_cleanup_thread", "MeshBridge-Cleanup"),
])
def test_a_DEAD_previous_non_leg_thread_does_not_refuse_start(attr, name):
    """I4 review LOW-2: the other direction — only a LIVE old forward/cleanup
    thread refuses. Previously pinned only by a test that skips without paho."""
    b = _bridge(("192.0.2.10", 4403), ("192.0.2.20", 4404))
    b.bridge_config.enabled = True
    dead = threading.Thread(target=lambda: None, name=name)
    dead.start()
    dead.join()
    setattr(b, attr, dead)
    try:
        with patch.object(b, "_primary_loop"), patch.object(b, "_secondary_loop"), \
             patch.object(b, "_bridge_loop"), patch.object(b, "_cleanup_loop"):
            assert b.start() is True
    finally:
        b._running = False
        b._stop_event.set()
