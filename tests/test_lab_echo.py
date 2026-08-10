"""Tests for src/lab/lxmf_echo.py — echo responder.

Unit-only: RNS / LXMF are mocked. We exercise the inbound callback
factory and ACK send path; we do NOT bring up a real RNS transport.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _fake_message(content, source_hash=b"\xaa" * 16):
    """Build a stand-in for the LXMF message object the callback receives."""
    msg = MagicMock()
    msg.content = content
    msg.source_hash = source_hash
    return msg


# --------------------------------------------------- _on_receive: PING path


@pytest.fixture(autouse=True)
def _allow_rns_tx_in_this_file():
    """This whole file exercises RNS/LXMF paths against MOCKED routers, so it
    declares RNS egress once here rather than per test. The RNS egress guard
    (utils.tx_guard) is fail-closed and cannot tell a mocked router from a real
    one — declaring the intent is the point."""
    from utils import tx_guard
    with tx_guard.allow_rns_egress():
        yield


def test_on_receive_replies_ack_to_valid_ping():
    """Valid PING → state.rx_count incremented, _send_ack invoked."""
    from lab.lxmf_echo import _EchoState, make_on_receive

    state = _EchoState()
    state.router = MagicMock()
    state.source = MagicMock()

    sent: list = []

    def _fake_send_ack(s, dest, ping):
        sent.append((s, dest, ping.seq, ping.sender))
        s.tx_count += 1

    with patch("lab.lxmf_echo._send_ack", side_effect=_fake_send_ack):
        cb = make_on_receive(state)
        cb(_fake_message("PING seq=7 from=box-a"))

    assert state.rx_count == 1
    assert state.tx_count == 1
    assert len(sent) == 1
    _, dest, seq, sender = sent[0]
    assert seq == 7
    assert sender == "box-a"
    assert dest == b"\xaa" * 16


def test_on_receive_decodes_bytes_body():
    """Newer LXMF builds deliver bytes — must decode before parsing.

    Mirrors the Issue #40 bytes-content fix in the gateway path.
    """
    from lab.lxmf_echo import _EchoState, make_on_receive

    state = _EchoState()
    state.router = MagicMock()
    state.source = MagicMock()

    with patch("lab.lxmf_echo._send_ack") as fake_send:
        cb = make_on_receive(state)
        cb(_fake_message(b"PING seq=11 from=moc3"))

    assert state.rx_count == 1
    fake_send.assert_called_once()
    _, _, ping = fake_send.call_args.args
    assert ping.seq == 11
    assert ping.sender == "moc3"


def test_on_receive_ignores_non_ping_payload():
    """Random LXMF chat must not trigger an ACK or bump rx_count."""
    from lab.lxmf_echo import _EchoState, make_on_receive

    state = _EchoState()
    state.router = MagicMock()
    state.source = MagicMock()

    with patch("lab.lxmf_echo._send_ack") as fake_send:
        cb = make_on_receive(state)
        cb(_fake_message("hello, this is a chat message"))

    assert state.rx_count == 0
    assert state.tx_count == 0
    fake_send.assert_not_called()


def test_on_receive_survives_send_ack_exception():
    """A transient send failure must NOT take the daemon down.

    Echo runs for weeks during soak; one path-loss can't be fatal.
    """
    from lab.lxmf_echo import _EchoState, make_on_receive

    state = _EchoState()
    state.router = MagicMock()
    state.source = MagicMock()

    def _boom(*_a, **_kw):
        raise RuntimeError("transient path loss")

    with patch("lab.lxmf_echo._send_ack", side_effect=_boom):
        cb = make_on_receive(state)
        # Must not raise.
        cb(_fake_message("PING seq=1 from=moc"))

    # We still record the rx — the operator needs to see the imbalance.
    assert state.rx_count == 1
    assert state.tx_count == 0  # ACK never landed


def test_on_receive_handles_invalid_utf8_bytes():
    """Garbled bytes must not crash; they parse to None and get ignored."""
    from lab.lxmf_echo import _EchoState, make_on_receive

    state = _EchoState()
    state.router = MagicMock()
    state.source = MagicMock()

    with patch("lab.lxmf_echo._send_ack") as fake_send:
        cb = make_on_receive(state)
        # Truncated UTF-8 sequence — decoder falls back to 'replace'.
        cb(_fake_message(b"\xff\xfe\xfd PING"))

    fake_send.assert_not_called()
    assert state.rx_count == 0


# -------------------------------------------------------- _send_ack: routing


def test_send_ack_skips_when_recall_returns_none():
    """Unknown identity even after a path request → skip, must NOT raise.

    The routed-leaf fix (moc5, 2026-06-04) means a recall miss now
    triggers RNS.Transport.request_path before giving up — assert the
    request happens AND the skip stays graceful when it doesn't help.
    """
    from lab.lxmf_echo import _EchoState, _send_ack
    from lab._lab_common import PingMessage

    state = _EchoState()
    state.router = MagicMock()
    state.source = MagicMock()

    fake_rns = MagicMock()
    fake_rns.Identity.recall.return_value = None
    fake_lxmf = MagicMock()

    ping = PingMessage(seq=1, sender="moc")
    with patch.dict("sys.modules", {"RNS": fake_rns, "LXMF": fake_lxmf}), \
            patch("lab.lxmf_echo.PATH_REQUEST_WAIT_S", 0.0):
        # Must not raise; router.handle_outbound must NOT be called.
        _send_ack(state, b"\xaa" * 16, ping)

    fake_rns.Transport.request_path.assert_called_once_with(b"\xaa" * 16)
    state.router.handle_outbound.assert_not_called()
    assert state.tx_count == 0


# ----------------------------------------------------- _on_receive: PINGBIG (A1)


def test_on_receive_replies_bigack_to_valid_pingbig():
    """Valid PINGBIG → _send_bigack invoked (NOT the PING path), rx/tx bumped."""
    from lab.lxmf_echo import _EchoState, make_on_receive

    state = _EchoState()
    state.router = MagicMock()
    state.source = MagicMock()

    sent: list = []

    def _fake_send_bigack(s, dest, bp):
        sent.append((dest, bp.seq, bp.sender, bp.want_bytes))
        s.tx_count += 1

    with patch("lab.lxmf_echo._send_bigack", side_effect=_fake_send_bigack), \
            patch("lab.lxmf_echo._send_ack") as fake_ack:
        cb = make_on_receive(state)
        cb(_fake_message("PINGBIG seq=12 from=rescanary-moc want=512"))

    assert state.rx_count == 1
    assert state.tx_count == 1
    assert sent == [(b"\xaa" * 16, 12, "rescanary-moc", 512)]
    fake_ack.assert_not_called()  # PINGBIG must NOT go through the PING path


def test_on_receive_ping_does_not_use_bigack_path():
    """Regression: a plain PING routes to _send_ack only — the two formats
    never cross-dispatch."""
    from lab.lxmf_echo import _EchoState, make_on_receive

    state = _EchoState()
    state.router = MagicMock()
    state.source = MagicMock()

    with patch("lab.lxmf_echo._send_ack") as fake_ack, \
            patch("lab.lxmf_echo._send_bigack") as fake_big:
        cb = make_on_receive(state)
        cb(_fake_message("PING seq=5 from=box-a"))

    fake_ack.assert_called_once()
    fake_big.assert_not_called()


def test_on_receive_survives_send_bigack_exception():
    """A failed resource reply must not kill the daemon either."""
    from lab.lxmf_echo import _EchoState, make_on_receive

    state = _EchoState()
    state.router = MagicMock()
    state.source = MagicMock()

    def _boom(*_a, **_kw):
        raise RuntimeError("transient path loss")

    with patch("lab.lxmf_echo._send_bigack", side_effect=_boom):
        cb = make_on_receive(state)
        cb(_fake_message("PINGBIG seq=1 from=rescanary-moc want=512"))

    assert state.rx_count == 1
    assert state.tx_count == 0  # reply never landed


# -------------------------------------------------------- _send_bigack: routing


def test_send_bigack_skips_when_recall_returns_none():
    """Unknown identity even after a path request → skip, must NOT raise."""
    from lab.lxmf_echo import _EchoState, _send_bigack
    from lab._lab_common import BigPingMessage

    state = _EchoState()
    state.router = MagicMock()
    state.source = MagicMock()

    fake_rns = MagicMock()
    fake_rns.Identity.recall.return_value = None
    fake_lxmf = MagicMock()

    bp = BigPingMessage(seq=1, sender="rescanary-moc", want_bytes=512)
    with patch.dict("sys.modules", {"RNS": fake_rns, "LXMF": fake_lxmf}), \
            patch("lab.lxmf_echo.PATH_REQUEST_WAIT_S", 0.0):
        _send_bigack(state, b"\xaa" * 16, bp)

    fake_rns.Transport.request_path.assert_called_once_with(b"\xaa" * 16)
    state.router.handle_outbound.assert_not_called()
    assert state.tx_count == 0


def test_send_bigack_builds_resource_sized_body():
    """When the identity resolves, the echo hands the router an LXMessage whose
    body is RESOURCE-sized (>319 B) — the property that forces RNS to deliver it
    as a Resource the requester's gateway must assemble (the EROFS path)."""
    from lab.lxmf_echo import _EchoState, _send_bigack
    from lab._lab_common import BigPingMessage

    state = _EchoState()
    state.router = MagicMock()
    state.source = MagicMock()

    fake_rns = MagicMock()
    fake_rns.Identity.recall.return_value = MagicMock()  # identity found
    fake_lxmf = MagicMock()

    bp = BigPingMessage(seq=7, sender="rescanary-moc", want_bytes=512)
    with patch.dict("sys.modules", {"RNS": fake_rns, "LXMF": fake_lxmf}):
        _send_bigack(state, b"\xaa" * 16, bp)

    state.router.handle_outbound.assert_called_once()
    # LXMF.LXMessage(destination, source, body, title) — body is the 3rd arg.
    body = fake_lxmf.LXMessage.call_args.args[2]
    assert len(body) == 512 and len(body) > 319
    assert body.startswith("ACKBIG seq=7 orig=rescanary-moc len=")
    assert state.tx_count == 1


