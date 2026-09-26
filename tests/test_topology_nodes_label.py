"""View Nodes must say what its list is (2026-09-26 live render).

"Found 820 nodes:" read as a live count while the tracker served a 54-day-old
node_cache.json — and only the first 50 were listed.
"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402
from launcher_tui.handlers.topology import TopologyHandler  # noqa: E402


def _node(i):
    return SimpleNamespace(id=f"!{i:08x}", name=f"n{i:03d}", short_name="",
                           network="meshtastic", is_online=False)


def _menu_text(n):
    d = FakeDialog()
    h = TopologyHandler()
    h.ctx = make_handler_context(dialog=d)
    tracker = SimpleNamespace(get_all_nodes=lambda: [_node(i) for i in range(n)])
    with patch.object(h, "_get_topology", return_value=None), \
         patch.object(h, "_get_node_tracker", return_value=tracker), \
         patch.object(h, "_node_cache_source", return_value="Source: X, saved 54d ago"):
        h._show_topology_nodes()
    return next(c[1][1] for c in d.calls if c[0] == "menu")


def test_truncated_list_says_so_and_names_its_source():
    text = _menu_text(820)
    assert "820 nodes (first 50 by name)" in text
    assert "saved 54d ago" in text


def test_complete_list_does_not_claim_truncation():
    text = _menu_text(12)
    assert text.startswith("12 nodes\n") and "first" not in text


# --- Export must not announce an empty graph as "Exported" (2026-09-26) ---

import pytest  # noqa: E402

from launcher_tui.handlers import topology as topo_mod  # noqa: E402


class _Topo:
    def __init__(self, tracking):
        self._t = tracking

    def is_tracking(self):
        return self._t


@pytest.mark.parametrize("fmt", ["geojson", "csv", "graphml", "d3"])
def test_export_refuses_a_graph_this_process_never_built(fmt):
    d = FakeDialog()
    h = TopologyHandler()
    h.ctx = make_handler_context(dialog=d)

    class _Viz:
        @staticmethod
        def from_topology(t):
            raise AssertionError("visualizer built for an unobservable graph")

    with patch.object(h, "_get_topology", return_value=_Topo(False)), \
         patch.object(topo_mod, "_TopologyVisualizer", _Viz):
        h._export_topology_data(fmt)
    (msg,) = [c[1][1] for c in d.calls if c[0] == "msgbox"]
    assert msg.startswith(topo_mod.GRAPH_NOT_HERE)
