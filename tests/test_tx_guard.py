"""Drills for the RF egress guard (``utils.tx_guard``).

Born 2026-08-09, after the full pytest suite transmitted the e2e fixture
``[RNS:abc] retry with bytes`` onto a live statewide public channel from a
fleet radio box. The suite reported 10,535 passed while keying the air.

⚠️ These are DRILLS, not assertions about wiring. The doctrine this incident
produced is that an instrument which has only ever produced ONE outcome is not
finished — a gate that has never rejected anything is not evidence it works.
So every test here PLANTS a real call at a real send site and requires the
guard to refuse it. Reading the code proves nothing; ``TestGuardIsWiredAtEvery
SendSite`` exists only as a coverage net *behind* the drills, never in place
of them.

Nothing here transmits: the guard raises before any socket is opened or any
subprocess spawned, which is exactly what makes it safe to reproduce the
incident faithfully in ``TestRealFallbackChainIsBlocked``.
"""

import os
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils import tx_guard
from utils.tx_guard import TransmitBlocked, assert_tx_allowed


@pytest.fixture(autouse=True)
def _clean_guard_state():
    """Each drill starts from a bare allowlist and an empty record."""
    tx_guard.clear_allowed_targets()
    tx_guard.clear_blocked_attempts()
    yield
    tx_guard.clear_allowed_targets()
    tx_guard.clear_blocked_attempts()


class TestArming:
    def test_armed_under_pytest(self):
        """The suite is the threat model — the guard must be live right now."""
        assert tx_guard.is_armed()

    def test_env_can_force_disarm(self, monkeypatch):
        """A deliberate live send from a test process must be an explicit act."""
        monkeypatch.setenv("MESHFORGE_TX_GUARD", "0")
        assert not tx_guard.is_armed()
        assert_tx_allowed("localhost", 9443, kind="drill")  # no raise

    def test_env_can_force_arm_outside_pytest(self, monkeypatch):
        monkeypatch.setenv("MESHFORGE_TX_GUARD", "1")
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        assert tx_guard.is_armed()


class TestAllowlist:
    def test_unlisted_target_raises(self):
        with pytest.raises(TransmitBlocked):
            assert_tx_allowed("localhost", 9443, kind="drill")

    def test_listed_target_passes(self):
        with tx_guard.allow_targets("127.0.0.1:8123"):
            assert_tx_allowed("127.0.0.1", 8123, kind="drill")

    def test_loopback_spellings_are_one_target(self):
        """A harness that allowlists 127.0.0.1 must not be defeated by a
        caller that says "localhost" — they are the same radio."""
        with tx_guard.allow_targets("127.0.0.1:8123"):
            assert_tx_allowed("localhost", 8123, kind="drill")

    def test_allowlist_does_not_leak_past_the_context(self):
        with tx_guard.allow_targets("127.0.0.1:8123"):
            pass
        with pytest.raises(TransmitBlocked):
            assert_tx_allowed("127.0.0.1", 8123, kind="drill")

    def test_env_allowlist_honored(self, monkeypatch):
        monkeypatch.setenv("MESHFORGE_TX_ALLOW", "127.0.0.1:8123")
        assert_tx_allowed("127.0.0.1", 8123, kind="drill")

    def test_mock_shaped_target_collapses_to_the_local_radio(self):
        """A config built from MagicMock must not mint an un-allowlistable
        target. Found porting to MeshAnchor: the refusal named
        ``<MagicMock name='...host.strip()' id='140735842580672'>:1``, unique
        per run, so the message's own advice was impossible to follow."""
        from unittest.mock import MagicMock
        assert tx_guard.normalize_target(MagicMock(), MagicMock()) == "127.0.0.1:4403"
        with tx_guard.allow_targets("127.0.0.1:4403"):
            assert_tx_allowed(MagicMock(), MagicMock(), kind="drill")

    def test_blocked_attempt_is_recorded(self):
        """The gate is also an instrument: a suite run must be able to name
        every hop that would have keyed the radio."""
        with pytest.raises(TransmitBlocked):
            assert_tx_allowed("localhost", 9443, kind="drill", detail="probe")
        recs = tx_guard.blocked_attempts()
        assert len(recs) == 1
        assert recs[0]["target"] == "127.0.0.1:9443"
        assert recs[0]["kind"] == "drill"


