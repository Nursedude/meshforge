"""Link Quality panes must not read "no links" from a graph never looked at.

The link graph lives in the gateway process; the TUI's topology singleton is
empty by construction. Every pane said "No links found" / "No link quality
data" on every box (2026-09-26 live render) — its Topology sibling was cured
2026-09-23 with graph_observable() and this handler was missed.
"""
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402
from launcher_tui.handlers import link_quality as lq  # noqa: E402
from launcher_tui.handlers.topology import GRAPH_NOT_HERE  # noqa: E402

PANES = ["_show_quality_overview", "_show_best_links", "_show_worst_links",
         "_show_quality_alerts", "_show_quality_trends"]


class _Topo:
    def __init__(self, tracking):
        self._t = tracking

    def is_tracking(self):
        return self._t


def _run(pane, tracking, scores):
    d = FakeDialog()
    h = lq.LinkQualityHandler()
    h.ctx = make_handler_context(dialog=d)
    with patch.object(lq, "get_network_topology", return_value=_Topo(tracking)), \
         patch.object(lq, "score_topology_edges", return_value=scores):
        getattr(h, pane)()
    return [c[1][1] for c in d.calls if c[0] == "msgbox"]


@pytest.mark.parametrize("pane", PANES)
def test_not_tracking_says_not_observable(pane):
    assert _run(pane, tracking=False, scores={}) == [GRAPH_NOT_HERE]


@pytest.mark.parametrize("pane", PANES)
def test_live_graph_with_no_links_says_so(pane):
    (msg,) = _run(pane, tracking=True, scores={})
    assert "live" in msg and "no links" in msg
