"""RNS path_table entries are LISTS with hops at IDX_PT_HOPS (2026-09-23).

Three readers parsed them as tuples with hops at [1]; a list never matched,
so every path-table node recorded hops=0 (1,746 of 2,138 on moc, zero real
hop counts). Pinned: the parser reads the real shape, unknown is None (never
0), and the indices match the installed RNS when it is importable.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gateway import network_topology as nt  # noqa: E402


class _Iface:
    hash = b"\x01" * 16


def _entry(hops=3, iface=None):
    # [timestamp, received_from, hops, expires, random_blobs, receiving_interface, packet_hash]
    return [1790000000.0, b"\xaa" * 16, hops, 1790600000.0, [], iface or _Iface(), b"\xbb" * 32]


def test_reads_hops_from_the_rns_list_shape():
    assert nt.path_entry_hops(_entry(4)) == 4


def test_received_from_bytes_is_never_read_as_hops():
    # The old parser read [1] (received_from) — bytes, so it fell to 0.
    e = _entry(2)
    assert nt.path_entry_hops(e) == 2 and e[1] != 2


@pytest.mark.parametrize("bad", [None, [], [1.0, b"x"], "abc", _entry(hops=None),
                                 _entry(hops=True), _entry(hops=-1)])
def test_unknown_is_none_never_zero(bad):
    assert nt.path_entry_hops(bad) is None


def test_interface_hash_is_read_from_the_receiving_interface():
    assert nt.path_entry_interface_hash(_entry()) == b"\x01" * 16
    assert nt.path_entry_interface_hash([1.0]) is None


def test_indices_match_installed_rns():
    pytest.importorskip("RNS")
    # IDX_PT_* are MODULE-level in RNS/Transport.py; RNS.Transport is the class.
    mod = sys.modules.get("RNS.Transport")
    if mod is None or not hasattr(mod, "IDX_PT_HOPS"):
        pytest.skip("installed RNS exposes no IDX_PT_* constants")
    assert (nt.IDX_PT_HOPS, nt.IDX_PT_RVCD_IF) == (mod.IDX_PT_HOPS, mod.IDX_PT_RVCD_IF)


def test_no_tuple_hops_parse_remains():
    # hfm #5: the defect lived in three copies; none may come back.
    root = os.path.join(os.path.dirname(__file__), "..", "src", "gateway")
    for name in ("network_topology.py", "node_tracker.py"):
        src = open(os.path.join(root, name), encoding="utf-8").read()
        assert "hops = path_data[1]" not in src, name


def test_monitor_skips_and_counts_unreadable_entries(monkeypatch):
    class _T:
        path_table = {b"\x02" * 16: _entry(5), b"\x03" * 16: ("garbage",)}

    class _RNS:
        Transport = _T

    monkeypatch.setattr(nt, "_get_rns", lambda: (_RNS, True))
    m = nt.PathTableMonitor()
    m._check_path_table()
    assert m.unparsed_path_entries == 1
    assert m._last_snapshot[b"\x02" * 16].hops == 5
    assert b"\x03" * 16 not in m._last_snapshot


def test_tracker_refuses_a_zero_echoed_back_by_add_edge():
    from gateway import node_tracker as ntr
    from types import SimpleNamespace as N
    tr = ntr.UnifiedNodeTracker.__new__(ntr.UnifiedNodeTracker)
    import threading
    tr._lock = threading.RLock()
    node = N(hops=None, update_seen=lambda: None)
    dest = b"\x09" * 16
    tr._nodes = {f"rns_{dest.hex()[:16]}": node}
    old = ntr.RNS_SERVICES_AVAILABLE
    ntr.RNS_SERVICES_AVAILABLE = True
    try:
        tr._on_topology_event(N(dest_hash=dest, new_value=0))
        assert node.hops is None          # the echoed sentinel is refused
        tr._on_topology_event(N(dest_hash=dest, new_value=3))
        assert node.hops == 3
        tr._on_topology_event(N(dest_hash=dest, new_value=0))
        assert node.hops == 3             # and never overwrites a real value
    finally:
        ntr.RNS_SERVICES_AVAILABLE = old


def test_tracker_accepts_a_zero_measured_by_the_path_table():
    # Review 2026-09-23: on a shared-instance client RNS DECREMENTS the inbound
    # hop for the interface to rnsd, so a destination announced by another
    # local client on this box is a REAL 0 (moc: 219 of 263 discoveries in one
    # afternoon). Only the add_edge echo (EDGE_*) is the sentinel.
    from gateway import node_tracker as ntr
    from gateway.network_topology import TopologyEvent, TopologyEventType
    from types import SimpleNamespace as N
    import threading
    tr = ntr.UnifiedNodeTracker.__new__(ntr.UnifiedNodeTracker)
    tr._lock = threading.RLock()
    node = N(hops=None, update_seen=lambda: None)
    dest = b"\x0a" * 16
    tr._nodes = {f"rns_{dest.hex()[:16]}": node}
    old = ntr.RNS_SERVICES_AVAILABLE
    ntr.RNS_SERVICES_AVAILABLE = True
    try:
        tr._on_topology_event(TopologyEvent(event_type=TopologyEventType.EDGE_UPDATED,
                                            dest_hash=dest, new_value=0))
        assert node.hops is None          # the echo is refused
        tr._on_topology_event(TopologyEvent(event_type=TopologyEventType.PATH_DISCOVERED,
                                            dest_hash=dest, new_value=0))
        assert node.hops == 0             # the measurement is kept
        tr._on_topology_event(TopologyEvent(event_type=TopologyEventType.HOP_COUNT_CHANGED,
                                            dest_hash=dest, old_value=0, new_value=2))
        assert node.hops == 2
    finally:
        ntr.RNS_SERVICES_AVAILABLE = old


def test_cache_load_drops_the_rns_zero_sentinel_only():
    from gateway.network_topology import cached_rns_hops as _cached_hops
    assert _cached_hops({"network": "rns", "hops": 0}) is None
    assert _cached_hops({"network": "rns", "hops": 2}) == 2
    assert _cached_hops({"network": "meshtastic", "hops": 0}) == 0
    assert _cached_hops({"network": "both", "hops": 0}) == 0
    assert _cached_hops({"network": "rns"}) is None
