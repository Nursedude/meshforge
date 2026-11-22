"""WITNESS-TUI r4 finding 1 — Shared Instance belongs to the LEG, not the health rows.

Fable r3: `_shown_interfaces` excluded "Shared Instance" from leg [6/6]
itself, so a Shared Instance with filtered ↓ > 0 printed nothing and the
summary said "All checks passed"; a listing with ONLY a Shared Instance
said "rnstatus returned nothing" (false — it returned a listing).

Contract kept: Shared Instance is NEVER a health row (rx-without-tx is
not an RX-only link fault; `check_rns_interface_health` 4-tuple callers).

Run: python3 -m pytest tests/test_rns_traffic_shared_instance.py -v
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))

from utils.rns_status_parser import FilterState, InterfaceTraffic, parse_rnstatus  # noqa: E402

SI = "Shared Instance[rns/default]"
SI_FILTERED = (
    f" {SI}\n    Status    : Up\n    Serving   : 2 programs\n"
    "    Traffic   : ↑0 B        0 bps\n                ↓900 B      0 bps\n"
    "    Filtered  : ↑0 B  0 bps\n                ↓491 B  0 bps\n\n"
)
TCP = (
    " TCPInterface[Regional entry point/example.invalid:4242]\n    Status    : Up\n"
    "    Traffic   : ↑478 B      0 bps\n                ↓254 B      0 bps\n"
    "    Filtered  : ↑0 B  0 bps\n                ↓0 B  0 bps\n\n"
)


def _traffic(raw, state=FilterState.COUNTED, reason=""):
    return InterfaceTraffic(parse_rnstatus(raw), state, reason)


def _leg6(traffic):
    from handlers._rns_diagnostics_engine import interface_traffic_leg
    return interface_traffic_leg(traffic, running=True, instance_ok=True,
                                 rnstatus_present=True)


def _health(traffic):
    from handlers._rns_diagnostics_engine import check_rns_interface_health
    return check_rns_interface_health(traffic)


def test_shared_instance_filtered_is_shown_and_warned():
    out = _leg6(_traffic(SI_FILTERED + TCP))
    lines = out.lines
    i = lines.index(f"  {SI}: ↑0 B ↓900 B")
    assert lines[i + 1].startswith("      rx accepted 900 B · filtered 491 B ⚠"), lines
    assert out.warnings == [
        "1 interface(s) filtered inbound traffic — dropped by this RNS "
        "without a log line (see [6/6])"], out.warnings
    assert out.issues == []                    # rx-without-tx is not RX-only here
    assert out.unobserved == []


def test_shared_instance_only_listing_is_not_returned_nothing():
    out = _leg6(_traffic(SI_FILTERED))
    assert out.unobserved == [], out.unobserved
    assert f"  {SI}: ↑0 B ↓900 B" in out.lines
    assert not any("Could not read" in l for l in out.lines), out.lines
    assert len(out.warnings) == 1


def test_shared_instance_unread_counters_are_named_blind():
    raw = f" {SI}\n    Status    : Up\n\n" + TCP
    out = _leg6(_traffic(raw))
    assert out.unobserved == [
        f"interface traffic (1 interface(s) listed without readable counters: {SI})"
    ], out.unobserved


def test_shared_instance_counted_without_filtered_line_is_blind():
    raw = SI_FILTERED.replace("    Filtered  : ↑0 B  0 bps\n                ↓491 B  0 bps\n", "")
    out = _leg6(_traffic(raw + TCP))
    assert out.unobserved == [
        f"filtered rx (1 interface(s) printed no Filtered line: {SI})"], out.unobserved


def test_shared_instance_is_still_never_a_health_row():
    rows = _health(_traffic(SI_FILTERED + TCP))
    assert [r[0] for r in rows] == [
        "TCPInterface[Regional entry point/example.invalid:4242]"]
    assert _health(_traffic(SI_FILTERED)) == []