class TestNotSwallowable:
    """The property the whole design rests on.

    Every send site in this tree sits inside ``except Exception`` — they must,
    because a downed radio is not a crash. A guard those handlers can absorb
    degrades silently back into the send it exists to prevent.
    """

    def test_transmit_blocked_is_not_an_exception_subclass(self):
        assert issubclass(TransmitBlocked, BaseException)
        assert not issubclass(TransmitBlocked, Exception)

    def test_broad_except_does_not_absorb_it(self):
        absorbed = False
        try:
            try:
                assert_tx_allowed("localhost", 9443, kind="drill")
            except Exception:
                absorbed = True
        except TransmitBlocked:
            pass
        assert not absorbed, (
            "an `except Exception` handler absorbed TransmitBlocked — the guard "
            "would degrade into a real transmission at every send site"
        )


class TestGuardedSendSitesRefuse:
    """Plant a real call at each guarded site; require a refusal.

    These call PRODUCTION functions with a target that is not allowlisted.
    Each one would have opened a socket to the operator's radio.
    """

    def test_send_text_direct_refuses(self):
        from gateway import meshtastic_protobuf_client as mpc
        with pytest.raises(TransmitBlocked):
            mpc.send_text_direct("drill: must never reach the air")

    def test_send_text_direct_with_id_refuses(self):
        from gateway import meshtastic_protobuf_client as mpc
        with pytest.raises(TransmitBlocked):
            mpc.send_text_direct_with_id("drill", host="localhost", port=9443)

    def test_send_text_direct_refuses_before_opening_a_socket(self):
        """Ordering matters: the guard must precede the I/O, not follow it."""
        from gateway import meshtastic_protobuf_client as mpc
        with patch.object(mpc.urllib.request, "urlopen") as urlopen:
            with pytest.raises(TransmitBlocked):
                mpc.send_text_direct("drill")
        urlopen.assert_not_called()

    def test_session_client_post_toradio_refuses(self):
        from gateway.meshtastic_protobuf_client import MeshtasticProtobufClient
        from gateway.meshtastic_protobuf_ops import ProtobufTransportConfig
        client = MeshtasticProtobufClient(ProtobufTransportConfig())
        with pytest.raises(TransmitBlocked):
            client._post_toradio(b"\x00")

    def test_mqtt_handler_cli_fallback_refuses_without_spawning(self):
        from gateway.mqtt_bridge_handler import MQTTBridgeHandler
        h = MQTTBridgeHandler.__new__(MQTTBridgeHandler)
        h.config = MagicMock()
        h.config.meshtastic.host = "127.0.0.1"
        with patch.object(subprocess, "run") as run:
            with pytest.raises(TransmitBlocked):
                h._send_via_cli("drill")
        run.assert_not_called()

    def test_meshtastic_handler_cli_fallback_refuses(self):
        from gateway.meshtastic_handler import MeshtasticHandler
        h = MeshtasticHandler.__new__(MeshtasticHandler)
        h.config = MagicMock()
        h.config.meshtastic.host = "127.0.0.1"
        with pytest.raises(TransmitBlocked):
            h._send_via_cli("drill")

    def test_iface_sendtext_site_refuses(self):
        """A MagicMock interface cannot reach a radio — but the guard cannot
        know that, and must refuse anyway. Fail-closed means the test declares
        its target, not that the guard guesses."""
        from monitoring.node_monitor import ConnectionState, NodeMonitor
        m = NodeMonitor.__new__(NodeMonitor)
        m.interface = MagicMock(hostname="127.0.0.1")
        m._state = ConnectionState.CONNECTED
        assert m.is_connected
        with pytest.raises(TransmitBlocked):
            m.send_text("drill", destination="!aabbccdd")
        m.interface.sendText.assert_not_called()

    def test_mqtt_downlink_inject_refuses(self):
        """Not "just MQTT": meshtasticd subscribes to this topic and keys the
        radio with whatever arrives."""
        from gateway.mqtt_downlink_inject import DownlinkInjector
        from unittest.mock import PropertyMock

        inj = DownlinkInjector.__new__(DownlinkInjector)
        inj._broker, inj._port = "127.0.0.1", 1883
        # patch.object RESTORES the real property. An earlier version of this
        # drill assigned `type(inj).usable` and `del`'d it in teardown, which
        # deleted the CLASS's genuine property for the rest of the session and
        # broke six unrelated tests in another file — visible only when the
        # files ran together. A test that mutates a class must restore it, and
        # only a multi-file run proves it did.
        with patch.object(DownlinkInjector, "usable",
                          new_callable=PropertyMock, return_value=True):
            with pytest.raises(TransmitBlocked):
                inj._publish("text", 0x1234, lambda: ("t", b"p", 1))


