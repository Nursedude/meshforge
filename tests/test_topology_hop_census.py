"""TUI Topology stats — hop census (2026-09-23).

The pane printed "Total Edges 0 · Average Hops 0.00 · Maximum Hops 0" on moc
while the tracker held 2,138 nodes: the edge graph lives in the gateway
process, and every RNS hop count was the pre-fix 0 sentinel. Pinned: unknown
is counted, RNS 0 is the sentinel, Meshtastic 0 is real, and the pane never
prints an edge/hop NUMBER it could not observe.
"""
import os
import sys
from types import SimpleNamespace as N
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "launcher_tui"))

from handlers import topology as t  # noqa: E402


def test_rns_zero_is_sentinel_mesh_zero_is_real():
    c = t.hop_census([N(network="rns", hops=0), N(network="rns", hops=3),
                      N(network="rns", hops=None), N(network="meshtastic", hops=0)])
    assert c["rns"] == {"known": [3], "unknown": 1, "sentinel": 1}
    assert c["meshtastic"]["known"] == [0]


def test_garbage_hops_are_unknown():
    c = t.hop_census([N(network="rns", hops=True), N(network="rns", hops=-2),
                      N(network="rns", hops="3")])
    assert c["rns"] == {"known": [], "unknown": 3, "sentinel": 0}


def test_format_names_the_sentinel_and_buckets():
    lines = "\n".join(t.format_hop_census(t.hop_census(
        [N(network="rns", hops=h) for h in (1, 2, 2, 7, 0)])))
    assert "4 known" in lines and "1:1 2:2 4+:1; max 7" in lines
    assert "pre-fix sentinel, NOT a measurement" in lines


class _Tracker:
    def __init__(self, nodes):
        self._n = nodes

    def get_topology_stats(self):
        return None  # the TUI process: no gateway graph

    def get_all_nodes(self):
        return self._n

    def get_service_stats(self):
        return {}


def test_pane_never_prints_edge_or_hop_numbers_it_cannot_see(monkeypatch):
    h = t.TopologyHandler()
    h.ctx = MagicMock()
    monkeypatch.setattr(h, "_get_node_tracker",
                        lambda: _Tracker([N(network="rns", hops=0, is_online=False)] * 5))
    monkeypatch.setattr(h, "_get_topology", lambda: None)
    h._show_topology_stats()
    text = h.ctx.dialog.msgbox.call_args.args[1]
    assert "NOT OBSERVABLE from the TUI" in text
    assert "Total Edges:    0" not in text and "Maximum Hops:   0" not in text
    assert "5 recorded as 0 = the pre-fix sentinel" in text
    assert "Source:" in text


class _Graph:
    def __init__(self, live):
        self._live = live

    def is_tracking(self):
        return self._live


import pytest  # noqa: E402


@pytest.mark.parametrize("method", ["_show_topology_edges", "_show_topology_events",
                                    "_trace_path", "_show_ascii_topology"])
def test_graph_panes_say_not_observable_when_graph_is_not_live(monkeypatch, method):
    h = t.TopologyHandler()
    h.ctx = MagicMock()
    monkeypatch.setattr(h, "_get_topology", lambda: _Graph(False))
    getattr(h, method)()
    body = h.ctx.dialog.msgbox.call_args.args[1]
    assert body == t.GRAPH_NOT_HERE
    h.ctx.dialog.inputbox.assert_not_called()   # trace never prompts for a hash


def test_graph_observable_is_false_for_unknown_shapes():
    assert t.graph_observable(None) is False
    assert t.graph_observable(object()) is False     # predates is_tracking
    assert t.graph_observable(_Graph(True)) is True


def test_network_topology_is_tracking_follows_its_monitor():
    from gateway.network_topology import NetworkTopology
    topo = NetworkTopology()
    assert topo.is_tracking() is False              # a TUI process never starts it
    topo._path_monitor._running = True
    assert topo.is_tracking() is True
