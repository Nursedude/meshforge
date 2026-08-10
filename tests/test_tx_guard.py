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
    """Each drill starts from a bare allowlist and empty records."""
    tx_guard.clear_allowed_targets()
    tx_guard.clear_blocked_attempts()
    tx_guard.clear_probe_attempts()
    yield
    tx_guard.clear_allowed_targets()
    tx_guard.clear_blocked_attempts()
    tx_guard.clear_probe_attempts()


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

    def test_env_armed_drill_does_not_sever_rns_attach(self, monkeypatch):
        """MESHFORGE_TX_GUARD=1 on a live gateway is the documented drill: the
        SEND sites must refuse loudly, but the RNS attach backstop is test
        isolation and must not turn the drill into a silent RNS outage
        (open_reticulum returning None forever while the log blames pytest —
        2026-08-09 review finding)."""
        monkeypatch.setenv("MESHFORGE_TX_GUARD", "1")
        monkeypatch.setattr(tx_guard, "_under_pytest", lambda: False)
        assert tx_guard.is_armed()
        assert tx_guard.rns_attach_allowed(), (
            "an env-armed production process must keep its RNS attach"
        )
        # ... while under pytest the backstop still holds:
        monkeypatch.setattr(tx_guard, "_under_pytest", lambda: True)
        assert not tx_guard.rns_attach_allowed()
        with tx_guard.allow_rns_egress():
            assert tx_guard.rns_attach_allowed()

    @pytest.mark.parametrize("value", [
        "0", "false", "False", "FALSE", "no", "NO", "No",
        "off", "OFF", "Off", "disabled", "DISABLED", "none", "null", " off ",
    ])
    def test_every_off_shaped_value_disarms(self, monkeypatch, value):
        """On a LIVE box ARMED is the dangerous state — a blocked send is a
        silent mesh outage — and this variable exists for the deliberate
        disarm someone performs standing at a radio. Seven of these armed
        before 2026-08-09 ('FALSE', 'NO', 'off', 'OFF', 'disabled', 'none',
        'null'): the operator's intent read as unrecognised, and unrecognised
        arms. Case and spelling must not decide whether the mesh goes quiet.
        """
        monkeypatch.setenv("MESHFORGE_TX_GUARD", value)
        assert not tx_guard.is_armed(), f"{value!r} reads as OFF but ARMED"

    @pytest.mark.parametrize("value", ["", " ", "\t"])
    def test_empty_value_is_nobodys_decision(self, monkeypatch, value):
        """An empty/whitespace value is what a shell typo
        (``MESHFORGE_TX_GUARD= pytest``) or an undefined CI variable produces
        — nobody DECIDED anything. It must fall through to pytest detection,
        not disarm: '' in the disarm set silently switched the whole stack
        off with no witness (2026-08-09 review finding). This test runs under
        pytest, so falling through means ARMED right here.
        """
        monkeypatch.setenv("MESHFORGE_TX_GUARD", value)
        assert tx_guard.is_armed(), (
            f"{value!r} read as an explicit disarm — an empty env value must "
            f"fall through to pytest detection"
        )

    @pytest.mark.parametrize("value", [
        "1", "true", "yes", "on", "armed", "please", "0x0", "00", "-0",
    ])
    def test_unrecognised_value_still_arms(self, monkeypatch, value):
        """The other direction, pinned so the disarm set cannot quietly widen:
        anything NOT plainly meaning off must fail CLOSED. '00' and '-0' are
        deliberate — they are not the documented '0', so they are unknown, and
        an unknown must never be read as permission to transmit.
        """
        monkeypatch.setenv("MESHFORGE_TX_GUARD", value)
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        assert tx_guard.is_armed(), f"{value!r} is not 'off' but DISARMED"


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

    def test_overlapping_windows_do_not_corrupt_each_other(self):
        """Pri-2 leg (e), 2026-08-10. The allowlist is process-global and the
        suite runs threads, so two grants' lifetimes can overlap WITHOUT
        nesting. The old snapshot/restore semantics corrupted exactly there:
        A-exit restored its pre-A snapshot, revoking still-active B; B-exit
        then restored its pre-B snapshot, RESURRECTING closed A — a test's
        permission leaking into another test's window, in both directions.
        (No threads needed to pin it: the corruption is the non-LIFO
        interleaving itself; threads only make it nondeterministic.)"""
        a = tx_guard.allow_targets("127.0.0.1:8123")
        b = tx_guard.allow_targets("127.0.0.1:9123")
        a.__enter__()
        b.__enter__()
        a.__exit__(None, None, None)
        # B is still open: its grant must survive A's exit...
        assert_tx_allowed("127.0.0.1", 9123, kind="drill")
        # ...and A's grant must be gone the moment A closed.
        with pytest.raises(TransmitBlocked):
            assert_tx_allowed("127.0.0.1", 8123, kind="drill")
        b.__exit__(None, None, None)
        # After B closes nothing may remain — A must not resurrect.
        with pytest.raises(TransmitBlocked):
            assert_tx_allowed("127.0.0.1", 9123, kind="drill")
        with pytest.raises(TransmitBlocked):
            assert_tx_allowed("127.0.0.1", 8123, kind="drill")

    def test_overlapping_coarse_gate_windows_do_not_corrupt(self):
        """Same leg-e class on the RNS/MeshCore coarse gates: their old
        saved-boolean restore meant A's exit switched the gate OFF while B
        was still open. Depth-counted now."""
        a, b = tx_guard.allow_rns_egress(), tx_guard.allow_rns_egress()
        a.__enter__()
        b.__enter__()
        a.__exit__(None, None, None)
        assert tx_guard.rns_egress_allowed(), "B is still open"
        b.__exit__(None, None, None)
        assert not tx_guard.rns_egress_allowed()

        a, b = tx_guard.allow_meshcore_egress(), tx_guard.allow_meshcore_egress()
        a.__enter__()
        b.__enter__()
        a.__exit__(None, None, None)
        assert tx_guard.meshcore_egress_allowed(), "B is still open"
        b.__exit__(None, None, None)
        assert not tx_guard.meshcore_egress_allowed()

    def test_wipe_hatch_beats_a_leaked_grant(self):
        """clear_allowed_targets is the suite-teardown wipe: it must revoke a
        grant whose context never exited (or a leak is permanent), and the
        stranded context must still exit harmlessly afterwards."""
        leaked = tx_guard.allow_targets("127.0.0.1:8123")
        leaked.__enter__()
        tx_guard.clear_allowed_targets()
        with pytest.raises(TransmitBlocked):
            assert_tx_allowed("127.0.0.1", 8123, kind="drill")
        leaked.__exit__(None, None, None)  # must not raise

    def test_env_allowlist_honored(self, monkeypatch):
        monkeypatch.setenv("MESHFORGE_TX_ALLOW", "127.0.0.1:8123")
        assert_tx_allowed("127.0.0.1", 8123, kind="drill")

    def test_mock_shaped_target_is_unresolved_and_never_allowlistable(self):
        """A config built from MagicMock must not mint a unique-per-run
        target (the refusal's own advice would be impossible to follow) — and
        it must not collapse to the LOCAL RADIO either: 127.0.0.1:4403 is the
        single most-allowlisted target in the tree, so collapsing meant an
        e2e harness blessing its mock daemon also blessed every send whose
        destination the guard could not even read (2026-08-09 review finding).
        Unknown → UNRESOLVED_TARGET, refused no matter what is allowlisted."""
        from unittest.mock import MagicMock
        assert tx_guard.normalize_target(MagicMock(), MagicMock()) == tx_guard.UNRESOLVED_TARGET
        with tx_guard.allow_targets("127.0.0.1:4403"):
            with pytest.raises(TransmitBlocked):
                assert_tx_allowed(MagicMock(), MagicMock(), kind="drill")

    def test_none_host_is_the_local_radio_not_unresolved(self):
        """None stays distinct from garbage: it is the documented 'local
        radio' default real call sites pass deliberately (meshtastic CLI with
        no --host keys localhost), so it must remain allowlistable."""
        assert tx_guard.normalize_target(None, None) == "127.0.0.1:4403"
        with tx_guard.allow_targets("127.0.0.1:4403"):
            assert_tx_allowed(None, None, kind="drill")

    def test_typoed_allowlist_port_raises_instead_of_minting_4403(self):
        """honest_failure_modes #3: the old parse resolved a non-numeric port
        to the DEFAULT — 4403, the operator's real radio. A typo must never
        allowlist the one target this guard exists to protect."""
        with pytest.raises(ValueError):
            tx_guard.allow_targets("127.0.0.1:9x43")
        with pytest.raises(ValueError):
            tx_guard.set_allowed_targets(["127.0.0.1:9x43"])

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
            # _publish's contract is "never raises, callers fall back", so its
            # guard catch is DELIBERATE (see tx_guard docstring): a refusal
            # returns False — and MUST leave the recorded witness, or the
            # catch would be the silent swallow the design bans.
            assert inj._publish("text", 0x1234, lambda: ("t", b"p", 1)) is False
        recs = tx_guard.blocked_attempts()
        assert recs and recs[-1]["kind"] == "mqtt_downlink_inject"


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

    def test_connect_ex_probe_idiom_is_permitted_and_recorded(self):
        """`connect_ex` returns an errno instead of raising — that IS the
        reachability-probe idiom, used by ~15 sites here to ask "is the radio
        port open?". Blocking it turned benign probes into failures (CI caught
        `fleet_snapshot._probe_radio`) while catching nothing: TCPInterface
        reaches the radio via create_connection -> connect, still blocked.

        Permitted-but-RECORDED (leg-b audit 2026-08-10): the permission must
        leave a witness or it cannot be audited. And the probe socket is
        CLOSED — this test's own earlier version left a connected PhoneAPI
        socket dangling on a live box, the exact single-consumer contention
        three production sites were refactored to stop causing."""
        tx_guard.clear_probe_attempts()
        s = socket.socket()
        try:
            rc = s.connect_ex(("127.0.0.1", 4403))
        finally:
            s.close()
        probes = tx_guard.probe_attempts()
        assert probes and probes[-1]["target"] == "127.0.0.1:4403"
        assert probes[-1]["kind"] == "connect_ex"
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
    # sendTraceRoute/sendWaypoint/sendTelemetry/sendAlert are genuine on-air
    # TX in meshtastic 2.7.9 and were missing until the 2026-08-09 review
    # drilled the sweep with planted violations (closed-enum hazard,
    # honest_failure_modes #7 — the sweep is itself a consumer of this enum).
    EGRESS_METHODS = {
        "sendText", "sendData", "sendPosition",
        "sendTraceRoute", "sendWaypoint", "sendTelemetry", "sendAlert",
    }

    @staticmethod
    def _code_only(lines):
        """Strip ``#`` comments so a COMMENT mentioning a guard or a shared
        runner cannot exempt an unguarded transmit site (drilled 2026-08-09:
        a nearby comment containing ``_run_command(`` silently exempted a
        planted violation)."""
        return [l.split("#", 1)[0] for l in lines]

    def test_every_interface_send_has_a_guard_before_it(self):
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
            lines = self._code_only(src.splitlines())
            raw = src.splitlines()
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
                # BEFORE the send only: a guard placed after the transmit has
                # already lost (drilled 2026-08-09 — the old ±window accepted
                # transmit-then-refuse ordering).
                window = "\n".join(lines[max(0, i - 10):i + 1])
                if "assert_iface_tx_allowed" in window or "assert_tx_allowed" in window:
                    continue
                offenders.append(
                    f"{path.relative_to(self.SRC)}:{node.lineno}: {raw[i].strip()}"
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
            raw = path.read_text(errors="ignore").splitlines()
            # Comments are stripped BEFORE matching, in both directions: a
            # comment mentioning a flag must not create noise, and a comment
            # mentioning a guard or shared runner must not exempt a real
            # transmit site (drilled 2026-08-09).
            lines = TestGuardIsWiredAtEverySendSite._code_only(raw)
            for i, line in enumerate(lines):
                # Match the flag itself AND its ``=``-form f-string spelling
                # (f"--sendtext={msg}") — the latter was invisible to the
                # quote-flanked match while being a real transmit.
                if not any(f"'{f}'" in line or f'"{f}"' in line or f"{f}=" in line
                           for f in tx_guard.TRANSMITTING_CLI_FLAGS):
                    continue
                # Guard must come BEFORE the flag line — a refusal after the
                # subprocess has spawned is a record of a transmission, not a
                # prevention of one.
                before = "\n".join(lines[max(0, i - 15):i + 1])
                if "assert_tx_allowed" in before or "assert_cli_args_allowed" in before:
                    continue
                # Covered transitively: the flag is handed to a shared runner
                # that guards on TRANSMITTING_CLI_FLAGS. That indirection is
                # the BETTER pattern (a new --traceroute caller is covered the
                # day it is written), so the sweep must not punish it — and
                # test_guarded_runners_still_guard below pins those runners so
                # this allowance cannot be silently defeated. The runner CALL
                # legitimately sits after the args build, so this check keeps
                # the forward window.
                window = "\n".join(lines[max(0, i - 15):i + 16])
                if "_run_command(" in window or "self.run(" in window:
                    continue
                offenders.append(f"{path.relative_to(self.SRC)}:{i + 1}: {raw[i].strip()}")
        assert not offenders, (
            "unguarded meshtastic CLI transmit site(s) — subprocess egress is "
            "invisible to the socket tripwire, so the call-site guard is the "
            "only cover:\n  " + "\n  ".join(offenders)
        )


class TestMeshCoreEgress:
    """The companion radio is a REAL LoRa radio (MeshAnchor's primary type),
    found entirely outside the egress architecture on 2026-08-09: no refusal,
    no record, no sweep coverage. Serial is invisible to the socket tripwire,
    so the gate + this sweep are the only cover."""

    # send_chan_msg is the meshcore_py channel-broadcast verb MeshAnchor's
    # diverged handler actually calls (found during the MA port — a sweep
    # that only knew MF's method names would have read MA's primary TX path
    # as guard-free and said nothing).
    MESHCORE_SEND_METHODS = {"send_msg", "send_channel_txt_msg", "send_chan_msg"}

    def test_refused_when_not_allowlisted(self):
        with pytest.raises(TransmitBlocked):
            tx_guard.assert_meshcore_tx_allowed(kind="meshcore_tx")
        recs = tx_guard.blocked_attempts()
        assert recs and recs[-1]["target"] == tx_guard.MESHCORE_TARGET

    def test_allowlisted_send_passes(self):
        with tx_guard.allow_meshcore_egress():
            tx_guard.assert_meshcore_tx_allowed(kind="meshcore_tx")

    def test_attach_blocked_under_pytest_only(self, monkeypatch):
        """Same shape as the RNS attach backstop: test isolation, not part
        of the env-armed drill surface — MESHFORGE_TX_GUARD=1 on a live
        gateway must not sever the companion radio."""
        assert not tx_guard.meshcore_attach_allowed()
        with tx_guard.allow_meshcore_egress():
            assert tx_guard.meshcore_attach_allowed()
        monkeypatch.setenv("MESHFORGE_TX_GUARD", "1")
        monkeypatch.setattr(tx_guard, "_under_pytest", lambda: False)
        assert tx_guard.meshcore_attach_allowed()

    def test_every_meshcore_send_site_is_guarded(self):
        """Function-level sweep: any function calling a MeshCore send method
        must contain assert_meshcore_tx_allowed. Function-level rather than
        line-window because the handler funnels all sends through one
        chokepoint whose branches span more than a window."""
        import ast

        src_root = Path(__file__).resolve().parents[1] / "src"
        offenders = []
        for path in src_root.rglob("*.py"):
            if path.name == "tx_guard.py":
                continue
            src = path.read_text(errors="ignore")
            if not any(m in src for m in self.MESHCORE_SEND_METHODS):
                continue
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                calls = [
                    n for n in ast.walk(fn)
                    if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr in self.MESHCORE_SEND_METHODS
                ]
                if not calls:
                    continue
                segment = ast.get_source_segment(src, fn) or ""
                if "assert_meshcore_tx_allowed" in segment:
                    continue
                offenders.append(
                    f"{path.relative_to(src_root)}:{fn.lineno} {fn.name}()"
                )
        assert not offenders, (
            "MeshCore send call(s) in function(s) with no "
            "assert_meshcore_tx_allowed — the companion radio is real RF and "
            "serial is invisible to the socket tripwire (2026-08-09):\n  "
            + "\n  ".join(offenders)
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
            "--request-position", "--request-telemetry", "--reply",
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

    def test_equals_form_is_refused(self):
        """Live-drilled 2026-08-09 against meshtastic 2.7.9:
        ``--sendtext=hello`` parses and transmits, and the exact-token match
        let it straight through the guard."""
        for flag in tx_guard.TRANSMITTING_CLI_FLAGS:
            with pytest.raises(TransmitBlocked):
                tx_guard.assert_cli_args_allowed([f"{flag}=x"], "localhost")

    def test_argparse_abbreviations_are_refused(self):
        """argparse allow_abbrev accepts any unambiguous prefix of a long
        option, so ``--sendt hello`` transmits too (drilled 2.7.9). Any
        prefix of a transmitting flag is refused — an AMBIGUOUS prefix makes
        argparse error without transmitting, so over-matching costs nothing,
        while under-matching is an unguarded send."""
        with pytest.raises(TransmitBlocked):
            tx_guard.assert_cli_args_allowed(["--sendt", "x"], "localhost")
        with pytest.raises(TransmitBlocked):
            tx_guard.assert_cli_args_allowed(["--sendt=x"], "localhost")
        with pytest.raises(TransmitBlocked):
            tx_guard.assert_cli_args_allowed(["--tracer", "!abcd"], "localhost")

    def test_non_flag_prefixes_still_pass(self):
        """Narrowness: values and unrelated flags must not trip the prefix
        match, or the guard fires on config reads and gets switched off."""
        tx_guard.assert_cli_args_allowed(["--set", "lora.region", "US"])
        tx_guard.assert_cli_args_allowed(["--seturl", "https://x"])
        tx_guard.assert_cli_args_allowed(["--reboot"])
        tx_guard.assert_cli_args_allowed(["--export-config"])


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


class TestRnsEgress:
    """RNS/LXMF egress — added 2026-08-09 at the operator's direction.

    Bigger blast radius than the Meshtastic leak, not smaller: an announce
    floods the whole Reticulum network the process is attached to, which on
    this fleet reaches public transport nodes AND a physical RNode on LoRa RF.
    And the suite was measured attaching to the operator's LIVE rnsd already.

    Coarse on/off by design — an announce has no per-destination target to
    allowlist, so pretending to offer granularity would be a fiction.
    """

    def test_rns_egress_refused_by_default(self):
        with pytest.raises(TransmitBlocked):
            tx_guard.assert_rns_tx_allowed(kind="rns_announce", detail="drill")

    def test_rns_egress_allowed_when_declared(self):
        with tx_guard.allow_rns_egress():
            tx_guard.assert_rns_tx_allowed(kind="rns_announce", detail="drill")

    def test_rns_declaration_does_not_leak(self):
        with tx_guard.allow_rns_egress():
            pass
        with pytest.raises(TransmitBlocked):
            tx_guard.assert_rns_tx_allowed(kind="rns_announce", detail="drill")

    def test_blocked_rns_attempt_is_recorded(self):
        with pytest.raises(TransmitBlocked):
            tx_guard.assert_rns_tx_allowed(kind="lxmf_outbound", detail="drill")
        recs = tx_guard.blocked_attempts()
        assert recs and recs[-1]["target"] == tx_guard.RNS_TARGET
        assert recs[-1]["kind"] == "lxmf_outbound"

    def test_gateway_announce_site_refuses(self):
        """Plant a real call at a real announce site."""
        from gateway.meshtastic_broadcast_bridge import MeshtasticBroadcastBridge
        b = MeshtasticBroadcastBridge.__new__(MeshtasticBroadcastBridge)
        b._destination_hash = b"\x01" * 16
        b._router = MagicMock()
        with pytest.raises(TransmitBlocked):
            b._safe_announce()
        b._router.announce.assert_not_called()

    def test_attach_backstop_declines_live_reticulum(self, monkeypatch):
        """open_reticulum must not hand a test a LIVE handle on the operator's
        Reticulum. Declining returns None — its own documented degraded
        outcome, and exactly what a box with no rnsd (every CI runner) gets.

        ⚠️ ``_HAS_RNS`` is pinned because without it this test's verdict
        depends on whether RNS is INSTALLED on the box running it: with RNS
        absent, ``open_reticulum`` returns None from its module-availability
        early-return BEFORE the guard is consulted, so the `is None` assert
        passes for the wrong reason and the record is never written. It passed
        on a dev box and failed in CI exactly that way (2026-08-09).
        feedback_tests_must_pin_ambient_state: ask what you would change about
        this BOX to make it fail — here, uninstall RNS.
        """
        from utils import rns_init
        monkeypatch.setattr(rns_init, "_HAS_RNS", True)
        assert not tx_guard.rns_attach_allowed()
        assert rns_init.open_reticulum("/etc/reticulum") is None
        assert any(r["kind"] == "rns_attach" for r in tx_guard.blocked_attempts())

    def test_guard_does_not_claim_a_decline_it_did_not_make(self, monkeypatch):
        """The other direction, pinned too.

        With RNS absent, None comes from the module-availability check, NOT the
        guard — and the guard must not record a decline it never made, or the
        blocked-attempt ledger (the instrument half) would overcount.
        """
        from utils import rns_init
        monkeypatch.setattr(rns_init, "_HAS_RNS", False)
        tx_guard.clear_blocked_attempts()
        assert rns_init.open_reticulum("/etc/reticulum") is None
        assert not any(r["kind"] == "rns_attach"
                       for r in tx_guard.blocked_attempts())

    def test_attach_backstop_is_off_in_production(self, monkeypatch):
        """A false ARM here would be a silent RNS outage on a gateway."""
        monkeypatch.setenv("MESHFORGE_TX_GUARD", "0")
        assert tx_guard.rns_attach_allowed()

    def test_low_level_init_raises_rather_than_returning_none(self):
        """`init_reticulum_with_watchdog`'s contract permits raising, and its
        callers (lab echo/tracer) exist to put traffic on the real network —
        so a refusal there must be LOUD, not a silent None."""
        from utils import rns_init
        with pytest.raises(TransmitBlocked):
            rns_init.init_reticulum_with_watchdog("/etc/reticulum")


class TestRnsGuardIsWiredAtEverySendSite:
    """Coverage net behind the RNS drills.

    ⚠️ AST attribute nodes, not `handle_outbound(` — the gateway's real send
    path passes the method BY REFERENCE into `bounded_call`/`call_boundary`,
    so a paren-based grep reports those sites as absent. That is exactly how a
    sweep can be green while the busiest egress path in the tree is unguarded.
    """

    SRC = Path(__file__).resolve().parents[1] / "src"
    RNS_EGRESS_ATTRS = {"announce", "handle_outbound"}

    def test_every_rns_egress_site_has_a_guard_within_12_lines(self):
        import ast

        offenders = []
        for path in self.SRC.rglob("*.py"):
            if path.name in ("tx_guard.py", "rns_init.py"):
                continue
            src = path.read_text(errors="ignore")
            if not any(a in src for a in self.RNS_EGRESS_ATTRS):
                continue
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            lines = src.splitlines()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute):
                    continue
                if node.attr not in self.RNS_EGRESS_ATTRS:
                    continue
                i = node.lineno - 1
                window = "\n".join(lines[max(0, i - 12):i + 2])
                if "assert_rns_tx_allowed" in window:
                    continue
                offenders.append(
                    f"{path.relative_to(self.SRC)}:{node.lineno}: {lines[i].strip()}"
                )
        assert not offenders, (
            "unguarded RNS/LXMF egress site(s) — an announce floods the whole "
            "Reticulum network (public transport nodes + LoRa RNode):\n  "
            + "\n  ".join(offenders)
        )