def test_send_ack_path_request_fetches_identity_routed_leaf():
    """The moc5 routed-leaf shape: recall misses (announce never reached
    this box), the on-demand path request delivers the identity, and the
    ACK goes out — instead of the silent skip that timed out every
    fleet->moc5 trace on 2026-06-04."""
    from lab.lxmf_echo import _EchoState, _send_ack
    from lab._lab_common import PingMessage

    state = _EchoState()
    state.router = MagicMock()
    state.source = MagicMock()

    fake_rns = MagicMock()
    fake_identity = MagicMock()
    # First recall misses; after request_path the identity is cached.
    fake_rns.Identity.recall.side_effect = [None, fake_identity]
    fake_lxmf = MagicMock()

    ping = PingMessage(seq=7, sender="hub-tester")
    with patch.dict("sys.modules", {"RNS": fake_rns, "LXMF": fake_lxmf}), \
            patch("lab.lxmf_echo.PATH_REQUEST_WAIT_S", 1.0):
        _send_ack(state, b"\xcc" * 16, ping)

    fake_rns.Transport.request_path.assert_called_once_with(b"\xcc" * 16)
    state.router.handle_outbound.assert_called_once()
    assert state.tx_count == 1


def test_send_ack_calls_handle_outbound_on_recall_success():
    from lab.lxmf_echo import _EchoState, _send_ack
    from lab._lab_common import PingMessage

    state = _EchoState()
    state.router = MagicMock()
    state.source = MagicMock()

    fake_rns = MagicMock()
    fake_identity = MagicMock()
    fake_rns.Identity.recall.return_value = fake_identity
    fake_lxmf = MagicMock()

    ping = PingMessage(seq=3, sender="moc1")
    with patch.dict("sys.modules", {"RNS": fake_rns, "LXMF": fake_lxmf}):
        _send_ack(state, b"\xbb" * 16, ping)

    state.router.handle_outbound.assert_called_once()
    assert state.tx_count == 1


# ------------------------------------------------------------ _EchoState wait


def test_echo_state_wait_returns_true_when_stopped():
    """Daemon main loop relies on wait() returning True after stop()."""
    from lab.lxmf_echo import _EchoState

    state = _EchoState()
    state.stop()
    # Stopped before wait is entered — must return True immediately.
    assert state.wait(0.5) is True


def test_echo_state_wait_returns_false_on_timeout():
    """If stop is never called, wait should timeout and return False."""
    from lab.lxmf_echo import _EchoState

    state = _EchoState()
    assert state.wait(0.05) is False
    assert not state.stopped