class TestRealFallbackChainIsBlocked:
    """THE regression drill for the 2026-08-09 incident.

    The e2e harness pointed the bridge at a mock daemon and then STOPPED it to
    force send failures. The primary hop failed against the dead port exactly
    as intended — and the handler's fallback chain then reached the operator's
    real radio two ways, neither of which consults the mock's address:

        1. ``get_protobuf_client()`` — a module singleton built from
           ``ProtobufTransportConfig()`` DEFAULTS, i.e. ``localhost:9443``.
        2. ``meshtastic --host 127.0.0.1 --sendtext`` — TCP 4403.

    Both are the real meshtasticd. **Loopback is not safety on a radio box.**

    Each test below allowlists ONLY the dead mock port, so the primary fails
    naturally the way it did on the day — and then asserts the fallback is
    refused and that the refusal names the REAL radio. This reproduces the
    leak faithfully without transmitting, because the guard stops it.
    """

    def _handler(self, dead_port):
        from gateway.mqtt_bridge_handler import MQTTBridgeHandler
        h = MQTTBridgeHandler.__new__(MQTTBridgeHandler)
        h.config = MagicMock()
        h.config.meshtastic.host = "127.0.0.1"
        h.config.meshtastic.http_port = dead_port
        h._load_balancer = None
        h._cli_path = None
        return h

    def _dead_port(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def test_session_client_fallback_targets_the_real_radio_and_is_blocked(self):
        import gateway.mqtt_bridge_handler as mod
        dead = self._dead_port()
        h = self._handler(dead)
        from gateway.meshtastic_protobuf_client import MeshtasticProtobufClient
        from gateway.meshtastic_protobuf_ops import ProtobufTransportConfig

        # A FRESH client with default config, rather than the process
        # singleton — same defaults (localhost:9443), no shared state left
        # behind for later tests.
        def _fresh_client():
            return MeshtasticProtobufClient(ProtobufTransportConfig())

        with tx_guard.allow_targets(f"127.0.0.1:{dead}"):
            with patch.object(mod, "_HAS_PROTOBUF_CLIENT", True), \
                    patch.object(mod, "_get_protobuf_client", _fresh_client):
                with pytest.raises(TransmitBlocked):
                    h._send_via_http_protobuf("[RNS:abc] retry with bytes")
        blocked = tx_guard.blocked_attempts()
        assert any(r["target"] == "127.0.0.1:9443" for r in blocked), (
            "the session-client fallback did not attempt the real radio at "
            f":9443 — recorded instead: {blocked}"
        )

    def test_cli_fallback_targets_the_real_radio_and_is_blocked(self):
        import gateway.mqtt_bridge_handler as mod
        dead = self._dead_port()
        h = self._handler(dead)
        with tx_guard.allow_targets(f"127.0.0.1:{dead}"):
            with patch.object(mod, "_HAS_PROTOBUF_CLIENT", False):
                with patch.object(subprocess, "run") as run:
                    with pytest.raises(TransmitBlocked):
                        h.send_text("[RNS:abc] retry with bytes")
        run.assert_not_called()
        blocked = tx_guard.blocked_attempts()
        assert any(r["target"] == "127.0.0.1:4403" for r in blocked), (
            "the CLI fallback did not attempt the real radio at :4403 — "
            f"recorded instead: {blocked}"
        )


class TestSocketTripwire:
    """Layer 2 — the backstop for send sites nobody guarded.

    It exists because the meshtastic library's own ``TCPInterface`` opens its
    socket well below any code we wrote, so a call-site guard cannot be the
    only defense. Deliberately narrow: only the meshtasticd radio ports.
    """

    def test_connect_to_radio_port_is_refused(self):
        with pytest.raises(TransmitBlocked):
            socket.socket().connect(("127.0.0.1", 4403))

    def test_connect_to_web_port_is_refused(self):
        with pytest.raises(TransmitBlocked):
            socket.socket().connect(("127.0.0.1", 9443))

    def test_connect_ex_probe_idiom_is_permitted(self):
        """`connect_ex` returns an errno instead of raising — that IS the
        reachability-probe idiom, used by ~15 sites here to ask "is the radio
        port open?". Blocking it turned benign probes into failures (CI caught
        `fleet_snapshot._probe_radio`) while catching nothing: TCPInterface
        reaches the radio via create_connection -> connect, still blocked."""
        rc = socket.socket().connect_ex(("127.0.0.1", 4403))
        assert isinstance(rc, int)  # errno or 0; no raise either way

    def test_declared_probe_may_use_plain_connect(self):
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("127.0.0.1", 4403))
        except OSError:
            pytest.skip("port 4403 is in use on this box (real meshtasticd)")
        srv.listen(1)
        try:
            with tx_guard.probe_connect():
                c = socket.socket()
                c.connect(("127.0.0.1", 4403))
                c.close()
        finally:
            srv.close()

    def test_probe_declaration_does_not_leak_past_the_context(self):
        with tx_guard.probe_connect():
            assert tx_guard.in_probe()
        assert not tx_guard.in_probe()
        with pytest.raises(TransmitBlocked):
            socket.socket().connect(("127.0.0.1", 4403))

    def test_probe_declaration_is_thread_local(self):
        """One thread declaring a probe must not excuse another thread's
        transmission — probes run on daemon threads here."""
        import threading
        seen = {}

        def other():
            seen["in_probe"] = tx_guard.in_probe()

        with tx_guard.probe_connect():
            t = threading.Thread(target=other)
            t.start()
            t.join()
        assert seen["in_probe"] is False

    def test_non_radio_port_is_not_touched(self):
        """Narrowness drill. A blanket block would break the map-server and
        MQTT harnesses — and would NOT have caught the 08-09 leak anyway,
        because the radio is on loopback here."""
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            c = socket.socket()
            c.connect(("127.0.0.1", port))  # must not raise
            c.close()
        finally:
            srv.close()

    def test_allowlisted_radio_port_passes_the_tripwire(self):
        """The tripwire must consult the same allowlist as the call sites, or
        a harness that legitimately binds a fake daemon there cannot run."""
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("127.0.0.1", 9443))
        except OSError:
            pytest.skip("port 9443 is in use on this box (real meshtasticd)")
        srv.listen(1)
        try:
            with tx_guard.allow_targets("127.0.0.1:9443"):
                c = socket.socket()
                c.connect(("127.0.0.1", 9443))
                c.close()
        finally:
            srv.close()


