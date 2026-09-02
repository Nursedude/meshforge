"""/api/status must report peers we deliberately stopped polling.

WHY THIS FILE EXISTS (2026-09-02)
---------------------------------
The role filter drops a map-less peer BEFORE the FederationCollector is built,
so that peer disappears from `peers` and `peer_status` entirely. If nothing
else names it, "deliberately not watched" occupies exactly the same silence as
"watched and fine" (honest_failure_modes #9).

I added `non_federating` to `_merge_federation` (the geojson block), verified
the filter live, and it did NOT appear on /api/status — because that endpoint
builds its OWN federation block, independently, in _map_status_endpoints.py.
Two consumers of one fact, and I had only fed one (hfm #5). That is the exact
regression this file guards: the ENDPOINT is the operator's consumer-of-record,
so it is the one that must be pinned.
"""
import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils._map_status_endpoints import StatusEndpointsMixin  # noqa: E402


class _Harness(StatusEndpointsMixin):
    """Minimal stand-in for the BaseHTTPRequestHandler the mixin lives on."""

    def __init__(self, collector):
        self.collector = collector
        self.wfile = io.BytesIO()
        self._headers = {}

    # --- HTTP plumbing the mixin calls ---
    def send_response(self, code):
        self.code = code

    def send_header(self, k, v):
        self._headers[k] = v

    def _send_cors_header(self):
        pass

    def end_headers(self):
        pass

    # --- blocks this test does not exercise ---
    def _read_watchdog_block(self):
        return {}

    def _read_mini_state_block(self):
        return {}

    def _read_claw_state_block(self):
        return {"installed": False}

    def _get_radio_status_summary(self):
        return {}

    def _get_local_radio_config(self):
        return {}

    # --- run it ---
    def payload(self):
        self._serve_status()
        return json.loads(self.wfile.getvalue().decode())


class _Collector:
    """Plain stub, deliberately NOT a MagicMock.

    _serve_status serializes the whole payload, and a MagicMock's auto-created
    attributes are not JSON-serializable — the test would then fail on the
    scaffolding rather than on the federation block it exists to pin.
    """

    def __init__(self, federation, non_federating):
        self._federation = federation
        self._non_federating = non_federating
        self._history = None
        self._directory_response_cache = None
        self._geojson_response_cache = None
        self._topology_response_cache = None

    def get_source_diagnostics(self):
        return {}

    def get_nodes_without_position(self):
        return []


def _collector(*, federation, non_federating):
    return _Collector(federation, non_federating)


def test_excluded_peer_is_named_on_the_status_endpoint():
    """The regression that got past me: filter worked, endpoint stayed silent."""
    snap = MagicMock()
    snap.last_sync = None
    snap.last_attempt = None
    snap.by_node = {}
    snap.peer_status = {}
    fed = MagicMock()
    fed.peers = ["mapbox.example"]
    fed.get_snapshot.return_value = snap
    c = _collector(federation=fed,
                   non_federating={"gwonly.example": "declared role serves no map"})

    block = _Harness(c).payload()["federation"]
    assert block["enabled"] is True
    assert "gwonly.example" not in block["peers"]
    assert block["non_federating"] == {
        "gwonly.example": "declared role serves no map"}


def test_key_present_even_when_nothing_is_excluded():
    """An always-present key means a reader never has to distinguish 'absent'
    from 'empty' — the ambiguity that hid this in the first place."""
    snap = MagicMock()
    snap.last_sync = None
    snap.last_attempt = None
    snap.by_node = {}
    snap.peer_status = {}
    fed = MagicMock()
    fed.peers = []
    fed.get_snapshot.return_value = snap
    block = _Harness(
        _collector(federation=fed, non_federating={})).payload()["federation"]
    assert block["non_federating"] == {}


def test_federation_disabled_branch_still_names_excluded_peers():
    """Every peer being map-less disables federation entirely. That is exactly
    when the excluded names matter most, so the disabled branch carries them."""
    c = _collector(federation=None,
                   non_federating={"gw1": "declared role serves no map",
                                   "gw2": "declared role serves no map"})
    block = _Harness(c).payload()["federation"]
    assert block["enabled"] is False
    assert set(block["non_federating"]) == {"gw1", "gw2"}


def test_snapshot_error_branch_still_names_excluded_peers():
    fed = MagicMock()
    fed.peers = ["x"]
    fed.get_snapshot.side_effect = RuntimeError("snapshot boom")
    c = _collector(federation=fed,
                   non_federating={"gwonly": "declared role serves no map"})
    block = _Harness(c).payload()["federation"]
    assert "error" in block
    assert block["non_federating"] == {"gwonly": "declared role serves no map"}


def test_collector_absent_does_not_crash():
    h = _Harness(None)
    block = h.payload()["federation"]
    assert block["enabled"] is False
    assert block["non_federating"] == {}
