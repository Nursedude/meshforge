"""MAP-H H16 — the map never draws a path nobody measured.

Two halves of one lie:

1. ``MessageListener._handle_text_message`` mapped a MISSING ``hopStart``
   (hop count unknown) to ``hops_away = 0`` — "heard direct". A degraded
   value inside the healthy domain (honest_failure_modes #1). Unknown must
   travel as ``None``.
2. ``web/node_map.html`` animated every live message as a straight line
   sender → ``'local'``, whatever the hop count (a 3-hop packet drawn as
   direct). A line may be drawn only for a packet MEASURED direct
   (``hops_away === 0``), to THIS box's own marker (``is_self``, taken from
   the radio connection, never inferred from traffic); otherwise no line,
   and the message row says how many hops it took, or that hops are unknown.

The Python half drives the REAL handler. The page half is static (no JS
runtime in the suite — the same convention as test_map_marker_icon_key).
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.message_listener import MessageListener  # noqa: E402

HTML_PATH = os.path.join(os.path.dirname(__file__), "..", "web", "node_map.html")
with open(HTML_PATH, encoding="utf-8") as _f:
    HTML = _f.read()


def _rx(**hops):
    """Run one real text packet through the listener; return the callback dict."""
    listener = MessageListener(store_messages=False)
    got = []
    listener.add_callback(got.append)
    packet = {"fromId": "!a1b2c3d4", "toId": "^all", "channel": 0,
              "rxSnr": 5.0, "rxRssi": -80, **hops}
    listener._handle_text_message(packet, {"payload": b"aloha"}, "!a1b2c3d4")
    assert len(got) == 1, "the packet must reach the callback"
    return got[0]


# ── 1. the listener: unknown hops are None, never 0 ─────────────────────────

def test_missing_hop_start_is_unknown_not_direct():
    msg = _rx(hopLimit=3)
    assert msg["hops_away"] is None
    assert msg["hop_start"] is None


def test_no_hop_fields_at_all_is_unknown():
    assert _rx()["hops_away"] is None


def test_hop_start_zero_is_unknown():
    # pre-2.3 firmware / stripped header: hopStart 0 carries no count
    assert _rx(hopStart=0, hopLimit=0)["hops_away"] is None


def test_direct_packet_is_zero():
    assert _rx(hopStart=3, hopLimit=3)["hops_away"] == 0


def test_relayed_packet_counts_hops():
    assert _rx(hopStart=3, hopLimit=1)["hops_away"] == 2


def test_absent_hop_limit_is_the_wire_zero():
    # review F1: hopLimit is proto3 implicit-presence; meshtastic's
    # MessageToDict omits it when 0 — the packet spent its LAST hop.
    msg = _rx(hopStart=3)
    assert msg["hops_away"] == 3
    assert msg["hop_limit"] == 0


def test_published_hop_fields_are_the_validated_ones():
    # review F4: no raw out-of-range value rides beside hops_away=None
    msg = _rx(hopStart=9, hopLimit=1)
    assert msg["hops_away"] is None and msg["hop_start"] is None


@pytest.mark.parametrize("hops", [
    {"hopStart": 3, "hopLimit": 5},        # limit above start: impossible
    {"hopStart": 9, "hopLimit": 1},        # start above the 7-hop ceiling
    {"hopStart": "3", "hopLimit": 1},      # wrong type
    {"hopStart": True, "hopLimit": 0},     # bool is not a count
])
def test_impossible_header_is_unknown(hops):
    assert _rx(**hops)["hops_away"] is None


# ── 2. the page: a line only for a packet measured direct ───────────────────

def _body(name: str) -> str:
    m = re.search(r"function %s\(([^)]*)\)\s*\{" % name, HTML)
    assert m, f"{name} not found in node_map.html"
    depth, i = 1, m.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(HTML[i], 0)
        i += 1
    return HTML[m.end():i - 1]


def test_no_flow_line_to_a_hardcoded_local_marker():
    assert "'local'" not in _body("handleNewMessage")


def test_flow_line_only_for_a_measured_direct_packet():
    body = _body("handleNewMessage")
    assert "animateMessageFlow(" in body
    call = body.index("animateMessageFlow(")
    guard = body.rfind("hops_away === 0", 0, call)
    assert guard != -1, "the flow line must be guarded by hops_away === 0"


def test_flow_target_is_this_boxs_own_marker():
    body = _body("selfMarkerId")
    assert "is_self" in body
    assert "selfMarkerId()" in _body("handleNewMessage")


def test_message_row_says_the_hops_it_could_not_draw():
    body = _body("hopLabel")
    assert "hops unknown" in body
    assert "not measured" in body
    assert "hops not recorded" in body          # review F2: stored rows
    render = _body("renderReceivedMessages")
    assert "hopLabel(" in render
    # review F3: shown ON the row, escaped — not only in the hover title
    assert '<span class="msg-hops">${escHtml(hops)}</span>' in render
