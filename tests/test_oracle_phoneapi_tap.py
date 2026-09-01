"""Tests for the read-only PhoneAPI oracle tap (multi-hop oracle visibility).

The tap reads meshtasticd's :4403 ``meshtastic.receive`` pubsub (EVERY decoded
packet, multi-hop included) so the oracle answers nodes the MQTT-json uplink
never carries. These tests exercise the gating + the receive path with fakes;
no real :4403 connection is opened.
"""
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from gateway.oracle_phoneapi_tap import OraclePhoneAPITap


def _cfg():
    return SimpleNamespace(meshtastic=SimpleNamespace(
        host="localhost", port=4403, http_port=9443, channel=2))


def _disabled_tap(monkeypatch):
    # Build with the tap OFF (so __init__ never touches :4403), then the caller
    # injects a fake oracle for the receive-path tests.
    monkeypatch.delenv("MESHFORGE_ORACLE_PHONEAPI_TAP", raising=False)
    return OraclePhoneAPITap(_cfg(), threading.Event())


def test_tap_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MESHFORGE_ORACLE_ENABLED", raising=False)
    monkeypatch.delenv("MESHFORGE_ORACLE_PHONEAPI_TAP", raising=False)
    tap = OraclePhoneAPITap(_cfg(), threading.Event())
    assert tap.enabled is False
    assert tap._oracle is None


def test_tap_requires_both_flags(monkeypatch):
    # ENABLED alone is not enough — the tap is a deliberate opt-in (it holds :4403)
    monkeypatch.setenv("MESHFORGE_ORACLE_ENABLED", "1")
    monkeypatch.delenv("MESHFORGE_ORACLE_PHONEAPI_TAP", raising=False)
    assert OraclePhoneAPITap(_cfg(), threading.Event()).enabled is False
    # TAP alone (no ENABLED) is not enough either
    monkeypatch.delenv("MESHFORGE_ORACLE_ENABLED", raising=False)
    monkeypatch.setenv("MESHFORGE_ORACLE_PHONEAPI_TAP", "1")
    assert OraclePhoneAPITap(_cfg(), threading.Event()).enabled is False


def test_tap_enabled_with_both_flags(monkeypatch):
    monkeypatch.setenv("MESHFORGE_ORACLE_ENABLED", "1")
    monkeypatch.setenv("MESHFORGE_ORACLE_PHONEAPI_TAP", "1")
    monkeypatch.delenv("MESHFORGE_ORACLE_CHANNELS", raising=False)  # no :4403 query
    monkeypatch.setenv("MESHFORGE_ORACLE_ALLOWLIST", "*")
    tap = OraclePhoneAPITap(_cfg(), threading.Event())
    assert tap.enabled is True


def test_run_loop_inert_when_disabled(monkeypatch):
    tap = _disabled_tap(monkeypatch)
    # run_loop must return immediately (no connect / no hang) when disabled.
    tap.run_loop()


def test_on_receive_runs_oracle_on_text(monkeypatch):
    tap = _disabled_tap(monkeypatch)
    tap._oracle = MagicMock()
    packet = {'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'payload': b'status'},
              'fromId': '!b29faa24', 'channel': 2}
    tap._on_receive(packet)
    # the multi-hop sender's id, decoded text, and inbound channel index
    tap._oracle.handle.assert_called_once_with('!b29faa24', 'status', 2)


def test_on_receive_decodes_str_payload(monkeypatch):
    tap = _disabled_tap(monkeypatch)
    tap._oracle = MagicMock()
    tap._on_receive({'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'payload': 'help'},
                     'fromId': '!y', 'channel': 0})
    tap._oracle.handle.assert_called_once_with('!y', 'help', 0)


def test_on_receive_ignores_non_text(monkeypatch):
    tap = _disabled_tap(monkeypatch)
    tap._oracle = MagicMock()
    tap._on_receive({'decoded': {'portnum': 'POSITION_APP', 'payload': b'x'},
                     'fromId': '!x', 'channel': 0})
    tap._oracle.handle.assert_not_called()


