"""Tests for src/lab/lxmf_tracer.py — sender / ACK matcher.

Unit-only. The end-to-end run_trace() requires RNS, so we cover its
component pieces (peer parsing, ACK matching) and reserve integration
testing for live-fleet smoke (box-a self-loopback).
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# --------------------------------------------------------- peer file parsing


def test_parse_peers_file_basic():
    from lab.lxmf_tracer import parse_peers_file

    text = """
        # Lab peers — one per line
        box-a=0123456789abcdef0123456789abcdef
        moc=fedcba9876543210fedcba9876543210
    """
    peers = parse_peers_file(text)
    names = [p.name for p in peers]
    assert names == ["box-a", "moc"]
    assert peers[0].dest_hash == bytes.fromhex(
        "0123456789abcdef0123456789abcdef"
    )


def test_parse_peers_file_preserves_order():
    """Operator can put the slow link last; tracer must respect order."""
    from lab.lxmf_tracer import parse_peers_file

    text = (
        "moc3=" + "11" * 16 + "\n"
        "moc=" + "22" * 16 + "\n"
        "moc1=" + "33" * 16 + "\n"
    )
    peers = parse_peers_file(text)
    assert [p.name for p in peers] == ["moc3", "moc", "moc1"]


def test_parse_peers_file_skips_invalid_lines():
    from lab.lxmf_tracer import parse_peers_file

    text = (
        "good=" + "ab" * 16 + "\n"
        "bare-line-no-equals\n"
        "noname=" + "cd" * 16 + "\n"  # ok actually
        "badhash=not-hex\n"
        "shorthash=abcd\n"
        "=" + "ef" * 16 + "\n"        # empty name
        "# only=ignored=comment\n"
    )
    peers = parse_peers_file(text)
    names = [p.name for p in peers]
    assert "good" in names
    assert "noname" in names
    assert "badhash" not in names
    assert "shorthash" not in names
    assert "" not in names


def test_parse_peers_file_handles_inline_comments():
    from lab.lxmf_tracer import parse_peers_file

    text = "moc=" + "aa" * 16 + "   # canonical gateway\n"
    peers = parse_peers_file(text)
    assert len(peers) == 1
    assert peers[0].name == "moc"


def test_parse_peers_file_normalizes_hash_case():
    from lab.lxmf_tracer import parse_peers_file

    text = "MOC=AABBCCDDEEFF00112233445566778899\n"
    peers = parse_peers_file(text)
    assert len(peers) == 1
    assert peers[0].hash_hex == "aabbccddeeff00112233445566778899"


# ----------------------------------------------------------- ACK matching


def _pending(seq, name="peer"):
    from lab.lxmf_tracer import _PendingPing

    p = _PendingPing(
        seq=seq, peer_name=name,
        sent_at_monotonic=time.monotonic(),
    )
    return p


def test_match_ack_to_pending_happy_path():
    from lab.lxmf_tracer import match_ack_to_pending

    p1 = _pending(7, "moc")
    pending = {7: p1}

    body = "ACK seq=7 orig=box-a recv_at=2026-05-12T03:00:00Z"
    match = match_ack_to_pending(body, "box-a", pending)
    assert match is not None
    seq, returned = match
    assert seq == 7
    assert returned is p1


def test_match_ack_ignores_other_origin():
    """An ACK destined for a different tracer instance must not match."""
    from lab.lxmf_tracer import match_ack_to_pending

    pending = {3: _pending(3, "peer")}
    body = "ACK seq=3 orig=other-box recv_at=2026-05-12T03:00:00Z"
    assert match_ack_to_pending(body, "box-a", pending) is None


def test_match_ack_ignores_unknown_seq():
    """Late ACK after the seq has been timed out and pruned must not match."""
    from lab.lxmf_tracer import match_ack_to_pending

    pending = {3: _pending(3)}
    body = "ACK seq=99 orig=box-a recv_at=2026-05-12T03:00:00Z"
    assert match_ack_to_pending(body, "box-a", pending) is None


def test_match_ack_handles_out_of_order_seq():
    """Two PINGs out, ACK for the later one arrives first — must match correctly."""
    from lab.lxmf_tracer import match_ack_to_pending

    p1 = _pending(1, "a")
    p2 = _pending(2, "b")
    pending = {1: p1, 2: p2}

    # ACK for seq=2 arrives while seq=1 is still pending.
    body2 = "ACK seq=2 orig=box-a recv_at=2026-05-12T03:00:01Z"
    match = match_ack_to_pending(body2, "box-a", pending)
    assert match is not None
    assert match[0] == 2
    assert match[1] is p2

    # Then seq=1 arrives.
    body1 = "ACK seq=1 orig=box-a recv_at=2026-05-12T03:00:02Z"
    match = match_ack_to_pending(body1, "box-a", pending)
    assert match is not None
    assert match[0] == 1
    assert match[1] is p1


def test_match_ack_rejects_garbage_body():
    from lab.lxmf_tracer import match_ack_to_pending

    pending = {1: _pending(1)}
    assert match_ack_to_pending("hello", "box-a", pending) is None
    assert match_ack_to_pending("", "box-a", pending) is None
    assert match_ack_to_pending(
        "PING seq=1 from=moc", "box-a", pending,
    ) is None


# --------------------------------------------- _PendingPing event signalling


def test_pending_ping_event_is_thread_safe():
    """RTT measurement uses Event for cross-thread signaling — verify shape."""
    from lab.lxmf_tracer import _PendingPing

    p = _PendingPing(seq=1, peer_name="x", sent_at_monotonic=time.monotonic())
    assert not p.ack_event.is_set()

    def _signal_later():
        time.sleep(0.05)
        p.rtt_ms = 42
        p.ack_event.set()

    t = threading.Thread(target=_signal_later)
    t.start()
    assert p.ack_event.wait(1.0) is True
    t.join()
    assert p.rtt_ms == 42


# --------------------------------------- load_peers reads from default path


def test_load_peers_returns_empty_when_file_absent(tmp_path):
    from lab.lxmf_tracer import load_peers

    missing = tmp_path / "nonexistent_peers"
    assert load_peers(missing) == []


def test_load_peers_reads_from_explicit_path(tmp_path):
    from lab.lxmf_tracer import load_peers

    peers_file = tmp_path / "peers"
    peers_file.write_text("moc=" + "ab" * 16 + "\n")
    peers = load_peers(peers_file)
    assert len(peers) == 1
    assert peers[0].name == "moc"