class TestGuardIsWiredAtEverySendSite:
    """Coverage net BEHIND the drills — a new send site must not ship unguarded.

    This is a static check and therefore weaker evidence than the drills above;
    it exists so that adding a ninth egress site fails a test instead of
    relying on someone remembering. Issue #29's Layer 2 sat inert for 891
    commits precisely because nobody drilled it, so this file leads with
    planted violations and keeps the static sweep as a backstop.
    """

    SRC = Path(__file__).resolve().parents[1] / "src"

    # Methods on a meshtastic interface object that put bytes on the air.
    EGRESS_METHODS = {"sendText", "sendData", "sendPosition"}

    def test_every_interface_send_has_a_guard_within_10_lines(self):
        import ast

        offenders = []
        for path in self.SRC.rglob("*.py"):
            if path.name == "tx_guard.py":
                continue
            src = path.read_text(errors="ignore")
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            lines = src.splitlines()
            for node in ast.walk(tree):
                # AST, not grep: a docstring that MENTIONS sendText() is not a
                # send, and a sweep that cannot tell the difference produces
                # noise that trains people to ignore it.
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                if not isinstance(fn, ast.Attribute) or fn.attr not in self.EGRESS_METHODS:
                    continue
                i = node.lineno - 1
                window = "\n".join(lines[max(0, i - 10):i + 1])
                if "assert_iface_tx_allowed" in window or "assert_tx_allowed" in window:
                    continue
                offenders.append(
                    f"{path.relative_to(self.SRC)}:{node.lineno}: {lines[i].strip()}"
                )
        assert not offenders, (
            "unguarded RF egress site(s) — every send must consult "
            "utils.tx_guard first, or the suite can key the operator's radio "
            "(2026-08-09):\n  " + "\n  ".join(offenders)
        )

    def test_every_cli_transmit_flag_has_a_guard_within_15_lines(self):
        """The subprocess half of the surface.

        ``meshtastic --host X --sendtext`` transmits from a CHILD process, so
        the socket tripwire in tests/conftest.py is structurally blind to it —
        this sweep and the call-site guards are the only cover. Includes the
        EMCOMM handlers, where an unguarded test would key a real emergency
        beacon.
        """
        offenders = []
        for path in self.SRC.rglob("*.py"):
            if path.name == "tx_guard.py":
                continue
            lines = path.read_text(errors="ignore").splitlines()
            for i, line in enumerate(lines):
                if not any(f"'{f}'" in line or f'"{f}"' in line
                           for f in tx_guard.TRANSMITTING_CLI_FLAGS):
                    continue
                window = "\n".join(lines[max(0, i - 15):i + 16])
                if "assert_tx_allowed" in window:
                    continue
                # Covered transitively: the flag is handed to a shared runner
                # that guards on TRANSMITTING_CLI_FLAGS. That indirection is
                # the BETTER pattern (a new --traceroute caller is covered the
                # day it is written), so the sweep must not punish it — and
                # test_guarded_runners_still_guard below pins those runners so
                # this allowance cannot be silently defeated.
                if "_run_command(" in window or "self.run(" in window:
                    continue
                offenders.append(f"{path.relative_to(self.SRC)}:{i + 1}: {line.strip()}")
        assert not offenders, (
            "unguarded meshtastic CLI transmit site(s) — subprocess egress is "
            "invisible to the socket tripwire, so the call-site guard is the "
            "only cover:\n  " + "\n  ".join(offenders)
        )


