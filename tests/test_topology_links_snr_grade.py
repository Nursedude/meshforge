"""Network Links grades SNR through the ONE grader (SNR stage 1, 2026-10-04).

It said "Bad" for anything <= -5 dB — the fleet's MEDIAN LongFast link
(-9 dB, 8.5 dB over the SF11 floor) — and "Excellent" only above +10, which
reported SNR never reaches (it saturates ~+6). Edges carry no SF yet (stage
2), so a below-ceiling SNR now says its margin is unknown instead of
guessing a grade.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402
from launcher_tui.handlers import topology as topo_mod  # noqa: E402
from launcher_tui.handlers.topology import TopologyHandler  # noqa: E402


class _Topo:
    def __init__(self, edges):
        self._e = edges

    def to_dict(self):
        return {"edges": self._e}


def _links_text(edges):
    d = FakeDialog()
    h = TopologyHandler()
    h.ctx = make_handler_context(dialog=d)
    with patch.object(h, "_get_topology", return_value=_Topo(edges)), \
         patch.object(topo_mod, "graph_observable", return_value=True):
        h._show_topology_edges()
    return "\n".join(c[1][1] for c in d.calls if c[0] == "msgbox")


def _edge(snr):
    return {"source_id": "!aaaaaaaa", "dest_id": "!bbbbbbbb", "hops": 1,
            "announce_count": 3, "is_active": True, "snr": snr}


def test_median_longfast_link_is_not_called_bad():
    text = _links_text([_edge(-9.0)])
    assert "Bad" not in text
    assert "margin: preset unknown" in text


def test_ceiling_reading_says_ceiling_not_excellent():
    text = _links_text([_edge(6.75)])
    assert "Excellent" not in text
    assert "at ceiling" in text


def test_missing_snr_is_unknown():
    assert "SNR: unknown" in _links_text([_edge(None)])
