"""Smoke tests — proves the harness wires up before we add real e2e tests."""

from __future__ import annotations

import urllib.error
import urllib.request


def test_mock_meshtasticd_accepts_toradio_put(mock_meshtasticd):
    body = b"\x08\x01"
    req = urllib.request.Request(
        url=f"{mock_meshtasticd.url}/api/v1/toradio",
        data=body,
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=2) as resp:
        assert resp.status == 200
    assert mock_meshtasticd.received_toradio == [body]


def test_mock_meshtasticd_rejects_fromradio_get(mock_meshtasticd):
    """Issue #29 forbids fromradio reads on TX paths. Mock returns 410 so
    any accidental read shows up as a loud test failure, not silent drift."""
    try:
        urllib.request.urlopen(
            f"{mock_meshtasticd.url}/api/v1/fromradio", timeout=2
        )
    except urllib.error.HTTPError as e:
        assert e.code == 410
    else:
        raise AssertionError("expected HTTP 410 for /api/v1/fromradio")


def test_mock_meshtasticd_clear_resets_capture(mock_meshtasticd):
    req = urllib.request.Request(
        url=f"{mock_meshtasticd.url}/api/v1/toradio",
        data=b"first",
        method="PUT",
    )
    urllib.request.urlopen(req, timeout=2).read()
    assert len(mock_meshtasticd.received_toradio) == 1
    mock_meshtasticd.clear()
    assert mock_meshtasticd.received_toradio == []