class TestTransmittingFlagSet:
    """The flag set is a CLOSED ENUM with an open consumer.

    ``assert_cli_args_allowed`` only guards flags it knows about, so a new
    transmitting flag is invisible until listed. Pinning the set here means a
    change to it is a deliberate, reviewed act rather than a silent widening
    or narrowing.
    """

    def test_known_transmitting_flags_are_pinned(self):
        assert tx_guard.TRANSMITTING_CLI_FLAGS == frozenset({
            "--sendtext", "--sendping", "--traceroute",
            "--request-position", "--reply",
        })

    def test_config_flags_are_not_guarded(self):
        """Narrowness drill: --info / --set touch the device but do not key
        the radio. A guard that fires on everything gets switched off."""
        tx_guard.assert_cli_args_allowed(["--info"])
        tx_guard.assert_cli_args_allowed(["--set", "lora.region", "US"])
        tx_guard.assert_cli_args_allowed(["--export-config"])

    def test_each_transmitting_flag_is_refused(self):
        for flag in tx_guard.TRANSMITTING_CLI_FLAGS:
            with pytest.raises(TransmitBlocked):
                tx_guard.assert_cli_args_allowed([flag, "x"], "localhost")


class TestGuardedRunnersStillGuard:
    """Pin the indirection the CLI sweep relies on.

    ``test_every_cli_transmit_flag_has_a_guard_within_15_lines`` forgives a
    call site that hands its flags to a shared runner. That forgiveness is
    only sound while those runners actually guard — otherwise deleting one
    line would silently un-cover every caller at once.
    """

    RUNNERS = (
        ("commands/meshtastic.py", "def _run_command("),
        ("cli/meshtastic_cli.py", "def _run_command("),
        ("cli/meshtastic_cli.py", "def _run_command_interactive("),
        ("core/meshtastic_cli.py", "def run("),
    )

    def test_guarded_runners_still_guard(self):
        src = Path(__file__).resolve().parents[1] / "src"
        for rel, sig in self.RUNNERS:
            text = (src / rel).read_text()
            start = text.index(sig)
            body = text[start:start + 2000]
            assert "assert_cli_args_allowed" in body, (
                f"{rel} {sig} no longer guards on TRANSMITTING_CLI_FLAGS — "
                "every caller that relies on this runner is now unguarded"
            )
