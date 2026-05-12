"""Tests for src/lab/_lab_common.py — wire format + helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lab._lab_common import (
    AckMessage,
    PingMessage,
    make_ack_body,
    make_ping_body,
    parse_ack,
    parse_ping,
    short_name,
)


# ---------------------------------------------------------------- PING/ACK


def test_make_ping_body_round_trip():
    body = make_ping_body(42, "box-a")
    parsed = parse_ping(body)
    assert parsed == PingMessage(seq=42, sender="box-a")


def test_make_ack_body_round_trip():
    body = make_ack_body(42, "box-a", "2026-05-12T03:00:00Z")
    parsed = parse_ack(body)
    assert parsed == AckMessage(
        seq=42, orig="box-a", recv_at_iso="2026-05-12T03:00:00Z",
    )


def test_make_ack_body_defaults_recv_at_to_now_iso():
    body = make_ack_body(1, "moc3")
    parsed = parse_ack(body)
    assert parsed is not None
    # Format must parse — exact value is now-ish, content sanity only.
    assert parsed.recv_at_iso.endswith("Z")
    assert "T" in parsed.recv_at_iso


@pytest.mark.parametrize("bad", [
    "",
    "hello world",
    "PING seq=abc from=moc",      # non-numeric seq
    "PING seq=1",                  # missing from
    "ACK seq=1 orig=moc",          # ACK shape passed to PING parser
])
def test_parse_ping_rejects_garbage(bad):
    assert parse_ping(bad) is None


@pytest.mark.parametrize("bad", [
    "",
    "hello world",
    "ACK seq=abc orig=moc recv_at=2026-05-12T03:00:00Z",
    "ACK seq=1 orig=moc",         # missing recv_at
    "PING seq=1 from=moc",         # PING shape passed to ACK parser
])
def test_parse_ack_rejects_garbage(bad):
    assert parse_ack(bad) is None


def test_parse_ping_handles_extra_whitespace():
    assert parse_ping("  PING   seq=7   from=moc1   ") == PingMessage(
        seq=7, sender="moc1",
    )


def test_parse_none_is_none():
    assert parse_ping(None) is None
    assert parse_ack(None) is None


# ------------------------------------------------------------------ short_name


def test_short_name_is_first_hostname_section(monkeypatch):
    monkeypatch.setattr(
        "lab._lab_common.socket.gethostname",
        lambda: "moc1.lan.example.org",
    )
    assert short_name() == "moc1"


def test_short_name_falls_back_on_oserror(monkeypatch):
    def _boom():
        raise OSError("hostname lookup failed")
    monkeypatch.setattr("lab._lab_common.socket.gethostname", _boom)
    assert short_name() == "unknown"
