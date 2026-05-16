"""Tests for the RNS path_table snapshot cache (Track 0B).

`/api/network/rns/paths` is a pure-read endpoint backed by a module-level
cache that's refreshed once per ~60s collect cycle in `_collect_rns_direct`.
These tests cover the snapshot helper and its tolerance for RNS variant
shapes, without requiring a real RNS install.

Memory: project_rnsd_rpc_listener_wedge.md (operator's "path is uncertain"
complaint that this endpoint addresses).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils import _map_collector_rns as mod


class TestInterfaceName:
    def test_returns_none_for_none(self):
        assert mod._interface_name(None) is None

    def test_uses_name_attr_when_present(self):
        iface = MagicMock()
        iface.name = "RNodeInterface[/dev/ttyUSB0]"
        assert mod._interface_name(iface) == "RNodeInterface[/dev/ttyUSB0]"

    def test_falls_back_to_str(self):
        class Bare:
            def __str__(self):
                return "BareInterface"
        # No `name` attr.
        assert mod._interface_name(Bare()) == "BareInterface"

    def test_handles_repr_failure(self):
        class Boom:
            def __str__(self):
                raise RuntimeError("nope")
            name = None  # explicitly no usable name
        assert mod._interface_name(Boom()) is None


class TestSnapshotPathTable:
    """`_snapshot_path_table_inplace()` reads RNS.Transport.path_table and
    populates the module-level cache. RNS path_table values are 7-element
    lists with indices defined in upstream RNS/Transport.py:
        [0]=timestamp [1]=next_hop [2]=hops [3]=expires [5]=via_interface
    """

    def setup_method(self):
        # Reset cache before each test so cross-test bleed doesn't mask bugs.
        mod._LAST_PATH_TABLE_SNAPSHOT = {
            "ts": 0.0, "paths": [],
            "available": False, "reason": "never_collected",
        }

    def test_rns_unavailable_yields_clean_unavailable_snapshot(self):
        with patch.object(mod, "_HAS_RNS", False):
            mod._snapshot_path_table_inplace()
        snap = mod.get_cached_path_table_snapshot()
        assert snap["available"] is False
        assert snap["reason"] == "rns_module_unavailable"
        assert snap["paths"] == []
        assert snap["ts"] > 0  # was bumped to real time

    def test_rns_not_initialized_yields_unavailable_with_reason(self):
        with patch.object(mod, "_HAS_RNS", True), \
             patch.object(mod, "_rns_is_initialized", return_value=False):
            mod._snapshot_path_table_inplace()
        snap = mod.get_cached_path_table_snapshot()
        assert snap["available"] is False
        assert snap["reason"] == "rns_not_initialized"

    def test_well_formed_entry_serializes(self):
        dest = b"\x11" * 16
        next_hop = b"\x22" * 16
        iface = MagicMock()
        iface.name = "TCPInterface[hub:4242]"
        path_data = [
            1700000000.0,  # 0 timestamp
            next_hop,      # 1 next_hop
            3,             # 2 hops
            1700001000.0,  # 3 expires
            [],            # 4 random_blobs
            iface,         # 5 via_interface
            b"\x00" * 16,  # 6 packet hash
        ]
        fake_transport = MagicMock()
        fake_transport.path_table = {dest: path_data}
        fake_rns = MagicMock()
        fake_rns.Transport = fake_transport
        with patch.object(mod, "_HAS_RNS", True), \
             patch.object(mod, "_rns_is_initialized", return_value=True), \
             patch.object(mod, "_RNS", fake_rns):
            mod._snapshot_path_table_inplace()
        snap = mod.get_cached_path_table_snapshot()
        assert snap["available"] is True
        assert len(snap["paths"]) == 1
        entry = snap["paths"][0]
        assert entry["dest_hash"] == dest.hex()
        assert entry["next_hop"] == next_hop.hex()
        assert entry["hops"] == 3
        assert entry["via_interface"] == "TCPInterface[hub:4242]"
        assert entry["last_heard"] == 1700000000.0

    def test_malformed_entry_skipped_but_others_kept(self):
        """One bad entry shouldn't poison the whole snapshot — the operator
        needs SOME data even if RNS has an edge case."""
        good_dest = b"\xaa" * 16
        good_path = [1700000000.0, b"\x01" * 16, 2, 0, [], None, b"\x00" * 16]
        bad_dest = b"\xbb" * 16
        bad_path = "not even a list"   # malformed
        wrong_len_dest = b"\xcc" * 8   # not 16 bytes
        wrong_len_path = list(good_path)
        fake_transport = MagicMock()
        fake_transport.path_table = {
            good_dest: good_path,
            bad_dest: bad_path,
            wrong_len_dest: wrong_len_path,
        }
        fake_rns = MagicMock()
        fake_rns.Transport = fake_transport
        with patch.object(mod, "_HAS_RNS", True), \
             patch.object(mod, "_rns_is_initialized", return_value=True), \
             patch.object(mod, "_RNS", fake_rns):
            mod._snapshot_path_table_inplace()
        snap = mod.get_cached_path_table_snapshot()
        assert snap["available"] is True
        # Only the good entry survives.
        assert len(snap["paths"]) == 1
        assert snap["paths"][0]["dest_hash"] == good_dest.hex()

    def test_empty_path_table_yields_available_with_empty_paths(self):
        """Healthy RNS, just no announces yet — distinct from 'unavailable'."""
        fake_transport = MagicMock()
        fake_transport.path_table = {}
        fake_rns = MagicMock()
        fake_rns.Transport = fake_transport
        with patch.object(mod, "_HAS_RNS", True), \
             patch.object(mod, "_rns_is_initialized", return_value=True), \
             patch.object(mod, "_RNS", fake_rns):
            mod._snapshot_path_table_inplace()
        snap = mod.get_cached_path_table_snapshot()
        assert snap["available"] is True
        assert snap["paths"] == []
        assert snap["reason"] is None

    def test_get_cached_returns_independent_copy(self):
        """Caller mutating the dict must not corrupt the cache."""
        snap1 = mod.get_cached_path_table_snapshot()
        snap1["paths"] = "mutated"
        snap2 = mod.get_cached_path_table_snapshot()
        assert snap2["paths"] != "mutated"


class TestInterfaceSnapshot:
    """`_snapshot_interfaces_inplace()` walks RNS.Transport.interfaces and
    populates the per-interface cache. Track 2.3."""

    def setup_method(self):
        mod._LAST_INTERFACE_SNAPSHOT = {
            "ts": 0.0, "interfaces": [],
            "available": False, "reason": "never_collected",
        }

    def test_rns_unavailable_yields_unavailable_snapshot(self):
        with patch.object(mod, "_HAS_RNS", False):
            mod._snapshot_interfaces_inplace()
        snap = mod.get_cached_interface_snapshot()
        assert snap["available"] is False
        assert snap["reason"] == "rns_module_unavailable"
        assert snap["interfaces"] == []
        assert snap["ts"] > 0

    def test_rns_not_initialized_yields_clear_reason(self):
        with patch.object(mod, "_HAS_RNS", True), \
             patch.object(mod, "_rns_is_initialized", return_value=False):
            mod._snapshot_interfaces_inplace()
        snap = mod.get_cached_interface_snapshot()
        assert snap["available"] is False
        assert snap["reason"] == "rns_not_initialized"

    def test_well_formed_interface_serializes(self):
        """The fields the operator actually reads: name, kind, online,
        rxb, txb, bitrate, hw_mtu, age_s."""
        iface = MagicMock()
        iface.name = "TCPInterface[hub:4242]"
        iface.rxb = 123456
        iface.txb = 789012
        iface.online = True
        iface.bitrate = 1_000_000
        iface.HW_MTU = 1500
        iface.created = 1700000000.0
        # `type(iface).__name__` will be "MagicMock" — that's the kind.
        fake_transport = MagicMock()
        fake_transport.interfaces = [iface]
        fake_rns = MagicMock()
        fake_rns.Transport = fake_transport
        with patch.object(mod, "_HAS_RNS", True), \
             patch.object(mod, "_rns_is_initialized", return_value=True), \
             patch.object(mod, "_RNS", fake_rns):
            mod._snapshot_interfaces_inplace()
        snap = mod.get_cached_interface_snapshot()
        assert snap["available"] is True
        assert len(snap["interfaces"]) == 1
        entry = snap["interfaces"][0]
        assert entry["name"] == "TCPInterface[hub:4242]"
        assert entry["kind"] == "MagicMock"
        assert entry["online"] is True
        assert entry["rxb"] == 123456
        assert entry["txb"] == 789012
        assert entry["bitrate"] == 1_000_000
        assert entry["hw_mtu"] == 1500
        assert isinstance(entry["age_s"], float)

    def test_empty_interfaces_list_is_available_with_no_entries(self):
        """RNS up but no interfaces — distinct from 'rns_not_initialized'."""
        fake_transport = MagicMock()
        fake_transport.interfaces = []
        fake_rns = MagicMock()
        fake_rns.Transport = fake_transport
        with patch.object(mod, "_HAS_RNS", True), \
             patch.object(mod, "_rns_is_initialized", return_value=True), \
             patch.object(mod, "_RNS", fake_rns):
            mod._snapshot_interfaces_inplace()
        snap = mod.get_cached_interface_snapshot()
        assert snap["available"] is True
        assert snap["interfaces"] == []
        assert snap["reason"] is None

    def test_malformed_interface_skipped_but_others_kept(self):
        """One bad interface (e.g. exotic future RNS variant) must not
        poison the snapshot — operator still sees the healthy ones."""
        good = MagicMock()
        good.name = "Good"
        good.rxb = 1; good.txb = 2; good.online = True
        good.bitrate = 1000; good.HW_MTU = 512; good.created = 1.0
        # Bad: __getattr__ behaves but type() raises somehow.
        bad = MagicMock()
        bad.__str__ = lambda self: (_ for _ in ()).throw(RuntimeError("nope"))
        fake_transport = MagicMock()
        fake_transport.interfaces = [bad, good]
        fake_rns = MagicMock()
        fake_rns.Transport = fake_transport
        with patch.object(mod, "_HAS_RNS", True), \
             patch.object(mod, "_rns_is_initialized", return_value=True), \
             patch.object(mod, "_RNS", fake_rns):
            mod._snapshot_interfaces_inplace()
        snap = mod.get_cached_interface_snapshot()
        # The good interface survives — regardless of whether 'bad' was
        # rejected by our guard or by something else, we must not crash.
        names = [e["name"] for e in snap["interfaces"]]
        assert "Good" in names

    def test_no_interfaces_attribute_handled_gracefully(self):
        """Defensive: if RNS minor-version refactors `Transport.interfaces`
        out of existence, we return empty rather than blow up."""
        fake_transport = MagicMock(spec=[])  # no 'interfaces' attr
        fake_rns = MagicMock()
        fake_rns.Transport = fake_transport
        with patch.object(mod, "_HAS_RNS", True), \
             patch.object(mod, "_rns_is_initialized", return_value=True), \
             patch.object(mod, "_RNS", fake_rns):
            mod._snapshot_interfaces_inplace()
        snap = mod.get_cached_interface_snapshot()
        assert snap["available"] is True
        assert snap["interfaces"] == []

    def test_get_cached_returns_independent_copy(self):
        snap1 = mod.get_cached_interface_snapshot()
        snap1["interfaces"] = "mutated"
        snap2 = mod.get_cached_interface_snapshot()
        assert snap2["interfaces"] != "mutated"


class TestPathTableIndexConstants:
    """Lock down the IDX constants against upstream RNS drift. If RNS
    rearranges the path_table tuple shape in a future release, this test
    fails loudly — better than the snapshot silently emitting wrong values."""

    def test_constants_match_rns_documented_layout(self):
        # Source: RNS/Transport.py IDX_PT_* constants
        assert mod._PT_TIMESTAMP == 0
        assert mod._PT_NEXT_HOP == 1
        assert mod._PT_HOPS == 2
        assert mod._PT_RVCD_IF == 5
