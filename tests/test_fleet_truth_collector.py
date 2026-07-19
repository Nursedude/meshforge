"""Tests for the fleet-truth HTTP fan-out + TTL cache (fleet_truth_collector).

I/O is mocked; the shaping logic itself is tested in test_fleet_truth.py. Here we
pin the honesty of the collector layer: a peer that can't be reached becomes a
DARK snapshot (never dropped), the TTL cache single-flights, and a build blow-up
yields a self-describing dark document rather than a 500.

Run: python3 -m pytest tests/test_fleet_truth_collector.py -v
"""
import sys
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils import fleet_truth_collector as c  # noqa: E402


class TestFetchPeer:
    def test_both_none_is_error_dark_snapshot(self):
        with patch.object(c, "_http_get_json", return_value=None), \
             patch.object(c, "_resolve_peer", return_value=("h", "dns")):
            snap = c._fetch_peer("moc9", is_self=False, port=5000)
        assert snap["status"] is None and snap["slo"] is None
        assert snap["error"] and "moc9" in snap["error"]
        assert snap["answered_at"] is None
        assert snap["resolution_method"] == "dns"

    def test_answered_snapshot_carries_data(self):
        with patch.object(c, "_http_get_json",
                          side_effect=[{"overall_status": "ready"}, {"app": {}}]), \
             patch.object(c, "_resolve_peer", return_value=("h", "bare")):
            snap = c._fetch_peer("moc", is_self=False, port=5000)
        assert snap["slo"] == {"overall_status": "ready"}
        assert snap["status"] == {"app": {}}
        assert snap["answered_at"] is not None

    def test_ip_shaped_hosts_entry_never_becomes_the_alias(self):
        """2026-07-19 adversarial review (MF014/MF015): an IP-shaped line in
        fleet_hosts must not surface as the box alias — masked label +
        ip_literal method instead, no dotted quad anywhere in the snapshot."""
        import re
        with patch.object(c, "_http_get_json", return_value=None), \
             patch.object(c, "_resolve_peer", return_value=("203.0.113.7", "ip_literal")):
            snap = c._fetch_peer("203.0.113.7", is_self=False, port=5000)
        assert snap["alias"].startswith("ip-entry-")
        assert snap["resolution_method"] == "ip_literal"
        assert not re.search(r"\d+\.\d+\.\d+\.\d+", repr(snap))

    def test_self_uses_localhost_no_resolve(self):
        with patch.object(c, "_http_get_json", return_value={"x": 1}), \
             patch.object(c, "_resolve_peer") as res:
            snap = c._fetch_peer("self-box", is_self=True, port=5000)
        res.assert_not_called()
        assert snap["resolution_method"] == "self"


class TestCache:
    def setup_method(self):
        c._cache["truth"] = None
        c._cache["built_at"] = 0.0

    def test_ttl_single_flight(self):
        calls = {"n": 0}

        def fake_collect(*, port):
            calls["n"] += 1
            return ([], 0)

        with patch.object(c, "collect_snapshots", side_effect=fake_collect):
            t1 = c.get_fleet_truth(force=True)   # builds
            t2 = c.get_fleet_truth()             # within TTL → cached
        assert calls["n"] == 1
        assert t1 is t2

    def test_force_rebuilds(self):
        calls = {"n": 0}

        def fake_collect(*, port):
            calls["n"] += 1
            return ([], 0)

        with patch.object(c, "collect_snapshots", side_effect=fake_collect):
            c.get_fleet_truth(force=True)
            c.get_fleet_truth(force=True)
        assert calls["n"] == 2

    def test_build_error_yields_dark_doc_not_raise(self):
        with patch.object(c, "collect_snapshots", side_effect=RuntimeError("boom")):
            t = c.get_fleet_truth(force=True)
        assert t["fleet_state"] == "dark"
        assert t["fanout"]["stale"] is True
        assert "boom" in t["fanout"].get("error", "")
        assert t["schema"] == "fleet_truth/v1"
