"""Tests for MapRequestHandler — focused on T1.2 gzip behavior.

The handler subclasses SimpleHTTPRequestHandler whose __init__ wants a real
socket. We bypass that by constructing instances via __new__ and stubbing
the minimum surface area each test exercises (`headers`, `wfile`, the
status/header sinks). This keeps the gzip path testable without standing
up a real HTTP server.
"""

from io import BytesIO
from unittest.mock import MagicMock

import gzip
import json

import pytest

from utils.map_http_handler import MapRequestHandler


def _make_handler(accept_encoding: str = "") -> MapRequestHandler:
    """Build a MapRequestHandler with just enough state to call _serve_json."""
    h = MapRequestHandler.__new__(MapRequestHandler)
    h.headers = {"Accept-Encoding": accept_encoding} if accept_encoding else {}
    h.wfile = BytesIO()
    h.send_response = MagicMock()
    h.end_headers = MagicMock()
    sent_headers: list = []
    h.send_header = lambda k, v: sent_headers.append((k, v))
    h._sent_headers = sent_headers
    return h


class TestClientAcceptsGzip:
    @pytest.mark.parametrize("header,expected", [
        ("", False),
        ("identity", False),
        ("gzip", True),
        ("gzip, deflate, br", True),
        ("deflate, gzip", True),
        ("gzip;q=0.5", True),
        ("gzip;q=0", False),
        ("identity;q=1, gzip;q=0", False),
    ])
    def test_header_parsing(self, header, expected):
        h = _make_handler(header)
        assert h._client_accepts_gzip() is expected


class TestServeJsonGzip:
    """Server gzips when client accepts AND payload exceeds threshold."""

    def test_small_payload_not_gzipped_even_when_accepted(self):
        # 100 features * ~50 bytes each is well under the 10 KB threshold.
        h = _make_handler("gzip")
        h._serve_json({"type": "FeatureCollection", "features": []})
        body = h.wfile.getvalue()
        # Must be raw JSON, not gzip magic bytes (0x1f 0x8b).
        assert not body.startswith(b"\x1f\x8b")
        encoding_headers = [v for k, v in h._sent_headers if k == "Content-Encoding"]
        assert encoding_headers == [], "small payload was gzipped"

    def test_large_payload_gzipped_when_client_accepts(self):
        # Build payload comfortably above the 10 KB threshold.
        payload = {"items": ["x" * 100 for _ in range(200)]}
        h = _make_handler("gzip")
        h._serve_json(payload)
        body = h.wfile.getvalue()
        assert body.startswith(b"\x1f\x8b"), "expected gzip magic bytes"
        # Round-trip: gunzip → json must equal original payload.
        assert json.loads(gzip.decompress(body)) == payload
        encoding_headers = [v for k, v in h._sent_headers if k == "Content-Encoding"]
        assert encoding_headers == ["gzip"]
        # Content-Length must equal the gzipped length, not the raw length.
        cl_headers = [int(v) for k, v in h._sent_headers if k == "Content-Length"]
        assert cl_headers == [len(body)]

    def test_large_payload_not_gzipped_without_accept_encoding(self):
        payload = {"items": ["x" * 100 for _ in range(200)]}
        h = _make_handler("")  # no Accept-Encoding header
        h._serve_json(payload)
        body = h.wfile.getvalue()
        assert not body.startswith(b"\x1f\x8b")
        # Plain JSON round-trips.
        assert json.loads(body) == payload
        encoding_headers = [v for k, v in h._sent_headers if k == "Content-Encoding"]
        assert encoding_headers == []

    def test_large_payload_not_gzipped_when_gzip_explicitly_disabled(self):
        payload = {"items": ["x" * 100 for _ in range(200)]}
        h = _make_handler("identity, gzip;q=0")
        h._serve_json(payload)
        body = h.wfile.getvalue()
        assert not body.startswith(b"\x1f\x8b")

    def test_vary_accept_encoding_always_sent(self):
        # Vary: Accept-Encoding must be advertised on EVERY response so
        # caches/CDNs key correctly, regardless of whether THIS response
        # was gzipped.
        h = _make_handler("")
        h._serve_json({"small": "payload"})
        vary_headers = [v for k, v in h._sent_headers if k == "Vary"]
        assert "Accept-Encoding" in vary_headers
