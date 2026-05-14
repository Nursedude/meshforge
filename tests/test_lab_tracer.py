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


# ---------------------------------------------------------- state file writes


def test_write_state_file_round_trip(tmp_path):
    """One JSON object per file, single-line, newline-terminated.

    The rollup ssh-fans `cat tracer-*.json` across the fleet; the result
    is one JSONL stream. The single-line + newline shape is load-bearing.
    """
    import json
    from lab.lxmf_tracer import TraceResult, write_state_file

    results = [
        TraceResult(seq=1, peer="box-a", result="ok", rtt_ms=42),
        TraceResult(seq=2, peer="box-b", result="timeout", rtt_ms=0),
        TraceResult(seq=3, peer="box-c", result="no-route", rtt_ms=0),
    ]
    path = write_state_file(
        results, fire_at_unix=1_700_000_000.0,
        self_short="test-host", state_dir=tmp_path,
    )
    assert path is not None
    assert path.name == "tracer-1700000000.json"

    raw = path.read_text()
    assert raw.endswith("\n"), "trailing newline required for cat-concat JSONL"
    assert "\n" not in raw.rstrip("\n"), "must be single-line"

    doc = json.loads(raw)
    assert doc["schema_version"] == 1
    assert doc["self_short"] == "test-host"
    assert doc["fire_at_unix"] == 1_700_000_000.0
    assert doc["fire_at_iso"].endswith("Z")
    assert len(doc["results"]) == 3
    assert doc["results"][0] == {
        "seq": 1, "peer": "box-a", "result": "ok", "rtt_ms": 42,
    }


def test_write_state_file_creates_dir(tmp_path):
    """First fire creates the state dir; we don't expect operator to mkdir."""
    from lab.lxmf_tracer import write_state_file

    nested = tmp_path / "does" / "not" / "yet" / "exist"
    assert not nested.exists()
    path = write_state_file(
        [], fire_at_unix=1.0, self_short="x", state_dir=nested,
    )
    assert path is not None
    assert nested.is_dir()


def test_write_state_file_atomic_replace(tmp_path):
    """Write goes via tmp + os.replace — a partial file shouldn't be visible."""
    from lab.lxmf_tracer import write_state_file

    write_state_file([], fire_at_unix=1.0, self_short="x", state_dir=tmp_path)
    files = sorted(tmp_path.iterdir())
    # Only the final file should exist — no stray .tmp leftover.
    assert [p.name for p in files] == ["tracer-1.json"]


def test_prune_state_dir_removes_only_expired(tmp_path):
    """TTL is mtime-based; files newer than cutoff stay."""
    import os
    from lab.lxmf_tracer import prune_state_dir

    now = 2_000_000_000.0
    fresh = tmp_path / "tracer-1999999999.json"
    stale = tmp_path / "tracer-100.json"
    other = tmp_path / "not-a-tracer.json"
    for p in (fresh, stale, other):
        p.write_text("{}\n")
    # Force mtimes (write_text sets to current time otherwise).
    os.utime(fresh, (now - 60, now - 60))
    os.utime(stale, (now - 30 * 86400, now - 30 * 86400))
    os.utime(other, (now - 30 * 86400, now - 30 * 86400))

    deleted = prune_state_dir(tmp_path, ttl_days=7, now_unix=now)
    assert deleted == 1
    assert fresh.exists()
    assert not stale.exists()
    # Unrelated files in the dir must be untouched — only tracer-*.json
    # matches the prune glob.
    assert other.exists()


def test_prune_state_dir_handles_missing_dir(tmp_path):
    from lab.lxmf_tracer import prune_state_dir

    assert prune_state_dir(tmp_path / "nope", ttl_days=7) == 0


def test_write_state_file_returns_none_on_oserror(tmp_path, monkeypatch):
    """A write failure must not propagate — tracer is observability, not gating."""
    from lab.lxmf_tracer import write_state_file

    # Point at a path where mkdir will fail (parent is a file, not a dir).
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir")
    result = write_state_file(
        [], fire_at_unix=1.0, self_short="x",
        state_dir=blocker / "child",
    )
    assert result is None
