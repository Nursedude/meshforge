"""Operator-declared extra trusted networks for the map's read gate (2026-09-25).

The gate trusted loopback + the box's OWN /24s, so the operator's workstation on
another of their LANs got a bare "forbidden" from the Fleet monitor on every box
not on that LAN. This pins: the file's parser (narrow on purpose — it widens who
may read journals), the merge into the gate, fail-closed on an unreadable file,
counts-not-networks in /api/status, and a 403 body that says why and how.
Values here are RFC 1918 examples, never an operator's real network (MF014).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils import map_http_handler as mh  # noqa: E402
from utils import map_data_service as mds  # noqa: E402


def test_parser_accepts_only_private_ipv4_slash_24():
    origins, refused = mh.load_extra_trusted_networks(
        "# operator's second building\n"
        "10.99.1.0/24\n"
        "172.20.5.0/24   # lab\n"
        "\n"
        "8.8.8.0/24\n"          # public
        "10.99.0.0/16\n"        # wider than /24
        "10.99.2.7/24\n"        # host bits set
        "not-a-network\n"
        "fd00::/64\n")          # IPv6
    assert origins == ["http://10.99.1.", "http://172.20.5."]
    assert len(refused) == 5
    assert any("private" in r for r in refused) and any("/16" in r for r in refused)


def test_a_declared_network_is_trusted_and_nothing_else_is(tmp_path):
    f = tmp_path / "trusted_networks"
    f.write_text("10.99.1.0/24\n")
    origins = mds._apply_extra_trusted_networks(["http://localhost", "http://10.50.0."], path=str(f))
    assert mh._client_ip_trusted("10.99.1.28", origins)            # the declared LAN
    assert mh._client_ip_trusted("10.50.0.9", origins)             # own LAN still trusted
    assert not mh._client_ip_trusted("10.99.2.28", origins)        # the next /24 is not
    assert mh.MapRequestHandler.extra_networks == {"state": "ok", "accepted": 1, "refused": 0}


def test_absent_file_changes_nothing(tmp_path):
    base = ["http://localhost", "http://10.50.0."]
    assert mds._apply_extra_trusted_networks(base, path=str(tmp_path / "nope")) == base
    assert mh.MapRequestHandler.extra_networks["state"] == "absent"


def test_unreadable_file_fails_closed(tmp_path):
    d = tmp_path / "trusted_networks"
    d.mkdir()                                                       # a directory: unreadable as a file
    base = ["http://localhost"]
    assert mds._apply_extra_trusted_networks(base, path=str(d)) == base
    assert mh.MapRequestHandler.extra_networks["state"] == "unreadable"
    assert not mh._client_ip_trusted("10.99.1.28", base)


def test_refused_lines_are_counted_and_the_rest_still_applies(tmp_path):
    f = tmp_path / "trusted_networks"
    f.write_text("10.99.1.0/24\n8.8.8.0/24\n")
    origins = mds._apply_extra_trusted_networks(None, path=str(f))
    assert origins == ["http://10.99.1."]
    assert mh.MapRequestHandler.extra_networks == {"state": "ok", "accepted": 1, "refused": 1}


class _FakeHandler:
    """Just enough of MapRequestHandler to call _reject_if_untrusted."""
    allowed_origins = ["http://localhost", "http://10.50.0."]
    _reject_if_untrusted = mh.MapRequestHandler._reject_if_untrusted
    _client_is_trusted = mh.MapRequestHandler._client_is_trusted

    def __init__(self, client):
        self.client_address = (client, 50000)
        self.sent = None

    def _serve_json(self, body, status=200):
        self.sent = (status, body)


def test_the_403_says_why_and_how_without_publishing_the_trusted_networks():
    h = _FakeHandler("10.99.1.28")
    assert h._reject_if_untrusted() is True
    status, body = h.sent
    assert status == 403 and body["error"] == "forbidden" and body["client"] == "10.99.1.28"
    assert "10.99.1.28" in body["detail"] and mh.TRUSTED_NETWORKS_FILE in body["detail"]
    assert "10.50.0" not in str(body)                               # MF015: never the trusted set


def test_a_trusted_client_is_not_rejected():
    h = _FakeHandler("10.50.0.9")
    assert h._reject_if_untrusted() is False and h.sent is None


def test_the_fleet_page_shows_the_403_detail():
    html = (Path(__file__).resolve().parents[1] / "web" / "fleet.html").read_text()
    assert "doc.detail || doc.error" in html