def test_on_receive_oracle_none_is_noop(monkeypatch):
    tap = _disabled_tap(monkeypatch)
    tap._oracle = None
    # No crash, no call — inert.
    tap._on_receive({'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'payload': b'status'},
                     'fromId': '!z', 'channel': 0})


def test_on_receive_skips_directly_heard_packet(monkeypatch):
    # hops_away == 0 (hopStart == hopLimit): the MQTT-json leg already answers
    # directly-heard packets, so the tap skips it to avoid a double-broadcast on
    # a box running both legs.
    tap = _disabled_tap(monkeypatch)
    tap._oracle = MagicMock()
    tap._on_receive({'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'payload': b'status'},
                     'fromId': '!direct', 'channel': 2,
                     'hopStart': 3, 'hopLimit': 3})
    tap._oracle.handle.assert_not_called()


def test_on_receive_processes_multihop_packet(monkeypatch):
    # hops_away > 0 (hopLimit < hopStart): a relayed node the MQTT-json leg never
    # carries — this is exactly what the tap exists for.
    tap = _disabled_tap(monkeypatch)
    tap._oracle = MagicMock()
    tap._on_receive({'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'payload': b'status'},
                     'fromId': '!relayed', 'channel': 2,
                     'hopStart': 3, 'hopLimit': 1})
    tap._oracle.handle.assert_called_once_with('!relayed', 'status', 2)


def test_on_receive_processes_when_hops_unknown(monkeypatch):
    # No hopStart (or hopStart==0) is UNKNOWN, not provably direct — process it
    # rather than risk dropping a multi-hop query (honest-failure-modes #2).
    tap = _disabled_tap(monkeypatch)
    tap._oracle = MagicMock()
    tap._on_receive({'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'payload': b'status'},
                     'fromId': '!unknown', 'channel': 2, 'hopLimit': 3})
    tap._oracle.handle.assert_called_once_with('!unknown', 'status', 2)
    tap._oracle.handle.reset_mock()
    # hopStart present but 0 — also unknown, also processed.
    tap._on_receive({'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'payload': b'status'},
                     'fromId': '!unknown', 'channel': 2, 'hopStart': 0, 'hopLimit': 0})
    tap._oracle.handle.assert_called_once_with('!unknown', 'status', 2)


def test_on_receive_swallows_malformed_packet(monkeypatch):
    tap = _disabled_tap(monkeypatch)
    tap._oracle = MagicMock()
    tap._on_receive("not a dict")          # must not raise
    tap._on_receive({})                    # no decoded
    tap._on_receive({'decoded': None})     # decoded None
    tap._oracle.handle.assert_not_called()



class TestTapSenderDerivation:
    """2026-09-01: two moc3 audit records carried from='' — the tap passed
    packet['fromId'] which the library fills only for nodes already in its
    nodedb. The numeric `from` is always present; the responder canonicalizes
    it. A packet with neither is skipped, never answered under key '!'."""

    def _tap(self):
        from gateway.oracle_phoneapi_tap import OraclePhoneAPITap
        from unittest.mock import MagicMock
        tap = OraclePhoneAPITap.__new__(OraclePhoneAPITap)
        tap._oracle = MagicMock()
        return tap

    def _packet(self, **kw):
        p = {"decoded": {"portnum": "TEXT_MESSAGE_APP", "payload": b"status"},
             "channel": 2, "hopStart": 3, "hopLimit": 1}
        p.update(kw)
        return p

    def test_numeric_from_is_used_when_fromid_is_absent(self):
        tap = self._tap()
        tap._on_receive(self._packet(fromId=None, **{"from": 3062965521}))
        tap._oracle.handle.assert_called_once_with(3062965521, "status", 2)

    def test_fromid_wins_when_present(self):
        tap = self._tap()
        tap._on_receive(self._packet(fromId="!b6903d11", **{"from": 3062965521}))
        tap._oracle.handle.assert_called_once_with("!b6903d11", "status", 2)

    def test_senderless_packet_is_not_answered(self):
        tap = self._tap()
        tap._on_receive(self._packet(fromId=None))
        tap._oracle.handle.assert_not_called()
